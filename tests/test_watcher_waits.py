#!/usr/bin/env python3
"""The waits the watcher-budget suite measures its children through.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once: as
the failure-reporting backstop on the single wait with no live process to
give up on, which renders the surviving pids, the parent's exit and the
captured output at expiry instead of a number of seconds.

There is no guard here that the budget suite carries no wall-clock bound,
and there was one for four rounds: a name list, then an arithmetic rule,
then a loop marker, then four questions said to have closed domains. Each
was defeated by an ordinary spelling its own decider did not read - a
from-imported clock, a default argument, a `**`-unpacked timeout - and a
guard whose reach is narrower than its reason for existing is the defect
it was written to catch, so it was removed rather than narrowed. What
carries the claim instead is the ledger: one control per arm of each wait
below, every arm killed by name against a planted defect, plus the two
mutation proofs on the teardown tests and the five review rounds recorded
on the pull request. A reader who wants a static check here is told the
trade rather than handed a spelling list.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
