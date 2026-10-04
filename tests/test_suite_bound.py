#!/usr/bin/env python3
"""The one per-suite bound both launchers read, and what it does on expiry.

`scripts/ci/suite_bound.py` is the subject here. Most of the controls
drive the real launchers over real planted suites rather than importing
its functions: a guard written against a fixture shows what the guard
thinks, and only a launcher that really ran says whether the bound held.

The teardown's arms are the exception: no launcher ever takes them, so they
are reached through stand-ins that record what the subject signalled or ran.
"""
import ast
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _processtree  # noqa: E402
import _util  # noqa: E402
from _coverage_suite_fixture import (  # noqa: E402
    FORCED_WITHOUT_GRACE,
    OPERATOR_ASKS_FIRST, OPERATOR_FORCES, REQUESTED_THEN_GRACED,
    TREE_WAS_KILLED, coverage_group, coverage_tree, kill_recorded,
    records, settle_gone)
from _processtree import taskkill_argv  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402
from _suite_bound_stubs import (  # noqa: E402
    GROUP, TINY_BOUND_S, Child, Clock, Escalation, Platform, Signals, Spawns,
    require_sigkill, swapped)

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

# A suite that answers its platform's request: SIGTERM on POSIX, where
# `pyproject.toml`'s `sigterm = true` saves what a terminated suite
# measured; CTRL_BREAK on Windows, where only a HANDLED request keeps
# anything, and where the pending SIGBREAK is read only when the main
# thread reaches the bytecode loop -- a blocking sleep would outlast the
# grace unread, so the wedge cycles on a short sleep instead. The marker
# below is the only evidence either way.
_STOPPABLE_SUITE = """import os, signal, sys, time
from pathlib import Path

root = Path(__file__).resolve().parent


def _stopped(signum, frame):
    del signum, frame
    (root / 'stopped.pid').write_text(str(os.getpid()), encoding='ascii')
    print('suite was asked to stop and flushed', flush=True)
    sys.exit(0)


signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), _stopped)
print('wedged suite reached its own body', flush=True)
while True:
    time.sleep(0.05)
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
     ' time.sleep(120)'], stdin=subprocess.DEVNULL)
(root / 'grandchild.pid').write_text(str(child.pid), encoding='ascii')
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""

# A suite that takes the request AND leaves a child of its own that does
# not. This is the one shape the survivor clause exists for, and no other
# control plants it: every other wedged suite is either compliant with
# nothing behind it, or non-compliant outright.
# The bound this control shrinks, as a FRACTION of the one definition the
# launcher reads. A number typed here would be a second premise, and it
# would stop relating to the default the moment that default moved -- the
# drift this issue is about. The suite's own sleep is a multiple of it, so
# the bound is what ends the run on any machine: no assertion anywhere
# below reads a wall clock.
_WEDGE_BOUND_S = max(1, round(SUITE_BOUND.DEFAULT_SUITE_TIMEOUT_S * 0.002))
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
    # The operator's sentence is the one sentence now, and the forced-only
    # wording is the one this run refuses. The record pin above reads the
    # cleanup clause, not this line, so a conditional that sent the wrong
    # sentence to stderr used to move no assertion at all.
    said, refused = OPERATOR_ASKS_FIRST, OPERATOR_FORCES
    assert (f'TIMED OUT: tests/test_wedged.py — each was ended at its '
            f'{float(_WEDGE_BOUND_S)} s bound; {said}'
            ) in result.stderr, result.stderr
    assert refused not in result.stderr, result.stderr
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


def test_a_wedged_suite_states_the_kill_its_platform_took(tmp):
    """Which contract the launcher gives on THIS platform, asserted as such.

    Both routes ask before they escalate, and the planted suite installs
    the handler each platform asks with, so the record has to say the
    request was made and taken. The forced-only clause is refused: no
    record this route can produce may say the tree died unasked again.
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
    assert TREE_WAS_KILLED in record['cleanup'], record
    assert marker.exists(), (
        f'the suite was killed rather than asked to stop, so it flushed '
        f'nothing; the record says returncode {record["returncode"]}')
    assert 'suite was asked to stop and flushed' in group, group
    assert int(record['returncode']) == 0, record
    # The cleanup has to say the suite TOOK THE REQUEST. Asserting the
    # route alone is satisfied by the record a suite that ignored the
    # request and was killed also produces, so a grace that asked the
    # wrong subject, or a group that was never signalled at all, would
    # both pass it. The two records differ here and nowhere a looser pin
    # can see.
    assert REQUESTED_THEN_GRACED in record['cleanup'], record
    assert FORCED_WITHOUT_GRACE not in record['cleanup'], record


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
    assert TREE_WAS_KILLED in group, (
        f'the record does not name the {TREE_WAS_KILLED} route the kill '
        f'took: {group}')
    assert REQUESTED_THEN_GRACED in group, (
        f'the record does not name what that route does about asking: '
        f'{group}')
    assert FORCED_WITHOUT_GRACE not in group, group


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
    properties a source read is sound for: the bound is unobservable at
    runtime, because a process the kill has already reached always reaps.

    Two shapes of unbounded wait are read, because the module uses two.
    A `.wait(`/`.communicate()` with no `timeout=` keyword, and a
    `time.sleep()` inside a `while` loop that never compares the clock
    again -- which is what a grace window spelled as a poll looks like with
    its deadline check deleted, and no member-call spelling would see it.
    Deleting either bound turns this red.
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


# The arms no launcher reaches. Most controls above drive a real launcher;
# these ask what the teardown does when the tree misbehaves, which no healthy
# CI run takes. Each asserts on what was SIGNALLED or RECORDED, never only on
# the string the subject returned: that string is its own account of the event,
# and rendering it without sending anything satisfies it just as well.


def test_a_process_group_that_cannot_be_looked_up_is_named_for_that(tmp):
    """Both refusals are outcomes; neither may leave the record empty."""
    del tmp
    for error, expected in (
            (ProcessLookupError(),
             'process group was already gone before cleanup'),
            (OSError('the group table is unreadable'),
             'process-group lookup failed: the group table is unreadable')):
        child = Child()
        signals = Signals(lookup_error=error)
        with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals):
            record = SUITE_BOUND.kill_process_tree(child)
        assert record == expected, (record, error)
        assert signals.sent == [], signals.sent
        assert child.killed == 0, 'nothing was signalled, so nothing was hit'


def test_a_child_in_the_launchers_own_group_is_killed_on_its_own(tmp):
    """Signalling that group would signal the launcher, so only the child."""
    del tmp
    for kill_error, expected in (
            (None, 'child shares the launcher group; '
                   'only the direct child was killed'),
            (OSError('the kill was refused'),
             'child shares the launcher group; '
             'direct kill failed: the kill was refused')):
        child = Child(kill_error=kill_error)
        signals = Signals(launcher_group=GROUP)
        with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals):
            record = SUITE_BOUND.kill_process_tree(child)
        assert child.killed == 1, 'the attempt is what the record is about'
        assert signals.sent == [], signals.sent
        assert record == expected, (record, kill_error)


def test_a_request_that_never_went_out_is_the_whole_record(tmp):
    """Nothing was signalled, so nothing after the request may be claimed."""
    del tmp
    for error, expected in (
            (ProcessLookupError(), f'process group {GROUP} was already gone'),
            (OSError('denied'), 'process-group SIGTERM failed: denied')):
        child = Child()
        signals = Signals(killpg_errors={signal.SIGTERM: error})
        with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals):
            record = SUITE_BOUND.kill_process_tree(child)
        assert signals.sent == [(GROUP, signal.SIGTERM)], signals.sent
        assert child.waits == [], 'the grace window was never entered'
        assert record == expected, (record, error)


def test_a_suite_that_ignored_the_request_reports_the_escalation_it_lost(tmp):
    """The grace ran out, and the escalation did not go out either."""
    del tmp
    require_sigkill()
    clock = Clock()
    signals = Signals(killpg_errors={signal.SIGKILL: ProcessLookupError()})
    with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals, time=clock):
        record = SUITE_BOUND.kill_process_tree(Child())
    assert [sig for _group, sig in signals.sent] == [
        signal.SIGTERM, signal.SIGKILL], signals.sent
    # Evidence the window was entered at all, which is the subject's own
    # loop and not elapsed time: `Clock` advances only when it sleeps.
    assert clock.slept >= SUITE_BOUND.CLEANUP_TIMEOUT_S, clock.slept
    assert record == (f'process group {GROUP} was already gone after '
                      f'{SUITE_BOUND.CLEANUP_TIMEOUT_S} s of grace'), record


def test_a_tree_kill_that_fails_outright_names_what_it_was_given(tmp):
    """A cleanup failure must not replace the expiry the caller reports."""
    del tmp
    boom = RuntimeError('the signal machinery is wedged')
    signals = Signals(killpg_errors={signal.SIGTERM: boom})
    with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals):
        record = SUITE_BOUND.kill_process_tree(Child())
    assert signals.sent == [(GROUP, signal.SIGTERM)], signals.sent
    assert record == f'process-tree kill raised {boom!r}', record


def test_the_windows_route_asks_then_insists_by_taskkill(tmp):
    """The dispatch and both commands it sends, not the platform's answer.

    This runs on every cell, so what it proves is that a Windows platform
    sends the request to the pid that leads the tree's group, spends the
    grace, and only then sends the tree kill's own argv with exactly this
    bound. Only a `windows-latest` cell proves that a real CTRL_BREAK and
    a real `taskkill` end a real tree.
    """
    del tmp
    child = Child()
    spawns = Spawns(child=child, returncode=0)
    clock = Clock()
    signals = Signals()
    with swapped(SUITE_BOUND, sys=Platform('win32'), os=signals,
                 signal=Escalation, time=clock, subprocess=spawns):
        record = SUITE_BOUND.kill_process_tree(child)
    assert signals.sent == [(GROUP, Escalation.CTRL_BREAK_EVENT)], (
        signals.sent)
    assert clock.slept >= SUITE_BOUND.CLEANUP_TIMEOUT_S, clock.slept
    assert len(spawns.runs) == 1, spawns.runs
    argv, keywords = spawns.runs[0]
    assert argv == taskkill_argv(child.pid), argv
    assert keywords['timeout'] == SUITE_BOUND.CLEANUP_TIMEOUT_S, keywords
    assert keywords['check'] is False, keywords
    assert child.killed == 0, 'the tree is ended by pid, not from here'
    assert 'ignored the request' in record, record
    assert 'taskkill /F' in record, record


def _launch(argv, tmp):
    """Every arm's launch, with the `env=` an unmapped tree's launch owes."""
    return SUITE_BOUND.launch_suite(
        argv, cwd=tmp, output_path=Path(tmp, 'suite.out'),
        env=_util.child_coverage('scrub'), timeout=TINY_BOUND_S)


def test_a_suite_that_overruns_is_stopped_then_reaped_and_both_are_named(tmp):
    """The bound expired: the tree is ended, then the child is collected."""
    require_sigkill()
    child = Child(wait_errors=[
        subprocess.TimeoutExpired('suite', TINY_BOUND_S)])
    spawns = Spawns(child=child)
    clock = Clock()
    signals = Signals()
    argv = [sys.executable, 'suite.py']
    with swapped(SUITE_BOUND, sys=Platform('linux'), subprocess=spawns,
                 os=signals, time=clock):
        returncode, cleanup = _launch(argv, tmp)
    assert spawns.spawns[0][0] == argv, spawns.spawns
    assert spawns.spawns[0][1]['start_new_session'] is True, spawns.spawns
    assert [sig for _group, sig in signals.sent] == [
        signal.SIGTERM, signal.SIGKILL], signals.sent
    assert clock.slept >= SUITE_BOUND.CLEANUP_TIMEOUT_S, clock.slept
    assert child.waits == [TINY_BOUND_S, SUITE_BOUND.CLEANUP_TIMEOUT_S], (
        child.waits)
    assert 'ignored the request' in cleanup, cleanup
    assert cleanup.endswith('; process reaped'), cleanup
    assert returncode == child.returncode, (returncode, child.returncode)


def test_a_reap_that_did_not_happen_is_named_by_why(tmp):
    """The bounded reap is bounded, and each of its refusals is recorded."""
    for second, expected in (
            (subprocess.TimeoutExpired('suite', 10),
             '; bounded reap timed out'),
            (OSError('the child could not be collected'),
             '; bounded reap failed: the child could not be collected')):
        child = Child(wait_errors=[
            subprocess.TimeoutExpired('suite', TINY_BOUND_S), second])
        with swapped(SUITE_BOUND, sys=Platform('linux'),
                     subprocess=Spawns(child=child), os=Signals(),
                     time=Clock()):
            returncode, cleanup = _launch(
                [sys.executable, 'suite.py'], tmp)
        assert child.waits == [TINY_BOUND_S, SUITE_BOUND.CLEANUP_TIMEOUT_S]
        assert cleanup.endswith(expected), cleanup
        assert '; process reaped' not in cleanup, cleanup
        assert returncode is None, returncode


def test_an_interrupt_around_the_launch_still_ends_and_reaps_the_child(tmp):
    """The spawn is inside the guard, so the teardown has to run anyway."""
    require_sigkill()
    child = Child(running=True, stops_after=1, wait_errors=[
        KeyboardInterrupt, OSError('the child could not be collected')])
    spawns = Spawns(child=child)
    clock = Clock()
    signals = Signals()
    interrupted = None
    with swapped(SUITE_BOUND, sys=Platform('linux'), subprocess=spawns,
                 os=signals, time=clock):
        try:
            _launch([sys.executable, 'suite.py'], tmp)
        except KeyboardInterrupt:
            interrupted = 're-raised'
    assert interrupted == 're-raised', 'the interrupt was swallowed'
    assert signals.sent == [(GROUP, signal.SIGTERM),
                            (GROUP, signal.SIGKILL)], signals.sent
    assert child.waits == [TINY_BOUND_S, SUITE_BOUND.CLEANUP_TIMEOUT_S], (
        child.waits)


def _record_clauses(path):
    """Whitespace-collapsed text of every non-docstring string literal.

    f-string placeholders drop out, so the mirrors' clause templates
    compare as plain text. Docstrings are skipped: a stale docstring
    must not mask a reworded code clause.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    parts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = ''.join(str(value.value) for value in node.values
                           if isinstance(value, ast.Constant))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        else:
            continue
        if id(node) not in docs and text.strip():
            parts.append(' '.join(text.split()))
    return ' || '.join(parts)


def test_the_two_windows_records_stay_clause_for_clause_mirrors(tmp):
    """The child-kill record mirrors suite_bound's, clause for clause.

    suite_bound's header declares `tests/_processtree.py` "the same
    shape"; prose binds nobody. Both sources are read and every clause
    below must appear in BOTH pools. The stopped clause is the one
    mandated fork -- each module names its own subject -- pinned as a
    pair.
    """
    del tmp
    suite_pool = _record_clauses(_BOUND_SOURCE)
    tree_pool = _record_clauses(Path(_processtree.__file__).resolve())
    shared = (
        'CTRL_BREAK_EVENT failed:',
        'process tree was already gone',
        'the escalation reached what was still in it',
        'ignored the request, and after s of grace:',
        'ignored the request and was killed by taskkill /F after s of grace',
        'taskkill /F gave up after s, so the tree may still be running',
        'taskkill /F could not run:',
        'the escalation found the tree already gone (taskkill exited )',
        'taskkill /F exited , so the tree may still be running',
    )
    for clause in shared:
        assert clause in suite_pool, (clause, 'suite_bound')
        assert clause in tree_pool, (clause, '_processtree')
    assert ('asked to stop and the suite did' in suite_pool
            and 'asked to stop and the tree did' in tree_pool), (
                'the stopped clause names its own subject on each side')


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(globals()))))
