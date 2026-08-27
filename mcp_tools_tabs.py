"""Tabs tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

    @mcp.tool()
    async def list_tabs() -> list[dict]:
        """List active Daedalus-registered Chrome tabs, each with the age of its last registration. Entries are not pruned by age: a tab persists until it is unregistered or replaced by a sync."""
        return await bridge.get('/tabs')

    @mcp.tool()
    async def open_tab(url: str, background: bool = False,
                       pinned: bool = False) -> dict:
        """Open a new Chrome tab at `url`. Returns {tabId, windowId, roundtrip_ms, ...}."""
        fields: dict = {'url': url}
        if background:
            fields['active'] = False
        if pinned:
            fields['pinned'] = True
        return await bridge.ext_cmd(
            '_open_tab', 'open-tab', include_roundtrip=True, **fields)

    @mcp.tool()
    async def open_tabs(urls: list[str], background: bool = False,
                        pinned: bool = False) -> dict:
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
    async def ext_navigate(url: str,
                           chrome_tab: int | None = None) -> dict:
        """Navigate `chrome_tab` (or active tab) to `url`. Works on chrome:// pages."""
        fields: dict = {'url': url}
        if chrome_tab is not None:
            fields['tabId'] = int(chrome_tab)
        return await bridge.ext_cmd('_nav', 'navigate', **fields)

    @mcp.tool()
    async def ext_reload(chrome_tab: int | None = None,
                         bypass_cache: bool = False) -> dict:
        """Reload `chrome_tab` (or active tab). `bypass_cache=True` forces no-cache."""
        fields: dict = {}
        if chrome_tab is not None:
            fields['tabId'] = int(chrome_tab)
        if bypass_cache:
            fields['bypassCache'] = True
        return await bridge.ext_cmd('_reload', 'reload', **fields)

    return {
        'list_tabs': list_tabs,
        'open_tab': open_tab,
        'open_tabs': open_tabs,
        'focus_tab': focus_tab,
        'close_tab': close_tab,
        'ext_navigate': ext_navigate,
        'ext_reload': ext_reload,
    }
