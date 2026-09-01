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
    'http_transport.py',
    'log_safe.py',
    'mcp_bootstrap.py',
    'parent_watch.py',
    'path_safety.py',
    'result_routes.py',
    'result_store.py',
    'route_answer.py',
    'segment_jobs.py',
    'segment_routes.py',
    'segment_store.py',
    'static_routes.py',
    'stream_route.py',
    'stream_service.py',
    'tab_registry.py',
    'upload_routes.py',
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


def _tracked_python(root=ROOT):
    root = Path(root)
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-sz', '*.py'],
        capture_output=True, check=True, timeout=30)
    entries = [entry for entry in listed.stdout.decode(
        'utf-8', 'surrogateescape').split('\0') if entry]
    assert entries, 'Git returned no tracked Python files'
    paths = [entry.split('\t', 1)[1] for entry in entries]
    # A symlink checks out as an ordinary file wherever core.symlinks is
    # off, so the recorded mode is the only reliable witness.
    recorded = sorted(
        entry.split('\t', 1)[1] for entry in entries
        if not entry.split(' ', 1)[0].startswith('100'))
    assert not recorded, (
        f'tracked Python paths not recorded as regular files: {recorded}')
    missing = sorted(
        path for path in paths
        if (root / path).is_symlink() or not (root / path).is_file())
    assert not missing, (
        f'tracked Python paths missing or not regular files: {missing}')
    return paths


def test_the_inventory_refuses_a_missing_tracked_python_file(tmp):
    """A tracked package module must also exist in the worktree."""
    tree = Path(tmp) / 'tree'
    subprocess.run(
        ['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(tree)],
        check=True, timeout=30)
    missing = tree / 'daedalus_bridge' / 'config.py'
    missing.unlink()
    try:
        _tracked_python(tree)
    except AssertionError as exc:
        assert 'daedalus_bridge/config.py' in str(exc), str(exc)
    else:
        raise AssertionError(
            'the layout inventory accepted a missing tracked Python file')


def test_the_inventory_refuses_a_tracked_symlink_blob(tmp):
    """A module recorded as a symlink is refused however it checks out."""
    tree = Path(tmp) / 'tree'
    subprocess.run(
        ['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(tree)],
        check=True, timeout=30)
    subprocess.run(
        ['git', '-C', str(tree), 'config', 'core.symlinks', 'false'],
        check=True, timeout=30)
    target = tree / 'symlink-target'
    target.write_text('auth.py')
    blob = subprocess.run(
        ['git', '-C', str(tree), 'hash-object', '-w', str(target)],
        capture_output=True, check=True, text=True, timeout=30).stdout.strip()
    target.unlink()
    module = 'daedalus_mcp/server.py'
    subprocess.run(
        ['git', '-C', str(tree), 'update-index', '--add', '--cacheinfo',
         f'120000,{blob},{module}'], check=True, timeout=30)
    (tree / module).unlink()
    subprocess.run(
        ['git', '-C', str(tree), 'checkout-index', '-f', '--', module],
        check=True, timeout=30)
    try:
        _tracked_python(tree)
    except AssertionError as exc:
        assert module in str(exc), str(exc)
    else:
        raise AssertionError(
            'the layout inventory accepted a tracked symlink blob')


def test_the_inventory_refuses_a_symlinked_tracked_python_file(tmp):
    """A tracked package module must be a regular worktree file."""
    tree = Path(tmp) / 'tree'
    subprocess.run(
        ['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(tree)],
        check=True, timeout=30)
    symlink = tree / 'daedalus_bridge' / 'config.py'
    symlink.unlink()
    symlink.symlink_to('__init__.py')
    try:
        _tracked_python(tree)
    except AssertionError as exc:
        assert 'daedalus_bridge/config.py' in str(exc), str(exc)
    else:
        raise AssertionError(
            'the layout inventory accepted a symlinked Python file')


def test_the_bridge_modules_live_in_the_bridge_package(tmp):
    """The package holds exactly its thirteen modules; the root holds none."""
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
