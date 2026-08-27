#!/usr/bin/env python3
"""Per-registration bridge binding for every returned MCP tool."""
import asyncio
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))
import mcp_tools_eval  # noqa: E402
import mcp_tools_tabs  # noqa: E402


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
    modules = (mcp_tools_tabs, mcp_tools_eval)
    for module in modules:
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
            arguments = _required_arguments(first_tool)
            first_reached = asyncio.run(_reached_bridge(
                first_tool, arguments, first_bridge, second_bridge))
            second_reached = asyncio.run(_reached_bridge(
                second_tools[name], arguments, first_bridge, second_bridge))
            assert first_reached == 'first', (
                f'{module.__name__}.{name}: first registered tool reached '
                f'{first_reached} bridge')
            assert second_reached == 'second', (
                f'{module.__name__}.{name}: second registered tool reached '
                f'{second_reached} bridge')
            covered.add(name)

        assert covered == set(first_tools)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
