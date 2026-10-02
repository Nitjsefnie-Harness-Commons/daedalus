#!/usr/bin/env python3
"""A POSIX-only API reached from a function that never asks the platform.

`signal.SIGALRM` and `signal.setitimer` do not exist on `windows-latest`, so
a control that arms one raises `AttributeError` the moment it arms, and four
legs of a twelve-cell matrix are red before the matrix runs a line of the
change that matrix exists to check. The tripwire is a rule about SHAPE — a
POSIX-only call in a function that never asks the platform — rather than a
list of files, so a helper beside the sites is covered the same way they are.

It was the second half of `tests/test_outer_bound.py`, which is where the
branch put it while the bound was the only subject. The two are unrelated —
one is about a hang detector, the other about what any test module may reach
— and sharing a suite put the rule over the 700-line ceiling.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent

# A POSIX-only API reached from a function that never asks the platform is
# the defect this suite exists for, and it is the ATTRIBUTE that
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
# question asked portably, so a control that asks it before reaching for the
# alarm is the distinguished form rather than the condemned one — it does not
# run where the capability is absent, and a regression in the arithmetic it
# watches goes red wherever it does run, because the guarantee is a
# fixed-point over a finite name space and never varied by platform. What
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
        if _asks_which_platform(node.test) or _names_the_reached(
                node.test, reached):
            return True
    return False


def _asks_which_platform(test):
    """Whether this `if` reads the platform's identity off a real attribute.

    Matched on the node, not the printed form: `sys.platform` inside a
    string is a value, and `if 'sys.platform' in os.environ.get(...)` says
    nothing about which platform this is. All three marks are
    `attribute.on_name`, so that shape is what separates the question from
    its spelling.
    """
    return any(isinstance(node, ast.Attribute)
               and ast.unparse(node) in PLATFORM_IDENTITY
               for node in ast.walk(test))


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


# The keywords that hand a function to the platform rather than call it.
# `preexec_fn` is the one this tree uses: the child runs the body before
# `exec`, so the holder chain of a handed-off name is still a chain.
HANDOFF_KEYWORDS = ('preexec_fn', 'user', 'group', 'extra_groups', 'umask')


def _reach_sites(tree, name):
    """The references to `name` that actually EXECUTE it.

    A call target, or a hand-off keyword the platform invokes — the second
    is why this is not simply "is it called", because
    `tests/test_plant_restore.py`'s privilege drop is run as
    `preexec_fn=`. A parameter, a local, a rebinding and a bare `return
    name` spell the name without running it, and treating any of those as
    a holder lets an unreachable function borrow a guard from a sibling
    that never reaches it — the same hole as matching a question by its
    spelling, one level up.
    """
    reached = {id(node.func) for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name)
               and node.func.id == name}
    reached |= {id(word.value) for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                for word in node.keywords
                if word.arg in HANDOFF_KEYWORDS
                and isinstance(word.value, ast.Name)
                and word.value.id == name}
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and id(node) in reached]


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
            for holder in (_innermost_def(tree, node)
                           for node in _reach_sites(tree, current.name))
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
    unrelated = list(_posix_only_uses(unrelated_probe))
    assert unrelated == [(3, 'signal.SIGALRM')], unrelated
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
    # The platform's identity spelled inside a STRING is a value, not a
    # question. This is the shape the substring match over an unparsed `if`
    # could not tell apart from the real thing.
    identity_in_a_string = ast.parse(
        'import os, signal\n'
        'def arm():\n'
        '    signal.signal(signal.SIGALRM, print)\n'
        'def call():\n'
        '    if "sys.platform" in os.environ.get("NOTES", ""):\n'
        '        arm()\n')
    assert list(_posix_only_uses(identity_in_a_string)) == [
        (3, 'signal.SIGALRM')], (
        list(_posix_only_uses(identity_in_a_string)))
    # And a name that is only ever SPELLED beside the platform question is
    # not a caller: a parameter and a bare `return name` reach nothing.
    spelled_not_called = ast.parse(
        'import os, signal\n'
        'def arm():\n'
        '    signal.signal(signal.SIGALRM, print)\n'
        'def unrelated(arm, notes):\n'
        '    if sys.platform == "win32":\n'
        '        return notes\n'
        '    return arm\n')
    assert list(_posix_only_uses(spelled_not_called)) == [
        (3, 'signal.SIGALRM')], list(_posix_only_uses(spelled_not_called))
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
    """The tripwire this suite's subject tripped, four legs ago.

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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='platformapis_')


if __name__ == '__main__':
    raise SystemExit(main())
