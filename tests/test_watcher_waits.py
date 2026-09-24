#!/usr/bin/env python3
"""The waits the watcher-budget suite measures its children through.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once: as
the failure-reporting backstop on the single wait with no live process to
give up on, which renders the surviving pids, the parent's exit and the
captured output at expiry instead of a number of seconds. The shape pin at
the end is what says the health-margin spelling has not come back into the
suite these serve.
"""
import ast
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

BUDGET_SUITE = Path(__file__).resolve().parent / 'test_watcher_budget.py'
# A wall-clock reader by any of its names. The class the shape pin reads is
# the arithmetic that follows one, not the module it came from.
WALL_READERS = frozenset({'monotonic', 'monotonic_ns', 'perf_counter',
                          'time', 'time_ns'})
# The names a wait's own bound travels under. A number arriving under one of
# these is a bound whatever the value is; a number arriving under any other
# name is caught anyway by the arithmetic rule, which reads no names.
BOUND_NAMES = frozenset({'backoff', 'bound', 'budget', 'deadline', 'grace',
                         'limit', 'seconds', 'timeout', 'wait'})
# A failure word beside a duration: a message whose only evidence is how long
# the runner took is the shape issue 1039 removed, whatever spells it.
FAILURE = re.compile(r'timed out|timeout|hung|deadline|wait exceeded',
                     re.IGNORECASE)
DURATION = re.compile(r'(\{[^}]*\}|\d+)\s*(ms|s|sec|secs|seconds?|minutes?)\b')
# The one function in the budget suite a bound is allowed to reach, and why.
# An assertion that a recorded instant a child logged is at or after a reset
# the fixture derived from a reading is not on the list: no reading this
# process took is compared with a number there, which is the shape below.
EXEMPT_FUNCTIONS = {
    '_ci_wait': (
        'the bounds are the subprocess timeout the script under test '
        'declares and takes, and neither reaches a wall-clock reading here'),
}
# The backstop on the one wait that cannot end on a state. 90s is the
# figure tests/test_parent_watch.py already waits a real grandchild's death
# with, and the suites job allows twenty minutes for the whole file, so a
# survivor is named long before the job's own limit ends the run nameless.
BACKSTOP = 90
# A wake-up interval, not a deadline: the waits below end on what the
# process under test did, and this only says how often a wait re-reads the
# record it is synchronised on.
POLL = 0.05
# A double whose probe never terminates turns an assertion mutation into a
# job timeout, so every double here ends by name instead.
RUNAWAY_CALL_LIMIT = 1000
# What a control's condition double waits for, so a wait that ignored the
# state it was handed ends in seconds rather than at the job's timeout.
DOUBLE_WAIT = 0.01


class Stream:
    """One child stream, drained, with its end carried as a state.

    The pump publishes under the condition's lock, so a waiter holding that
    lock while it looks cannot miss a line published between the look and
    the wait.
    """

    def __init__(self, changed=None):
        self.lines = []
        self.changed = (threading.Condition() if changed is None
                        else changed)
        self.ended = False

    def publish(self, line):
        with self.changed:
            self.lines.append(line)
            self.changed.notify_all()

    def pump(self, pipe):
        for line in pipe:
            self.publish(line.rstrip('\n'))
        with self.changed:
            self.ended = True
            self.changed.notify_all()


def await_lines(stream, match, count, what):
    """The first `count` lines of `stream` that match.

    The wait is synchronised on the pump: it ends when the process under
    test published the line, not when the runner next looked for one. The
    early exit is a state rather than a duration - a stream at its end can
    never print the line again - and the failure carries everything the
    process did print, which is what says which line arrived instead. A
    process that stays up and stays silent leaves this wait nothing to end
    it, and the hung job naming this wait is the trade the repository takes
    deliberately (the `_await_alive` in tests/test_stream_lifecycle.py).
    """
    while True:
        with stream.changed:
            found = [line for line in stream.lines if match(line)]
            if len(found) >= count:
                return found
            if stream.ended:
                raise AssertionError(
                    f'{what}: the stream ended first:\n'
                    + '\n'.join(stream.lines))
            stream.changed.wait()


def await_calls(fake, count, child, what):
    """The call log, once it holds `count` entries.

    The log is a file the watcher children append to, so there is no signal
    to wait on and this polls the record: what it waits for is the record
    itself, and how long the runner took to write it is not the claim. A
    child that has exited can make no further call, which is the state that
    ends the wait early, with the child's own output in the failure.
    """
    while True:
        calls = fake.calls()
        if len(calls) >= count:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
        time.sleep(POLL)


def await_gone(pids, child, what, alive, backstop=BACKSTOP):
    """Wait for those processes to be gone, reporting live state at expiry.

    A wait for a child to DIE is the one wait with no live process to give
    up on: the parent is reaped, the pids are what is being waited for, and
    the only thing that can end the wait is a real survivor - which is the
    defect itself. The bound is therefore a failure report and not a health
    margin: it never sets the passing wall, and at expiry it names the pids
    still alive, the parent's exit and everything the parent printed.
    """
    started = time.monotonic()
    while any(alive(pid) for pid in pids):
        if time.monotonic() - started >= backstop:
            survivors = [pid for pid in pids if alive(pid)]
            raise AssertionError(
                f'{what}: pids still alive {survivors}, parent exit '
                f'{child.proc.returncode}:\n{child.captured()}')
        time.sleep(POLL)


class _ScriptedCondition(threading.Condition):
    """A condition that says when a wait began and ends a runaway one.

    A helper that ignored the state it was handed would wait here forever,
    so the double names the run instead of letting a job timeout do it. A
    real condition's `wait` returns as soon as a publisher notifies, and
    this one returns on a short slice as well, which is what lets a mutated
    helper exhaust the count above in seconds.
    """

    def __init__(self):
        super().__init__()
        self.waited = threading.Event()
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits > RUNAWAY_CALL_LIMIT:
            raise AssertionError('condition double exceeded call limit')
        self.waited.set()
        return super().wait(DOUBLE_WAIT)


class _GrowingLog:
    """A call log that hands over its entries only after the first read."""

    def __init__(self, entries):
        self._entries = entries
        self.reads = 0

    def calls(self):
        self.reads += 1
        if self.reads > RUNAWAY_CALL_LIMIT:
            raise AssertionError('log double exceeded call limit')
        return self._entries if self.reads > 1 else []


class _DeadProc:
    """The `proc` a finished child leaves behind: an exit code, no more."""

    def __init__(self, code):
        self.returncode = code


class _ScriptedChild:
    """A child stand-in for the waits' liveness escape and their reports."""

    def __init__(self, alive=False, output='poll failed (1)', code=1):
        self._alive = alive
        self._output = output
        self.proc = _DeadProc(code)

    def alive(self):
        return self._alive

    def captured(self):
        return self._output


def test_the_line_wait_ends_on_a_line_published_after_it_began_waiting(tmp):
    """The wait is synchronised on the pump, not on a lucky first read: the
    line lands only once the waiter is inside the condition, and the wait
    still hands it over.
    """
    changed = _ScriptedCondition()
    stream = Stream(changed)

    def publish():
        assert changed.waited.wait(BACKSTOP), 'the wait never began'
        stream.publish('watcher pid 42')

    threading.Thread(target=publish, daemon=True).start()
    found = await_lines(stream, lambda line: 'watcher pid' in line, 1,
                        'both children to announce their pid')
    assert found == ['watcher pid 42'], found


def test_the_line_wait_gives_up_by_name_when_the_stream_ends(tmp):
    del tmp
    stream = Stream(_ScriptedCondition())
    stream.publish('watcher pid 41')
    with stream.changed:
        stream.ended = True
    message = None
    try:
        await_lines(stream, lambda line: 'watcher pid' in line, 2,
                    'both children to announce their pid')
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError('a line that never arrived did not fail')
    assert message is not None
    assert 'both children to announce their pid' in message, message
    assert 'the stream ended first' in message, message
    assert 'watcher pid 41' in message, message


def test_the_call_wait_ends_on_a_record_gained_after_the_first_read(tmp):
    del tmp
    log = _GrowingLog([{'request': 'query one'}, {'request': 'query two'}])
    child = _ScriptedChild(alive=True)
    calls = await_calls(log, 2, child, '2 gh call(s)')
    assert len(calls) == 2, calls


def test_the_call_wait_gives_up_by_name_when_the_child_exits(tmp):
    del tmp
    log = _GrowingLog([])
    child = _ScriptedChild(output='gh: no fixture carries the query')
    message = None
    try:
        await_calls(log, 2, child, '2 gh call(s)')
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError('calls that never arrived did not fail')
    assert message is not None
    assert '2 gh call(s)' in message, message
    assert 'gh: no fixture carries the query' in message, message


def test_the_death_wait_ends_when_the_pids_are_gone(tmp):
    del tmp
    states = iter((True, True, False, False))
    calls = 0

    def alive(_pid):
        nonlocal calls
        calls += 1
        if calls > RUNAWAY_CALL_LIMIT:
            raise AssertionError('pid double exceeded call limit')
        return next(states, False)

    child = _ScriptedChild()
    assert await_gone([7, 8], child, 'children to die', alive) is None


def test_the_death_wait_names_the_survivors_when_the_backstop_passes(tmp):
    """A survivor past the backstop is named, and the double that reports it
    ends by name too: a wait that never reached its backstop would otherwise
    take this control to the job's timeout instead of failing.
    """
    del tmp
    calls = 0

    def alive(_pid):
        nonlocal calls
        calls += 1
        if calls > RUNAWAY_CALL_LIMIT:
            raise AssertionError('pid double exceeded call limit')
        return True

    child = _ScriptedChild(output='started ci watcher pid 9', code=-9)
    message = None
    try:
        await_gone([7, 8], child, 'children to die with the parent',
                   alive, backstop=0)
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError('a surviving child did not fail')
    assert message is not None
    assert 'children to die with the parent' in message, message
    assert 'pids still alive [7, 8]' in message, message
    assert 'parent exit -9' in message, message
    assert 'started ci watcher pid 9' in message, message


def _wall_clock_margins(source):
    """Every wall-clock margin the budget suite's waits carry, as a list.

    The class is the arithmetic, not the spelling: a wait's bound is a
    number that reaches a wall-clock reading, whether it arrives as a
    parameter, as a constant a parameter defaults to, as a literal beside a
    reading in a comparison, or as a local such a comparison reads. All
    four are read here, plus a failure message whose only evidence is a
    duration. Two things it does not claim: a value a child process logged
    is not a reading this process took, so an assertion comparing one with a
    fixture's reset is not this shape; and a bound handed in from another
    module, or a margin in any file other than the one it reads, is out of
    reach here - which is what issue 1038 enumerates for the rest of the
    tree.
    """
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    numbers, walls, deadlines = _tables(tree)
    exempt = EXEMPT_FUNCTIONS
    found = []

    def report(node, what):
        owner = _owner(node, parents)
        if owner not in exempt:
            found.append((owner, what))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for argument, default in _defaults(node):
                if (argument.arg in BOUND_NAMES
                        and _number(default, numbers) is not None):
                    report(node, f'parameter {argument.arg} defaults to a '
                                 f'number')
        elif isinstance(node, ast.Compare):
            for first, second in zip([node.left] + node.comparators,
                                     node.comparators):
                if ((_is_wall(first, walls)
                     and _is_bound(second, numbers, deadlines))
                        or (_is_bound(first, numbers, deadlines)
                            and _is_wall(second, walls))):
                    report(node, 'a number decides against a wall-clock '
                                 'reading')
        elif isinstance(node, (ast.Constant, ast.JoinedStr)):
            rendered = _rendered(node)
            if rendered and DURATION.search(rendered) and FAILURE.search(
                    rendered):
                report(node, f'message reporting only a duration: '
                             f'{rendered!r}')
    return sorted(set(found))


def _tables(tree):
    """The numbers, the wall readings and the deadlines the source names."""
    numbers, walls, deadlines = {}, set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = ([node.target] if isinstance(node, ast.AnnAssign)
                       else node.targets)
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                value = _number(node.value, numbers)
                if value is not None:
                    numbers[target.id] = value
                elif (_is_wall(getattr(node.value, 'left', None), walls)
                      and _is_bound(getattr(node.value, 'right', None),
                                    numbers, deadlines)):
                    # A reading beside a number is a bound, not a reading: it
                    # is what a later comparison measures against.
                    deadlines.add(target.id)
                elif _is_wall(node.value, walls):
                    walls.add(target.id)
        elif isinstance(node, ast.FunctionDef):
            for argument, default in _defaults(node):
                if _number(default, numbers) is not None:
                    numbers[argument.arg] = _number(default, numbers)
    return numbers, walls, deadlines


def _rendered(node):
    """A string as the source spells it, an f-string's parts put back
    together, so a duration the format supplies is read beside the words
    around it rather than in a fragment of its own.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = (part.value if isinstance(part, ast.Constant) else '{...}'
                 for part in node.values)
        return ''.join(str(part) for part in parts)
    return None


def _defaults(node):
    """The positional parameters that carry a default, each with it.

    Defaults align with the tail of the list, so a signature with two plain
    parameters before a bounded one pairs the bounded one and not the first.
    """
    defaults = node.args.defaults
    return list(zip(node.args.args[len(node.args.args) - len(defaults):],
                    defaults))


def _number(node, numbers):
    """The value of a numeric expression the source states, else None."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)):
            return None
        return float(node.value)
    if isinstance(node, ast.Name):
        return numbers.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)):
        value = _number(node.operand, numbers)
        return None if value is None else -value if isinstance(
            node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult)):
        left, right = _number(node.left, numbers), _number(node.right, numbers)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        return left * right
    return None


def _is_wall(node, walls):
    """Whether this expression is a wall-clock reading, directly or by name."""
    if node is None:
        return False
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in WALL_READERS:
                return True
        return any(_is_wall(argument, walls) for argument in node.args)
    if isinstance(node, ast.Name):
        return node.id in walls
    if isinstance(node, ast.UnaryOp):
        return _is_wall(node.operand, walls)
    if isinstance(node, ast.BinOp):
        return _is_wall(node.left, walls) or _is_wall(node.right, walls)
    return False


def _is_bound(node, numbers, deadlines):
    """Whether this expression is a number, or a name one was read into."""
    if _number(node, numbers) is not None:
        return True
    return isinstance(node, ast.Name) and node.id in deadlines


def _owner(node, parents):
    """The function a node sits in, or the module when it sits in none."""
    while True:
        if isinstance(node, ast.FunctionDef):
            return node.name
        if node not in parents:
            return '<module>'
        node = parents[node]


def test_no_wait_in_the_budget_suite_carries_a_wall_clock_margin(tmp):
    del tmp
    margins = _wall_clock_margins(BUDGET_SUITE.read_text(encoding='utf-8'))
    assert margins == [], margins


def test_the_shape_pin_reads_the_arithmetic_rather_than_a_list_of_names(tmp):
    """Every spelling a bound can arrive in is caught, and one that shares
    no name with any of them is caught too, which is what says the pin reads
    the class rather than a handful of the shapes it has seen.
    """
    del tmp
    spellings = {
        'a parameter named for the bound': (
            'def _until(p, what, seconds=45):\n'
            '    pass\n'),
        'a bound read from a constant': (
            'WAIT = 45\n'
            'def _until(p, what, limit=WAIT):\n'
            '    pass\n'),
        'a literal in a comparison': (
            'def test_a_wait(tmp):\n'
            '    start = time.monotonic()\n'
            '    if time.monotonic() - start > 45:\n'
            '        raise AssertionError("x")\n'),
        'a local the comparison reads': (
            'def test_a_wait(tmp):\n'
            '    deadline = time.monotonic() + 45\n'
            '    if time.monotonic() > deadline:\n'
            '        raise AssertionError("x")\n'),
        'neither: another module, no bound-ish name': (
            'def test_a_wait(tmp):\n'
            '    import time as clock\n'
            '    limit = 7\n'
            '    began = clock.monotonic()\n'
            '    while clock.monotonic() - began > limit:\n'
            '        pass\n'),
        'a message whose only evidence is a duration': (
            'def _until(p, what):\n'
            '    raise AssertionError(f"timed out after {45}s for {what}")\n'),
    }
    for description, source in spellings.items():
        assert _wall_clock_margins(source), description


def test_the_shape_pin_reads_a_suite_that_waits_on_state_as_clean(tmp):
    """The two pause tests are the suite's only wall-clock sites, and their
    headroom runs the direction a loaded runner cannot fail: the pause must
    not end before the reset the fixture reported. Each exemption says so,
    and the scanner refuses a site whose function is not one of them.
    """
    del tmp
    name = next(iter(EXEMPT_FUNCTIONS))
    clean = (
        f'def {name}(fake, extra=(), bound=30, limit=120):\n'
        '    return subprocess.run([str(SKILL / "ci_wait.py"),\n'
        '                            "--timeout", str(bound)],\n'
        '                           timeout=limit)\n')
    assert _wall_clock_margins(clean) == [], _wall_clock_margins(clean)
    for reason in EXEMPT_FUNCTIONS.values():
        assert reason, EXEMPT_FUNCTIONS
    moved = clean.replace(name, 'test_some_other_name')
    assert _wall_clock_margins(moved), 'an unnamed site is exempt'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
