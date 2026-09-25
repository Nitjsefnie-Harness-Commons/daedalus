#!/usr/bin/env python3
"""A launcher a decorator binds, run for real beside the guard's verdict.

The guard refuses the module statically. Running the same module is what
shows the refusal is worth making: the child really does land in the
directory the module chdir'd into, with the collector still in its
environment, and nothing in the module declares either.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_guard import _coverage_environment_violations  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402

_DECORATED_CHILD = (
    'import os, sys\n'
    'with open(sys.argv[1], "w") as handle:\n'
    '    handle.write(os.getcwd())\n'
    '    handle.write("\\n")\n'
    '    handle.write(str("COVERAGE_PROCESS_START" in os.environ))\n')
_DECORATED_MARKER = '@_bind(subprocess.run)'
_DECORATED_PROBE = f'''\
import os
import subprocess
import sys


def _bind(launcher):
    def decorate(function):
        return launcher
    return decorate


{_DECORATED_MARKER}
def _decorated(argv):
    return argv


os.chdir(sys.argv[2])
_decorated([sys.executable, '-c', {_DECORATED_CHILD!r}, sys.argv[1]])
'''


def test_a_decorated_launch_really_inherits_the_moved_cwd(tmp):
    """The guard refuses it; the child lands where the module stands."""
    root = Path(tmp) / 'tree'
    copy_test_tree(root)
    probe = root / 'tests' / 'test_decorated_launch_probe.py'
    moved = root / 'tests'
    report = Path(tmp) / 'child.txt'
    before = _coverage_environment_violations(root)
    probe.write_text(_DECORATED_PROBE, encoding='utf-8')
    planted = _coverage_environment_violations(root)
    line = _DECORATED_PROBE[:_DECORATED_PROBE.index(
        _DECORATED_MARKER)].count('\n') + 1
    relative = 'tests/test_decorated_launch_probe.py'
    assert (f'{relative}:{line}: a launcher is bound through a form the '
            'guard cannot follow') in planted, planted
    assert not any(v.startswith(f'{relative}:') for v in before), before
    env = dict(os.environ)
    env['COVERAGE_PROCESS_START'] = 'planted'
    result = subprocess.run(
        [sys.executable, str(probe), str(report), str(moved)],
        cwd=str(root), env=_util.child_coverage('keep', env, cwd=root),
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert report.read_text() == f'{moved}\nTrue'


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
