#!/usr/bin/env python3
"""The waits the watcher-budget suite measures its children through.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once: as
the failure-reporting backstop on the single wait with no live process to
give up on, which renders the surviving pids, the parent's exit and the
captured output at expiry instead of a number of seconds. The shape pin at
the end reads the budget suite's own bindings and refuses every comparison
a numeric bound takes part in against a wall-clock reading - the shape
issue 1039 removed - and names beside itself what it cannot resolve.
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


def _wall_clock_margins(source):
    """Every comparison a numeric bound takes part in against a reading.

    The class, stated once: **a numeric bound compared against a wall-clock
    reading**. Both halves resolve through the file's own bindings, because
    a name is not what it denotes - `from time import monotonic as _clock`,
    `c = time`, `r = time.monotonic` and `getattr(time, "monotonic")` are the
    same reading under four bindings, and a required parameter is the same
    bound as a defaulted one. A number is a literal, a constant, a constant
    expression, a parameter of any kind, or a local holding a reading and a
    bound; a reading is a standard-library clock called through any of those
    bindings.

    Declared limits, each a thing no static reading of one file can resolve,
    and each with a control that pins the behaviour it names: a star import
    (`from time import *`), a number that arrives from a call's return value
    or from the environment, a bound handed to a call as an argument
    (`time.sleep(45)`, `queue.get(timeout=45)`), a clock outside the standard
    library, and a margin in any file other than the one this reads - which
    is what issue 1038 enumerates for the rest of the tree.
    """
    tree = ast.parse(source)
    parents = _parents(tree)
    readings, clocks, numbers, deadlines, parameters = _bindings(tree, parents)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left] + node.comparators
        owner = _owner(node, parents)
        if not any(_reads(side, readings, clocks) for side in sides):
            continue
        counted = [_counts(side, numbers, deadlines, owner, parameters)
                   for side in sides]
        if any(counted):
            found.add((owner, ast.unparse(node)[:72]))
    return sorted(found)


# The clock universe, as the standard library names it: the domain's
# specification rather than a spelling set of the file under the pin.
CLOCK_READERS = {
    'datetime': frozenset({'now', 'today', 'utcnow'}),
    'time': frozenset({'monotonic', 'monotonic_ns', 'perf_counter',
                       'perf_counter_ns', 'process_time', 'time', 'time_ns'}),
}
# How many times the binding pass runs. An alias chain resolves when a pass
# adds a name the next pass can use, and the cap is what stops a cycle of two
# aliases from spinning.
ALIAS_PASSES = 8


def _parents(tree):
    return {child: node for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)}


def _bindings(tree, parents):
    """What every name denotes, resolved until the alias chains settle."""
    clocks, readings, numbers, deadlines, parameters = {}, set(), {}, set(), {}
    for _ in range(ALIAS_PASSES):
        before = (len(clocks), len(readings), len(numbers), len(deadlines),
                  len(parameters))
        clocks, readings, numbers, deadlines, parameters = _one_pass(
            tree, parents, clocks, readings, numbers, deadlines, parameters)
        if before == (len(clocks), len(readings), len(numbers),
                      len(deadlines), len(parameters)):
            break
    return readings, clocks, numbers, deadlines, parameters


def _one_pass(tree, parents, clocks, readings, numbers, deadlines, parameters):
    clocks, readings, numbers = dict(clocks), set(readings), dict(numbers)
    deadlines, parameters = set(deadlines), dict(parameters)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # The module decides what the name is; the name it is bound
                # to may be the module's own or any alias of it.
                root = alias.name.split('.')[0]
                if root in CLOCK_READERS:
                    clocks[alias.asname or alias.name] = root
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or '').split('.')[0]
            if module not in CLOCK_READERS:
                continue
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name in CLOCK_READERS[module]:
                    readings.add(name)
                elif module == 'datetime' and alias.name == 'datetime':
                    clocks[name] = module
        elif isinstance(node, ast.FunctionDef):
            # A name a second function also declares is still this one's
            # parameter, which is how the pre-fix file spelled its pair.
            for argument in node.args.args + node.args.kwonlyargs:
                parameters.setdefault(argument.arg, set()).add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = ([node.target] if isinstance(node, ast.AnnAssign)
                       else node.targets)
            for target in targets:
                if isinstance(target, ast.Name):
                    _bind(target.id, node, parents, clocks, readings, numbers,
                          deadlines, parameters)
    return clocks, readings, numbers, deadlines, parameters


def _bind(name, node, parents, clocks, readings, numbers, deadlines,
          parameters):
    """Record what a name now holds, from what the value is."""
    value = node.value
    if _number(value, numbers) is not None:
        numbers[name] = _number(value, numbers)
        return
    owner = _owner(node, parents)
    if _reader(value, readings, clocks):
        readings.add(name)
    elif isinstance(value, ast.Name) and value.id in clocks:
        clocks[name] = clocks[value.id]
    elif (_reads(value, readings, clocks)
          and _counts(value, numbers, deadlines, owner, parameters)):
        deadlines.add(name)


def _reader(node, readings, clocks):
    """A reference to a reader, called or waiting to be called."""
    if isinstance(node, ast.Name):
        return node.id in readings
    if isinstance(node, ast.Attribute):
        return _clock_reads(node, clocks)
    return _getattr_reads(node, clocks)


def _clock_reads(node, clocks):
    """A reader under a clock name: `time.monotonic`, `datetime.datetime.now`
    and `when.datetime.now` are the same read three depths down, so the
    attribute chain is walked to the name it hangs from.
    """
    if not isinstance(node, ast.Attribute):
        return False
    path, cursor = [], node
    while isinstance(cursor, ast.Attribute):
        path.append(cursor.attr)
        cursor = cursor.value
    if not (isinstance(cursor, ast.Name) and cursor.id in clocks):
        return False
    module = clocks[cursor.id]
    return any(step in CLOCK_READERS[module] for step in path)


def _getattr_reads(node, clocks):
    """`getattr(clock, "monotonic")`, the computed spelling of a read."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'getattr' and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)):
        return False
    module = clocks.get(node.args[0].id)
    return bool(module and node.args[1].value in CLOCK_READERS[module])


def _reads(node, readings, clocks):
    """Whether this expression reads the wall clock."""
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in readings
    if isinstance(node, ast.Call):
        if _reader(node.func, readings, clocks) or _getattr_reads(
                node.func, clocks):
            return True
        return any(_reads(argument, readings, clocks)
                   for argument in node.args) or any(
                       _reads(keyword.value, readings, clocks)
                       for keyword in node.keywords)
    if isinstance(node, ast.UnaryOp):
        return _reads(node.operand, readings, clocks)
    if isinstance(node, ast.BinOp):
        return (_reads(node.left, readings, clocks)
                or _reads(node.right, readings, clocks))
    return False


def _counts(node, numbers, deadlines, owner, parameters):
    """Whether this expression is a number or a bound the file decided on."""
    if _number(node, numbers) is not None:
        return True
    if isinstance(node, ast.Name):
        return (node.id in deadlines
                or owner in parameters.get(node.id, ()))
    if isinstance(node, ast.Call):
        return any(_counts(argument, numbers, deadlines, owner, parameters)
                   for argument in node.args) or any(
                       _counts(keyword.value, numbers, deadlines, owner,
                               parameters) for keyword in node.keywords)
    if isinstance(node, ast.UnaryOp):
        return _counts(node.operand, numbers, deadlines, owner, parameters)
    if isinstance(node, ast.BinOp):
        return (_counts(node.left, numbers, deadlines, owner, parameters)
                or _counts(node.right, numbers, deadlines, owner, parameters))
    return False


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
        if value is None:
            return None
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult)):
        left = _number(node.left, numbers)
        right = _number(node.right, numbers)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        return left * right
    return None


def _owner(node, parents):
    """The function a node sits in, or the module when it sits in none."""
    while True:
        if isinstance(node, ast.FunctionDef):
            return node.name
        if node not in parents:
            return '<module>'
        node = parents[node]


def _indent(lines, pad='    '):
    return [pad + line if line else line for line in lines]


# The bypass universe, crossed from the rule's own productions rather than
# from the plants of the last round: a reading binding, a source a number
# arrives from, a place the wait sits, and a shape the comparison takes. The
# cross product is what the rule claims to refuse, so a shape it misses is a
# finding about the rule rather than a spelling for the next round to name.
_READINGS = (
    ('a canonical reader', ('import time',), 'time.monotonic()'),
    ('the epoch reader', ('import time',), 'time.time()'),
    ('a module aliased at import', ('import time as c',), 'c.monotonic()'),
    ('a from-imported reader', ('from time import monotonic',),
     'monotonic()'),
    ('a from-imported alias', ('from time import monotonic as m',), 'm()'),
    ('a reader bound to a name', ('import time', 'r = time.monotonic'), 'r()'),
    ('a module aliased again', ('import time', 'c = time'), 'c.monotonic()'),
    ('a computed attribute', ('import time',),
     'getattr(time, "monotonic")()'),
    ('a reader imported under a guard',
     ('try:', '    from time import monotonic as m', 'except ImportError:',
      '    from time import monotonic_ns as m'), 'm()'),
    ('a datetime class', ('from datetime import datetime',),
     'datetime.now()'),
    ('a datetime module aliased', ('import datetime as when',),
     'when.datetime.now()'),
)
_BOUNDS = (
    # label, setup, the signature it adds, the bound, a line that holds it
    ('a literal', (), '', '45', ()),
    ('a negative literal', (), '', '-45', ()),
    ('a constant', ('WAIT = 45',), '', 'WAIT', ()),
    ('a constant expression', ('WAIT = 30 + 15',), '', 'WAIT', ()),
    ('a parameter with a default', (), ', seconds=45', 'seconds', ()),
    ('a required parameter', (), ', seconds', 'seconds', ()),
    ('a local holding both', (), '', 'deadline',
     ('deadline = {read} + 45',)),
    ('a timedelta', ('from datetime import timedelta',), '', 'deadline',
     ('deadline = {read} + timedelta(seconds=45)',)),
)
_PLACES = ('a test body', 'a helper at module level', 'a nested function',
           'beside a function declaring the same name')
_SHAPES = ('{read} > {bound}', '{bound} < {read}', '{read} - began > {bound}')


def _reintroductions():
    """Every reintroduction the rule's grammar produces, as (label, source)."""
    for read_label, reading_setup, read in _READINGS:
        for bound_label, bound_setup, signature, bound, held in _BOUNDS:
            lines = [line.format(read=read) for line in held]
            for place in _PLACES:
                for shape in _SHAPES:
                    expression = shape.format(read=read, bound=bound)
                    label = (f'{read_label} / {bound_label} / {place} / '
                             f'{shape}')
                    yield (label, _source(reading_setup, bound_setup,
                                          signature, lines, expression, place))


def _source(reading_setup, bound_setup, signature, held, expression, place):
    """One reintroduced wait, written the way `place` says it sits."""
    setup = list(reading_setup) + list(bound_setup)
    wait = ([f'if {expression}:', "    raise AssertionError('x')"])
    if place == 'a helper at module level':
        return '\n'.join(setup + [f'def _until(predicate, what{signature}):']
                         + _indent(held + wait))
    if place == 'beside a function declaring the same name':
        head = f'def _until(predicate, what{signature}):'
        twin = 'def _sibling(predicate, what):\n    return None'
        return '\n'.join(setup + [twin, head] + _indent(held + wait))
    inner = setup + held + wait
    if place == 'a nested function':
        return '\n'.join(
            ['def test_a_wait(tmp):']
            + _indent([f'def _until(predicate, what{signature}):']
                      + _indent(inner)))
    return '\n'.join(
        [f'def test_a_wait(tmp{signature}):'] + _indent(inner))


def test_no_wait_in_the_budget_suite_carries_a_wall_clock_margin(tmp):
    """The class the wait helpers replaced, read out of the file: no
    comparison there takes a numeric bound against a wall-clock reading.
    """
    del tmp
    margins = _wall_clock_margins(BUDGET_SUITE.read_text(encoding='utf-8'))
    assert margins == [], margins


def test_the_shape_pin_refuses_every_reintroduction_its_grammar_produces(tmp):
    """The bypass universe, crossed from the rule's own productions rather
    than from the last round's plants. Every shape the grammar can spell has
    to be refused, so a shape it misses is a finding about the rule.
    """
    del tmp
    produced = list(_reintroductions())
    assert len(produced) > 500, len(produced)
    missed = [label for label, source in produced
              if not _wall_clock_margins(source)]
    assert missed == [], f'{len(missed)} shapes pass: {missed[:3]}'


def test_the_shape_pin_reports_only_what_its_grammar_can_resolve(tmp):
    """The limits the rule's docstring declares, each pinned by what it
    leaves alone: a bound the process under test enforces itself, a pause
    asserted against an instant a child recorded, and the two the rule
    cannot resolve - a bound handed to a call, and a number from the
    environment.
    """
    del tmp
    clean = {
        'a subprocess timeout the process enforces':
            ('import subprocess\n'
             'def _ci_wait(fake, bound=30, limit=120):\n'
             '    return subprocess.run(["ci_wait.py", "--timeout",\n'
             '                            str(bound)], timeout=limit)\n'),
        'a pause asserted against an instant a child recorded':
            ('import time\n'
             'def test_a_refused_poll_pauses(tmp):\n'
             '    before = time.time()\n'
             '    reset = int(time.time()) + 6\n'
             '    assert calls[1]["t"] >= before + 4\n'
             '    assert calls[1]["t"] >= reset\n'),
        'numbers compared with numbers':
            ('def test_a_ratio():\n'
             '    assert 45 - 40 == 5\n'),
        'a bound handed to a call as an argument':
            ('import time\n'
             'def test_a_sleep(tmp):\n'
             '    time.sleep(45)\n'),
        'a number that arrives from the environment':
            ('import os\n'
             'import time\n'
             'def test_a_wait(tmp):\n'
             '    limit = int(os.environ["LIMIT"])\n'
             '    began = time.monotonic()\n'
             '    if time.monotonic() - began > limit:\n'
             '        raise AssertionError("x")\n'),
    }
    for description, source in clean.items():
        margins = _wall_clock_margins(source)
        assert margins == [], (description, margins)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
