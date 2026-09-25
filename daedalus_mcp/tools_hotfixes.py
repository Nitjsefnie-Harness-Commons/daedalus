"""Hotfix tools for the Daedalus MCP front end."""


def register(mcp, bridge):
    @mcp.tool()
    async def store_hotfix(fix_id: str, code: str,
                           permanent: bool | None = None,
                           match: str | None = None,
                           clear_scope: bool = False) -> dict:
        """Store inline JS as a persistent hotfix. `permanent=True` marks the
    fix as surviving extension version bumps, `False` clears that mark, and
    None (the default) keeps the flag a fix of this id already has stored.
    `match` is a Chrome match pattern; None (the default) keeps the scope the
    fix already has, and a fix stored without one runs wherever it is asked
    for. `clear_scope=True` removes the scope instead, leaving the code and
    the flag alone; it cannot be combined with `match`."""
        if not code:
            raise ValueError('code required')
        if clear_scope and match is not None:
            raise ValueError(
                'clear_scope asks for the scope to go, so it cannot travel '
                'with a match pattern')
        fields: dict = {'fixId': fix_id, 'code': code}
        # The extension keeps a re-stored fix's flag and its scope only when
        # the field is absent, so an unstated choice must not travel as one.
        if permanent is not None:
            fields['permanent'] = permanent
        if clear_scope:
            fields['clearScope'] = True
        # Not a truthy guard: an empty pattern is the extension's refusal to
        # read, and dropping it here would keep the old scope and report
        # success.
        if match is not None:
            fields['match'] = match
        return await bridge.ext_cmd('_store_hf', 'store-hotfix', **fields)

    @mcp.tool()
    async def clear_hotfix(fix_id: str) -> dict:
        """Remove a specific hotfix by id."""
        return await bridge.ext_cmd(
            '_clear_hf', 'clear-hotfix', fixId=fix_id)

    @mcp.tool()
    async def clear_hotfixes(include_permanent: bool = False) -> dict:
        """Remove stored hotfixes. By default, permanent fixes are preserved;
        set `include_permanent=True` to nuke everything."""
        return await bridge.ext_cmd(
            '_clear_all_hf', 'clear-all-hotfixes',
            includePermanent=include_permanent)

    @mcp.tool()
    async def list_hotfixes() -> dict:
        """List stored hotfixes. Returns
        {version, fixes:[{id,ts,code,permanent,match},...]} — `match` is the
        fix's site scope, and a fix stored without one has no `match`."""
        return await bridge.ext_cmd('_list_hf', 'list-hotfixes')

    @mcp.tool()
    async def set_permanent(fix_id: str, permanent: bool) -> dict:
        """Toggle the permanent flag on an existing hotfix. Permanent fixes
        survive extension version bumps. Returns {id, permanent, found}."""
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
