#!/usr/bin/env python3
"""Per-registration bridge binding for every returned MCP tool."""
import asyncio
import ast
import importlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))


class ToolRegistry:
    """Minimal MCP registry that preserves decorated coroutine functions."""

    def __init__(self):
        self.registered = {}

    def tool(self):
        def decorate(fn):
            self.registered[fn.__name__] = fn
            return fn

        return decorate


class BridgeProbe:
    """Record which per-registration bridge a tool actually calls."""

    def __init__(self, marker):
        self.marker = marker
        self.calls = []

    def checked_timeout(self, timeout):
        self.calls.append(('checked_timeout', timeout))

    async def get(self, path, **params):
        self.calls.append(('get', path, params))
        if path == '/tabs':
            return [{'title': self.marker}]
        return {'result': self.marker}

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
}


def _discover_tool_modules():
    paths = sorted(_util.ROOT.glob('mcp_tools_*.py'))
    return {
        path.stem: importlib.import_module(path.stem)
        for path in paths
    }


def _composed_tool_modules():
    source = (_util.ROOT / 'mcp_server.py').read_text(encoding='utf-8')
    tree = ast.parse(source, filename='mcp_server.py')
    composed = set()
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
            continue
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not isinstance(function, ast.Attribute):
                continue
            owner = function.value
            if (function.attr == 'register'
                    and isinstance(owner, ast.Name)
                    and owner.id.startswith('mcp_tools_')):
                composed.add(owner.id)
    return composed


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
    modules = _discover_tool_modules()
    discovered = set(modules)
    composed = _composed_tool_modules()
    assert discovered == composed, (
        'MCP tool module mismatch: '
        f'not composed={sorted(discovered - composed)}; '
        f'no module file={sorted(composed - discovered)}')

    returned_callables = set()
    exercised_callables = set()
    for module_name, module in modules.items():
        first_mcp = ToolRegistry()
        second_mcp = ToolRegistry()
        first_bridge = BridgeProbe('first')
        second_bridge = BridgeProbe('second')
        first_tools = module.register(first_mcp, first_bridge)
        second_tools = module.register(second_mcp, second_bridge)

        assert set(first_tools) == set(first_mcp.registered)
        assert set(second_tools) == set(second_mcp.registered)
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
