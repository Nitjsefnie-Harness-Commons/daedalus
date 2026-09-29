#!/usr/bin/env python3
"""The one per-suite bound both launchers read, and what it does on expiry.

`scripts/ci/suite_bound.py` is the subject here. The controls drive the
real launchers over real planted suites rather than importing its
functions: a guard written against a fixture shows what the guard thinks,
and only a launcher that really ran says whether the bound held.
"""
import ast
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_suite_fixture import coverage_tree  # noqa: E402
from _repo import ROOT  # noqa: E402

SUITE_BOUND = _util.load(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                         'suite_bound_under_test')

_WEDGED_SUITE = """import os, time
from pathlib import Path

root = Path(__file__).resolve().parent
(root / 'suite.pid').write_text(str(os.getpid()), encoding='ascii')
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""

_FAST_SUITE = "print('measured output arrived', flush=True)\n"

_GRANDCHILD_SUITE = """import subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parent
child = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(120)'],
    stdin=subprocess.DEVNULL)
(root / 'grandchild.pid').write_text(str(child.pid), encoding='ascii')
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""

# The bound this control shrinks, as a FRACTION of the one definition the
# launcher resolves when it launches. A number typed here would be a second
# premise, and it would stop relating to the default the moment that
# default moved -- the drift this issue is about. The suite's own sleep is
# a multiple of it, so the bound is what ends the run on any machine: no
# assertion anywhere below reads a wall clock.
_WEDGE_BOUND_S = max(1, round(
    SUITE_BOUND.DEFAULT_SUITE_TIMEOUT_S * 0.002))
# This control's own ceiling, derived from the bound it observes so the two
# cannot drift into one another, and INDEPENDENT of it: the bound under
# test cannot also be what ends the control that proves it, or a launcher
# that never ends its wedged suite reproduces the hang and reports nothing.
_WEDGE_OUTER_S = _WEDGE_BOUND_S * 20
# Reaping a killed tree is itself bounded, by a deadline read off the
# clock rather than asserted as a margin.
_WEDGE_SETTLE_S = 10

# The reader's whole accept/reject surface. `float(raw)` plus a `<= 0`
# test admits `inf`, and a bound of infinity never expires, so the setting
# meant to bound each suite reinstates the unbounded wait. A reader that
# MOVED is a reader that can arrive half converted, so both launchers are
# asked the same question and must answer it the same way.
UNUSABLE_BOUNDS = (
    ('inf', 'finite positive'),
    ('-inf', 'finite positive'),
    ('INF', 'finite positive'),
    ('1e400', 'finite positive'),
    ('nan', 'finite positive'),
    ('0', 'finite positive'),
    ('-1', 'finite positive'),
    ('soon', 'not a number'),
    ('', 'not a number'),
)


def _coverage_group(stdout, name):
    """The block the coverage launcher printed for `name`."""
    start = stdout.index(f'::group::tests/{name}\n')
    end = stdout.index('::endgroup::\n', start)
    return stdout[start:end]


def _record_of(bound):
    return f'SUITE TIMED OUT after {float(bound)} s (returncode '


def _kill_recorded(path):
    """SIGKILL a pid a planted suite wrote down, so a red control leaks none.

    The outer bound kills the launcher, not the suite the launcher started:
    each suite is launched into a session of its own precisely so the kill
    can reach its tree, which is also what stops the fixture's own timeout
    from reaching it.
    """
    try:
        pid = int(path.read_text(encoding='ascii'))
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, 9)
    except OSError:
        return


def _pid_alive(pid):
    """Whether this host can still signal `pid`.

    Signal 0 is the POSIX probe and has no meaning on Windows, where
    `os.kill(pid, 0)` is a request to terminate with exit code 0 -- a
    probe that killed what it measured would report a survivor gone.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _settle_gone(pid, seconds):
    """Wait out a kill's own settling, on a deadline, not a margin."""
    deadline = time.monotonic() + seconds
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _pid_alive(pid)


def _refusal(result, value, expected):
    """What a launcher said about a bound it would not accept."""
    reported = result.stdout + result.stderr
    assert result.returncode != 0, (value, result.returncode, reported)
    assert 'DAEDALUS_SUITE_TIMEOUT' in reported, (value, reported)
    assert expected in reported, (value, reported)
    return reported


def test_a_wedged_suite_is_named_and_fails_the_run(tmp):
    """A suite that never returns must end, be named, and turn the job red."""
    recorded = Path(tmp) / 'tree' / 'tests' / 'suite.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _WEDGED_SUITE,
                  'test_fast.py': _FAST_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
    finally:
        _kill_recorded(recorded)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    group = _coverage_group(result.stdout, 'test_wedged.py')
    assert _record_of(_WEDGE_BOUND_S) in group, group
    assert 'test_wedged.py' in group, group
    assert 'test_wedged.py' in result.stderr, result.stderr
    assert 'TIMED OUT' in result.stderr, result.stderr
    # The sibling that finished is still measured, and is not swept up in
    # the refusal: partial coverage is a different report from none.
    assert 'measured output arrived' in _coverage_group(
        result.stdout, 'test_fast.py'), result.stdout


def test_a_fast_suite_is_measured_and_not_reported_as_timed_out(tmp):
    """A suite that finishes keeps its own output and its own verdict."""
    result, _invocations = coverage_tree(
        tmp, {'test_fast.py': _FAST_SUITE}, suite_bound=_WEDGE_BOUND_S,
        outer_timeout=_WEDGE_OUTER_S)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    group = _coverage_group(result.stdout, 'test_fast.py')
    assert 'measured output arrived' in group, group
    assert 'SUITE TIMED OUT' not in result.stdout, result.stdout
    assert result.stderr == '', result.stderr


def test_the_cleanup_that_ended_a_wedged_suite_is_reported(tmp):
    """A kill whose outcome is discarded is a kill nothing can be told from."""
    recorded = Path(tmp) / 'tree' / 'tests' / 'suite.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _WEDGED_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
    finally:
        _kill_recorded(recorded)
    group = _coverage_group(result.stdout, 'test_wedged.py')
    route = 'taskkill' if sys.platform == 'win32' else 'process group'
    assert route in group, (
        f'the record does not name the {route} route the kill took: {group}')


def test_a_timed_out_suites_own_child_does_not_survive_it(tmp):
    """The direct child is not the tree; the tree is what a wedge leaves."""
    if sys.platform == 'win32':
        _util.skip('the liveness probe is POSIX; see _pid_alive')
    recorded = Path(tmp) / 'tree' / 'tests' / 'grandchild.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _GRANDCHILD_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
        assert result.returncode != 0, (result.returncode, result.stdout,
                                        result.stderr)
        pid = int(recorded.read_text(encoding='ascii'))
        group = _coverage_group(result.stdout, 'test_wedged.py')
        assert _settle_gone(pid, _WEDGE_SETTLE_S), (
            f'pid {pid} outlived the bound the launcher enforced, so the '
            f'direct child was killed and its own tree left running; the '
            f'record says: {group}')
    finally:
        _kill_recorded(recorded)


def test_the_coverage_launcher_refuses_every_unusable_bound(tmp):
    for value, expected in UNUSABLE_BOUNDS:
        result, _unused = coverage_tree(
            tmp, {'test_fast.py': _FAST_SUITE},
            timeout_env={'DAEDALUS_SUITE_TIMEOUT': value})
        _refusal(result, value, expected)


def test_the_two_launchers_refuse_a_bound_identically(tmp):
    """One reader, one message: a launcher's own copy is a second answer."""
    sandbox = Path(tmp) / 'runner'
    (sandbox / 'tests').mkdir(parents=True)
    (sandbox / 'scripts' / 'ci').mkdir(parents=True)
    (sandbox / 'tests' / 'test_fast.py').write_text(
        _FAST_SUITE, encoding='utf-8')
    for relative in ('run_tests.py', 'scripts/ci/suite_bound.py'):
        (sandbox / relative).write_bytes((ROOT / relative).read_bytes())
    for value, expected in UNUSABLE_BOUNDS:
        runner = subprocess.run(
            [sys.executable, 'run_tests.py'], cwd=str(sandbox),
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, DAEDALUS_SUITE_TIMEOUT=value,
                     PYTHONDONTWRITEBYTECODE='1'))
        coverage, _unused = coverage_tree(
            tmp, {'test_fast.py': _FAST_SUITE},
            timeout_env={'DAEDALUS_SUITE_TIMEOUT': value})
        from_runner = _refusal(runner, value, expected)
        from_coverage = _refusal(coverage, value, expected)
        assert from_runner == from_coverage, (value, from_runner,
                                              from_coverage)


def test_the_per_suite_bound_is_defined_exactly_once_in_the_tree(_tmp):
    """A second copy of the number is a premise that can drift from the first.

    A source-level check, and this is the one property it is sound for: it
    reads the tree, not a run, so it can say a definition is duplicated. It
    says nothing about behaviour, and nothing here asks it to -- the two
    controls above ask that by running the launchers.
    """
    definitions, readers = [], []
    tracked = sorted((ROOT / 'scripts').rglob('*.py'))
    for path in tracked + [ROOT / 'run_tests.py']:
        source = path.read_text(encoding='utf-8')
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = ([node.target] if isinstance(node, ast.AnnAssign)
                       else node.targets)
            if not any(getattr(target, 'id', '') == 'DEFAULT_SUITE_TIMEOUT_S'
                       for target in targets):
                continue
            assert isinstance(node, ast.Assign), (
                f'{relative}:{node.lineno} binds the bound in a form this '
                f'control does not read, so it may not be the only one')
            definitions.append(relative)
        if 'DAEDALUS_SUITE_TIMEOUT' in source:
            readers.append(relative)
    assert definitions == ['scripts/ci/suite_bound.py'], definitions
    assert readers == ['scripts/ci/suite_bound.py'], readers


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(globals()))))
