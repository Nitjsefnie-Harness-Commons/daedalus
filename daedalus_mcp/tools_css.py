"""CSS and request-blocking tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    @mcp.tool()
    async def inject_css(css: str, chrome_tab: int | None = None,
                         all_frames: bool = False,
                         wait: bool = True) -> dict:
        """Inject inline CSS into a tab. `wait=False` returns the command
        the bridge enqueued."""
        if not css:
            raise ValueError('css required')
        fields: dict = {'css': css}
        if chrome_tab is not None:
            fields['tabId'] = int(chrome_tab)
        if all_frames:
            fields['allFrames'] = True
        return await bridge.ext_cmd(
            '_inject_css', 'inject-css', wait=wait, **fields)

    @mcp.tool()
    async def remove_css(css: str, chrome_tab: int | None = None,
                         all_frames: bool = False,
                         wait: bool = True) -> dict:
        """Remove previously-injected inline CSS (must match the injected
        text). `wait=False` returns the command the bridge enqueued."""
        if not css:
            raise ValueError('css required')
        fields: dict = {'css': css}
        if chrome_tab is not None:
            fields['tabId'] = int(chrome_tab)
        if all_frames:
            fields['allFrames'] = True
        return await bridge.ext_cmd(
            '_remove_css', 'remove-css', wait=wait, **fields)

    @mcp.tool()
    async def block_requests(pattern: str, chrome_tab: int | None = None,
                             wait: bool = True) -> dict:
        """Block requests matching a declarativeNetRequest URL pattern.
        Returns {ruleId, pattern, tabIds}, or the command the bridge
        enqueued with `wait=False`."""
        fields: dict = {'pattern': pattern}
        if chrome_tab is not None:
            fields['tabId'] = int(chrome_tab)
        return await bridge.ext_cmd(
            '_block', 'block-requests', wait=wait, **fields)

    @mcp.tool()
    async def unblock_requests(rule_id: int | None = None,
                               wait: bool = True) -> dict:
        """Remove a block rule by id, or all rules if `rule_id` is None.
        `wait=False` returns the command the bridge enqueued."""
        fields: dict = {}
        if rule_id is not None:
            # Zero is not "no id": it reached the extension as a
            # present-but-false value and widened into removing every rule.
            if int(rule_id) <= 0:
                return {'error': 'rule_id must be a positive integer'}
            fields['ruleId'] = int(rule_id)
        return await bridge.ext_cmd(
            '_unblock', 'unblock-requests', wait=wait, **fields)

    @mcp.tool()
    async def list_block_rules(wait: bool = True) -> list[dict] | dict:
        """List currently-active declarativeNetRequest block rules.
        `wait=False` returns the command the bridge enqueued."""
        return await bridge.ext_cmd(
            '_list_rules', 'list-block-rules', wait=wait)

    return {
        'inject_css': inject_css,
        'remove_css': remove_css,
        'block_requests': block_requests,
        'unblock_requests': unblock_requests,
        'list_block_rules': list_block_rules,
    }
