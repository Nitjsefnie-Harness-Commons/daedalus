#!/usr/bin/env python3
"""The waits the watcher-budget suite measures its children through.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once: as
the failure-reporting backstop on the single wait with no live process to
give up on, which renders the surviving pids, the parent's exit and the
captured output at expiry instead of a number of seconds. The control at
the end is what the budget suite's being clock-free rests on, and it is
four decidable questions rather than a scan: no repetition there reads a
clock, no function there calls itself, every wait it performs is one of
the three helpers above, and the six `timeout=` bounds it hands to a call
are the six the control names. What proves these particular waits carry
no margin is the mutation ledger, which killed the parent-death
guarantee with the names of the survivors in the message; the control
says where its own reach stops, and the ledger carries the rest.
"""
import ast
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

BUDGET_SUITE = Path(__file__).resolve().parent / 'test_watcher_budget.py'
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


def _repetitions_reading_a_clock(source):
    """Every repetition in the file whose own body reads a wall clock.

    Two closed domains and one question. Python's repetitions are the three
    loop statements and the four comprehension expressions - seven node
    types, all read here - and the clock side is the standard library's own
    inventory of readers, `time`'s seven and `datetime`'s three, named as the
    library spells them. That list is a fact about the library rather than
    about this file, and a repetition that does not read a clock cannot wait
    on one whatever its iterable is - which is why the file's four loops and
    its comprehensions, none of which reads a clock, are not reported.
    """
    tree = ast.parse(source)
    return sorted(ast.unparse(node)[:72] for node in ast.walk(tree)
                  if isinstance(node, REPETITIONS)
                  and _reads_a_clock(node))


def _reads_a_clock(node):
    """Whether this expression calls a standard-library clock reader."""
    return any(isinstance(child, ast.Call)
               and isinstance(child.func, ast.Attribute)
               and child.func.attr in CLOCK_READERS
               for child in ast.walk(node))


def _callees(node):
    """The names a function body calls, a method counted by its own name."""
    return {func.attr if isinstance(func, ast.Attribute) else func.id
            for child in ast.walk(node) if isinstance(child, ast.Call)
            for func in [child.func] if isinstance(func, (ast.Name,
                                                          ast.Attribute))}


def _self_calls(source):
    """Every function in the file that calls itself, directly or through the
    file's own call graph.

    The domain is the file's own definitions - functions and methods - which
    is a closed set, and a cycle through one of them is decidable over it.
    """
    tree = ast.parse(source)
    defs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, []).append(node)
    edges = {name: {callee for node in nodes for callee in _callees(node)
                    if callee in defs} for name, nodes in defs.items()}
    recursive = set()
    for name, reached in edges.items():
        seen, pending = set(), list(reached)
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(edges.get(current, ()))
        if name in seen:
            recursive.add(name)
    return sorted(recursive)


def _waits_import(source):
    """The name this file binds the waits module to, from its own import."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for entry in node.names:
                if entry.name == WAITS_MODULE:
                    return entry.asname or entry.name
    return None


def _waits_module_calls(source):
    """Every call this file makes into the waits module.

    The domain is the file's own import: one module bound to one name, so
    what it uses from that module is decidable, and `WAIT_HELPERS` is every
    helper the module exports. Anything else reached through the import is a
    call the control reports.
    """
    alias = _waits_import(source)
    if alias is None:
        return None
    return sorted({node.attr for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name)
                   and node.value.id == alias})


def _bounds_handed_to_calls(source):
    """Every `timeout=` bound the file hands to a call, as a census of
    (function, the call as written, the value).

    The domain is a syntactic form: the `timeout` keyword, on any callee -
    a method, a bare name, anything. What is NOT read, and is named as a
    limit, is a bound handed in a positional argument, because which
    positions a wait takes its bound from is a property of each callee and
    enumerating them is the spelling list this control exists to avoid.
    """
    tree = ast.parse(source)
    parents = _parents(tree)
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == 'timeout':
                rows.append((_owner(node, parents), ast.unparse(node.func),
                             ast.unparse(keyword.value)))
    return sorted(rows)


def _parents(tree):
    return {child: node for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)}


def _owner(node, parents):
    """The function a node sits in, or the module when it sits in none."""
    while True:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        if node not in parents:
            return '<module>'
        node = parents[node]


# Every `timeout=` bound the budget suite hands to a call, with the reason each
# is the module's own contract rather than a wait's health margin: a bound on
# a child this module has already decided to stop - killed in `stop`, or
# signalled in the two teardown tests - and a bound on a one-shot script under
# test. `_ci_wait`'s `limit` is this runner's kill bound on the `ci_wait.py`
# process; the script's own `--timeout` is the separate `bound=30` argument.
# A seventh row is a finding, and the control below arrives as a diff against
# this list rather than as a name-keyed exemption.
BOUND_CALLS = (
    ('_ci_wait', 'subprocess.run', 'limit'),
    ('stop', 'self.proc.wait', '60'),
    ('test_a_graceful_exit_leaves_no_children_behind', 'parent.proc.wait',
     '60'),
    ('test_a_review_whose_inline_comments_overflow_is_followed_once',
     'subprocess.run', '60'),
    ('test_the_children_die_with_their_parent', 'parent.proc.wait', '60'),
    ('test_the_once_trial_counts_the_checks_that_have_not_concluded',
     'subprocess.run', '60'),
)
# The two inventories check one reads: the standard library's clock readers,
# and Python's repetition forms. Both are closed, and both are properties of
# the language and the library rather than of the file under the control.
CLOCK_READERS = frozenset({'monotonic', 'monotonic_ns', 'perf_counter',
                           'perf_counter_ns', 'process_time', 'time',
                           'time_ns', 'now', 'today', 'utcnow'})
REPETITIONS = (ast.While, ast.For, ast.AsyncFor, ast.ListComp, ast.SetComp,
               ast.DictComp, ast.GeneratorExp)
# Everything the waits module exports that the budget suite uses: the three
# waits, and the stream primitive it drains into, which cannot wait. The
# domain is the module's own surface, so what the file reaches for is
# decidable against it.
WAITS_EXPORTS = frozenset({'Stream', 'await_calls', 'await_gone',
                           'await_lines'})
WAITS_MODULE = 'test_watcher_waits'


def test_the_budget_suite_carries_no_wait_of_its_own(tmp):
    """The claim, as the four things this file's grammar admits a wait to be,
    with the reason each of those four domains is closed:

    * a repetition that reads a clock - Python's repetitions are the three
      loop statements and the four comprehension expressions, seven node
      types, and the clocks are the standard library's own inventory, so
      both sides are closed;
    * a self-call - the domain is this file's own definitions, a closed set,
      and a cycle through one of them is decidable over it;
    * a call into the waits module - the domain is the file's own import,
      one module bound to one name, and `WAITS_EXPORTS` is the whole of
      what that module offers the file: the three waits and the stream
      primitive, which cannot wait;
    * a `timeout=` keyword - a syntactic form, read on any callee.

    Not claimed, and named rather than half-read: a bound handed to a call
    in a positional argument (`time.sleep(45)`, `sock.settimeout(45)`), a
    repetition inside a comprehension, and a wait inside an imported module
    (`_util`, `_fake_gh`). Deciding any of those means enumerating the
    standard library's bound-taking signatures, or the test tree's other
    files, which is the enumeration this control exists to avoid - so what
    proves these waits carry no margin is the mutation ledger instead.
    """
    del tmp
    source = BUDGET_SUITE.read_text(encoding='utf-8')
    loops = _repetitions_reading_a_clock(source)
    assert loops == [], loops
    recursion = _self_calls(source)
    assert recursion == [], recursion
    used = _waits_module_calls(source)
    assert used is not None, 'the waits module is not imported by name'
    assert set(used) <= WAITS_EXPORTS, used
    assert _bounds_handed_to_calls(source) == list(BOUND_CALLS), (
        'a timeout= bound arrived; name it and its reason here')


def test_each_of_the_four_checks_decides_one_case_alone(tmp):
    """One case per check, and exactly one check per case - the count a
    sweep would have supplied, and the one that is not decoration: a
    generator is not used here because the property is invariant across
    every axis value, so a crossing of unchanged values decides nothing.
    """
    del tmp
    cases = {
        'a repetition': ('def spin():\n'
                         '    for _ in (None,) * 45:\n'
                         '        if time.monotonic() - began > 44:\n'
                         "            raise AssertionError('gave up')\n"),
        'a self-call': ('def spin():\n'
                        '    return spin()\n'),
        'a call into the waits module': (
            f'import {WAITS_MODULE} as waits\n'
            'def spin(predicate):\n'
            '    return waits.await_everything(predicate)\n'),
        'a timeout= bound': ('def spin(queue):\n'
                             '    get = queue.get\n'
                             '    return get(timeout=45)\n'),
    }
    for description, source in cases.items():
        used = _waits_module_calls(source) or []
        reported = [
            name for name, rows in (
                ('a repetition', _repetitions_reading_a_clock(source)),
                ('a self-call', _self_calls(source)),
                ('a call into the waits module',
                 [entry for entry in used if entry not in WAITS_EXPORTS]),
                ('a timeout= bound', _bounds_handed_to_calls(source)))
            if rows]
        assert reported == [description], (description, reported)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
