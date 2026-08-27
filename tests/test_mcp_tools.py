#!/usr/bin/env python3
"""Per-registration bridge binding for every returned MCP tool."""
import ast
import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

INTERACTIONS = []


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
        headers = kwargs.get('headers', {})
        self.bridge.record(
            'http_client.get',
            authorization=headers.get('Authorization'))
        if path == '/segment-job':
            return HTTPResponseProbe({'sig': self.bridge.marker})
        return HTTPResponseProbe({'done': []})


class BridgeProbe:
    """Record which per-registration bridge a tool actually calls."""

    def __init__(self, marker):
        self.marker = marker
        self.calls = []
        self.transport = object()

    def record(self, surface, **details):
        self.calls.append((surface, details))
        INTERACTIONS.append((self.marker, surface, details))

    def http_client(self):
        self.record('http_client')
        return HTTPClientProbe(self)

    def auth(self):
        self.record('auth')
        return {'Authorization': self.marker}

    def checked_timeout(self, timeout):
        self.record('checked_timeout', timeout=timeout)

    async def get(self, path, **params):
        self.record('get', path=path, params=params)
        if path == '/tabs':
            return [{'title': self.marker}]
        return {'result': self.marker}

    async def get_raw(self, endpoint, **params):
        self.record('get_raw', endpoint=endpoint, params=params)
        return b'image'

    async def post(self, path, body):
        self.record('post', path=path, body=body)
        return {'bridge': self.marker}

    async def delete(self, path, body):
        self.record('delete', path=path, body=body)
        return {'bridge': self.marker}

    async def ext_cmd(self, *args, **kwargs):
        self.record('ext_cmd', args=args, kwargs=kwargs)
        if len(args) > 1 and args[1] == 'screenshot':
            return {
                'path': f'{self.marker}/shot.png',
                'size': len(self.marker),
            }
        return {'bridge': self.marker}

    async def put(self, path, payload):
        self.record('put', path=path, payload=payload)
        return {'did': self.marker}

    async def poll_result(self, *args, **kwargs):
        self.record('poll_result', args=args, kwargs=kwargs)
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
BRANCH_ARGUMENTS = {
    'screenshot': {'include_image': True},
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

    registered = set(composition.mcp.registered.values())
    assert registered == inventoried, (
        'MCP registry and tool module inventory differ: '
        f'unrecorded={len(registered - inventoried)}; '
        f'unregistered={len(inventoried - registered)}')


def _composition_tool_uses():
    path = _util.ROOT / 'mcp_server.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    return sorted(
        f'{path.name}:{node.lineno}:{node.col_offset + 1}'
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == 'mcp'
        and node.attr == 'tool')


def _tool_arguments(tool):
    arguments = {}
    for parameter in inspect.signature(tool).parameters.values():
        if parameter.default is not inspect.Parameter.empty:
            continue
        assert parameter.name in REQUIRED_ARGUMENTS, (
            f'{tool.__name__}: no test value for required argument '
            f'{parameter.name}')
        arguments[parameter.name] = REQUIRED_ARGUMENTS[parameter.name]
    arguments.update(BRANCH_ARGUMENTS.get(tool.__name__, {}))
    return arguments


async def _bridge_interactions(tool, arguments):
    offset = len(INTERACTIONS)
    await tool(**arguments)
    return INTERACTIONS[offset:]


def _assert_bound_interactions(
        qualified_name, registration, expected, interactions):
    assert interactions, (
        f'{qualified_name}: {registration} registered tool recorded no '
        'bridge interaction')
    bearer_crossings = [
        (marker, details['authorization'])
        for marker, surface, details in interactions
        if surface == 'http_client.get'
        and details.get('authorization') is not None
        and details['authorization'] != marker
    ]
    assert not bearer_crossings, (
        f'{qualified_name}: {registration} registered tool crossed '
        'http_client.get bearer ownership: '
        f'{bearer_crossings}')
    surface_crossings = [
        f'{surface} via {marker} bridge'
        for marker, surface, _details in interactions
        if marker != expected
    ]
    assert not surface_crossings, (
        f'{qualified_name}: {registration} registered tool crossed bridge '
        f'surfaces: {surface_crossings}')


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
            arguments = _tool_arguments(first_tool)
            first_interactions = asyncio.run(_bridge_interactions(
                first_tool, arguments))
            _assert_bound_interactions(
                qualified_name, 'first', 'first', first_interactions)
            second_interactions = asyncio.run(_bridge_interactions(
                second_tools[name], arguments))
            _assert_bound_interactions(
                qualified_name, 'second', 'second', second_interactions)
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


def test_composition_point_defines_no_tools_directly(_tmp):
    """The composition source never accesses its registry decorator."""
    direct_uses = _composition_tool_uses()
    assert not direct_uses, (
        'mcp_server.py contains direct mcp.tool use: '
        f'{direct_uses}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
