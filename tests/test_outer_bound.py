#!/usr/bin/env python3
"""The bound a control holds on a call that can wedge, and its platform floor.

A control written for a hang detector cannot use the detector: the code under
test is exactly the machinery that would have ended the wait, so against a
launcher reverted to an unbounded one the control hangs and reports nothing.
The bound lives in `tests/_outer_bound.py` because the mechanism that ends
such a wait must itself sit OUTSIDE the call, run on every matrix leg, and be
obliged to kill the child rather than merely stop waiting for it.

The second half of this suite is the class that cost four legs: a POSIX-only
API reached unguarded from a test module. `signal.SIGALRM` and
`signal.setitimer` do not exist on Windows, so a control that arms one raises
`AttributeError` the moment it arms. The tripwire is a rule about SHAPE — a
POSIX-only call in a function that never asks the platform — rather than a
list of files, so a helper beside the two sites is covered the same way they
were.
"""
import ast
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launcher_stand_ins as stand_ins  # noqa: E402
import _util  # noqa: E402
from _outer_bound import (  # noqa: E402
    OuterBoundExpired, _announced_pid, outer_bound)

TESTS = Path(__file__).resolve().parent

# --- the two shapes a wedged call takes here ---------------------------------
#
# Both are miniature launchers: a real child in its own session that announces
# its pid and then never settles, waited on with NO bound of its own. That is
# the reversion the two controls this bound serves exist to catch, in a few
# lines, so the bound's own behaviour is observable without editing a launcher.
#
# The bound is one second because these controls expect it to FIRE, so the
# number is the subject of the assertion rather than a margin correct code has
# to clear: a bound that passed here would be a bound that did nothing.
WEDGE_BOUND_S = 1
# The reap in a `finally`, after the child is already dead: a kill followed by
# an unbounded wait is the shape `tests/test_drain_bounds.py` refuses, and
# bounding it is the remedy the drain rule prescribes rather than a margin
# anything here has to clear.
REAP_BOUND_S = 5
# A call that returns is held well above what a healthy child costs, so the
# bound is not what the healthy path is being asked to clear.
HEALTHY_BOUND_S = 60
# An announcement this far out is the slow-start shape the pid lookup is
# polled for. It is comfortably inside the grace the lookup allows and
# comfortably outside a single read's reach, which is the whole difference.
LATE_ANNOUNCE_S = 0.2


def _announcing_child(tmp, name, holds):
    """Launch a child that writes its own pid, then settles or never does.

    `start_new_session` is the launcher's own line, carried here because the
    bound kills the tree this child roots, and a process group is what makes
    that possible where a group exists at all.
    """
    pid_file = Path(tmp) / name
    source = ('import os, time\n'
              f'with open({str(pid_file)!r}, "w") as handle:\n'
              '    handle.write(str(os.getpid()))\n'
              f'{holds}\n')
    process = subprocess.Popen(
        [sys.executable, '-c', source], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != 'win32')
    return process, pid_file


def _awaited_pid(process, pid_file):
    """Wait for a child with no bound, then read what it announced.

    The wait has no bound of its own, exactly as the reverted launcher leaves
    it. Reaching the read at all is what proves the bound released the block:
    the only thing that could have ended this wait is the child ending.
    """
    process.wait()
    return pid_file.read_text()


def test_a_bound_that_does_not_pass_returns_what_the_call_returned(tmp):
    """The healthy path is untouched by a bound that never fires.

    A bound that changed what a correct call did would be a control on the
    call rather than a backstop against it, so the value is asserted as the
    call produced it.
    """
    process, pid_file = _announcing_child(tmp, 'healthy.pid', 'pass')
    with outer_bound(HEALTHY_BOUND_S, pid_file, 'the healthy child'):
        announced = _awaited_pid(process, pid_file)
    assert announced.strip() == str(process.pid), announced
    process.wait(timeout=REAP_BOUND_S)


def test_a_call_that_raised_keeps_its_own_exception_under_an_unpassed_bound(
        tmp):
    """The call's own failure is the one reported, not the bound's.

    A bound that replaced the exception of a call that failed correctly would
    turn a classified expiry into a generic one, and the classification is
    what both controls this bound serves are about. No child is launched:
    the bound never fires here, so it never reads the pid file, and the claim
    is about the exception rather than about the kill.
    """
    caught = None
    try:
        with outer_bound(HEALTHY_BOUND_S, Path(tmp) / 'never-read.pid',
                         'the raising child'):
            raise ValueError('the call classified its own failure')
    except ValueError as failure:
        caught = failure
    assert isinstance(caught, ValueError), caught
    assert not isinstance(caught, OuterBoundExpired), caught
    assert str(caught) == 'the call classified its own failure', caught


def test_a_passed_bound_names_the_wedge_and_kills_the_child(tmp):
    """The backstop ends the wait, names the defect, and kills the child.

    All three, or it is not a backstop: a bound that only raised left the
    child running, and a bound that only waited is what the suite ceiling
    already is. The kill is asserted against the process itself, not against
    the report the bound wrote about it.
    """
    process, pid_file = _announcing_child(
        tmp, 'wedged.pid', 'time.sleep(3600)')
    raised = None
    try:
        try:
            with outer_bound(WEDGE_BOUND_S, pid_file, 'the wedged child'):
                process.wait()
        except OuterBoundExpired as expired:
            raised = expired
        assert raised is not None, 'the bound passed and nothing was raised'
        report = str(raised)
        assert 'the wedged child' in report, report
        assert str(WEDGE_BOUND_S) in report, report
        assert str(process.pid) in report, report
        assert stand_ins.child_is_gone(pid_file.read_text()), (
            'the bound reported a kill and left the child running')
    finally:
        process.kill()
        process.wait(timeout=REAP_BOUND_S)


def test_a_child_that_announces_after_the_bound_is_still_found(tmp):
    """The pid is looked for again, not read once.

    This is the shape that hung: the bound fires, the child has not reached
    its first statement yet, and a single read finds nothing to kill — so the
    block waits for a child the bound had already given up on, which is the
    one outcome the bound exists to make impossible. Asserted against
    `_announced_pid` rather than through a wedged block, because a control
    that HANGS when the fix is absent is the failure this suite is here to
    stop repeating.
    """
    pid_file = Path(tmp) / 'late.pid'
    announced = threading.Thread(
        target=_announce_after, args=(pid_file, '43210'), daemon=True)
    announced.start()
    found = _announced_pid(pid_file)
    assert found == 43210, f'the late announcement was missed: {found!r}'


def _announce_after(pid_file, pid, delay=LATE_ANNOUNCE_S):
    time.sleep(delay)
    pid_file.write_text(pid, encoding='utf-8')


def test_a_passed_bound_with_no_announced_pid_still_names_the_wedge(tmp):
    """The child that announced nothing is still a named failure.

    The kill needs a pid, and a child that died before announcing one leaves
    the bound nothing to kill. That is a narrower backstop, not a reason to
    hang: the bound still fails and still names what it could not do, which is
    the whole difference between a red suite and a burnt CI budget.
    """
    del tmp
    raised = None
    try:
        with outer_bound(WEDGE_BOUND_S, Path(os.devnull) / 'never-written',
                         'the silent child'):
            time.sleep(WEDGE_BOUND_S * 10)
    except OuterBoundExpired as expired:
        raised = expired
    assert raised is not None, 'the bound passed and nothing was raised'
    report = str(raised)
    assert 'the silent child' in report, report
    assert 'no pid' in report, report


# --- the platform floor ------------------------------------------------------

# A POSIX-only API reached from a function that never asks the platform is
# the defect this suite's second half exists for, and it is the ATTRIBUTE that
# is the tell: `signal.signal` is portable and `signal.SIGALRM` is not, so a
# control that armed the portable call still raised `AttributeError` one
# expression later, on the argument. `os.getpid` and `os.pipe` are
# deliberately absent — both are portable, and a list carrying them would
# fire on correct code.
#
# Keyed by the module the name hangs off rather than spelled whole, because
# `test_noderun_deadline.py` holds a control that reads the tree for the
# markers of the one module allowed to end a child, and a tripwire that
# spelled a call whole would be the second module carrying that text.
POSIX_ONLY_MEMBERS = {
    'signal': frozenset({
        'alarm', 'setitimer', 'getitimer', 'siginterrupt', 'sigwait',
        'sigwaitinfo', 'pthread_kill', 'SIGALRM', 'ITIMER_REAL',
        'ITIMER_VIRTUAL', 'ITIMER_PROF', 'SIGVTALRM', 'SIGINFO', 'SIGCLD',
        'SIGWINCH', 'SIGPOLL', 'SIGIO', 'SIGPWR', 'SIGSYS', 'SIGUNUSED'}),
    'os': frozenset({
        'fork', 'forkpty', 'killpg', 'setsid', 'setpgid', 'getpgrp',
        'tcgetpgrp', 'tcsetpgrp', 'uname', 'getuid', 'geteuid', 'setuid',
        'setgid', 'chroot', 'mkfifo', 'getloadavg', 'sched_getaffinity',
        'sched_setaffinity', 'startfile', 'pread', 'pwrite'})}
# A function that reaches one of these may still own it, provided it says
# which platform it is on. `tests/_processtree.py`'s tree kill and
# `_dashnode`'s file lock branch on the platform and neither is refused.
#
# A capability probe counts as asking: `hasattr(signal, 'alarm')` is the same
# question asked portably, and `tests/test_cmdqueue_bounds.py`'s cyclic-binding
# control asks it before reaching for the alarm. That control is the
# distinguished form the corpus records rather than the condemned one — it
# does not run where the capability is absent, and a regression in the
# arithmetic it watches goes red wherever it does run, because the guarantee
# is a fixed-point over a finite name space and never varied by platform. What
# is refused is the reach with nothing said, which is the shape that put four
# matrix legs red.
PLATFORM_TESTS = ('sys.platform', 'os.name', 'platform.system',
                  'hasattr(', 'getattr(')


def _innermost_def(tree, node):
    """The innermost `def` of `tree` that `node` sits in, or None."""
    holders = [other for other in ast.walk(tree)
               if isinstance(other, (ast.FunctionDef, ast.AsyncFunctionDef))
               and other.lineno <= node.lineno
               <= (other.end_lineno or other.lineno)]
    return max(holders, key=lambda other: other.lineno) if holders else None


def _asks_the_platform(function):
    """Whether `function` tests which platform it is running on."""
    return any(
        isinstance(node, ast.If)
        and any(mark in ast.unparse(node.test) for mark in PLATFORM_TESTS)
        for node in ast.walk(function))


def _platform_decided(tree, function):
    """Whether something above `function` already asked which platform this is.

    The reach is a defect when NOTHING has said. A function may say it
    itself, which is the shape the tripwire was written for; but the
    question can also be asked one or two levels up, at the only place that
    can answer it — the caller that guards the call, or the function that
    hands the body to the platform rather than calling it. That is the shape
    `tests/test_plant_restore.py` arrived with when this branch rebased onto
    a base carrying it: the privilege drop is reached only where
    `hasattr(os, 'geteuid')` has already said the platform can do it, and
    a rule that cannot follow the chain manufactures a red on correct code,
    which is the same defect as one that passes on broken code.

    The walk stops the moment a function asks, and gives up rather than
    guesses at anything else: a reference nobody holds, a reach nobody
    guards, or a cycle with no question in it. So the defect the tripwire
    exists for is still named — the two suites that armed `SIGALRM` did it
    from a chain on which nothing asks.
    """
    seen = set()
    pending = [function]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            return False
        seen.add(id(current))
        if _asks_the_platform(current):
            continue
        holders = {
            id(holder): holder
            for holder in (
                _innermost_def(tree, node)
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id == current.name)
            if holder is not None}
        if not holders:
            return False
        pending.extend(holders.values())
    return True


def _posix_only_uses(tree):
    """Every POSIX-only attribute read the platform rule would refuse.

    A read and a call are the same thing to this: a name reached but never
    invoked still raised `AttributeError` on the leg that lacks it. Returns
    `(lineno, spelling)` per offending read, so a caller can report where.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        members = POSIX_ONLY_MEMBERS.get(node.value.id)
        if members is None or node.attr not in members:
            continue
        function = _innermost_def(tree, node)
        if function is not None and _platform_decided(tree, function):
            continue
        yield node.lineno, f'{node.value.id}.{node.attr}'


def test_the_platform_rule_names_an_unguarded_read_and_spares_a_guarded_one(
        tmp):
    """The tripwire fires, and the shape it refuses is the one that bit.

    A rule that is only ever run over the tree cannot say whether it reads:
    it passed clean on the very defect it was written for, because it asked
    each node for its enclosing function and a node cannot see the tree. So
    both directions are driven here from planted source — the unguarded read
    named, the guarded one spared, and a portable one spared whatever
    surrounds it.
    """
    del tmp
    unguarded = ast.parse(
        'import signal\n'
        'def arm():\n'
        '    signal.signal(signal.SIGALRM, print)\n')
    assert list(_posix_only_uses(unguarded)) == [(3, 'signal.SIGALRM')], (
        list(_posix_only_uses(unguarded)))
    guarded = ast.parse(
        'import os, sys\n'
        'def own_group():\n'
        "    if sys.platform == 'win32':\n"
        '        return None\n'
        '    return os.getpgrp()\n')
    assert list(_posix_only_uses(guarded)) == [], (
        list(_posix_only_uses(guarded)))
    portable = ast.parse(
        'import os\n'
        'def where():\n'
        '    return os.getpid()\n')
    assert list(_posix_only_uses(portable)) == [], (
        list(_posix_only_uses(portable)))
    # The question asked at the CALL SITE rather than inside the function
    # that reaches the API, which is the shape `tests/test_plant_restore.py`
    # arrived with after this branch rebased onto a base carrying it: the
    # privilege drop is only reached where `hasattr(os, 'geteuid')` has
    # already said the platform can do it.
    called_from_a_guard = ast.parse(
        'import os, sys, subprocess\n'
        'def drop():\n'
        '    os.setgid(65534)\n'
        'def run():\n'
        '    if hasattr(os, "geteuid"):\n'
        '        return subprocess.run(["x"], preexec_fn=drop)\n'
        '    return None\n')
    assert list(_posix_only_uses(called_from_a_guard)) == [], (
        list(_posix_only_uses(called_from_a_guard)))
    # And the same body called from a site that asks nothing is still named,
    # or the exemption would be a hole rather than a reading.
    unguarded_caller = ast.parse(
        'import os, subprocess\n'
        'def drop():\n'
        '    os.setgid(65534)\n'
        'def run():\n'
        '    return subprocess.run(["x"], preexec_fn=drop)\n')
    assert list(_posix_only_uses(unguarded_caller)) == [(3, 'os.setgid')], (
        list(_posix_only_uses(unguarded_caller)))


def test_no_posix_only_api_sits_in_a_function_that_never_asks_the_platform(
        tmp):
    """The tripwire this suite's own subject tripped, four legs ago.

    `tests/test_noderun_deadline.py` and `tests/test_gate_extensions.py` each
    armed `signal.signal(signal.SIGALRM, ...)` and `signal.setitimer(...)`
    from a function that never asked the platform, and neither the signal nor
    the timer exists on `windows-latest`: the control raised `AttributeError`
    the moment it armed, so four legs of a twelve-cell matrix were red before
    the matrix ran a line of the change that matrix exists to check.
    """
    del tmp
    offences = []
    for source in sorted(TESTS.glob('*.py')):
        tree = ast.parse(source.read_text(encoding='utf-8'),
                         filename=str(source))
        offences.extend(f'{source.name}:{line} {spelled}'
                        for line, spelled in _posix_only_uses(tree))
    assert offences == [], offences


# --- the stalling controls, and the bound each one owes --------------------
#
# A control that drives a real child which never settles has exactly one
# failure mode against a reversion: the wait runs until the runner's ceiling,
# and the suite's only report of it is that the job timed out. The bound above
# is what turns that into a named failure, so every such control is held by
# one — the announcement beside it, because a bound with no pid to kill only
# reports.
#
# The modules are named rather than globbed over every file under `tests/`
# because the marker's shape is not a definition. An empty timer holding an
# event loop open is also how a harness's OWN keepalive child is written, so
# a marker read across the whole tree would refuse children that are supposed
# to hold the loop open and settle. The scope is therefore the suites this
# branch added a stalling control to, and a child in another suite is that
# suite's own budget to hold.
STALLING_CONTROL_MODULES = (
    'test_noderun_deadline.py',
    'test_real_browser_control_extension.py',
    'test_real_browser_harness.py',
    'test_real_browser_environment.py',
)
# An empty timer with nothing to do is what "never settles" looks like in
# JavaScript, and a control's own source reaches that name no other way.
STALL_MARKER = 'setInterval'


def _calls_named(function, name):
    """Whether `function` makes a call to `name`."""
    return any(isinstance(node, ast.Call) and ast.unparse(node.func) == name
               for node in ast.walk(function))


def test_every_stalling_control_is_held_by_a_bound_and_announces_a_pid(tmp):
    """The class, not the one control: a wedge is caught, never waited out.

    Both channels this branch has for a reversion of a launch bound — the one
    that reads the bound out of the module and the one that waits for it —
    are in `tests/test_noderun_deadline.py`, and the control that waits for
    it hung. The bound that control was missing worked in isolation, so
    nothing was wrong with it; it was simply never reached, because an
    earlier control in the same suite waited on a child nothing in the suite
    could end. A census over the three suites the branch put a stalling
    control in is what keeps the next one from arriving the same way.
    """
    del tmp
    unwrapped = []
    for name in STALLING_CONTROL_MODULES:
        tree = ast.parse((TESTS / name).read_text(encoding='utf-8'))
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef):
                continue
            if STALL_MARKER not in ast.unparse(function):
                continue
            missing = [held for held in ('outer_bound', 'announcing_pid')
                       if not _calls_named(function, held)]
            if missing:
                unwrapped.append(
                    f'{name}:{function.lineno} {function.name} carries no '
                    f'{" and no ".join(missing)}, so a wedged child there is '
                    f'a suite timeout rather than a named failure')
    assert unwrapped == [], unwrapped


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='outerbound_')


if __name__ == '__main__':
    raise SystemExit(main())
