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
import json, os, socket, sys, threading
from typing import Any
import httpx
from mcp.server.mcpserver import MCPServer, Image
from mcp.server.transport_security import TransportSecuritySettings
import mcp_auth
from daedalus_cli import SEGMENT_SIG_HEADER
from daedalus_cli.output import configure_stdio
from env_config import env_int
from log_safe import log_safe
import mcp_request_guard
from mcp_transport import BridgeSession, BridgeTransport

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

bridge = BridgeSession(LOCAL_URL, _token)

mcp = MCPServer('daedalus')


@mcp.tool()
async def list_tabs() -> list[dict]:
    """List active Daedalus-registered Chrome tabs, each with the age of its last registration. Entries are not pruned by age: a tab persists until it is unregistered or replaced by a sync."""
    return await bridge.get('/tabs')


@mcp.tool()
async def open_tab(url: str, background: bool = False, pinned: bool = False) -> dict:
    """Open a new Chrome tab at `url`. Returns {tabId, windowId, roundtrip_ms, ...}."""
    fields: dict = {'url': url}
    if background:
        fields['active'] = False
    if pinned:
        fields['pinned'] = True
    return await bridge.ext_cmd(
        '_open_tab', 'open-tab', include_roundtrip=True, **fields)


@mcp.tool()
async def open_tabs(urls: list[str], background: bool = False, pinned: bool = False) -> dict:
    """Open multiple Chrome tabs in one call. Returns {opened:[{tabId,url,windowId}], errors:[{url,error}], roundtrip_ms}."""
    fields: dict = {'urls': list(urls)}
    if background:
        fields['active'] = False
    if pinned:
        fields['pinned'] = True
    return await bridge.ext_cmd(
        '_open_tabs', 'open-tabs', timeout=30, include_roundtrip=True,
        **fields)


@mcp.tool()
async def focus_tab(chrome_tab: int) -> dict:
    """Bring Chrome tab `chrome_tab` to the foreground."""
    return await bridge.ext_cmd(
        '_focus', 'focus-tab', tabId=int(chrome_tab))


@mcp.tool()
async def close_tab(chrome_tabs: list[int]) -> dict:
    """Close one or more Chrome tabs by id."""
    ids = [int(x) for x in chrome_tabs]
    fields: dict = {}
    if len(ids) == 1:
        fields['tabId'] = ids[0]
    else:
        fields['tabIds'] = ids
    return await bridge.ext_cmd('_close_tab', 'close-tab', **fields)


@mcp.tool()
async def ext_navigate(url: str, chrome_tab: int | None = None) -> dict:
    """Navigate `chrome_tab` (or active tab) to `url`. Works on chrome:// pages."""
    fields: dict = {'url': url}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    return await bridge.ext_cmd('_nav', 'navigate', **fields)


@mcp.tool()
async def ext_reload(chrome_tab: int | None = None, bypass_cache: bool = False) -> dict:
    """Reload `chrome_tab` (or active tab). `bypass_cache=True` forces no-cache."""
    fields: dict = {}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if bypass_cache:
        fields['bypassCache'] = True
    return await bridge.ext_cmd('_reload', 'reload', **fields)


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


async def _send_eval(cmd_id: str, code: str, tab_id: str, wait: bool, timeout: float) -> dict | None:
    if not cmd_id:
        raise ValueError('cmd_id is required')
    if not code:
        raise ValueError('code is empty')
    if wait:
        bridge.checked_timeout(timeout)
    payload: dict = {'id': cmd_id, 'code': code}
    if tab_id:
        payload['tab'] = tab_id
    sent = await bridge.put('/command', payload)
    if not wait:
        return None
    body = await bridge.poll_result(
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
    body = await bridge.get('/result', **params)
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
    sent = await bridge.put('/command', payload)
    res = await bridge.poll_result(
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
    result_blob = await bridge.ext_cmd(
        cmd_id, 'screenshot', timeout=timeout, **fields)
    meta = {'path': result_blob.get('path', ''), 'size': result_blob.get('size', 0)}
    if not include_image:
        return meta
    # By path, not by id: ids are reused, so an id names a directory rather
    # than a capture and its newest file belongs to whichever invocation
    # finished last.
    selector = {'path': meta['path']} if meta['path'] else {'id': cmd_id}
    img_bytes = await bridge.get_raw('/screenshot', **selector)
    return [meta, Image(data=img_bytes, format=format)]


@mcp.tool()
async def segment_job(job: str) -> dict:
    """Create (or re-fetch) the HLS segment job `job` and return its job-scoped
    capability as {ok, sig}. The sig is what examples/hls-segment-relay.js
    substitutes for __SIG__; minting is idempotent for the owning token, so a
    resumed run gets the same one back."""
    return await bridge.post('/segment-job', {'job': job})


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
    client = bridge.http_client()
    found = await client.get(
        '/segment-job', params={'job': job}, headers=bridge.auth())
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
    return await bridge.get('/upload', **params)


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
    return await bridge.delete('/upload', body)


@mcp.tool()
async def get_cookies(domain: str = '', target_url: str = '') -> list[dict]:
    """List cookies via extension. Filter by domain or URL."""
    fields: dict = {}
    if domain:
        fields['domain'] = domain
    if target_url:
        fields['url'] = target_url
    return await bridge.ext_cmd('_cookies', 'cookies', **fields)


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
    return await bridge.ext_cmd('_set_cookie', 'set-cookie', **fields)


@mcp.tool()
async def remove_cookie(target_url: str, name: str) -> dict:
    """Remove a specific cookie by name at `target_url`."""
    return await bridge.ext_cmd(
        '_rm_cookie', 'remove-cookie', url=target_url, name=name)


@mcp.tool()
async def clear_cookies(domain: str = '', target_url: str = '') -> dict:
    """Clear all cookies matching domain/url. Returns {removed: N}."""
    fields: dict = {}
    if domain:
        fields['domain'] = domain
    if target_url:
        fields['url'] = target_url
    return await bridge.ext_cmd(
        '_clear_cookies', 'clear-cookies', **fields)


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
    return await bridge.ext_cmd('_inject_css', 'inject-css', **fields)


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
    return await bridge.ext_cmd('_remove_css', 'remove-css', **fields)


@mcp.tool()
async def block_requests(pattern: str, chrome_tab: int | None = None) -> dict:
    """Block requests matching a declarativeNetRequest URL pattern. Returns {ruleId, pattern, tabIds}."""
    fields: dict = {'pattern': pattern}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    return await bridge.ext_cmd('_block', 'block-requests', **fields)


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
    return await bridge.ext_cmd('_unblock', 'unblock-requests', **fields)


@mcp.tool()
async def list_block_rules() -> list[dict]:
    """List currently-active declarativeNetRequest block rules."""
    return await bridge.ext_cmd('_list_rules', 'list-block-rules')


@mcp.tool()
async def store_hotfix(fix_id: str, code: str, permanent: bool = False) -> dict:
    """Store inline JS as a persistent hotfix. Set `permanent=True` to mark the fix as surviving extension version bumps."""
    if not code:
        raise ValueError('code required')
    return await bridge.ext_cmd(
        '_store_hf', 'store-hotfix', fixId=fix_id, code=code,
        permanent=permanent)


@mcp.tool()
async def clear_hotfix(fix_id: str) -> dict:
    """Remove a specific hotfix by id."""
    return await bridge.ext_cmd(
        '_clear_hf', 'clear-hotfix', fixId=fix_id)


@mcp.tool()
async def clear_hotfixes(include_permanent: bool = False) -> dict:
    """Remove stored hotfixes. By default, permanent fixes are preserved; set `include_permanent=True` to nuke everything."""
    return await bridge.ext_cmd(
        '_clear_all_hf', 'clear-all-hotfixes',
        includePermanent=include_permanent)


@mcp.tool()
async def list_hotfixes() -> dict:
    """List stored hotfixes. Returns {version, fixes:[{id,ts,code},...]}."""
    return await bridge.ext_cmd('_list_hf', 'list-hotfixes')


@mcp.tool()
async def set_permanent(fix_id: str, permanent: bool) -> dict:
    """Toggle the permanent flag on an existing hotfix. Permanent fixes survive extension version bumps. Returns {id, permanent, found}."""
    return await bridge.ext_cmd(
        '_set_perm', 'set-permanent', fixId=fix_id,
        permanent=permanent)


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
    return await bridge.ext_cmd(
        '_net_cap', 'net-capture', timeout=15, **fields)


@mcp.tool()
async def net_capture_stop(chrome_tab: int | None = None, bodies: bool = False) -> dict:
    """Stop capture and return buffered requests. `bodies=True` fetches response bodies."""
    fields: dict = {}
    if chrome_tab is not None:
        fields['tabId'] = int(chrome_tab)
    if bodies:
        fields['bodies'] = True
    return await bridge.ext_cmd(
        '_net_stop', 'net-capture-stop', timeout=30, **fields)


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
    return await bridge.ext_cmd(
        '_net_get', 'net-capture-get', timeout=30, **fields)


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
    return await bridge.ext_cmd('_cdp', 'cdp', timeout=30, **fields)


@mcp.tool()
async def fetch_timings(reset: bool = False) -> dict:
    """Fetch the background fetch-relay timing ring buffer. `reset=True` clears it after."""
    fields: dict = {}
    if reset:
        fields['reset'] = True
    return await bridge.ext_cmd(
        '_fetch_timings', 'fetch-timings', **fields)


@mcp.tool()
async def ext_self_reload() -> dict:
    """Reload the Chrome extension from disk via chrome.runtime.reload()."""
    return await bridge.ext_cmd('_ext_reload', 'ext-reload')


# A cell rather than a rebound global: this flag is read and written only
# inside start_in_thread, and a module global written there reads as dead.
_start_state = {'started': False}

_transport = bridge.transport
_resolved_local_url = bridge.resolved_local_url
_http_client = bridge.http_client
_tok = bridge.token
_bridge_auth = bridge.auth
_get = bridge.get
_put = bridge.put
_post = bridge.post
_delete = bridge.delete
_get_raw = bridge.get_raw
_poll_result = bridge.poll_result
_ext_cmd = bridge.ext_cmd
_checked_timeout = bridge.checked_timeout


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
        app.add_middleware(
            mcp_auth.BearerAuth,
            token_var=_token,
            max_body_size=MAX_BODY_SIZE,
        )

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
    global _transport
    if _start_state['started']:
        raise RuntimeError(
            'start_in_thread called more than once for this module')
    _start_state['started'] = True
    bridge.rebind(local_url)
    _transport = bridge.transport
    t = threading.Thread(target=_serve, daemon=True, name='mcp-server')
    t.start()
    return t


if __name__ == '__main__':
    start_in_thread().join()
