"""Cookie tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

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

    return {
        'get_cookies': get_cookies,
        'set_cookie': set_cookie,
        'remove_cookie': remove_cookie,
        'clear_cookies': clear_cookies,
    }
