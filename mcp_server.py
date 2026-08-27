"""Daedalus MCP server — exposes the extension command surface as MCP tools.

Runs in-process alongside server.py as a daemon thread on 127.0.0.1:8086 by
default (override with DAEDALUS_MCP_PORT), fronted by a reverse proxy at /mcp.
Tool handlers reach the bridge over HTTP rather than sharing its state, which
is the same indirection the CLI uses.

The Bearer token is compared with the bridge token resolved by the CLI's
existing configuration path before it enters the _token ContextVar and is
forwarded to the local bridge. Missing configuration fails closed.
"""
import contextlib
import math
import json, os, socket, sys, threading
from typing import Any
import httpx
from mcp.server.mcpserver import MCPServer, Image
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from daedalus_cli import SEGMENT_SIG_HEADER, ambiguous_request_carrier
from daedalus_cli.output import configure_stdio
from env_config import env_int
from log_safe import log_safe
import mcp_request_guard
from mcp_transport import BridgeTransport

# Same reason as the bridge: this process prints crash lines carrying values
# it did not choose. See server.py.
configure_stdio()

# The standalone MCP entry point derives its bridge URL from DAEDALUS_PORT.
# The in-process server passes the bridge's actual bound URL to start_in_thread,
# which matters when DAEDALUS_PORT=0. DAEDALUS_LOCAL_URL remains the explicit
# override for a standalone MCP deployment fronting a bridge that runs elsewhere.
LOCAL_URL = os.environ.get(
    'DAEDALUS_LOCAL_URL',
    f'http://127.0.0.1:{os.environ.get("DAEDALUS_PORT", "8081")}')


# Matches NET_CAPTURE_MAX in extension/background.js and the CLI.
NET_CAPTURE_MAX = 20000

MCP_PORT = env_int('DAEDALUS_MCP_PORT', 8086, 0, 65535)
# Mirrors the bridge's own DAEDALUS_MAX_BODY_SIZE, default and bound alike.
# The front end had no bound at all, so one unauthenticated request could
# make the process hold whatever it chose to send.
MAX_BODY_SIZE = env_int(
    'DAEDALUS_MCP_MAX_BODY_SIZE', 64 * 1024 * 1024, 0)
_token = mcp_request_guard.request_token
# The app auto-enables DNS rebinding protection for a localhost bind only when
# it is given no settings of its own; these are passed explicitly, so the list
# has to include the public hostname the reverse proxy fronts us with or
# proxied requests are rejected with a 421.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
    'DAEDALUS_MCP_ALLOWED_HOSTS',
    '127.0.0.1:*,localhost:*'
).split(',') if h.strip()]

mcp = MCPServer('daedalus')


@mcp.tool()
async def list_tabs() -> list[dict]:
    """List active Daedalus-registered Chrome tabs, each with the age of its last registration. Entries are not pruned by age: a tab persists until it is unregistered or replaced by a sync."""
    return await _get('/tabs')


@mcp.tool()
async def open_tab(url: str, background: bool = False, pinned: bool = False) -> dict:
    """Open a new Chrome tab at `url`. Returns {tabId, windowId, roundtrip_ms, ...}."""
    fields: dict = {'url': url}
    if background:
        fields['active'] = False
    if pinned:
        fields['pinned'] = True
    return await _ext_cmd('_open_tab', 'open-tab', include_roundtrip=True, **fields)


@mcp.tool()
async def open_tabs(urls: list[str], background: bool = False, pinned: bool = False) -> dict:
    """Open multiple Chrome tabs in one call. Returns {opened:[{tabId,url,windowId}], errors:[{url,error}], roundtrip_ms}."""
    fields: dict = {'urls': list(urls)}
    if background:
        fields['active'] = False
    if pinned:
        fields['pinned'] = True
    return await _ext_cmd('_open_tabs', 'open-tabs', timeout=30, include_roundtrip=True, **fields)


@mcp.tool()
async def focus_tab(chrome_tab: int) -> dict:
    """Bring Chrome tab `chrome_tab` to the foreground."""
    return await _ext_cmd('_focus', 'focus-tab', tabId=int(chrome_tab))


@mcp.tool()
async def close_tab(chrome_tabs: list[int]) -> dict:
    """Close one or more Chrome tabs by id."""
    ids = [int(x) for x in chrome_tabs]
    fields: dict = {}
    if len(ids) == 1:
        fields['tabId'] = ids[0]
    else:
        fields['tabIds'] = ids
    return await _ext_cmd('_close_tab', 'close-tab', **fields)


@mcp.tool()
async def ext_navigate(url: str, chrome_tab: int | None = None) -> dict:
    """Navigate `chrome_tab` (or active tab) to `url`. Works on chrome:// pages."""
    fields: dict = {'url': url}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    return await _ext_cmd('_nav', 'navigate', **fields)


@mcp.tool()
async def ext_reload(chrome_tab: int | None = None, bypass_cache: bool = False) -> dict:
    """Reload `chrome_tab` (or active tab). `bypass_cache=True` forces no-cache."""
    fields: dict = {}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if bypass_cache:
        fields['bypassCache'] = True
    return await _ext_cmd('_reload', 'reload', **fields)


def _flatten_eval(body: dict | None) -> dict | None:
    """The MCP client renders a tool's dict return under a top-level `result` key,
    and an eval body carries its own `result` field (the JS return value), so callers
    would see a confusing `result.result`. Surface it as `value` — same info, no
    double nesting. If the value is a JSON string (e.g. JSON.stringify output),
    parse it so the structure surfaces directly; non-JSON strings stay untouched.
    The `world` marker stays unchanged, including a `page:<hostname>` prefix."""
    if isinstance(body, dict) and 'result' in body:
        v = body.pop('result')
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                # A result that is not JSON is a plain string result, which is
                # the ordinary case. It travels through unchanged.
                pass
        body['value'] = v
    return body


def _checked_timeout(timeout: float) -> float:
    """Refuse a wait that cannot wait, BEFORE the command is submitted.

    The command is PUT first and the deadline evaluated afterwards, so a
    non-positive or non-finite timeout polls zero times and raises a timeout
    for a command the browser has already been handed. The caller is told
    nothing ran; retrying then runs the side effect a second time.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f'timeout must be a finite positive number of seconds; got {timeout!r}')
    return timeout


async def _send_eval(cmd_id: str, code: str, tab_id: str, wait: bool, timeout: float) -> dict | None:
    if not cmd_id:
        raise ValueError('cmd_id is required')
    if not code:
        raise ValueError('code is empty')
    if wait:
        _checked_timeout(timeout)
    payload: dict = {'id': cmd_id, 'code': code}
    if tab_id:
        payload['tab'] = tab_id
    sent = await _put('/command', payload)
    if not wait:
        return None
    body = await _poll_result(
        tab_id, timeout, expect_id=cmd_id,
        expect_delivery=sent.get('did'))
    return _flatten_eval(body)


@mcp.tool()
async def exec(cmd_id: str, code: str, tab_id: str = '', broadcast: bool = False,
               wait: bool = True, timeout: float = 15.0) -> dict | None:
    """Evaluate JS in a tab. `tab_id=''` + `broadcast=True` fans out to all tabs.
    Waited results retain the server's exact `world` marker, including
    `page:<hostname>`."""
    target = '' if broadcast else tab_id
    return await _send_eval(cmd_id, code.strip(), target, wait, timeout)


@mcp.tool()
async def put(cmd_id: str, code: str, tab_id: str = '', broadcast: bool = False,
              wait: bool = True, timeout: float = 15.0) -> dict | None:
    """Evaluate inline JS source in the tab. MCP callers read their own files;
    the bridge server does not open caller-named paths. Waited results retain
    the server's exact `world` marker, including `page:<hostname>`."""
    target = '' if broadcast else tab_id
    return await _send_eval(cmd_id, code.strip(), target, wait, timeout)


@mcp.tool()
async def result(tab_id: str = '', consume: bool = False) -> dict:
    """Fetch the newest unconsumed result for `tab_id` (or the broadcast slot).
    A waited exec/put consumes its own result, so this only finds one after
    `wait=False` (or a raw command-file drop). `consume=True` deletes after read.
    The returned result retains the server's exact `world` marker, including
    `page:<hostname>`."""
    params: dict = {}
    if tab_id:
        params['tab'] = tab_id
    if consume:
        params['consume'] = '1'
    body = await _get('/result', **params)
    if isinstance(body, dict) and body.get('pending'):
        return {'no_result': True,
                'note': 'no unconsumed result for this target — a waited exec/put '
                        'consumes its own result; send with wait=false to leave one readable'}
    return _flatten_eval(body) or {}


@mcp.tool()
async def ping(tab_id: str = '') -> dict:
    """Round-trip a `document.title` eval to `tab_id` (or broadcast)."""
    import time
    t0 = time.time()
    payload: dict = {'id': '_ping', 'code': 'document.title'}
    if tab_id:
        payload['tab'] = tab_id
    sent = await _put('/command', payload)
    res = await _poll_result(
        tab_id, 10.0, expect_id='_ping', expect_delivery=sent.get('did'))
    if res.get('error'):
        raise RuntimeError(f'ping: {res["error"]}')
    return {'ms': int((time.time() - t0) * 1000), 'title': res.get('result', ''),
            'world': res.get('world', '')}


@mcp.tool()
async def navigate(url: str, tab_id: str = '') -> None:
    """Set `location.href = url` in `tab_id` (via eval, does not wait for result)."""
    code = f'location.href = {json.dumps(url)}'
    await _send_eval('_nav', code, tab_id, wait=False, timeout=0)


@mcp.tool()
async def reload(tab_id: str = '', broadcast: bool = False) -> None:
    """Call `location.reload()` in `tab_id` or broadcast."""
    target = '' if broadcast else tab_id
    await _send_eval('_reload', 'location.reload()', target, wait=False, timeout=0)


@mcp.tool()
async def title(tab_id: str = '') -> dict:
    """Return `document.title` for `tab_id`."""
    res = await _send_eval('_title', 'document.title', tab_id, wait=True, timeout=10)
    assert res is not None
    return res


@mcp.tool()
async def url(tab_id: str = '') -> dict:
    """Return `location.href` for `tab_id`."""
    res = await _send_eval('_url', 'location.href', tab_id, wait=True, timeout=10)
    assert res is not None
    return res


@mcp.tool()
async def screenshot(cmd_id: str = '_ss', chrome_tab: int | None = None,
                     format: str = 'png', quality: int | None = None,
                     include_image: bool = False, timeout: float = 15.0):
    """Capture a screenshot via extension.

    Default: returns {path, size} — client can fetch the image separately.
    `include_image=True`: also returns the image bytes inline as an MCP Image
    so the caller can Read it directly without another round-trip.
    """
    fields: dict = {}
    if format:
        fields['format'] = format
    if quality is not None:
        fields['quality'] = quality
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    result_blob = await _ext_cmd(cmd_id, 'screenshot', timeout=timeout, **fields)
    meta = {'path': result_blob.get('path', ''), 'size': result_blob.get('size', 0)}
    if not include_image:
        return meta
    # By path, not by id: ids are reused, so an id names a directory rather
    # than a capture and its newest file belongs to whichever invocation
    # finished last.
    selector = {'path': meta['path']} if meta['path'] else {'id': cmd_id}
    img_bytes = await _get_raw('/screenshot', **selector)
    return [meta, Image(data=img_bytes, format=format)]


@mcp.tool()
async def segment_job(job: str) -> dict:
    """Create (or re-fetch) the HLS segment job `job` and return its job-scoped
    capability as {ok, sig}. The sig is what examples/hls-segment-relay.js
    substitutes for __SIG__; minting is idempotent for the owning token, so a
    resumed run gets the same one back."""
    return await _post('/segment-job', {'job': job})


@mcp.tool()
async def segment_status(job: str) -> dict:
    """HLS segment relay status for `job`. Returns {count, done, gaps}.

    Creates nothing: the capability is looked up, not minted. A job that does
    not exist raises rather than being brought into being by the question, and
    one owned by a different token raises too.
    """
    # /segment-status takes the job's minted capability, not the bridge token,
    # and GET /segment-job is the lookup that hands it over without creating
    # the job when the name has never been used.
    client = _http_client()
    found = await client.get(
        '/segment-job', params={'job': job}, headers=_bridge_auth())
    if found.status_code == 404:
        raise RuntimeError(f'segment_status: no job named {job!r}')
    if found.status_code == 409:
        raise RuntimeError(f'segment_status: job {job!r} is owned by a different token')
    found.raise_for_status()
    sig = found.json()['sig']
    r = await client.get('/segment-status', params={'job': job},
                         headers={SEGMENT_SIG_HEADER: sig})
    r.raise_for_status()
    data = r.json()
    done = data.get('done', [])
    full = set(range(min(done), max(done) + 1)) if done else set()
    data['gaps'] = sorted(full - set(done))
    return data


@mcp.tool()
async def uploads(upload_id: str = '', limit: int | None = None,
                  offset: int | None = None):
    """List uploaded files. When limit/offset given, returns {items,total,limit,offset}.
    Without paging, returns a bare array (back-compat with the server surface).
    """
    params: dict = {}
    if upload_id:
        params['id'] = upload_id
    if limit is not None:
        params['limit'] = limit
    if offset is not None:
        params['offset'] = offset
    return await _get('/upload', **params)


@mcp.tool()
async def delete_upload(upload_id: str = '', filename: str = '') -> dict:
    """Delete uploads. No args → all for token; id only → all files under that id;
    id+filename → single file. Returns the server response."""
    # A filename alone has no narrower target than the whole token, so it
    # would delete every upload rather than the one file it names.
    if filename and not upload_id:
        return {'error': 'filename requires upload_id'}
    body: dict = {}
    if upload_id:
        body['id'] = upload_id
    if filename:
        body['filename'] = filename
    return await _delete('/upload', body)


@mcp.tool()
async def get_cookies(domain: str = '', target_url: str = '') -> list[dict]:
    """List cookies via extension. Filter by domain or URL."""
    fields: dict = {}
    if domain:
        fields['domain'] = domain
    if target_url:
        fields['url'] = target_url
    return await _ext_cmd('_cookies', 'cookies', **fields)


@mcp.tool()
async def set_cookie(target_url: str, name: str, value: str, domain: str = '',
                     path: str = '', http_only: bool = False, secure: bool = False,
                     same_site: str = '', expires: float | None = None) -> dict:
    """Set a cookie on `target_url`."""
    fields: dict = {'url': target_url, 'name': name, 'value': value}
    if domain:
        fields['domain'] = domain
    if path:
        fields['path'] = path
    if http_only:
        fields['httpOnly'] = True
    if secure:
        fields['secure'] = True
    if same_site:
        fields['sameSite'] = same_site
    if expires is not None:
        fields['expirationDate'] = float(expires)
    return await _ext_cmd('_set_cookie', 'set-cookie', **fields)


@mcp.tool()
async def remove_cookie(target_url: str, name: str) -> dict:
    """Remove a specific cookie by name at `target_url`."""
    return await _ext_cmd('_rm_cookie', 'remove-cookie', url=target_url, name=name)


@mcp.tool()
async def clear_cookies(domain: str = '', target_url: str = '') -> dict:
    """Clear all cookies matching domain/url. Returns {removed: N}."""
    fields: dict = {}
    if domain:
        fields['domain'] = domain
    if target_url:
        fields['url'] = target_url
    return await _ext_cmd('_clear_cookies', 'clear-cookies', **fields)


@mcp.tool()
async def inject_css(css: str, chrome_tab: int | None = None,
                     all_frames: bool = False) -> dict:
    """Inject inline CSS into a tab."""
    if not css:
        raise ValueError('css required')
    fields: dict = {'css': css}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if all_frames:
        fields['allFrames'] = True
    return await _ext_cmd('_inject_css', 'inject-css', **fields)


@mcp.tool()
async def remove_css(css: str, chrome_tab: int | None = None,
                     all_frames: bool = False) -> dict:
    """Remove previously-injected inline CSS (must match the injected text)."""
    if not css:
        raise ValueError('css required')
    fields: dict = {'css': css}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if all_frames:
        fields['allFrames'] = True
    return await _ext_cmd('_remove_css', 'remove-css', **fields)


@mcp.tool()
async def block_requests(pattern: str, chrome_tab: int | None = None) -> dict:
    """Block requests matching a declarativeNetRequest URL pattern. Returns {ruleId, pattern, tabIds}."""
    fields: dict = {'pattern': pattern}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    return await _ext_cmd('_block', 'block-requests', **fields)


@mcp.tool()
async def unblock_requests(rule_id: int | None = None) -> dict:
    """Remove a block rule by id, or all rules if `rule_id` is None."""
    fields: dict = {}
    if rule_id is not None:
        # Zero is not "no id": it reached the extension as a present-but-false
        # value and widened into removing every rule.
        if int(rule_id) <= 0:
            return {'error': 'rule_id must be a positive integer'}
        fields['ruleId'] = int(rule_id)
    return await _ext_cmd('_unblock', 'unblock-requests', **fields)


@mcp.tool()
async def list_block_rules() -> list[dict]:
    """List currently-active declarativeNetRequest block rules."""
    return await _ext_cmd('_list_rules', 'list-block-rules')


@mcp.tool()
async def store_hotfix(fix_id: str, code: str, permanent: bool = False) -> dict:
    """Store inline JS as a persistent hotfix. Set `permanent=True` to mark the fix as surviving extension version bumps."""
    if not code:
        raise ValueError('code required')
    return await _ext_cmd('_store_hf', 'store-hotfix', fixId=fix_id, code=code, permanent=permanent)


@mcp.tool()
async def clear_hotfix(fix_id: str) -> dict:
    """Remove a specific hotfix by id."""
    return await _ext_cmd('_clear_hf', 'clear-hotfix', fixId=fix_id)


@mcp.tool()
async def clear_hotfixes(include_permanent: bool = False) -> dict:
    """Remove stored hotfixes. By default, permanent fixes are preserved; set `include_permanent=True` to nuke everything."""
    return await _ext_cmd('_clear_all_hf', 'clear-all-hotfixes', includePermanent=include_permanent)


@mcp.tool()
async def list_hotfixes() -> dict:
    """List stored hotfixes. Returns {version, fixes:[{id,ts,code},...]}."""
    return await _ext_cmd('_list_hf', 'list-hotfixes')


@mcp.tool()
async def set_permanent(fix_id: str, permanent: bool) -> dict:
    """Toggle the permanent flag on an existing hotfix. Permanent fixes survive extension version bumps. Returns {id, permanent, found}."""
    return await _ext_cmd('_set_perm', 'set-permanent', fixId=fix_id, permanent=permanent)


@mcp.tool()
async def net_capture(chrome_tab: int | None = None, max_requests: int = 1000) -> dict:
    """Start CDP network capture on a tab. Returns {tabId, already?, buffered?}."""
    fields: dict = {}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if max_requests:
        # Same range the extension enforces on arrival (NET_CAPTURE_MAX): the
        # buffer is a service-worker memory budget, so a nonpositive value
        # evicts its only event and an enormous one bounds nothing.
        if not isinstance(max_requests, int) or isinstance(max_requests, bool):
            raise ValueError('max_requests must be an integer')
        if max_requests < 1 or max_requests > NET_CAPTURE_MAX:
            raise ValueError(
                f'max_requests must be an integer from 1 to {NET_CAPTURE_MAX}; '
                f'got {max_requests}')
        fields['maxRequests'] = int(max_requests)
    return await _ext_cmd('_net_cap', 'net-capture', timeout=15, **fields)


@mcp.tool()
async def net_capture_stop(chrome_tab: int | None = None, bodies: bool = False) -> dict:
    """Stop capture and return buffered requests. `bodies=True` fetches response bodies."""
    fields: dict = {}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if bodies:
        fields['bodies'] = True
    return await _ext_cmd('_net_stop', 'net-capture-stop', timeout=30, **fields)


@mcp.tool()
async def net_capture_get(chrome_tab: int | None = None, url_filter: str = '',
                          bodies: bool = False) -> dict:
    """Return current capture buffer (does not stop). Optional regex `url_filter` on URL or type."""
    fields: dict = {}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if url_filter:
        fields['filter'] = url_filter
    if bodies:
        fields['bodies'] = True
    return await _ext_cmd('_net_get', 'net-capture-get', timeout=30, **fields)


@mcp.tool()
async def cdp(method: str, params: dict | None = None, chrome_tab: int | None = None,
              keep_session: bool = False) -> dict:
    """Send a raw CDP command. Example: method='Page.captureScreenshot'.

    Pass keep_session=True to keep the chrome.debugger session attached after the
    call returns — required for CDP domains that hold state across calls
    (Profiler.enable → Profiler.start → … → Profiler.stop, HeapProfiler, Tracing).
    The next call without keep_session=True detaches.
    """
    fields: dict = {'method': method, 'params': params or {}}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if keep_session:
        fields['keep_session'] = True
    return await _ext_cmd('_cdp', 'cdp', timeout=30, **fields)


@mcp.tool()
async def fetch_timings(reset: bool = False) -> dict:
    """Fetch the background fetch-relay timing ring buffer. `reset=True` clears it after."""
    fields: dict = {}
    if reset:
        fields['reset'] = True
    return await _ext_cmd('_fetch_timings', 'fetch-timings', **fields)


@mcp.tool()
async def ext_self_reload() -> dict:
    """Reload the Chrome extension from disk via chrome.runtime.reload()."""
    return await _ext_cmd('_ext_reload', 'ext-reload')


class _BearerAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        refusal = mcp_request_guard.early_refusal(request, MAX_BODY_SIZE)
        if refusal is not None:
            await mcp_request_guard.drain_refused_body(request)
            return refusal

        if request.method == 'POST':
            raw = await request.body()
            # A body sent without a declared length is bounded here rather than
            # by the check above; it has still been read, which is why the
            # declared case is refused before reading at all.
            if len(raw) > MAX_BODY_SIZE:
                return JSONResponse(
                    {'error': 'request body too large'}, status_code=413)
            duplicate = _ambiguous_json_carrier(raw)
            if duplicate is not None:
                return JSONResponse(
                    {'error': f'duplicate {duplicate}'}, status_code=400)

        return await call_next(request)


class _CarrierJSONObject(dict):
    """A parsed JSON object that keeps the pairs it was built from.

    A duplicate key collapses into one dict entry, so the pair list is the
    only record that it was sent twice — which is exactly what the repeated
    `job` argument check reads. Equality includes it for that reason:
    inherited dict equality would report two bodies as the same request when
    only one of them repeated a key. Comparison against a plain dict is
    unchanged.
    """

    def __init__(self, pairs):
        super().__init__(pairs)
        self.pairs = pairs

    def __eq__(self, other):
        if isinstance(other, _CarrierJSONObject):
            return super().__eq__(other) and self.pairs == other.pairs
        return super().__eq__(other)


def _job_carrier_names(request_body):
    names = []
    if not isinstance(request_body, _CarrierJSONObject):
        return names
    for key, params in request_body.pairs:
        if (key != 'params'
                or not isinstance(params, _CarrierJSONObject)
                or params.get('name') not in ('segment_job', 'segment_status')):
            continue
        for param_key, arguments in params.pairs:
            if (param_key == 'arguments'
                    and isinstance(arguments, _CarrierJSONObject)):
                names.extend(key for key, _value in arguments.pairs
                             if key == 'job')
    return names


def _ambiguous_json_carrier(raw):
    """Return a repeated segment-tool job carrier in one JSON request."""
    try:
        body = json.loads(raw, object_pairs_hook=_CarrierJSONObject)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    requests = body if isinstance(body, list) else (body,)
    for request_body in requests:
        duplicate = ambiguous_request_carrier(
            _job_carrier_names(request_body))
        if duplicate is not None:
            return duplicate
    return None


_started_local_url: str | None = None
# A cell rather than a rebound global: this flag is read and written only
# inside start_in_thread, and a module global written there reads as dead.
_start_state = {'started': False}


def _resolved_local_url(local_url: str | None = None) -> str:
    """Resolve the bridge URL for this client lookup.

    The caller's bridge URL is resolved before its transport facade is bound.
    The URL supplied after the bridge binds wins over the port-derived
    fallback, while the explicit standalone override remains strongest.
    ``LOCAL_URL`` is the import-time fallback retained for callers that load
    this module by path and restore the environment afterwards.
    """
    override = os.environ.get('DAEDALUS_LOCAL_URL')
    if override:
        return override
    if local_url:
        return local_url
    if _started_local_url:
        return _started_local_url
    if 'DAEDALUS_PORT' in os.environ:
        return f'http://127.0.0.1:{os.environ["DAEDALUS_PORT"]}'
    return LOCAL_URL


_transport = BridgeTransport(_resolved_local_url())


def _http_client(local_url: str | None = None) -> httpx.AsyncClient:
    """Return this caller's client, or a compatibility client for a URL."""
    if local_url is not None:
        return BridgeTransport(_resolved_local_url(local_url)).client()
    return _transport.client()


def _tok() -> str:
    t = _token.get()
    if not t:
        raise RuntimeError('no token in context')
    return t


def _bridge_auth() -> dict[str, str]:
    """The bridge's pre-body credential carrier.

    The bridge settles credentials before it reads a request body, so a body
    token alone caps what this client may send at the unauthenticated window.
    It is also what keeps a reusable credential out of a request target, which
    a reverse-proxy access log retains and a query parameter cannot avoid.
    Same header, same value the MCP listener itself required to get here.
    """
    return {'Authorization': f'Bearer {_tok()}'}


async def _get(path: str, **params) -> Any:
    r = await _http_client().get(path, params=params, headers=_bridge_auth())
    r.raise_for_status()
    return r.json()


async def _put(path: str, body: dict) -> dict:
    body = {**body, 'token': _tok()}
    r = await _http_client().put(path, json=body, headers=_bridge_auth())
    r.raise_for_status()
    return r.json()


async def _post(path: str, body: dict) -> dict:
    body = {**body, 'token': _tok()}
    r = await _http_client().post(path, json=body, headers=_bridge_auth())
    r.raise_for_status()
    return r.json()


async def _delete(path: str, body: dict) -> dict:
    body = {**body, 'token': _tok()}
    r = await _http_client().request(
        'DELETE', path, json=body, headers=_bridge_auth())
    r.raise_for_status()
    return r.json()


async def _get_raw(route: str, **params) -> bytes:
    """Fetch one bridge route as raw bytes. The route is named `route` rather
    than `path` so a caller can pass a `path` query parameter, which the
    screenshot download does."""
    r = await _http_client().get(route, params=params, headers=_bridge_auth())
    r.raise_for_status()
    return r.content


async def _poll_result(tab: str, timeout: float, interval: float = 0.5,
                       expect_id: str | None = None,
                       expect_delivery: str | None = None) -> dict:
    """Poll until the named command delivery is conditionally consumed.

    The delivery id rejects stale results from an earlier invocation even when
    its command id is reused. The result generation makes peek then consume
    safe when another caller replaces the shared slot between those requests.

    The wait ramps 20ms -> `interval` instead of sleeping a flat `interval` up
    front: most commands finish in tens of milliseconds, and the fixed first
    sleep was adding half a second of dead time to every single tool call."""
    import asyncio, time
    peek = {}
    if tab:
        peek['tab'] = tab
    if expect_delivery:
        peek['delivery'] = expect_delivery
    auth = _bridge_auth()
    deadline = time.time() + timeout
    wait = 0.02
    while time.time() < deadline:
        await asyncio.sleep(wait)
        wait = min(wait * 2, interval)
        r = await _http_client().get('/result', params=peek, headers=auth)
        r.raise_for_status()
        data = r.json()
        if data.get('pending'):
            continue
        if (expect_id is not None and data.get('id') != expect_id
                or expect_delivery is not None
                and data.get('deliveryId') != expect_delivery):
            # Someone else's result. Leave it where it is for them.
            continue
        generation = data.get('resultGeneration')
        if not generation:
            continue
        take = {**peek, 'consume': '1', 'expected': generation}
        consumed = await _http_client().get(
            '/result', params=take, headers=auth)
        consumed.raise_for_status()
        receipt = consumed.json()
        if (receipt.get('consumed') is not True
                or receipt.get('resultGeneration') != generation):
            continue
        return data
    raise TimeoutError(f'no result within {timeout}s')


async def _ext_cmd(cmd_id: str, cmd_type: str, timeout: float = 10.0,
                   include_roundtrip: bool = False, **fields) -> Any:
    """Send a typed extension command (tab=extension) and return result.result.

    The server computes `roundtrip_ms` (enqueue -> result arrival) as a sibling of
    `result` in the body, so returning result.result alone drops it.
    include_roundtrip merges it back in, for tools where how long the extension
    took is part of the answer."""
    _checked_timeout(timeout)
    payload = {'id': cmd_id, 'type': cmd_type, 'tab': 'extension', **fields}
    sent = await _put('/command', payload)
    res = await _poll_result(
        'extension', timeout, expect_id=cmd_id,
        expect_delivery=sent.get('did'))
    if res.get('error'):
        raise RuntimeError(f'ext {cmd_type}: {res["error"]}')
    out = res.get('result', {})
    if include_roundtrip and isinstance(out, dict) and 'roundtrip_ms' in res:
        out = {**out, 'roundtrip_ms': res['roundtrip_ms']}
    return out


# The listener's actual port, for whoever started it: with DAEDALUS_MCP_PORT=0
# the kernel picks, so anything printed or probed must come from the bound
# socket, never from the configured value. _bound/_serve set these for
# in-process callers; the child-process variant travels on the startup line.
bound_port = 0
startup_error = ''
_bound = threading.Event()


def _serve():
    global bound_port, startup_error
    try:
        app = mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=ALLOWED_HOSTS,
            ),
        )
        app.add_middleware(_BearerAuth)

        inner_lifespan = app.router.lifespan_context

        @contextlib.asynccontextmanager
        async def lifespan_context(_app):
            try:
                async with inner_lifespan(_app):
                    yield
            finally:
                await BridgeTransport.close_current_loop_clients()

        app.router.lifespan_context = lifespan_context
        import uvicorn
        # Bind ourselves and hand the socket over: the actual port is known
        # synchronously (0 included), and a collision raises here — where the
        # catch below can report it — instead of inside uvicorn, which logs
        # and returns silently on bind failure.
        config = uvicorn.Config(
            app, host='127.0.0.1', port=MCP_PORT, log_level='warning')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', MCP_PORT))
        bound_port = sock.getsockname()[1]
        _bound.set()
        print(f'[MCP] streamable-http on 127.0.0.1:{bound_port}', flush=True)
        uvicorn.Server(config).run(sockets=[sock])
    except Exception as e:
        startup_error = f'[MCP] serve crashed: {log_safe(e)}'
        print(startup_error, file=sys.stderr, flush=True)


def start_in_thread(local_url: str | None = None) -> threading.Thread:
    global _started_local_url, _transport
    if _start_state['started']:
        raise RuntimeError(
            'start_in_thread called more than once for this module')
    _start_state['started'] = True
    if local_url is not None:
        _started_local_url = local_url
    _transport = BridgeTransport(_resolved_local_url())
    t = threading.Thread(target=_serve, daemon=True, name='mcp-server')
    t.start()
    return t


if __name__ == '__main__':
    start_in_thread().join()
