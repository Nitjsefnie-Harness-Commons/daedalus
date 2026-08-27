#!/usr/bin/env python3
"""The repository's module layout, pinned so it cannot silently drift back.

Generic module names at the repository root occupy the top-level import
namespace of every process started there. The bridge's modules live in the
`daedalus_bridge/` package instead; this suite is what keeps them there.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT

BRIDGE_PACKAGE = (
    '__init__.py',
    'atomic_file.py',
    'command_queue.py',
    'config.py',
    'delivery_stripes.py',
    'env_config.py',
    'log_safe.py',
    'path_safety.py',
    'result_store.py',
    'segment_store.py',
    'stream_service.py',
)

MCP_PACKAGE = (
    '__init__.py',
    'auth.py',
    'request_guard.py',
    'server.py',
    'tools_cookies.py',
    'tools_css.py',
    'tools_eval.py',
    'tools_hotfixes.py',
    'tools_media.py',
    'tools_network.py',
    'tools_tabs.py',
    'transport.py',
)

MCP_OLD_NAMES = (
    'mcp_auth.py',
    'mcp_request_guard.py',
    'mcp_server.py',
    'mcp_tools_cookies.py',
    'mcp_tools_css.py',
    'mcp_tools_eval.py',
    'mcp_tools_hotfixes.py',
    'mcp_tools_media.py',
    'mcp_tools_network.py',
    'mcp_tools_tabs.py',
    'mcp_transport.py',
)


def _tracked_python():
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '*.py'],
        capture_output=True, check=True, timeout=30)
    paths = [path for path in listed.stdout.decode(
        'utf-8', 'surrogateescape').split('\0') if path]
    assert paths, 'Git returned no tracked Python files'
    return paths


def test_the_bridge_modules_live_in_the_bridge_package(tmp):
    """The package holds exactly its eleven modules, and the root holds none."""
    del tmp
    tracked = _tracked_python()
    packaged = sorted(
        path.split('/', 1)[1] for path in tracked
        if path.startswith('daedalus_bridge/') and '/' not in path.split('/', 1)[1])
    assert packaged == sorted(BRIDGE_PACKAGE), (
        f'daedalus_bridge/ holds {packaged}, expected {sorted(BRIDGE_PACKAGE)}')
    stray = sorted(
        path for path in tracked if '/' not in path
        and path in set(BRIDGE_PACKAGE) | {'bridge_config.py'})
    assert not stray, (
        f'these bridge modules are still tracked at the repository root: {stray}')


def test_the_mcp_modules_live_in_the_mcp_package(tmp):
    """The package holds exactly its twelve modules, and none of the eleven
    old `mcp_*.py` names is tracked anywhere."""
    del tmp
    tracked = _tracked_python()
    packaged = sorted(
        path.split('/', 1)[1] for path in tracked
        if path.startswith('daedalus_mcp/') and '/' not in path.split('/', 1)[1])
    assert packaged == sorted(MCP_PACKAGE), (
        f'daedalus_mcp/ holds {packaged}, expected {sorted(MCP_PACKAGE)}')
    stray = sorted(
        path for path in tracked
        if path.rsplit('/', 1)[-1] in set(MCP_OLD_NAMES))
    assert not stray, (
        f'these moved MCP modules are still tracked under their old names: {stray}')


def test_the_root_holds_no_python_module_but_the_entry_points(tmp):
    """Only the two process entry points stay at the repository root."""
    del tmp
    tracked = _tracked_python()
    root_modules = sorted(path for path in tracked if '/' not in path)
    assert root_modules == ['run_tests.py', 'server.py'], (
        f'the repository root holds {root_modules}, '
        "expected ['run_tests.py', 'server.py']")


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
