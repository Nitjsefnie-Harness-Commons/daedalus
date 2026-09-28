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

One arm of `await_polls` does end on that shape, and it is the one that
needed it. A marker assignment no-opped in either watcher leaves the loop
child up, healthy and publishing nothing new, and the three budget
controls waiting on one would spin until the job's own limit ended the run
nameless. The arm is a ceiling on the CALLS a run may log before the index
advances, not a bound in seconds: it is monotone in evidence, so a loaded
runner reaches it later or not at all and cannot be failed by it, where a
timeout buys an early failure with a flaky leg. It names what it saw and
says plainly that one very wide poll reads the same as a frozen index,
with the idle bound as the other refusal. `await_lines` and `await_calls`
take no such bound, and the trade they take is unchanged.
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _watcher_waits  # noqa: E402
from _watcher_waits import (  # noqa: E402
    POLL_WIDTH,
    ChildProcess,
    Stream,
    await_calls,
    await_gone,
    await_lines,
    await_polls,
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


def test_the_poll_wait_ends_on_the_nth_distinct_marker(tmp):
    del tmp
    log = _GrowingLog([{'poll': '1', 'request': 'query one'},
                       {'poll': '2', 'request': 'query two'}])
    child = _ScriptedChild(alive=True)
    calls = await_polls(log, 2, child, '2 poll(s)')
    assert [call['poll'] for call in calls] == ['1', '2'], calls


def test_the_poll_wait_gives_up_by_name_when_the_child_exits(tmp):
    del tmp
    log = _GrowingLog([])
    child = _ScriptedChild(output='poll failed (1): no fixture carries it')
    message = None
    try:
        await_polls(log, 2, child, '2 poll(s)')
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError('a poll that never arrived did not fail')
    assert message is not None
    assert '2 poll(s)' in message, message
    assert 'poll failed (1): no fixture carries it' in message, message


# How many polls the ceiling control asks for, and the log it hands over:
# one entry past the ceiling, every entry carrying the SAME marker, so the
# distinct count can never reach the poll count and the child is alive
# throughout. Those two facts are what leave the ceiling as the only arm
# that can end this wait.
_FROZEN_POLLS = 2
_FROZEN_LOG = [{'poll': '1', 'request': f'query {index}'}
               for index in range(_FROZEN_POLLS * POLL_WIDTH + 1)]


def test_the_poll_wait_gives_up_by_name_when_the_index_never_advances(tmp):
    """The call ceiling, on the one shape no other arm of this wait reaches.

    A child that stays up and keeps logging the same marker is a frozen
    poll index: it is not silent, and it is not dead, so neither the
    marker count nor `child.alive` can end the wait. Without the ceiling
    this control ends on the log double's own runaway guard, which names
    the double rather than the defect. The refusal has to carry what a
    reader needs - the markers, the calls logged, the polls expected - and
    to say that this log cannot tell a frozen index from one very wide
    poll, because it cannot.
    """
    del tmp
    log = _GrowingLog(_FROZEN_LOG)
    child = _ScriptedChild(alive=True)
    message = None
    try:
        await_polls(log, _FROZEN_POLLS, child, '2 poll(s)')
    except AssertionError as exc:
        message = str(exc)
    assert message is not None, 'a frozen poll index did not fail'
    assert "markers seen ['1']" in message, message
    assert f'{len(_FROZEN_LOG)} gh call(s) logged' in message, message
    assert '2 advancing marker(s) expected' in message, message
    assert 'cannot tell a frozen index from one very wide poll' in message, (
        message)
    assert 'IDLE_POLL_BOUND' in message, message


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


def test_a_cancel_ends_the_whole_tree_and_not_only_the_child(tmp):
    """The pin #1255's fix otherwise lacked: the tree kill had no assertion
    that could fail.

    `stop()` is reached only in `finally` blocks, and the two controls that
    assert on a child's death assert BEFORE `stop()` and use the process's
    own `kill()` - so replacing the whole delegation with a bare
    `proc.kill()` left that suite green. This one drives the property and
    not the plumbing: a real `ChildProcess` that has spawned a grandchild,
    stopped, and the grandchild must be gone. A direct kill would leave it
    running, which is the orphan the first paragraph of `_cancel`'s
    docstring is about.
    """
    del tmp
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               PYTHONUNBUFFERED='1')
    # The child backgrounds a grandchild, names it, and stays up. On POSIX
    # only: Windows has no process group to signal and the tree is named by
    # pid, which is the other half of the owner's delegation.
    if sys.platform.startswith('win'):
        _util.skip('the tree kill under test is the POSIX group kill')
    program = (
        'import subprocess, sys, time;'
        'kid = subprocess.Popen([sys.executable, "-c",'
        ' "import time; time.sleep(120)"]);'
        'print(kid.pid, flush=True); time.sleep(120)')
    child = ChildProcess([sys.executable, '-c', program], env)
    grandchild = None
    try:
        deadline = time.time() + 30
        while not child.out.lines and time.time() < deadline:
            time.sleep(0.05)
        assert child.out.lines, child.captured()
        grandchild = int(child.out.lines[0].strip())
        assert _alive(grandchild), grandchild
        child.stop()
        deadline = time.time() + 30
        while _alive(grandchild) and time.time() < deadline:
            time.sleep(0.05)
        assert not _alive(grandchild), (
            f'grandchild {grandchild} outlived the child it belonged to')
    finally:
        if child.proc.poll() is None:
            child.proc.kill()
            child.proc.wait(timeout=10)
        if grandchild is not None and _alive(grandchild):
            try:
                os.kill(grandchild, 9)
            except OSError:
                pass


class _Unkillable:
    """A child the owner could not end: alive, and refusing a direct kill.

    The one state `_cancel`'s own fallback fires on, with a `kill()` that
    raises - the reviewer's E6, as a double rather than as a real EPERM.
    """

    pid = 4242

    def __init__(self):
        self.kills = 0

    def poll(self):
        return None

    def kill(self):
        self.kills += 1
        raise PermissionError(1, 'Operation not permitted')

    def wait(self, timeout=None):
        return -9


def test_a_failing_direct_kill_is_reported_and_never_raised(tmp):
    """The owner's steps are wrapped for a stated reason - a cleanup failure
    must not replace the expiry the caller is about to report - and the
    local fallback has to be wrapped the same way. It sits on exactly the
    state the owner has just described as failed, so an uncontained
    `OSError` there escapes `_cancel`, and so `ChildProcess.stop`, taking
    the classified error with it.
    """
    del tmp
    proc = _Unkillable()
    real = _watcher_waits.cleanup_process_tree
    _watcher_waits.cleanup_process_tree = (
        lambda process, bound: 'process group 4242 was already gone')
    try:
        # The call itself must not raise: that is the whole assertion.
        answer = _watcher_waits._cancel(proc)
    finally:
        _watcher_waits.cleanup_process_tree = real
    assert proc.kills == 1, proc.kills
    assert 'process group 4242 was already gone' in answer, answer
    assert 'direct fallback kill raised' in answer, answer
    assert 'PermissionError' in answer, answer


def _alive(pid):
    """Whether a pid is still a process this runner can see."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchwaits_')


if __name__ == '__main__':
    raise SystemExit(main())
