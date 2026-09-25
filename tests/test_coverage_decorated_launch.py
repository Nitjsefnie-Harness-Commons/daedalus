#!/usr/bin/env python3
"""Launchers a `cwd=`-less call carries, run for real beside the verdict.

The guard refuses each module statically. Running the same module is what
shows the refusal is worth making: the child really does land in the
directory the module chdir'd into, with the collector still in its
environment, and nothing in the module declares either.

Both probes are here because the argument rule is the one under review.
A decorator binds the launcher; a wrapper handed one invokes it. Neither
needs a callee this guard recognises, which is why the rule judges the
value it carries rather than the name it is called.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_guard import _coverage_environment_violations  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402

_CHILD = (
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
_decorated([sys.executable, '-c', {_CHILD!r}, sys.argv[1]])
'''

_WRAPPED_MARKER = 'partial(subprocess.run)(['
_WRAPPED_PROBE = f'''\
import os
import subprocess
import sys
from functools import partial


os.chdir(sys.argv[2])
{_WRAPPED_MARKER}sys.executable, '-c', {_CHILD!r}, sys.argv[1]])
'''

_BINDING_MESSAGE = 'a launcher is bound through a form the guard cannot follow'


def _planted(root, tmp, name, source, marker):
    """Write a probe into a tree this control owns, and read the verdict."""
    probe = root / 'tests' / name
    report = Path(tmp) / 'child.txt'
    before = _coverage_environment_violations(root)
    probe.write_text(source, encoding='utf-8')
    planted = _coverage_environment_violations(root)
    line = source[:source.index(marker)].count('\n') + 1
    relative = f'tests/{name}'
    assert f'{relative}:{line}: {_BINDING_MESSAGE}' in planted, planted
    assert not any(v.startswith(f'{relative}:') for v in before), before
    env = dict(os.environ)
    env['COVERAGE_PROCESS_START'] = 'planted'
    result = subprocess.run(
        [sys.executable, str(probe), str(report), str(root / 'tests')],
        cwd=str(root), env=_util.child_coverage('keep', env, cwd=root),
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert report.read_text() == f'{root / "tests"}\nTrue'


def test_a_decorated_launch_really_inherits_the_moved_cwd(tmp):
    """The guard refuses it; the child lands where the module stands."""
    root = Path(tmp) / 'tree'
    copy_test_tree(root)
    _planted(root, tmp, 'test_decorated_launch_probe.py', _DECORATED_PROBE,
             _DECORATED_MARKER)


def test_a_wrapped_launch_really_inherits_the_moved_cwd(tmp):
    """A wrapper handed the launcher invokes it; the callee need not be one.

    `partial(subprocess.run)(argv)` is a call whose callee is another
    call, so no name here reads as a launcher to a guard that judges
    callees by name. The launcher is in the inner call's argument, which
    is the position this rule judges.
    """
    root = Path(tmp) / 'tree'
    copy_test_tree(root)
    _planted(root, tmp, 'test_wrapped_launch_probe.py', _WRAPPED_PROBE,
             _WRAPPED_MARKER)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
