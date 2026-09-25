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
    """Records each ext_cmd call and answers with a fixed bridge marker."""

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


def test_store_hotfix_forwards_the_empty_pattern_it_cannot_swallow(_tmp):
    """C15: `match=""` travels to the extension, which is what refuses it.

    The extension owns the pattern grammar, so the surface's whole job is to
    forward what the caller wrote and let the extension answer. A guard that
    drops an empty string turns that refusal into silence: the store keeps
    the scope the operator was trying to change and reports success. This is
    the one routing decision on this surface whose truthy-guard mutant
    changes behaviour without turning any other control red — the empty
    string is refused, so no accepted-store control sees it — and it is
    pinned here rather than left to the extension's own refusal.
    """
    composition = _load_composition('hotfix-empty-match')
    registered = composition.mcp.registered

    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)',
                                           match=''))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)', match=''),
    ]

    # The anti-vacuity half: the same field forwards a real pattern and
    # forwards nothing at all when it is unstated, so the assertion above
    # reads the value rather than the field's presence.
    composition.bridge.calls.clear()
    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)',
                                           match=SCOPE))
    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)'))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)', match=SCOPE),
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)'),
    ], composition.bridge.calls


def test_store_hotfix_carries_a_clear_out_and_refuses_a_contradiction(_tmp):
    """C12/C13, MCP direction: the clear reaches the extension as the clear.

    An unstated clear is not sent — the extension reads an absent field as
    "leave the scope alone", so sending `False` by default would be a second
    spelling of the same instruction. A clear that travels beside a pattern
    is refused here rather than sent as a contradiction the extension has to
    guess at.
    """
    composition = _load_composition('hotfix-clear')
    registered = composition.mcp.registered

    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)',
                                           clear_scope=True))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)', clearScope=True),
    ]

    composition.bridge.calls.clear()
    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)'))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)'),
    ], composition.bridge.calls

    refused = False
    composition.bridge.calls.clear()
    try:
        asyncio.run(registered['store_hotfix'](
            'fix', 'console.log(1)', match=SCOPE, clear_scope=True))
    except ValueError:
        refused = True
    # The anti-vacuity half: the refusal is the PAIR, so the same call
    # without the clear is still forwarded.
    assert refused, 'clear_scope with a match was not refused'
    assert composition.bridge.calls == [], composition.bridge.calls
    asyncio.run(registered['store_hotfix']('fix', 'console.log(1)',
                                           match=SCOPE, clear_scope=False))

    assert composition.bridge.calls == [
        _mcp_tool_commands._ext('_store_hf', 'store-hotfix', fixId='fix',
                                code='console.log(1)', match=SCOPE),
    ], composition.bridge.calls


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='mcphotfix_')


if __name__ == '__main__':
    raise SystemExit(main())
