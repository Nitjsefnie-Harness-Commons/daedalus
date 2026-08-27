"""CSS and request-blocking tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

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

    return {
        'inject_css': inject_css,
        'remove_css': remove_css,
        'block_requests': block_requests,
        'unblock_requests': unblock_requests,
        'list_block_rules': list_block_rules,
    }
