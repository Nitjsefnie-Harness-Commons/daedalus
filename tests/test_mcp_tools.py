#!/usr/bin/env python3
"""Per-registration bridge binding for every returned MCP tool."""
import asyncio
import builtins
import gc
import importlib
import inspect
import sys
import types
import weakref
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
PROJECT_ROOT = _util.ROOT.resolve()
TEST_SOURCE = Path(__file__).resolve()
HARNESS_REFERENCE_IDS = {
    id(globals()),
    id(sys),
    id(vars(sys)),
    id(sys.modules),
    id(builtins),
    id(vars(builtins)),
}
HARNESS_REFERENCE_IDS.add(id(sys.modules[__name__]))


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


def _project_source(filename):
    path = Path(filename)
    if not path.is_absolute():
        return None
    path = path.resolve()
    if path.is_relative_to(PROJECT_ROOT):
        return path
    return None


def _function_references(function):
    source = _project_source(function.__code__.co_filename)
    if source == TEST_SOURCE:
        return
    known = {
        id(function.__code__),
        id(function.__globals__),
        id(getattr(function, '__builtins__', None)),
    }
    if function.__closure__:
        known.add(id(function.__closure__))
        for index, cell in enumerate(function.__closure__):
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            yield f'.__closure__[{index}]', value
    if function.__defaults__:
        known.add(id(function.__defaults__))
        yield '.__defaults__', function.__defaults__
    if function.__kwdefaults__:
        known.add(id(function.__kwdefaults__))
        yield '.__kwdefaults__', function.__kwdefaults__
    namespace = vars(function)
    known.add(id(namespace))
    if namespace:
        yield '.__dict__', namespace
    if source is not None:
        for name, value in function.__globals__.items():
            if name != '__builtins__':
                yield f'.__globals__[{name!r}]', value
    for index, member in enumerate(gc.get_referents(function)):
        if id(member) not in known:
            yield f'.<function-referent-{index}>', member


def _project_module(module):
    filename = getattr(module, '__file__', None)
    if not filename:
        return False
    source = _project_source(filename)
    return source is not None and source != TEST_SOURCE


def _object_references(value):
    if inspect.isfunction(value):
        yield from _function_references(value)
        return
    if isinstance(value, types.ModuleType):
        if _project_module(value):
            for name, member in vars(value).items():
                if name != '__builtins__':
                    yield f'.__dict__[{name!r}]', member
        return
    if isinstance(value, dict):
        for key, member in value.items():
            yield '.<dict-key>', key
            yield f'[{key!r}]', member
        if value.__class__ is dict:
            return
    if isinstance(value, (list, tuple)):
        for index, member in enumerate(value):
            yield f'[{index}]', member
        if value.__class__ in (list, tuple):
            return
    if isinstance(value, (frozenset, set)):
        for member in value:
            yield '.<set-member>', member
        if value.__class__ in (frozenset, set):
            return
    if isinstance(value, type):
        known = set()
        for name, member in vars(value).items():
            yield f'.__dict__[{name!r}]', member
            known.add(id(member))
        for index, base in enumerate(value.__mro__[1:], 1):
            yield f'.__mro__[{index}]', base
            known.add(id(base))
        for index, member in enumerate(gc.get_referents(value)):
            if id(member) not in known:
                yield f'.<class-referent-{index}>', member
        return
    try:
        namespace = vars(value)
    except TypeError:
        namespace = None
    if namespace:
        yield '.__dict__', namespace
    value_type = type(value)
    yield '.__class__', value_type
    known = {id(value_type)}
    if namespace is not None:
        known.add(id(namespace))
    for index, member in enumerate(gc.get_referents(value)):
        if id(member) not in known:
            yield f'.<referent-{index}>', member


def _reference_path(root, target):
    targets = {id(target)}
    targets.update(id(alias) for alias in weakref.getweakrefs(target))
    stack = [('tool', root)]
    visited = set()
    while stack:
        path, value = stack.pop()
        if id(value) in targets:
            return path
        identity = id(value)
        if identity in visited or identity in HARNESS_REFERENCE_IDS:
            continue
        visited.add(identity)
        if isinstance(value, (
                bool, bytes, complex, float, int, str, type(None))):
            continue
        for edge, member in _object_references(value):
            stack.append((path + edge, member))
    return None


def _assert_peer_bridge_unreachable(composition, peer, registration):
    for name, tool in sorted(composition.mcp.registered.items()):
        path = _reference_path(tool, peer.bridge)
        assert path is None, (
            f'{tool.__module__}.{name}: {registration} registered tool '
            f'reaches peer bridge via {path}')


def _wrapper_chain(tool):
    unwrapped = inspect.unwrap(tool)
    current = tool
    while True:
        yield current
        if current is unwrapped:
            return
        current = current.__wrapped__


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
    """Invoked tools stay local and post-run state holds no peer bridge.

    This covers state present after registration and one invocation of every
    tool. It cannot detect a crossing whose store and read both occur only on
    unexecuted paths.
    """
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
    _assert_peer_bridge_unreachable(
        first_composition, second_composition, 'first')
    _assert_peer_bridge_unreachable(
        second_composition, first_composition, 'second')


def test_no_registered_tool_behavior_is_authored_in_composition(_tmp):
    """No registered tool behavior is authored in the composition module.

    Every ordinary ``__wrapped__`` layer is checked. Helper-authored behavior
    is allowed; deliberately forged code origins are outside this property.
    """
    composition = _load_composition('composition-origin')
    composition_path = (_util.ROOT / 'mcp_server.py').resolve()
    direct_tools = sorted(
        f'{name} via {layer.__name__} at {layer.__code__.co_filename}:'
        f'{layer.__code__.co_firstlineno}'
        for name, tool in composition.mcp.registered.items()
        for layer in _wrapper_chain(tool)
        if Path(layer.__code__.co_filename).resolve() == composition_path)
    assert not direct_tools, (
        'tool behavior authored in mcp_server.py: '
        f'{direct_tools}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
