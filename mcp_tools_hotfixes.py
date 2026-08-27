"""Hotfix tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    """Define this group's tools against `mcp`, bound to `bridge`.

    Returns {tool name: coroutine function} so the composition point can keep
    the tools callable as its own attributes.
    """

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

    return {
        'store_hotfix': store_hotfix,
        'clear_hotfix': clear_hotfix,
        'clear_hotfixes': clear_hotfixes,
        'list_hotfixes': list_hotfixes,
        'set_permanent': set_permanent,
    }
