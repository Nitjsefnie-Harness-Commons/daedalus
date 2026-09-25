#!/usr/bin/env python3
"""The hotfix tool's site scope, driven through the real registration.

The scope an operator sets is a value the MCP surface forwards and then
reads back out of the extension's own record, so both directions are
pinned here against the tool that registration actually produced — not
against a re-implementation of it. Absence is pinned alongside, because a
surface that always sends the field would satisfy the forward direction
while narrowing every unscoped fix to a pattern nobody wrote.
"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _mcp_tool_commands  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

SCOPE = '*://*.example.com/*'


def _load_composition(marker):
    import importlib
    mcpserver = importlib.import_module('mcp.server.mcpserver')
    mcp_transport = importlib.import_module('daedalus_mcp.transport')

    def bridge_session(*_args, **_kwargs):
        return _BridgeProbe(marker)

    with mock.patch.object(mcpserver, 'MCPServer', _ToolRegistry), \
            mock.patch.object(
                mcp_transport, 'BridgeSession', bridge_session):
        with _daedalus_env.isolated({}):
            return _util.load(_util.ROOT / 'daedalus_mcp' / 'server.py',
                              f'mcp_server_hotfix_{marker}')


class _ToolRegistry:
    def __init__(self, *_args, **_kwargs):
        self.registered = {}

    def tool(self):
        def decorate(fn):
            self.registered[fn.__name__] = fn
            return fn

        return decorate


class _BridgeProbe:
    """Records each call; answers the one screenshot needed by nothing here."""

    def __init__(self, marker):
        self.marker = marker
        self.calls = []

    def record(self, kind, **payload):
        self.calls.append((kind, payload))

    async def ext_cmd(self, *args, **kwargs):
        self.record('ext_cmd', args=args, kwargs=kwargs)
        return {'bridge': self.marker}


def test_store_hotfix_carries_a_scope_out_and_hands_the_record_back(_tmp):
    """C9/C11: the MCP surface forwards the scope, and an unstated one is
    not sent at all.

    The extension keeps a re-stored fix's scope only when the field is
    absent, so an unstated scope travelling as an empty string or a null
    would either be refused as unparseable or read as a scope the operator
    never set. Absence is the only spelling that leaves the stored one
    alone.
    """
    scope = '*://*.example.com/*'
    composition = _load_composition('hotfix-scope')
    registered = composition.mcp.registered

    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)',
                                           match=scope))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)', match=scope),
    ]

    composition.bridge.calls.clear()
    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)'))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)'),
    ]

    record = {'version': '1.0', 'fixes': [
        {'id': 'fix', 'ts': 1, 'code': 'console.log(1)', 'match': scope},
        {'id': 'bare', 'ts': 2, 'code': 'console.log(2)'},
    ]}

    async def answer(*args, **kwargs):
        composition.bridge.record('ext_cmd', args=args, kwargs=kwargs)
        return record

    with mock.patch.object(composition.bridge, 'ext_cmd', answer):
        listed = asyncio.run(registered['list_hotfixes']())

    # The read direction is the extension's own record: the tool rewrites
    # nothing, so the scope that came back is the one that was stored and
    # a fix that carried none still reads as carrying none.
    assert listed == record, listed
    assert listed['fixes'][0]['match'] == scope, listed
    assert 'match' not in listed['fixes'][1], listed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='mcphotfix_')


if __name__ == '__main__':
    raise SystemExit(main())
