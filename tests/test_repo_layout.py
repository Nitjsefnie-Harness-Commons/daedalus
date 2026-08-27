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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
