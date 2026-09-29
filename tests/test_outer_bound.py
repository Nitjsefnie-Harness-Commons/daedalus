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
import _util  # noqa: E402
from _outer_bound import (  # noqa: E402
    OuterBoundExpired, _announced_pid, outer_bound)
from _processtree import (  # noqa: E402
    kill_process_tree, process_is_gone)

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
# The receipt's two settles. A live process is read once, because a live
# process is live however long one waits; a killed one is polled to a
# bound, because a kill is not the death it causes and a just-killed
# grandchild stays in the table until something reaps it.
LIVE_SETTLE_S = 0
GONE_SETTLE_S = 10
# The kill's own bound, the same figure `tests/test_outer_bound.py` hands
# every other teardown here.
KILL_SETTLE_S = 5
# An announcement this far out is the slow-start shape the pid lookup is
# polled for. It is comfortably inside the grace the lookup allows and
# comfortably outside a single read's reach, which is the whole difference.
LATE_ANNOUNCE_S = 0.2


def _announcing_child(tmp, name, holds, grandchild=False):
    """Launch a child that writes its own pid, then settles or never does.

    `start_new_session` is the launcher's own line, carried here because the
    bound kills the tree this child roots, and a process group is what makes
    that possible where a group exists at all.

    `grandchild` adds the second pid the tree control needs: a grandchild the
    child launches, which announces its OWN pid. The bound kills a TREE, and a
    tree's failure is only visible on something the direct child's death
    already explains — so the direct child is the wrong thing to observe and
    the grandchild is the right one. The grandchild's pid is written FIRST, so
    the file exists before the child's own pid lets the bound fire and take
    the group out from under the write.
    """
    pid_file = Path(tmp) / name
    tree_file = Path(tmp) / f'{name}.tree'
    source = 'import os, time\n'
    if grandchild:
        source += (
            'import subprocess, sys\n'
            "inner = subprocess.Popen([sys.executable, '-c',\n"
            '                        "import time; time.sleep(3600)"])\n'
            f'with open({str(tree_file)!r}, "w") as handle:\n'
            '    handle.write(str(inner.pid))\n')
    source += (f'with open({str(pid_file)!r}, "w") as handle:\n'
               '    handle.write(str(os.getpid()))\n'
               f'{holds}\n')
    process = subprocess.Popen(
        [sys.executable, '-c', source], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != 'win32')
    return process, pid_file, tree_file


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
    process, pid_file, _ = _announcing_child(
        tmp, 'healthy.pid', 'pass')
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


def test_a_passed_bound_names_the_wedge_and_kills_the_tree(tmp):
    """The backstop ends the wait, names the defect, and kills the TREE.

    All three, or it is not a backstop: a bound that only raised left the
    child running, and a bound that only waited is what the suite ceiling
    already is.

    What it is asserted against is a GRANDCHILD, and that is the whole
    difficulty. The bound kills a process group, and the direct child's death
    is exactly what a direct-child-only kill produces — so watching the direct
    child cannot tell a tree kill from a process kill, and an orphan
    left behind is invisible to it. The grandchild survives a kill of the
    child alone, so it is the observation that discriminates. Neither half
    is a string assertion: the report says what the kill did, and the
    receipt says whether it did.
    """
    process, pid_file, tree_file = _announcing_child(
        tmp, 'wedged.pid', 'time.sleep(3600)', grandchild=True)
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
        assert process_is_gone(int(pid_file.read_text())), (
            'the bound reported a kill and left the child it launched running')
        assert process_is_gone(int(tree_file.read_text())), (
            'the bound killed the child it launched and left the rest of the '
            'tree running')
    finally:
        process.kill()
        process.wait(timeout=REAP_BOUND_S)


def test_the_receipt_tells_a_live_process_from_a_dead_one(tmp):
    """The receipt can fail, in both directions, or it is not a receipt.

    A reading hard-wired to `True` reports a kill that never happened and one
    hard-wired to `False` refuses a kill that did, and the tree control above
    leans on this reading for both of its halves. So the reading has a
    control of its own, in the only direction that settles it: a process
    left running is not gone, and the same process killed is.

    The kill here is the module's own, so the control cannot pass by reading
    a string the kill wrote about itself.

    The reap is inside the sequence and is not incidental. A process this
    process launched stays in the table until this process reaps it, so
    "gone" is a reading taken after the reap — which is exactly what a
    grandchild's death looks like from outside, where the grandchild is
    reparented and reaped by something other than the reader.
    """
    del tmp
    process = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(3600)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=sys.platform != 'win32')
    try:
        assert not process_is_gone(process.pid, LIVE_SETTLE_S), (
            'the receipt called a running process gone')
        kill_process_tree(process.pid, KILL_SETTLE_S)
        process.wait(timeout=REAP_BOUND_S)
        assert process_is_gone(process.pid, GONE_SETTLE_S), (
            'the receipt called a killed process alive')
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
# A platform IDENTITY question — which platform is this — is about every
# POSIX-only member at once, so it answers any of them. These three are
# spelled the same way on every platform and are the only such question.
#
# A CAPABILITY question is different, and used to be matched by the same
# substring test as the identity ones: any `hasattr(` or `getattr(` anywhere
# in an `if` spared any reach below it. `hasattr(os, 'geteuid')` says whether
# the process may drop privileges; it says nothing whatever about a signal,
# and a rule that cannot tell the two apart spares an unguarded
# `signal.SIGALRM` under a probe about `os`. A capability question therefore
# counts only when it is about the module the reach used — which is the
# granularity `tests/test_plant_restore.py` needs and which the measured
# blind case does not.
PLATFORM_IDENTITY = ('sys.platform', 'os.name', 'platform.system')


def _innermost_def(tree, node):
    """The innermost `def` of `tree` that `node` sits in, or None."""
    holders = [other for other in ast.walk(tree)
               if isinstance(other, (ast.FunctionDef, ast.AsyncFunctionDef))
               and other.lineno <= node.lineno
               <= (other.end_lineno or other.lineno)]
    return max(holders, key=lambda other: other.lineno) if holders else None


def _asks_the_platform(function, reached):
    """Whether `function` asks the question this reach makes relevant.

    `reached` is the `(module, member)` the read went through, and the
    test is walked rather than unparsed and searched, so a mention inside a
    message string is not a question. What counts as the right question is
    `PLATFORM_IDENTITY`'s comment, which carries the measurement.
    """
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        spelled = ast.unparse(node.test)
        if any(mark in spelled for mark in PLATFORM_IDENTITY):
            return True
        if _names_the_reached(node.test, reached):
            return True
    return False


def _names_the_reached(test, reached):
    """Whether `test` asks about the module the read went through.

    Module-level on purpose, and the limit is stated rather than papered
    over: a probe of one `os` member spares a reach on another, because
    `tests/test_plant_restore.py` guards `os.setuid` with
    `hasattr(os, 'geteuid')` and that is the shape a caller reaching a
    sibling member really writes. What the module match buys is that a
    probe about `os` cannot spare a reach on `signal`, which is the hole
    this replaces.
    """
    module, _ = reached
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and node.id == module:
            return True
        if isinstance(node, ast.Attribute) and isinstance(
                node.value, ast.Name) and node.value.id == module:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ('hasattr', 'getattr') and any(
                    isinstance(argument, ast.Constant)
                    and argument.value == module
                    for argument in node.args[:1]):
            return True
    return False


def _platform_decided(tree, function, reached):
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
        if _asks_the_platform(current, reached):
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
        if function is not None and _platform_decided(
                tree, function, (node.value.id, node.attr)):
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
    # An UNRELATED capability probe above the reach, which is the shape the
    # substring matcher over an unparsed `if` could not tell from a
    # relevant one: `hasattr(os, 'geteuid')` says whether the process may
    # drop privileges, and says nothing whatever about a signal. The reach
    # is still named.
    unrelated_probe = ast.parse(
        'import os, signal\n'
        'def arm():\n'
        '    signal.signal(signal.SIGALRM, print)\n'
        'def call():\n'
        '    if hasattr(os, "geteuid"):\n'
        '        arm()\n')
    assert list(_posix_only_uses(unrelated_probe)) == [(3, 'signal.SIGALRM')], (
        list(_posix_only_uses(unrelated_probe)))
    # And the same question, asked about the module the reach used, spares
    # it: that is `tests/test_plant_restore.py`'s own shape, and the file is
    # real and green in the suite.
    related_probe = ast.parse(
        'import os, signal\n'
        'def arm():\n'
        '    signal.signal(signal.SIGALRM, print)\n'
        'def call():\n'
        '    if hasattr(signal, "SIGALRM"):\n'
        '        arm()\n')
    assert list(_posix_only_uses(related_probe)) == [], (
        list(_posix_only_uses(related_probe)))
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
    the moment it armed, so the four `windows-latest` cells of the twelve in
    `scripts/ci/classify_changes.py`'s `FULL_MATRIX` were red before the
    matrix ran a line of the change that matrix exists to check.
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
# branch put a stalling control in, and a child in another suite is that
# suite's own budget to hold.
STALLING_CONTROL_MODULES = (
    'test_gate_extensions.py',
    'test_noderun_deadline.py',
    'test_real_browser_control_extension.py',
    'test_real_browser_harness.py',
    'test_real_browser_environment.py',
)
# What a child that never settles looks like, spelled EVERY way this tree
# spells it. A sweep keyed on one spelling's surface tokens is a sweep over
# one spelling, and a `setTimeout(() => {}, 900)` wedge was outside the
# population entirely.
#
# Both are CALLS, and the parenthesis is the whole narrowing. A harness that
# installs its own fake timer writes `global.setTimeout = …`, and that is the
# keepalive hazard the scope note below is about: the assignment is not a
# child holding an event loop open, it is the code that makes sure no real
# timer ever arms. An empty `while (true) {}` is left out for the other
# half of the same reason — in this tree it is how a stand-in's child is
# described, and a stand-in raises at once rather than waiting.
STALL_SOURCES = ('setInterval(', 'setTimeout(')
# How far the reach follows a stall source that is not in the control's own
# text. Bounded because an unbounded walk over a cyclic tree is a hang, and
# this census exists to stop hangs.
STALL_REACH_DEPTH = 8


def _module_level(tree):
    """The module's own constants and functions, by the names they bind."""
    constants, functions = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = ast.unparse(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
    return constants, functions


def _stalls_a_wedge(function, module, depth=0, seen=None):
    """Whether this control can drive a child that never settles.

    Three routes, because a control that hides its stall source is the same
    defect as one that inlines it: the marker's own text, a module-level
    CONSTANT it reads, and a module-level function it CALLS whose body
    carries one. Only the first was followed before, and a source held in a
    constant passed the census while being unwrapped — a blind spot in the
    exact class the census exists for.

    The walk gives up rather than guesses: past the depth bound, or into a
    cycle, there is no answer and the caller treats it as no.
    """
    if any(mark in ast.unparse(function) for mark in STALL_SOURCES):
        return True
    if depth > STALL_REACH_DEPTH:
        return False
    if seen is None:
        seen = set()
    if id(function) in seen:
        return False
    seen.add(id(function))
    constants, functions = module
    for node in ast.walk(function):
        if not isinstance(node, ast.Name):
            continue
        if any(mark in constants.get(node.id, '')
               for mark in STALL_SOURCES):
            return True
        called = functions.get(node.id)
        if called is not None and _stalls_a_wedge(
                called, module, depth + 1, seen):
            return True
    return False


def _stalling_controls(tree):
    """Every control in `tree` that can drive a child which never settles.

    A `test_` function, and only that: a helper that BUILDS a stall source
    launches nothing, so requiring a bound of it would be a refusal of
    correct code, while the control that calls it is the one the bound
    belongs to. The whole tree is walked rather than `tree.body`, so a
    control is in the population wherever it is written.
    """
    module = _module_level(tree)
    return [function for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and function.name.startswith('test_')
            and _stalls_a_wedge(function, module)]


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
    could end.

    This census is what keeps the next one from arriving the same way, and it
    names its modules rather than counting them, so widening the scope is a
    visible edit rather than a sentence that quietly stops being true.
    """
    del tmp
    unwrapped = []
    for name in STALLING_CONTROL_MODULES:
        tree = ast.parse((TESTS / name).read_text(encoding='utf-8'))
        for function in _stalling_controls(tree):
            missing = [held for held in ('outer_bound', 'announcing_pid')
                       if not _calls_named(function, held)]
            if missing:
                unwrapped.append(
                    f'{name}:{function.lineno} {function.name} carries no '
                    f'{" and no ".join(missing)}, so a wedged child there is '
                    f'a suite timeout rather than a named failure')
    assert unwrapped == [], unwrapped


def test_the_census_finds_a_stall_it_cannot_see_in_the_controls_own_text(tmp):
    """The census's population, in the three shapes that were outside it.

    A census is only as good as what it cannot miss, and three arrivals of
    the same defect were all invisible: a source spelled `setTimeout` rather
    than `setInterval`, a source held in a module-level constant, and a
    source built by a helper the control calls. Each is planted here as an
    unwrapped control, and each must be named — the first two by the
    widened marker and the constant route, the third by the call route.
    """
    del tmp
    shapes = (
        ('a different spelling', 'test_a_timeout_wedge',
         'import subprocess\n'
         'def test_a_timeout_wedge(tmp):\n'
         '    run("setTimeout(() => {}, 900)", tmp)\n'),
        ('a module constant', 'test_a_constant_wedge',
         'import subprocess\n'
         'SOURCE = "setInterval(() => {}, 1000)"\n'
         'def test_a_constant_wedge(tmp):\n'
         '    run(SOURCE, tmp)\n'),
        ('a helper that builds it', 'test_a_helper_wedge',
         'import subprocess\n'
         'def _source():\n'
         '    return "setInterval(() => {}, 1000)"\n'
         'def test_a_helper_wedge(tmp):\n'
         '    run(_source(), tmp)\n'),
    )
    for label, expected, source in shapes:
        found = [function.name
                 for function in _stalling_controls(ast.parse(source))]
        assert found == [expected], (label, found)
    # And a control that neither names a stall nor reaches one is NOT in the
    # population, or the census would refuse every control in the tree.
    assert _stalling_controls(ast.parse(
        'def test_a_healthy_child(tmp):\n'
        '    run("console.log(1)", tmp)\n')) == []


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='outerbound_')


if __name__ == '__main__':
    raise SystemExit(main())
