"""Network and CDP tools for the Daedalus MCP front end."""


# Matches NET_CAPTURE_MAX in extension/background.js and the CLI.
NET_CAPTURE_MAX = 20000


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

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

    return {
        'net_capture': net_capture,
        'net_capture_stop': net_capture_stop,
        'net_capture_get': net_capture_get,
        'cdp': cdp,
        'fetch_timings': fetch_timings,
    }
