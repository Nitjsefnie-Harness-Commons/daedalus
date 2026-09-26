#!/usr/bin/env python3
"""The waits the watcher-budget suite measures its children through.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once: as
the failure-reporting backstop on the single wait with no live process to
give up on, which renders the surviving pids, the parent's exit and the
captured output at expiry instead of a number of seconds.

No guard here asserts that the budget suite carries no wall-clock bound.
Five were tried and each was defeated by an ordinary spelling its own
decider did not read, and a guard narrower than its reason for existing is
the defect it was written to catch, so there is none. The claim is carried
by the ledger below: a control per arm of each wait, every arm killed by
name against a planted defect, and the mutation proofs on the teardown
tests.
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _watcher_waits import (  # noqa: E402
    Stream,
    await_calls,
    await_gone,
    await_lines,
)

# A double that never terminates turns an assertion mutation into a job
# timeout, so every double here ends by name instead.
RUNAWAY_CALL_LIMIT = 1000
# The slice a condition double waits, so a wait that ignored the state it
# was handed ends in seconds rather than at the job's timeout.
DOUBLE_WAIT = 0.01


class _ScriptedCondition(threading.Condition):
    """A condition that says when a wait began and ends a runaway one.

    A helper that ignored the state it was handed would wait here forever,
    so the double names the run instead of letting a job timeout do it: it
    returns on a short slice as well as on a notify, which is what lets a
    mutated helper exhaust the count in seconds.
    """

    def __init__(self):
        super().__init__()
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits > RUNAWAY_CALL_LIMIT:
            raise AssertionError('condition double exceeded call limit')
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


class _LateStream:
    """A stream double that hands its line over only once a waiter has
    looked and found nothing.

    The control's claim is an ordering - the line lands after the wait has
    begun - and the ordering is produced by the look count rather than by a
    second thread, so nothing in this control can be starved. A waiter that
    never suspends spins here, is handed the line anyway, and is caught by
    the control's own assertion on the wait count. `await_lines` reads
    exactly the three attributes this carries.
    """

    def __init__(self, changed, line):
        self.changed = changed
        self.ended = False
        self._line = line
        self._drained = []
        self.looks = 0

    @property
    def lines(self):
        self.looks += 1
        if self.looks == 2:
            self._drained.append(self._line)
        return self._drained


def test_the_line_wait_ends_on_a_line_published_after_it_began_waiting(tmp):
    """The wait is synchronised on the pump, not on a lucky first read.

    The double hands the line over on the second look whatever the waiter
    did, so the claim is the pair of them in one assertion: the line
    arrived, and reaching it took at least one suspension of the condition.
    A waiter that spun its way to the second look satisfies the first half
    and fails the second.
    """
    del tmp
    changed = _ScriptedCondition()
    stream = _LateStream(changed, 'watcher pid 42')
    found = await_lines(stream, lambda line: 'watcher pid' in line, 1,
                        'both children to announce their pid')
    assert found == ['watcher pid 42'] and changed.waits >= 1, (
        found, changed.waits)


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
    # 7 answers alive, 7 again, then 8 answers dead: the survivors list the
    # backstop builds must be the pids that are still up, not every pid it
    # was handed.
    states = iter((True, True, False))
    calls = 0

    def alive(_pid):
        nonlocal calls
        calls += 1
        if calls > RUNAWAY_CALL_LIMIT:
            raise AssertionError('pid double exceeded call limit')
        return next(states, True)

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
    assert 'pids still alive [7]' in message, message
    assert 'pids still alive [7, 8]' not in message, message
    assert 'parent exit -9' in message, message
    assert 'started ci watcher pid 9' in message, message


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
