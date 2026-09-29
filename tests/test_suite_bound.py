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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_suite_fixture import (  # noqa: E402
    coverage_group, coverage_tree, kill_recorded, records, settle_gone)
from _repo import ROOT, iter_tree_files  # noqa: E402

SUITE_BOUND = _util.load(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                         'suite_bound_under_test')
_BOUND_SOURCE = ROOT / 'scripts' / 'ci' / 'suite_bound.py'

_WEDGED_SUITE = """import os, time
from pathlib import Path

root = Path(__file__).resolve().parent
(root / 'suite.pid').write_text(str(os.getpid()), encoding='ascii')
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""

_FAST_SUITE = "print('measured output arrived', flush=True)\n"

# A suite that answers SIGTERM. `pyproject.toml` sets `sigterm = true` so a
# terminated suite still flushes what it measured, and this is the suite
# that flush is for: a launcher that kills rather than asks gives it no
# chance, and the marker below is the only evidence either way.
_STOPPABLE_SUITE = """import os, signal, sys, time
from pathlib import Path

root = Path(__file__).resolve().parent


def _stopped(signum, frame):
    del signum, frame
    (root / 'stopped.pid').write_text(str(os.getpid()), encoding='ascii')
    print('suite was asked to stop and flushed', flush=True)
    sys.exit(0)


signal.signal(signal.SIGTERM, _stopped)
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""

# A suite that IGNORES SIGTERM, and a child of its own that does the same.
# Only the second phase of the tree kill reaches either of them, so this is
# the control that keeps the escalation from being decoration.
_STUBBORN_GRANDCHILD_SUITE = """import signal, subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parent
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen(
    [sys.executable, '-c',
     'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);'
     ' time.sleep(120)'],
    stdin=subprocess.DEVNULL)
(root / 'grandchild.pid').write_text(str(child.pid), encoding='ascii')
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""

# The bound this control shrinks, as a FRACTION of the one definition the
# launcher reads. A number typed here would be a second premise, and it
# would stop relating to the default the moment that default moved -- the
# drift this issue is about. The suite's own sleep is a multiple of it, so
# the bound is what ends the run on any machine: no assertion anywhere
# below reads a wall clock.
_WEDGE_BOUND_S = max(1, round(
    SUITE_BOUND.DEFAULT_SUITE_TIMEOUT_S * 0.002))
# This control's own ceiling, derived from the bound it observes so the two
# cannot drift into one another, and INDEPENDENT of it: the bound under
# test cannot also be what ends the control that proves it, or a launcher
# that never ends its wedged suite reproduces the hang and reports nothing.
# It also has to outlast the launcher's SIGTERM grace window, which is
# spent before the escalation can reach a suite that ignores the request.
_WEDGE_OUTER_S = _WEDGE_BOUND_S * 20 + SUITE_BOUND.CLEANUP_TIMEOUT_S * 2
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


def _assert_one_record(text, bound, name):
    """The one timeout record in `text`, whole, naming `name` at `bound`.

    Matched whole, because a prefix stops before the suite name and the
    launcher prints that name in the group header anyway -- so a prefix
    pin is satisfied by a sibling occurrence and says nothing about the
    record. The name has to be where the record puts it.
    """
    found = records(text)
    assert len(found) == 1, (
        f'expected exactly one whole timeout record naming {name}, found '
        f'{len(found)}; the text was: {text}')
    record = found[0].groupdict()
    assert record['name'] == name, record
    assert record['bound'] == str(float(bound)), record
    return record


def test_a_wedged_suite_is_named_and_fails_the_run(tmp):
    """A suite that never returns must end, be named, and turn the job red."""
    recorded = Path(tmp) / 'tree' / 'tests' / 'suite.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _WEDGED_SUITE,
                  'test_fast.py': _FAST_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
    finally:
        kill_recorded(recorded)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    group = coverage_group(result.stdout, 'test_wedged.py')
    _assert_one_record(group, _WEDGE_BOUND_S, 'tests/test_wedged.py')
    assert 'test_wedged.py' in result.stderr, result.stderr
    assert 'TIMED OUT' in result.stderr, result.stderr
    # The sibling that finished kept its own block and is named in no
    # record, which is what makes the refusal a diagnosis rather than a
    # blanket verdict on the run.
    sibling = coverage_group(result.stdout, 'test_fast.py')
    assert 'measured output arrived' in sibling, result.stdout
    assert not records(sibling), sibling


def test_a_fast_suite_is_measured_and_not_reported_as_timed_out(tmp):
    """A suite that finishes keeps its own output and its own verdict."""
    result, _invocations = coverage_tree(
        tmp, {'test_fast.py': _FAST_SUITE}, suite_bound=_WEDGE_BOUND_S,
        outer_timeout=_WEDGE_OUTER_S)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    group = coverage_group(result.stdout, 'test_fast.py')
    assert 'measured output arrived' in group, group
    assert 'SUITE TIMED OUT' not in result.stdout, result.stdout
    assert result.stderr == '', result.stderr


def test_a_wedged_suite_is_asked_to_stop_before_it_is_killed(tmp):
    """The kill asks first, so a suite that flushes on SIGTERM still does.

    `pyproject.toml` sets `sigterm = true` for exactly this: a terminated
    suite still writes what it measured. A launcher that SIGKILLs first
    takes that away, and nothing else in the tree would notice.
    """
    marker = Path(tmp) / 'tree' / 'tests' / 'stopped.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _STOPPABLE_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
    finally:
        kill_recorded(marker)
    group = coverage_group(result.stdout, 'test_wedged.py')
    record = _assert_one_record(group, _WEDGE_BOUND_S, 'tests/test_wedged.py')
    assert marker.exists(), (
        f'the suite was killed rather than asked to stop, so it flushed '
        f'nothing; the record says returncode {record["returncode"]}')
    assert 'suite was asked to stop and flushed' in group, group
    assert int(record['returncode']) == 0, record
    # The cleanup has to say the suite TOOK THE REQUEST. Asserting the
    # route alone -- `process group` -- is satisfied by the record a suite
    # that ignored the request and was killed also produces, so a grace
    # that asked the wrong subject, or a group that was never signalled at
    # all, would both pass it. The two records differ here and nowhere a
    # looser pin can see.
    assert 'asked to stop and the suite did' in record['cleanup'], record


def test_the_cleanup_that_ended_a_wedged_suite_is_reported(tmp):
    """A kill whose outcome is discarded is a kill nothing can be told from."""
    recorded = Path(tmp) / 'tree' / 'tests' / 'suite.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _WEDGED_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
    finally:
        kill_recorded(recorded)
    group = coverage_group(result.stdout, 'test_wedged.py')
    route = 'taskkill' if sys.platform == 'win32' else 'process group'
    assert route in group, (
        f'the record does not name the {route} route the kill took: {group}')


def test_a_timed_out_suites_own_child_does_not_survive_it(tmp):
    """The direct child is not the tree; the tree is what a wedge leaves."""
    if sys.platform == 'win32':
        _util.skip('the liveness probe is POSIX; see pid_alive')
    recorded = Path(tmp) / 'tree' / 'tests' / 'grandchild.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _STUBBORN_GRANDCHILD_SUITE},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S)
        assert result.returncode != 0, (result.returncode, result.stdout,
                                        result.stderr)
        pid = int(recorded.read_text(encoding='ascii'))
        group = coverage_group(result.stdout, 'test_wedged.py')
        record = _assert_one_record(
            group, _WEDGE_BOUND_S, 'tests/test_wedged.py')
        assert settle_gone(pid, _WEDGE_SETTLE_S), (
            f'pid {pid} outlived the bound the launcher enforced. It ignores '
            f'SIGTERM, so only the escalation reaches it, and it did not; '
            f'the record says: {group}')
        # The other direction of the same claim. This suite DID ignore the
        # request, so a record that says it took one is false in the same
        # way the other control's would be -- and the pair together is what
        # pins the clause, because either assertion alone survives a branch
        # that simply always takes one side.
        assert 'ignored the request' in record['cleanup'], record
    finally:
        kill_recorded(recorded)


def test_a_wedge_under_require_all_prints_the_timeout_and_nothing_else(tmp):
    """The timeout is the diagnosis, and it is the one this run gets.

    The `--require-all` refusals share this stderr with it, so which of
    them a wedge suppresses is a precedence the launcher chose and nothing
    else holds: without this control the order could be flipped back with
    the suite still green.
    """
    recorded = Path(tmp) / 'tree' / 'tests' / 'suite.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _WEDGED_SUITE,
                  'test_failing.py': 'raise SystemExit(1)\n'},
            suite_bound=_WEDGE_BOUND_S, outer_timeout=_WEDGE_OUTER_S,
            args=('--require-all',))
    finally:
        kill_recorded(recorded)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'TIMED OUT: tests/test_wedged.py' in result.stderr, result.stderr
    for shadowed in ('refusing partial coverage',
                     'refusing to\nreport a coverage number'):
        assert shadowed not in result.stderr, result.stderr


def test_the_coverage_launcher_binds_the_module_the_workflow_path_finds(tmp):
    """The import path `coverage-matrix` runs, which is not the one the
    other controls here exercise.

    A workflow step runs the launcher by path, so `sys.path[0]` is
    `scripts/ci` and the repository root is on no path at all: the
    package spelling cannot resolve and the flat one is the only spelling
    there is. Every other control here puts its synthetic tree on
    `PYTHONPATH` too, and that changes NOTHING about the spelling -- the
    launcher is run by path either way, so it takes the same branch.
    What `PYTHONPATH` does decide is whether the fixture's
    `sitecustomize` -- which carries the shrunk constant -- is found at
    all. This control turns it off, so the launcher's own import is what
    is under test rather than anything the fixture put there, and its
    passing IS the evidence that the branch CI runs works.
    """
    recorded = Path(tmp) / 'tree' / 'tests' / 'suite.pid'
    try:
        result, _invocations = coverage_tree(
            tmp, {'test_wedged.py': _WEDGED_SUITE},
            outer_timeout=_WEDGE_OUTER_S,
            timeout_env={'DAEDALUS_SUITE_TIMEOUT': str(_WEDGE_BOUND_S)},
            tree_on_path=False)
    finally:
        kill_recorded(recorded)
    group = coverage_group(result.stdout, 'test_wedged.py')
    _assert_one_record(group, _WEDGE_BOUND_S, 'tests/test_wedged.py')


def test_the_coverage_launcher_refuses_every_unusable_bound(tmp):
    for value, expected in UNUSABLE_BOUNDS:
        result, _unused = coverage_tree(
            tmp, {'test_fast.py': _FAST_SUITE},
            timeout_env={'DAEDALUS_SUITE_TIMEOUT': value})
        _refusal(result, value, expected)


def _refusal(result, value, expected):
    """What a launcher said about a bound it would not accept."""
    reported = result.stdout + result.stderr
    assert result.returncode != 0, (value, result.returncode, reported)
    assert 'DAEDALUS_SUITE_TIMEOUT' in reported, (value, reported)
    assert expected in reported, (value, reported)
    return reported


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
            # The child refuses the bound at startup and measures nothing,
            # so its coverage collector is scrubbed rather than kept.
            env=_util.child_coverage('scrub', dict(
                os.environ, DAEDALUS_SUITE_TIMEOUT=value,
                PYTHONDONTWRITEBYTECODE='1')))
        coverage, _unused = coverage_tree(
            tmp, {'test_fast.py': _FAST_SUITE},
            timeout_env={'DAEDALUS_SUITE_TIMEOUT': value})
        from_runner = _refusal(runner, value, expected)
        from_coverage = _refusal(coverage, value, expected)
        assert from_runner == from_coverage, (value, from_runner,
                                              from_coverage)


def test_every_wait_this_module_makes_is_bounded(_tmp):
    """A bounded cleanup path is the only one that can be trusted to end.

    Read from the source rather than from a run, and this is one of the two
    properties a source read is sound for. The bound is unobservable at
    runtime: a process the kill has already reached always reaps, so the
    wait returns whether or not it carried a bound, and no run can tell
    the two apart.

    Two shapes of unbounded wait are read, because the module uses two.
    A `.wait(`/`.communicate()` with no `timeout=` keyword, and a
    `time.sleep()` inside a `while` loop that never compares the clock
    again -- which is what a grace window spelled as a poll looks like
    with its deadline check deleted, and no member-call spelling would see
    it. Deleting either bound turns this red.
    """
    unbounded = []
    for function in ast.walk(ast.parse(
            _BOUND_SOURCE.read_text(encoding='utf-8'))):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        where = function.name
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if not (isinstance(callee, ast.Attribute)
                    and callee.attr in ('wait', 'communicate')):
                continue
            if not any(kw.arg == 'timeout' for kw in node.keywords):
                unbounded.append(f'{where}:{node.lineno} waits unbounded')
        for loop in _loops(function):
            if not _sleeps(loop) or _reads_the_clock(loop):
                continue
            unbounded.append(
                f'{where}:{loop.lineno} sleeps in a loop that never '
                f'compares the clock again')
    assert not unbounded, f'these waits carry no bound: {unbounded}'


def _loops(function):
    """Every loop in `function`, not the ones belonging to a nested def."""
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    for node in ast.walk(function):
        if not isinstance(node, (ast.For, ast.While)):
            continue
        inner = [one for one in ast.walk(node) if isinstance(one, nested)]
        if inner and inner[0] is not node:
            continue
        yield node


def _sleeps(node):
    """Whether `node` contains a sleep, at any depth."""
    return any(isinstance(inner, ast.Call)
               and isinstance(inner.func, ast.Attribute)
               and inner.func.attr == 'sleep'
               for inner in ast.walk(node))


def _reads_the_clock(node):
    """Whether `node` compares what the clock returns, in any expression."""
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Compare):
            continue
        operands = [inner.left, *inner.comparators]
        if any(isinstance(part, ast.Call)
               and isinstance(part.func, ast.Attribute)
               and part.func.attr == 'monotonic' for part in operands):
            return True
    return False


def test_the_per_suite_bound_is_defined_exactly_once_in_the_tree(_tmp):
    """A second copy of the number is a premise that can drift from the first.

    A source-level check, and this is the one property it is sound for: it
    reads the tree, not a run, so it can say a definition is duplicated. It
    says nothing about behaviour, and nothing here asks it to -- the two
    controls above ask that by running the launchers.

    The walk is every tracked Python file OUTSIDE `tests/`, not only the
    two launchers. A copy planted in `daedalus_bridge/`, `daedalus_cli/`
    or `daedalus_mcp/` is a second premise exactly as much as one in
    `scripts/`, and this branch's own argument is that the number is the
    product's one. `tests/` is excluded because these controls
    legitimately name the constant and the setting.
    """
    definitions, readers = [], []
    for path in iter_tree_files(ROOT):
        relative = path.relative_to(ROOT).as_posix()
        if not relative.endswith('.py') or relative.startswith('tests/'):
            continue
        source = path.read_text(encoding='utf-8', errors='surrogateescape')
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
