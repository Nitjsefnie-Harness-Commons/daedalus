#!/usr/bin/env python3
"""Per-registration bridge binding for every returned MCP tool."""
import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))


class ToolRegistry:
    """Minimal MCP registry that preserves decorated coroutine functions."""

    def __init__(self, *_args, **_kwargs):
        self.registered = {}

    def tool(self):
        def decorate(fn):
            self.registered[fn.__name__] = fn
            return fn

        return decorate


class HTTPResponseProbe:
    """Minimal response for the media status tool."""

    def __init__(self, body):
        self.status_code = 200
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class HTTPClientProbe:
    """Record direct HTTP client calls against the owning bridge probe."""

    def __init__(self, bridge):
        self.bridge = bridge

    async def get(self, path, **kwargs):
        self.bridge.calls.append(('http_client.get', path, kwargs))
        if path == '/segment-job':
            return HTTPResponseProbe({'sig': self.bridge.marker})
        return HTTPResponseProbe({'done': []})


class BridgeProbe:
    """Record which per-registration bridge a tool actually calls."""

    def __init__(self, marker):
        self.marker = marker
        self.calls = []
        self.transport = object()

    def http_client(self):
        return HTTPClientProbe(self)

    def auth(self):
        return {'Authorization': self.marker}

    def checked_timeout(self, timeout):
        self.calls.append(('checked_timeout', timeout))

    async def get(self, path, **params):
        self.calls.append(('get', path, params))
        if path == '/tabs':
            return [{'title': self.marker}]
        return {'result': self.marker}

    async def post(self, path, body):
        self.calls.append(('post', path, body))
        return {'bridge': self.marker}

    async def delete(self, path, body):
        self.calls.append(('delete', path, body))
        return {'bridge': self.marker}

    async def ext_cmd(self, *args, **kwargs):
        self.calls.append(('ext_cmd', args, kwargs))
        return {'bridge': self.marker}

    async def put(self, path, payload):
        self.calls.append(('put', path, payload))
        return {'did': self.marker}

    async def poll_result(self, *args, **kwargs):
        self.calls.append(('poll_result', args, kwargs))
        return {'result': self.marker, 'error': None, 'world': self.marker}


REQUIRED_ARGUMENTS = {
    'cmd_id': 'cmd',
    'code': '1 + 1',
    'url': 'https://example.com',
    'urls': ['https://example.com'],
    'chrome_tab': 7,
    'chrome_tabs': [7],
    'job': 'job',
    'target_url': 'https://example.com',
    'name': 'name',
    'value': 'value',
    'css': 'body {}',
    'pattern': '*://example.com/*',
    'fix_id': 'fix',
    'permanent': True,
    'method': 'Page.getFrameTree',
}


def _load_composition(marker):
    mcpserver = importlib.import_module('mcp.server.mcpserver')
    mcp_transport = importlib.import_module('mcp_transport')

    def bridge_session(*_args, **_kwargs):
        return BridgeProbe(marker)

    with mock.patch.object(mcpserver, 'MCPServer', ToolRegistry), \
            mock.patch.object(
                mcp_transport, 'BridgeSession', bridge_session):
        return _util.load(
            _util.ROOT / 'mcp_server.py', f'mcp_server_tools_{marker}')


def _assert_inventory_matches_registry(composition):
    inventoried = set()
    for module, tools in composition.tool_module_inventory:
        for name, tool in tools.items():
            qualified_name = f'{module.__name__}.{name}'
            assert composition.mcp.registered.get(name) is tool, (
                f'{qualified_name}: inventory does not match MCP registry')
            inventoried.add(tool)

    unrecorded = sorted(
        f'{tool.__module__}.{name}'
        for name, tool in composition.mcp.registered.items()
        if tool not in inventoried
        and tool.__module__ != composition.__name__)
    assert not unrecorded, (
        'tools registered outside tool module inventory: '
        f'{unrecorded}')


def _required_arguments(tool):
    arguments = {}
    for parameter in inspect.signature(tool).parameters.values():
        if parameter.default is not inspect.Parameter.empty:
            continue
        assert parameter.name in REQUIRED_ARGUMENTS, (
            f'{tool.__name__}: no test value for required argument '
            f'{parameter.name}')
        arguments[parameter.name] = REQUIRED_ARGUMENTS[parameter.name]
    return arguments


async def _reached_bridge(tool, arguments, first_bridge, second_bridge):
    first_calls = len(first_bridge.calls)
    second_calls = len(second_bridge.calls)
    await tool(**arguments)
    reached = []
    if len(first_bridge.calls) > first_calls:
        reached.append(first_bridge.marker)
    if len(second_bridge.calls) > second_calls:
        reached.append(second_bridge.marker)
    assert len(reached) == 1, (
        f'{tool.__name__}: expected one bridge call, reached {reached}')
    return reached[0]


def test_every_registered_tool_keeps_its_own_bridge(_tmp):
    """Every returned tool calls the bridge from its own registration."""
    first_composition = _load_composition('first')
    second_composition = _load_composition('second')
    assert hasattr(first_composition, 'tool_module_inventory'), (
        'mcp_server.tool_module_inventory missing')
    assert hasattr(second_composition, 'tool_module_inventory'), (
        'mcp_server.tool_module_inventory missing')
    first_inventory = first_composition.tool_module_inventory
    second_inventory = second_composition.tool_module_inventory
    first_modules = [module.__name__ for module, _tools in first_inventory]
    second_modules = [module.__name__
                      for module, _tools in second_inventory]
    assert first_modules == second_modules, (
        'MCP tool inventory changed between registrations: '
        f'first={first_modules}; second={second_modules}')

    wired_modules = set(first_modules)
    unwired_modules = sorted(
        path.stem for path in _util.ROOT.glob('mcp_tools_*.py')
        if path.stem not in wired_modules)
    assert not unwired_modules, (
        f'MCP tool modules not registered: {unwired_modules}')
    _assert_inventory_matches_registry(first_composition)
    _assert_inventory_matches_registry(second_composition)

    returned_callables = set()
    exercised_callables = set()
    first_bridge = first_composition.bridge
    second_bridge = second_composition.bridge
    for first_entry, second_entry in zip(first_inventory, second_inventory):
        module, first_tools = first_entry
        second_module, second_tools = second_entry
        module_name = module.__name__
        assert module is second_module, (
            'MCP tool inventory module mismatch: '
            f'first={module_name}; second={second_module.__name__}')

        assert set(first_tools) == set(second_tools)
        assert all(callable(tool) for tool in first_tools.values())
        assert all(callable(tool) for tool in second_tools.values())

        covered = set()
        for name, first_tool in first_tools.items():
            qualified_name = f'{module_name}.{name}'
            returned_callables.add(qualified_name)
            arguments = _required_arguments(first_tool)
            first_reached = asyncio.run(_reached_bridge(
                first_tool, arguments, first_bridge, second_bridge))
            second_reached = asyncio.run(_reached_bridge(
                second_tools[name], arguments, first_bridge, second_bridge))
            assert first_reached == 'first', (
                f'{qualified_name}: first registered tool reached '
                f'{first_reached} bridge')
            assert second_reached == 'second', (
                f'{qualified_name}: second registered tool reached '
                f'{second_reached} bridge')
            covered.add(name)
            exercised_callables.add(qualified_name)

        returned = set(first_tools)
        assert covered == returned, (
            f'{module_name}: not exercised={sorted(returned - covered)}; '
            f'not returned={sorted(covered - returned)}')

    assert exercised_callables == returned_callables, (
        'registered tool coverage mismatch: '
        f'not exercised={sorted(returned_callables - exercised_callables)}; '
        f'not returned={sorted(exercised_callables - returned_callables)}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
