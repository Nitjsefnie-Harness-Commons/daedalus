"""Eval and debug tools for the Daedalus MCP front end."""

import json


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

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

    async def _send_eval(cmd_id: str, code: str, tab_id: str, wait: bool,
                         timeout: float) -> dict | None:
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
    async def exec(cmd_id: str, code: str, tab_id: str = '',
                   broadcast: bool = False, wait: bool = True,
                   timeout: float = 15.0) -> dict | None:
        """Evaluate JS in a tab. `tab_id=''` + `broadcast=True` fans out to all tabs.
    Waited results retain the server's exact `world` marker, including
    `page:<hostname>`."""
        target = '' if broadcast else tab_id
        return await _send_eval(cmd_id, code.strip(), target, wait, timeout)

    @mcp.tool()
    async def put(cmd_id: str, code: str, tab_id: str = '',
                  broadcast: bool = False, wait: bool = True,
                  timeout: float = 15.0) -> dict | None:
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
            tab_id, 10.0, expect_id='_ping',
            expect_delivery=sent.get('did'))
        if res.get('error'):
            raise RuntimeError(f'ping: {res["error"]}')
        return {'ms': int((time.time() - t0) * 1000),
                'title': res.get('result', ''),
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
        await _send_eval(
            '_reload', 'location.reload()', target, wait=False, timeout=0)

    @mcp.tool()
    async def title(tab_id: str = '') -> dict:
        """Return `document.title` for `tab_id`."""
        res = await _send_eval(
            '_title', 'document.title', tab_id, wait=True, timeout=10)
        assert res is not None
        return res

    @mcp.tool()
    async def url(tab_id: str = '') -> dict:
        """Return `location.href` for `tab_id`."""
        res = await _send_eval(
            '_url', 'location.href', tab_id, wait=True, timeout=10)
        assert res is not None
        return res

    @mcp.tool()
    async def ext_self_reload() -> dict:
        """Reload the Chrome extension from disk via chrome.runtime.reload()."""
        return await bridge.ext_cmd('_ext_reload', 'ext-reload')

    return {
        'exec': exec,
        'put': put,
        'result': result,
        'ping': ping,
        'navigate': navigate,
        'reload': reload,
        'title': title,
        'url': url,
        'ext_self_reload': ext_self_reload,
    }
