#!/usr/bin/env python3
"""The waits the watcher-budget suite measures its children through.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once: as
the failure-reporting backstop on the single wait with no live process to
give up on, which renders the surviving pids, the parent's exit and the
captured output at expiry instead of a number of seconds. The control at
the end is what the waits' being clock-free rests on, and it is a
structural fact rather than a scan: the budget suite holds no loop, so
every wait it performs is a call - into the waits above, or into one of
the six calls whose bounds the control names - and there is nothing in it
for a wall-clock bound to decide. What proves these particular waits
carry no margin is the mutation ledger, which is what killed the
parent-death guarantee with the names of the survivors in the message.
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


def _waits_of_its_own(source):
    """Every loop a wait of this file's own would spin in.

    Two forms, and they are the two a bounded wait takes: a `while`, and a
    `for` over a numeric range. Either is a place a wall-clock bound can be
    decided, so either in a suite whose waits are all calls is the margin
    coming back.
    """
    tree = ast.parse(source)
    return sorted(ast.unparse(node)[:72] for node in ast.walk(tree)
                  if isinstance(node, ast.While)
                  or (isinstance(node, ast.For)
                      and isinstance(node.iter, ast.Call)
                      and isinstance(node.iter.func, ast.Name)
                      and node.iter.func.id == 'range'))


def _bounds_handed_to_calls(source):
    """Every wall-clock bound the file hands to a CALL, as a census of
    (function, callee, the value). A census and not a rule: it names what
    the loop marker does not reach.
    """
    tree = ast.parse(source)
    parents = _parents(tree)
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        for keyword in node.keywords:
            if keyword.arg == 'timeout':
                rows.append((_owner(node, parents), node.func.attr,
                             ast.unparse(keyword.value)))
    return sorted(rows)


def _parents(tree):
    return {child: node for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)}


def _owner(node, parents):
    """The function a node sits in, or the module when it sits in none."""
    while True:
        if isinstance(node, ast.FunctionDef):
            return node.name
        if node not in parents:
            return '<module>'
        node = parents[node]


# Every wall-clock bound the budget suite hands to a call, and why each is the
# module's own contract rather than a wait's health margin: a `wait` on a
# child this module has already decided to stop, and the `--timeout` of the
# script under test. A seventh is a finding, and the control below arrives as
# a diff against this list rather than as a name-keyed exemption.
BOUND_CALLS = (
    ('_ci_wait', 'run', 'limit'),
    ('stop', 'wait', '60'),
    ('test_a_graceful_exit_leaves_no_children_behind', 'wait', '60'),
    ('test_a_review_whose_inline_comments_overflow_is_followed_once', 'run',
     '60'),
    ('test_the_children_die_with_their_parent', 'wait', '60'),
    ('test_the_once_trial_counts_the_checks_that_have_not_concluded', 'run',
     '60'),
)

# The ways Python binds a name, from the language's own grammar rather than
# from any recogniser: the crossing below is a property of the marker, which
# does not care what any of them bound. A form that cannot carry a clock - a
# `with` target, an `except ... as`, a `global`, a `nonlocal` - appears beside
# a bound of its own, so the wait is a real one either way. A `for` target
# brings its own loop, because a for target exists only in one; the wait's own
# loop stands beside it, and the marker is that one.
BINDING_FORMS = (
    ('a plain assignment', 'deadline = {read} + {bound}', 'deadline'),
    ('an annotated assignment', 'deadline: float = {read} + {bound}',
     'deadline'),
    ('an augmented assignment', 'deadline = {read}\ndeadline += {bound}',
     'deadline'),
    ('a tuple target', 'start, deadline = {read}, {read} + {bound}',
     'deadline'),
    ('an attribute target', 'holder.deadline = {read} + {bound}',
     'holder.deadline'),
    ('a subscript target', "state['deadline'] = {read} + {bound}",
     "state['deadline']"),
    ('a for target', 'for deadline in iter(({read} + {bound},), 1):\n    pass',
     'deadline'),
    ('a with target', 'with _held() as held:\n    pass\n'
     'deadline = {read} + {bound}', 'deadline'),
    ('an except target', 'try:\n    _held()\nexcept ValueError as seconds:\n'
     '    pass\ndeadline = {read} + {bound}', 'deadline'),
    ('a global declaration', 'global deadline\ndeadline = {read} + {bound}',
     'deadline'),
    ('a nonlocal declaration',
     'deadline = _held()\nnonlocal deadline\ndeadline = {read} + {bound}',
     'deadline'),
)
# Every way a standard-library clock reaches the comparison, including the two
# that arrive as a binding rather than as a spelling: a reader bound to a
# name, and a clock the caller injects as a default argument.
CLOCK_BINDINGS = (
    ('a module', ('import time',), '', 'time.monotonic()'),
    ('a module aliased at import', ('import time as c',), '', 'c.monotonic()'),
    ('a from-imported reader', ('from time import monotonic',), '',
     'monotonic()'),
    ('a from-imported alias', ('from time import monotonic as m',), '', 'm()'),
    ('a reader bound to a name', ('import time', 'clock = time.monotonic'),
     '', 'clock()'),
    ('a computed attribute', ('import time',), '',
     'getattr(time, "monotonic")()'),
    ('a datetime class', ('from datetime import datetime',), '',
     'datetime.now()'),
    ('a clock injected as a default argument', ('import time',),
     ', clock=time.monotonic', 'clock()'),
)
# The three shapes a comparison between a reading and a bound can take.
COMPARISON_SHAPES = (
    ('the reading on the left', '{read} > {bound}'),
    ('the bound on the left', '{bound} < {read}'),
    ('an elapsed form', '{read} - began > {bound}'),
)


def _reintroductions():
    """Every reintroduction the marker is claimed to refuse.

    The universe is the language's and the runtime's, not a recogniser's:
    every way a name can be bound, every way a standard-library clock can
    reach a comparison, and every shape that comparison can take. There is
    no recogniser here to derive it from - the marker is a loop, so what the
    crossing measures is the marker.
    """
    for clock_label, clock_setup, signature, read in CLOCK_BINDINGS:
        for form_label, form, bound in BINDING_FORMS:
            for shape_label, shape in COMPARISON_SHAPES:
                label = (f'{clock_label} / {form_label} / {shape_label}')
                yield label, _source(clock_setup, signature, read, form,
                                     bound, shape)


def _source(clock_setup, signature, read, form, bound, shape):
    """One working wait: a clock and a bound, bound the way the form says,
    compared the way the shape says, spun in a loop the marker can read."""
    body = [form.format(read=read, bound=bound),
            'began = time.monotonic()', 'holder = _Holder()', 'state = {}',
            'while predicate():',
            '    if {}:'.format(shape.format(read=read, bound=bound)),
            "        raise AssertionError(what)"]
    wait = [f'def test_a_wait(tmp{signature}):'] + _indent(body)
    if form.startswith('nonlocal'):
        # `nonlocal` needs a binding in an enclosing scope, so the wait is
        # nested in one that has it.
        return '\n'.join(['def outer():', '    deadline = None']
                         + _indent(wait) + list(clock_setup))
    return '\n'.join(list(clock_setup) + wait)


def test_the_budget_suite_runs_no_wait_of_its_own(tmp):
    """The suite holds no loop, so every wait it performs is a call: into
    `test_watcher_waits.py`, whose waits synchronise on what the process
    under test did, or into one of the six calls the census below names.
    That is the property the three claims rest on, and it is why the class a
    static scan cannot decide is not scanned for here.
    """
    del tmp
    source = BUDGET_SUITE.read_text(encoding='utf-8')
    loops = _waits_of_its_own(source)
    assert loops == [], loops
    assert _bounds_handed_to_calls(source) == list(BOUND_CALLS), (
        'a bound handed to a call arrived; name it and its reason here')


def test_the_marker_is_the_loop_and_not_what_the_names_denote(tmp):
    """The marker's boundary, in one place: the same clock and the same
    bound, spun in a loop, is a wait and is found; compared once, it is not
    a wait and carries nothing to bound. What decides that is the loop.
    """
    del tmp
    head = ('import time\n'
            'def test_a_wait(tmp, predicate, what, bound=45):\n'
            '    clock = time.monotonic\n'
            '    began = clock()\n')
    comparison = ['if clock() - began > bound:',
                  '    raise AssertionError(what)']
    looped = '\n'.join(
        [head.rstrip('\n'), '    while predicate():']
        + _indent(comparison, '        '))
    found = _waits_of_its_own(looped)
    assert len(found) == 1, found
    assert found[0].startswith('while predicate():'), found
    once = '\n'.join([head] + _indent(comparison))
    assert _waits_of_its_own(once) == [], once


def test_the_loop_marker_refuses_every_reintroduction_the_language_offers(tmp):
    """The bypass universe, crossed from the language's binding grammar and
    the clock's own spellings rather than from any recogniser: whatever
    binds the number, the loop is what the control reads, and a wait that
    has one is refused.
    """
    del tmp
    produced = list(_reintroductions())
    assert len(produced) > 200, len(produced)
    missed = [label for label, source in produced
              if not _waits_of_its_own(source)]
    assert missed == [], f'{len(missed)} shapes pass: {missed[:3]}'


def _indent(lines, pad='    '):
    """Pad every line, so an entry that is itself several lines keeps its
    shape instead of losing all but its first line's indent."""
    return [pad + line for entry in lines
            for line in entry.splitlines() or ['']]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
