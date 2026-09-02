#!/usr/bin/env python3
"""Per-registration bridge binding for every returned MCP tool."""
import asyncio
import gc
import importlib
import inspect
import sys
import types
import weakref
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_tool_commands  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

INTERACTIONS = []


class ToolRegistry:
    def __init__(self, *_args, **_kwargs):
        self.registered = {}

    def tool(self):
        def decorate(fn):
            self.registered[fn.__name__] = fn
            return fn

        return decorate


class HTTPResponseProbe:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class HTTPClientProbe:
    def __init__(self, bridge):
        self.bridge = bridge

    async def get(self, path, **kwargs):
        self.bridge.record(
            'http_client.get', path=path, params=kwargs.get('params', {}),
            headers=kwargs.get('headers', {}))
        status, body = self.bridge.http_bodies.get(path, (200, {'done': []}))
        return HTTPResponseProbe(status, body)


class BridgeProbe:
    def __init__(self, marker):
        self.marker = marker
        self.calls = []
        self.transport = object()
        # A case sets these to drive a branch the default bodies cannot reach.
        self.get_bodies = {}
        self.http_bodies = {'/segment-job': (200, {'sig': self.marker})}
        self.ext_bodies = {}
        self.poll_body = None

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
        if path in self.get_bodies:
            return self.get_bodies[path]
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
            return self.ext_bodies.get('screenshot', {
                'path': f'{self.marker}/shot.png',
                'size': len(self.marker),
            })
        return {'bridge': self.marker}

    async def put(self, path, payload):
        self.record('put', path=path, payload=payload)
        return {'did': self.marker}

    async def poll_result(self, *args, **kwargs):
        self.record('poll_result', args=args, kwargs=kwargs)
        if self.poll_body is not None:
            return self.poll_body
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
    mcp_transport = importlib.import_module('daedalus_mcp.transport')

    def bridge_session(*_args, **_kwargs):
        return BridgeProbe(marker)

    with mock.patch.object(mcpserver, 'MCPServer', ToolRegistry), \
            mock.patch.object(
                mcp_transport, 'BridgeSession', bridge_session):
        return _util.load(
            _util.ROOT / 'daedalus_mcp' / 'server.py', f'mcp_server_tools_{marker}')


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


def _composition_module_names(composition):
    return [
        module.__name__
        for module, _tools in composition.tool_module_inventory
    ]


def _assert_complete_inventory(composition):
    module_names = set(_composition_module_names(composition))
    tool_paths = list((_util.ROOT / 'daedalus_mcp').glob('tools_*.py'))
    assert tool_paths, 'no daedalus_mcp tools_*.py module found'
    unwired_modules = sorted(
        f'daedalus_mcp.{path.stem}'
        for path in tool_paths
        if f'daedalus_mcp.{path.stem}' not in module_names)
    assert not unwired_modules, (
        f'MCP tool modules not registered: {unwired_modules}')
    _assert_inventory_matches_registry(composition)


def _assert_matching_inventories(first_composition, second_composition):
    first_inventory = first_composition.tool_module_inventory
    second_inventory = second_composition.tool_module_inventory
    assert len(first_inventory) == len(second_inventory)
    for first_entry, second_entry in zip(first_inventory, second_inventory):
        module, first_tools = first_entry
        second_module, second_tools = second_entry
        assert module is second_module, (
            'MCP tool inventory module mismatch: '
            f'first={module.__name__}; second={second_module.__name__}')
        assert set(first_tools) == set(second_tools)


def _nested_code_objects(code):
    pending = [code]
    visited = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        yield current
        pending.extend(
            value for value in current.co_consts
            if isinstance(value, types.CodeType))


def _tool_code_objects(tool):
    pending = [tool]
    visited_functions = set()
    visited_codes = set()
    while pending:
        function = pending.pop()
        if id(function) in visited_functions:
            continue
        visited_functions.add(id(function))
        for code in _nested_code_objects(function.__code__):
            if id(code) not in visited_codes:
                visited_codes.add(id(code))
                yield code
        if function.__closure__:
            for cell in function.__closure__:
                try:
                    value = cell.cell_contents
                except ValueError:
                    continue
                if inspect.isfunction(value):
                    pending.append(value)
        for value in function.__defaults__ or ():
            if inspect.isfunction(value):
                pending.append(value)
        for value in (function.__kwdefaults__ or {}).values():
            if inspect.isfunction(value):
                pending.append(value)
        wrapped = getattr(function, '__wrapped__', None)
        if inspect.isfunction(wrapped):
            pending.append(wrapped)


def _tool_arguments(tool, overrides):
    arguments = {}
    for parameter in inspect.signature(tool).parameters.values():
        if parameter.default is not inspect.Parameter.empty:
            continue
        assert parameter.name in REQUIRED_ARGUMENTS, (
            f'{tool.__name__}: no test value for required argument '
            f'{parameter.name}')
        arguments[parameter.name] = REQUIRED_ARGUMENTS[parameter.name]
    arguments.update(overrides)
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
        (marker, details['headers']['Authorization'])
        for marker, surface, details in interactions
        if surface == 'http_client.get'
        and details['headers'].get('Authorization') is not None
        and details['headers']['Authorization'] != marker
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


def _exercise_composition(composition, registration, expected):
    returned_callables = set()
    exercised_callables = set()
    for module, tools in composition.tool_module_inventory:
        module_name = module.__name__
        assert all(callable(tool) for tool in tools.values())
        covered = set()
        for name, tool in tools.items():
            qualified_name = f'{module_name}.{name}'
            returned_callables.add(qualified_name)
            arguments = _tool_arguments(
                tool, BRANCH_ARGUMENTS.get(tool.__name__, {}))
            interactions = asyncio.run(_bridge_interactions(
                tool, arguments))
            _assert_bound_interactions(
                qualified_name, registration, expected, interactions)
            covered.add(name)
            exercised_callables.add(qualified_name)
        returned = set(tools)
        assert covered == returned, (
            f'{module_name}: not exercised={sorted(returned - covered)}; '
            f'not returned={sorted(covered - returned)}')
    assert exercised_callables == returned_callables, (
        'registered tool coverage mismatch: '
        f'not exercised={sorted(returned_callables - exercised_callables)}; '
        f'not returned={sorted(exercised_callables - returned_callables)}')


def _release_composition(composition):
    composition.mcp.registered.clear()
    composition.tool_module_inventory.clear()
    sys.modules.pop(composition.__name__, None)


def test_every_registered_tool_keeps_its_own_bridge(_tmp):
    """No executed tool retains the peer bridge object at teardown.

    All registered tools run once on the harness-selected paths, including
    ``screenshot(include_image=True)``. The first bridge must have no weak
    alias, and its observation weak reference must be cleared after collection.
    The check cannot see independently usable derivatives, such as a cached
    authentication dictionary or a copied bridge. Cyclic finalization can
    resurrect the bridge after clearing that weak reference, so the assertion
    establishes only that the observation weak reference was cleared.
    """
    first_composition = _load_composition('first')
    assert hasattr(first_composition, 'tool_module_inventory'), (
        'daedalus_mcp.server.tool_module_inventory missing')
    _assert_complete_inventory(first_composition)
    first_modules = _composition_module_names(first_composition)
    _exercise_composition(first_composition, 'first', 'first')

    second_composition = _load_composition('second')
    assert hasattr(second_composition, 'tool_module_inventory'), (
        'daedalus_mcp.server.tool_module_inventory missing')
    _assert_complete_inventory(second_composition)
    second_modules = _composition_module_names(second_composition)
    assert first_modules == second_modules, (
        'MCP tool inventory changed between registrations: '
        f'first={first_modules}; second={second_modules}')
    _assert_matching_inventories(first_composition, second_composition)
    _exercise_composition(second_composition, 'second', 'second')

    weak_aliases = weakref.getweakrefs(first_composition.bridge)
    assert not weak_aliases, (
        'first bridge has a weak alias after executed registration and tool '
        'paths')
    first_bridge_ref = weakref.ref(first_composition.bridge)
    _release_composition(first_composition)
    del first_composition
    gc.collect()
    assert first_bridge_ref() is None, (
        'first bridge remains alive after harness teardown')


def test_screenshot_default_returns_metadata_without_fetching_image(_tmp):
    composition = _load_composition('screenshot-default')
    screenshot = composition.mcp.registered['screenshot']

    result = asyncio.run(screenshot())

    expected = {
        'path': 'screenshot-default/shot.png',
        'size': len('screenshot-default'),
    }
    assert result == expected, (result, expected)
    assert all(
        surface != 'get_raw'
        for surface, _details in composition.bridge.calls)


def test_no_registered_tool_behavior_is_authored_in_composition(_tmp):
    """No registered tool lexically reaches composition-authored behavior.

    The check follows the callable's own and nested code, functions in closure
    cells, direct function-valued positional and keyword defaults, and
    ``__wrapped__`` links. Functions held inside default containers are outside
    the property, as are composition-authored functions found dynamically at
    call time through globals, imports, module attributes, ``getattr``, or
    dictionaries. Code objects with deliberately forged origins are also
    outside the property.
    """
    composition = _load_composition('composition-origin')
    composition_path = (_util.ROOT / 'daedalus_mcp' / 'server.py').resolve()
    scanned = {
        name for name, tool in composition.mcp.registered.items()
        if any(_tool_code_objects(tool))}
    assert scanned == set(composition.mcp.registered), (
        'the origin scan traversed no code for: '
        f'{sorted(set(composition.mcp.registered) - scanned)}')
    direct_tools = sorted(
        f'{name} via {code.co_name} at {code.co_filename}:'
        f'{code.co_firstlineno}'
        for name, tool in composition.mcp.registered.items()
        for code in _tool_code_objects(tool)
        if Path(code.co_filename).resolve() == composition_path)
    assert not direct_tools, (
        'tool behavior authored in daedalus_mcp/server.py: '
        f'{direct_tools}')


def test_every_registered_tool_command_is_pinned(_tmp):
    """Each registered tool puts its pinned command on the probe bridge.

    The table is keyed by registered tool name, so a tool registered without a
    case fails here instead of shipping without an executable test.
    """
    composition = _load_composition(_mcp_tool_commands.MARKER)
    registered = composition.mcp.registered
    commands = _mcp_tool_commands.TOOL_COMMANDS
    assert set(registered) == set(commands), (
        'registered tools and pinned commands differ: '
        f'unpinned={sorted(set(registered) - set(commands))}; '
        f'not registered={sorted(set(commands) - set(registered))}')
    orphans = set(_mcp_tool_commands.UNPINNED_PARAMETERS) - set(commands)
    assert not orphans, (
        'UNPINNED_PARAMETERS names tools that are not registered: '
        f'{sorted(orphans)}')
    unwitnessed = {}
    tools = {}
    for name, cases in commands.items():
        assert cases, f'{name}: registered with no pinned case'
        tool = registered[name]
        tools[name] = tool
        optional = [
            parameter.name
            for parameter in inspect.signature(tool).parameters.values()
            if parameter.default is not inspect.Parameter.empty]
        covered = {key for overrides, _expected in cases for key in overrides}
        gaps = _mcp_tool_commands.unpinned_parameter_gaps(
            name, optional, covered)
        if gaps:
            unwitnessed[name] = gaps
    assert not unwitnessed, (
        'unwitnessed or stale parameter pins: '
        + '; '.join(f'{name}: {"; ".join(gaps)}'
                    for name, gaps in unwitnessed.items()))
    for name, cases in commands.items():
        tool = tools[name]
        for overrides, expected in cases:
            assert expected, f'{name} {overrides}: pins no bridge call'
            composition.bridge.calls.clear()
            asyncio.run(tool(**_tool_arguments(tool, overrides)))
            assert composition.bridge.calls == expected, (
                f'{name} {overrides}: bridge calls differ: '
                f'{composition.bridge.calls}')
    refusals = _mcp_tool_commands.TOOL_REFUSALS
    assert refusals, 'no tool has a pinned refusal'
    sites = _mcp_tool_commands.tool_guards(
        sorted((_util.ROOT / 'daedalus_mcp').glob('tools_*.py')))
    assert sites, 'no raise site found in the MCP tool modules'
    witnessed = set()
    for name, cases in refusals.items():
        assert name in registered, (
            f'{name}: refusal pinned for a tool that is not registered')
        assert cases, f'{name}: registered with no pinned refusal'
        tool = registered[name]
        for overrides, exception, message in cases:
            assert message, f'{name} {overrides}: refusal pinned with no message'
            composition.bridge.calls.clear()
            try:
                asyncio.run(tool(**_tool_arguments(tool, overrides)))
            except exception as raised:
                assert message in str(raised), (name, overrides, raised)
                fired = _mcp_tool_commands.witnessed_guard(sites, raised)
                assert fired, (
                    f'{name} {overrides}: refused from no known guard')
                witnessed.add(fired)
            else:
                raise AssertionError(f'{name} {overrides}: nothing refused')
            assert composition.bridge.calls == [], (
                f'{name} {overrides}: refused after a bridge call')
    orphans = (set(_mcp_tool_commands.UNWITNESSED_GUARDS)
               - set(_mcp_tool_commands.guard_conditions(sites)))
    assert not orphans, (
        'UNWITNESSED_GUARDS names functions that guard no raise: '
        f'{sorted(orphans)}')
    stranded = _mcp_tool_commands.stranded_guards(sites, witnessed)
    assert not stranded, (
        'unwitnessed or stale guard pins: '
        + '; '.join(f'{function}: {"; ".join(gaps)}'
                    for function, gaps in stranded.items()))


def test_the_parameter_floor_refuses_a_stranded_parameter(_tmp):
    """unpinned_parameter_gaps detects, not merely passes a healthy table.

    Called directly with synthetic tools, so the floor's detection limb is
    banked even though every entry in TOOL_COMMANDS is clean and the
    registration-driven pass therefore sees no gaps.
    """
    assert _mcp_tool_commands.unpinned_parameter_gaps(
        't', {'a', 'b'}, {'a'}) == ["no case witnesses 'b'"]
    with mock.patch.dict(
            _mcp_tool_commands.UNPINNED_PARAMETERS, {'t': {'b'}}):
        assert _mcp_tool_commands.unpinned_parameter_gaps(
            't', {'a', 'b'}, {'a'}) == []
        assert _mcp_tool_commands.unpinned_parameter_gaps(
            't', {'a', 'b'}, {'b'}) == [
                "UNPINNED_PARAMETERS names 'b', which a case already "
                'witnesses',
                "no case witnesses 'a'"]
    with mock.patch.dict(
            _mcp_tool_commands.UNPINNED_PARAMETERS, {'t': {'c'}}):
        assert _mcp_tool_commands.unpinned_parameter_gaps(
            't', {'a', 'b'}, {'a'}) == [
                "UNPINNED_PARAMETERS names 'c', which the signature does not "
                'have',
                "no case witnesses 'b'"]


GUARD_SHAPES = """
raise ValueError('at module level')


def outer():
    def helper(value):
        if not value or value < 0:
            raise ValueError('guarded by two conditions')
        assert value < 10
        if value:
            pass
        else:
            raise RuntimeError('in an else')
        raise RuntimeError('guarded by nothing')
    return helper
"""


def test_the_guard_scan_reads_every_raise_shape_the_tools_use(_tmp):
    """Each raise shape the scan can meet resolves to a deliberate condition.

    A raise in a nested helper belongs to that helper; an `or` test gives one
    condition per operand; a raise no `if` body holds stands for itself, an
    `else` included, since its guard is a negation rather than an operand of
    the test; an `assert` is not a raise. A shape resolving to nothing would
    leave the floor passing vacuously, the defect it exists to close.
    """
    path = Path(_tmp) / 'shapes.py'
    path.write_text(GUARD_SHAPES, encoding='utf-8')

    conditions = _mcp_tool_commands.guard_conditions(
        _mcp_tool_commands.tool_guards([path]))

    assert {name: sorted(texts) for name, texts in conditions.items()} == {
        '': ["raise ValueError('at module level')"],
        'helper': ['not value',
                   "raise RuntimeError('guarded by nothing')",
                   "raise RuntimeError('in an else')",
                   'value < 0'],
    }, conditions


def test_the_refusal_floor_refuses_a_stranded_guard(_tmp):
    """unwitnessed_guard_gaps detects, not merely passes a healthy table.

    Called directly with synthetic guards, so the floor's detection limb is
    banked even though every guard in the tool modules is witnessed and the
    registration-driven pass therefore sees no gaps.
    """
    gaps = _mcp_tool_commands.unwitnessed_guard_gaps
    assert gaps('f', ['a', 'b'], {'a'}) == ["no refusal case witnesses 'b'"]
    assert gaps('f', ['a', 'a'], {'a'}) == ["'a' spells two of the guards"]
    with mock.patch.dict(
            _mcp_tool_commands.UNWITNESSED_GUARDS, {'f': {'b'}}):
        assert gaps('f', ['a', 'b'], {'a'}) == []
        assert gaps('f', ['a', 'b'], {'b'}) == [
            "UNWITNESSED_GUARDS names 'b', which a refusal case already "
            'witnesses',
            "no refusal case witnesses 'a'"]
    with mock.patch.dict(
            _mcp_tool_commands.UNWITNESSED_GUARDS, {'f': {'c'}}):
        assert gaps('f', ['a', 'b'], {'a'}) == [
            "UNWITNESSED_GUARDS names 'c', which is guarded by nothing",
            "no refusal case witnesses 'b'"]


def test_result_reports_an_absent_result_without_a_value(_tmp):
    composition = _load_composition('result-absent')
    composition.bridge.get_bodies['/result'] = {'pending': True}

    answer = asyncio.run(composition.mcp.registered['result']())

    assert answer['no_result'] is True, answer
    assert 'wait=false' in answer['note'], answer
    assert composition.bridge.calls == [_mcp_tool_commands._get('/result')]


def test_ping_raises_the_bridge_error(_tmp):
    composition = _load_composition('ping-error')
    composition.bridge.poll_body = {'error': 'tab gone'}
    try:
        asyncio.run(composition.mcp.registered['ping']())
    except RuntimeError as raised:
        assert str(raised) == 'ping: tab gone', raised
    else:
        raise AssertionError('ping: no error was raised')
    assert [surface for surface, _details in composition.bridge.calls] == [
        'put', 'poll_result']


def test_segment_status_reports_the_gaps_between_segments(_tmp):
    composition = _load_composition('segment-gaps')
    composition.bridge.http_bodies['/segment-status'] = (
        200, {'done': [0, 1, 3]})

    answer = asyncio.run(composition.mcp.registered['segment_status'](
        job='job'))

    assert answer == {'done': [0, 1, 3], 'gaps': [2]}, answer


def test_segment_status_refuses_an_absent_job(_tmp):
    composition = _load_composition('segment-absent')
    composition.bridge.http_bodies['/segment-job'] = (404, {})
    try:
        asyncio.run(composition.mcp.registered['segment_status'](job='job'))
    except RuntimeError as raised:
        assert str(raised) == "segment_status: no job named 'job'", raised
    else:
        raise AssertionError('segment_status: an absent job was not refused')


def test_segment_status_refuses_a_job_owned_by_another_token(_tmp):
    composition = _load_composition('segment-foreign')
    composition.bridge.http_bodies['/segment-job'] = (409, {})
    try:
        asyncio.run(composition.mcp.registered['segment_status'](job='job'))
    except RuntimeError as raised:
        assert str(raised) == (
            "segment_status: job 'job' is owned by a different token"), raised
    else:
        raise AssertionError('segment_status: a foreign job was not refused')


def test_unblock_requests_refuses_a_nonpositive_rule_id(_tmp):
    composition = _load_composition('rule-zero')

    answer = asyncio.run(
        composition.mcp.registered['unblock_requests'](rule_id=0))

    assert answer == {'error': 'rule_id must be a positive integer'}, answer
    assert composition.bridge.calls == []


def test_delete_upload_refuses_a_filename_without_an_id(_tmp):
    composition = _load_composition('filename-only')

    answer = asyncio.run(
        composition.mcp.registered['delete_upload'](filename='shot.png'))

    assert answer == {'error': 'filename requires upload_id'}, answer
    assert composition.bridge.calls == []


def test_screenshot_fetches_by_id_when_the_extension_names_no_path(_tmp):
    composition = _load_composition('shot-id')
    composition.bridge.ext_bodies['screenshot'] = {}

    answer = asyncio.run(
        composition.mcp.registered['screenshot'](include_image=True))

    assert answer[0] == {'path': '', 'size': 0}, answer
    assert composition.bridge.calls[-1] == _mcp_tool_commands._get_raw(
        '/screenshot', id='_ss')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
