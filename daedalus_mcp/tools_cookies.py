"""Cookie tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    @mcp.tool()
    async def get_cookies(domain: str = '', target_url: str = '',
                          wait: bool = True) -> list[dict] | dict:
        """List cookies via extension. Filter by domain or URL.
        `wait=False` returns the command the bridge enqueued."""
        fields: dict = {}
        if domain:
            fields['domain'] = domain
        if target_url:
            fields['url'] = target_url
        return await bridge.ext_cmd(
            '_cookies', 'cookies', wait=wait, **fields)

    @mcp.tool()
    async def set_cookie(target_url: str, name: str, value: str,
                         domain: str = '', path: str = '',
                         http_only: bool = False, secure: bool = False,
                         same_site: str = '',
                         expires: float | None = None,
                         wait: bool = True) -> dict:
        """Set a cookie on `target_url`. `wait=False` returns the command
        the bridge enqueued."""
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
        return await bridge.ext_cmd(
            '_set_cookie', 'set-cookie', wait=wait, **fields)

    @mcp.tool()
    async def remove_cookie(target_url: str, name: str,
                            wait: bool = True) -> dict:
        """Remove a specific cookie by name at `target_url`. `wait=False`
        returns the command the bridge enqueued."""
        return await bridge.ext_cmd(
            '_rm_cookie', 'remove-cookie', wait=wait, url=target_url,
            name=name)

    @mcp.tool()
    async def clear_cookies(domain: str = '', target_url: str = '',
                            wait: bool = True) -> dict:
        """Clear all cookies matching domain/url. Returns {removed: N}.
        `wait=False` returns the command the bridge enqueued."""
        fields: dict = {}
        if domain:
            fields['domain'] = domain
        if target_url:
            fields['url'] = target_url
        return await bridge.ext_cmd(
            '_clear_cookies', 'clear-cookies', wait=wait, **fields)

    return {
        'get_cookies': get_cookies,
        'set_cookie': set_cookie,
        'remove_cookie': remove_cookie,
        'clear_cookies': clear_cookies,
    }
