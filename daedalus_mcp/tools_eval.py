"""Eval and debug tools for the Daedalus MCP front end."""

import json
from typing import overload

from daedalus_cli.result_view import public_result


def register(mcp, bridge):
    @overload
    def _flatten_eval(body: dict) -> dict:
        ...

    @overload
    def _flatten_eval(body: None) -> None:
        ...

    def _flatten_eval(body: dict | None) -> dict | None:
        """Use `value` to avoid the MCP client's `result.result` nesting.

        Parse JSON strings for structured display; preserve other strings and
        the server's exact `world` marker. Overloaded on the argument so a
        caller holding a dict is not told it may get `None` back: the `None`
        is the argument's own, passed through.
        """
        if isinstance(body, dict):
            body = public_result(body)
        if isinstance(body, dict) and 'result' in body:
            v = body.pop('result')
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            body['value'] = v
        return body

    async def _send_eval(cmd_id: str, code: str, tab_id: str, wait: bool,
                         timeout: float) -> dict:
        """Send an eval, answering its result when waited and the command the
        bridge enqueued when not.

        The no-wait branch does not validate `timeout`, poll or flatten a
        result: there is none to read, and the command is what a caller that
        has to answer it needs.
        """
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
            return {'command': sent.get('command')}
        body = await bridge.poll_result(
            tab_id, timeout, expect_id=cmd_id,
            expect_delivery=sent.get('did'))
        return _flatten_eval(body)

    @mcp.tool()
    async def exec(cmd_id: str, code: str, tab_id: str = '',
                   broadcast: bool = False, wait: bool = True,
                   timeout: float = 15.0) -> dict:
        """Evaluate JS in a tab. `tab_id=''` + `broadcast=True` sends the
    command with no tab; the extension runs it once, in the browser's
    active tab. Waited results retain the server's exact `world` marker,
    including `page:<hostname>`."""
        target = '' if broadcast else tab_id
        return await _send_eval(cmd_id, code.strip(), target, wait, timeout)

    @mcp.tool()
    async def put(cmd_id: str, code: str, tab_id: str = '',
                  broadcast: bool = False, wait: bool = True,
                  timeout: float = 15.0) -> dict:
        """Evaluate inline JS source in the tab. MCP callers read their own
    files; the bridge server does not open caller-named paths. Waited results
    retain the server's exact `world` marker, including `page:<hostname>`."""
        target = '' if broadcast else tab_id
        return await _send_eval(cmd_id, code.strip(), target, wait, timeout)

    @mcp.tool()
    async def result(tab_id: str = '', consume: bool = False) -> dict:
        """Fetch the newest unconsumed result for `tab_id` (or the broadcast
    slot). A waited exec/put consumes its own result, so this only finds one
    after `wait=False` (or a raw command-file drop). `consume=True` deletes
    after read.
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
                    'note': 'no unconsumed result for this target — '
                            'a waited exec/put consumes its own result; '
                            'send with wait=false to leave one readable'}
        return _flatten_eval(body) or {}

    @mcp.tool()
    async def ping(tab_id: str = '', wait: bool = True) -> dict:
        """Round-trip a `document.title` eval to `tab_id` (or, with no tab,
    the browser's active tab). `wait=False` sends it and returns the command
    the bridge enqueued, measuring nothing."""
        import time
        t0 = time.time()
        payload: dict = {'id': '_ping', 'code': 'document.title'}
        if tab_id:
            payload['tab'] = tab_id
        sent = await bridge.put('/command', payload)
        if not wait:
            return {'command': sent.get('command')}
        res = await bridge.poll_result(
            tab_id, 10.0, expect_id='_ping',
            expect_delivery=sent.get('did'))
        if res.get('error'):
            raise RuntimeError(f'ping: {res["error"]}')
        return {'ms': int((time.time() - t0) * 1000),
                'title': res.get('result', ''),
                'world': res.get('world', '')}

    @mcp.tool()
    async def navigate(url: str, tab_id: str = '') -> dict:
        """Set `location.href = url` in `tab_id` (via eval, does not wait for
        result). Returns the command the bridge enqueued."""
        code = f'location.href = {json.dumps(url)}'
        return await _send_eval(
            '_nav', code, tab_id, wait=False, timeout=0)

    @mcp.tool()
    async def reload(tab_id: str = '', broadcast: bool = False) -> dict:
        """Call `location.reload()` in `tab_id`, or with no tab in the
    browser's active tab. Returns the command the bridge enqueued."""
        target = '' if broadcast else tab_id
        return await _send_eval(
            '_reload', 'location.reload()', target, wait=False, timeout=0)

    @mcp.tool()
    async def title(tab_id: str = '', wait: bool = True) -> dict:
        """Return `document.title` for `tab_id`. `wait=False` returns the
        command the bridge enqueued instead."""
        return await _send_eval(
            '_title', 'document.title', tab_id, wait=wait, timeout=10)

    @mcp.tool()
    async def url(tab_id: str = '', wait: bool = True) -> dict:
        """Return `location.href` for `tab_id`. `wait=False` returns the
        command the bridge enqueued instead."""
        return await _send_eval(
            '_url', 'location.href', tab_id, wait=wait, timeout=10)

    @mcp.tool()
    async def ext_self_reload(wait: bool = True) -> dict:
        """Reload the Chrome extension from disk via
        chrome.runtime.reload(). `wait=False` returns the command the bridge
        enqueued instead."""
        return await bridge.ext_cmd(
            '_ext_reload', 'ext-reload', wait=wait)

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
