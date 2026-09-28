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

One arm of `await_polls` does end on that shape. A marker assignment
no-opped in either watcher leaves the loop child up, healthy and publishing
nothing new, and the budget controls waiting on one would spin until the
job's own limit ended the run nameless. The arm bounds CALLS PER PUBLISHED
MARKER, not seconds: a run
whose polls are no wider than the tolerance spends about that many calls
per marker however long it runs, so a loaded runner reaches it later or
not at all, where a timeout buys an early failure with a flaky leg. It is
a tolerance and not a promise: a poll wider than it is refused by name,
with the idle bound as the other refusal, and `gh_client.Watcher.poll`
re-entering its own body on a rate-limit refusal can spend more under
one marker than any single poll is expected to. What the arm cannot
settle on its own it does not claim to, and it says how many rows are
ambiguous rather than marking one: two of the four. Marking one row
unresolvable says nothing about the others, so the count is named rather
than one row flagged. `await_lines` and `await_calls` take no such bound,
and the trade they take is unchanged.
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _watcher_waits  # noqa: E402
from _watcher_waits import _reading  # noqa: E402
from _watcher_waits import (  # noqa: E402
    POLL_WIDTH,
    READINGS,
    SEQUENCE,
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
    """The bound on calls per marker, on the shape no other arm reaches.

    A child that stays up and keeps logging the same marker is a frozen
    poll index: it is not silent, and it is not dead, so neither the
    marker count nor `child.alive` can end the wait. Without the bound
    this control ends on the log double's own runaway guard, which names
    the double rather than the defect.

    This is the ONE-VALUE row of `READINGS`, and it is the row that
    cannot be settled: a single marker over `width + 1` calls is an index
    stuck where it started and one wide first poll at once. The control
    holds the refusal to naming that, rather than to either cause.
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
    assert (f'2 poll(s): the poll markers did not reach {_FROZEN_POLLS}'
            in message), message
    assert f'within {len(_FROZEN_LOG)} gh call(s): 1 distinct, ' in message, (
        message)
    assert 'sequence 1,' in message, message
    assert f'over the {POLL_WIDTH} call(s) per marker' in message, message
    assert ('a single value and nothing after it, which is both an index '
            'stuck where it started and one wide first poll' in message), (
        message)
    assert 'IDLE_POLL_BOUND' in message, message


def _log_of(rows):
    """One call-log entry per call, `rows` carrying how many each marker."""
    return [{'poll': marker, 'request': f'query {marker} {call}'}
            for marker, calls in rows
            for call in range(calls)]


# One log per row of `READINGS` after the re-use, each shaped so the wait
# ends on the bound with that row's rendering: an all-absent seam, a
# cycle longer than the window, a run of new values over the bound, and a
# run of wide polls. One control per row, each asserting its own clause, so
# a fifth cause is a fifth row here with nothing over it.
_REUSED_LOG = _log_of([('1', POLL_WIDTH), ('2', 1), ('1', POLL_WIDTH)])
# A cycle LONGER than the window: thirteen new values and then the first
# again, so the repeat falls outside `SEQUENCE` and only a reading made
# over the whole run can see it.
_LONG_CYCLE_LOG = _log_of(
    [(str(n), POLL_WIDTH) for n in range(1, SEQUENCE + 2)]
    + [('1', 2)])
# A seam that is not wired at all, and one wired on its first poll only.
_UNWIRED_LOG = [{'poll': None, 'request': f'query {n}'}
                for n in range(2 * POLL_WIDTH + 2)]
_OVER_LOG = _log_of(
    [(str(marker), POLL_WIDTH) for marker in range(1, SEQUENCE + 1)]
    + [(str(SEQUENCE + 1), POLL_WIDTH + 1)])
_WIDE_LOG = _log_of([('1', POLL_WIDTH + 1), ('2', POLL_WIDTH + 1),
                     ('3', POLL_WIDTH + 1)])


def _refusal_for(entries, polls, what):
    """The message a synthetic log earns, or None if the wait succeeded."""
    message = None
    try:
        await_polls(_GrowingLog(entries), polls, _ScriptedChild(alive=True),
                    what)
    except AssertionError as exc:
        message = str(exc)
    return message


def test_a_repeated_boundary_is_named_as_a_re_used_index(tmp):
    """The one row the payload settles, and the control that keeps it so.

    A value that comes back after a DIFFERENT one has been published in
    between cannot be produced by a wide poll and cannot be produced by a
    sticky index: both of those leave the current value current. So this
    row carries a cause, and the two rows below it do not.
    """
    del tmp
    message = _refusal_for(_REUSED_LOG, 3, '3 poll(s)')
    assert message is not None, 'a re-used boundary did not fail'
    assert 'sequence 1, 2, 1,' in message, message
    assert ('a value came round again, so a poll published one it had '
            'published before and then another: a re-used index'
            in message), message


def test_a_cycle_longer_than_the_window_is_still_a_re_use(tmp):
    """The CALL SITE, which is the subject here, not `_reading`.

    `_reading` reads the whole run and the wait hands it the whole run;
    those are two claims, and only the second is a property of the CALL
    SITE - `_reading(sequence[:SEQUENCE])` there survives every control
    that calls `_reading` itself, because a function-level control cannot
    see what its caller passes. So this drives `await_polls` with a cycle
    whose repeat falls OUTSIDE the window: the
    window shows twelve new values and `... 2 more`, and a reading made
    over the window would call that a run of new values.
    """
    del tmp
    message = _refusal_for(_LONG_CYCLE_LOG, SEQUENCE + 4, '16 poll(s)')
    assert message is not None, 'a long cycle did not fail'
    assert ', ... 2 more' in message, message
    assert ('a value came round again, so a poll published one it had '
            'published before and then another: a re-used index'
            in message), message
    assert 'no value came round again' not in message, message


def test_a_poll_that_published_no_marker_is_an_unwired_seam(tmp):
    """The marker field is `os.environ.get(POLL_MARK)`, so it is `None` on
    any call made while the seam is not wired - which is a fact no other
    row can state, and one every other row was getting wrong. It is not
    an index that stopped advancing, and it is not a wide poll: nothing
    was ever published.
    """
    del tmp
    message = _refusal_for(_UNWIRED_LOG, 3, '3 poll(s)')
    assert message is not None, 'an unwired seam did not fail'
    assert ('a boundary the log carries as no marker at all is a seam that '
            'is not wired there' in message), message
    assert 'stopped advancing, and not a poll that cost more' in message, (
        message)
    assert 're-used index' not in message, message


def test_a_run_of_new_values_names_both_candidates(tmp):
    """New values over the bound, and the two things that can mean.

    A control per row proves each row in isolation, which cannot catch a
    row claiming more than the payload carries: this subject alone cannot
    tell a wide poll from a sticky index, so the clause has to name both.
    The control in the poll-index suite drives two plants with OPPOSITE
    causes through the real loop; this one says what the answer is.
    """
    del tmp
    message = _refusal_for(_OVER_LOG, SEQUENCE + 4, '16 poll(s)')
    assert message is not None, 'a run over the bound did not fail'
    assert ', ... 1 more' in message, message
    assert 'no value came round again' in message, message
    assert 'some poll cost more than that' in message, message
    assert 'republished the value already current' in message, message
    assert ('but which of two things that is, this log cannot say'
            in message), message


def test_a_run_of_wide_polls_names_both_candidates(tmp):
    """The same row from a three-poll subject, which is what a real wide
    poll looks like: three boundaries and no repetition anywhere."""
    del tmp
    message = _refusal_for(_WIDE_LOG, 4, '4 poll(s)')
    assert message is not None, 'a run of wide polls did not fail'
    assert 'sequence 1, 2, 3,' in message, message
    assert 'some poll cost more than that' in message, message
    assert 'republished the value already current' in message, message


def test_the_reading_covers_every_rendering_the_renderer_can_produce(tmp):
    """The rows are a function and this reads the mapping it is.

    The previous version carried a `row` column it never asserted, so
    collapsing two rows left it passing - a totality check wearing a
    mapping's clothes. The fixture says what the control checks.

    The long-run case is what the call-site control drives from outside:
    a cycle longer than the window repeats only outside the window, and
    a reading that tested the window put it in the no-repetition row.
    Every sequence here is one `poll_sequence` can actually emit, and
    the two `None` shapes are here as well as in their own control
    because the row they take is the one a reader would not guess.
    """
    del tmp
    new_run = [str(n) for n in range(1, SEQUENCE + 2)]
    for sequence, row in (([None], 0), (['1', None], 0),
                          (['1', '2', '1'], 1), (['1', '2', '3', '1'], 1),
                          (new_run, 2), (new_run + ['1'], 1),
                          (['1', '2', '3'], 2), (['1'], 3),
                          # No calls at all: no refusal can carry it, but
                          # `_reading` is given it here rather than
                          # excused, because totality is the claim.
                          ([], 2)):
        assert _reading(sequence) == row, (sequence, row)
    assert len(READINGS) == 4, READINGS


# A seam wired below the first request: the first entry carries a marker
# and every one after it carries none, which is what a real partial
# wiring produces. Rendering that by sorting it raises, so the wait still
# ends - but by a traceback that names the renderer instead of the seam,
# and carries no marker, no call count and no `what`.
#
# Its own name, and not `_UNWIRED_LOG`: that name is the ALL-ABSENT seam
# above, and a module body runs top to bottom, so sharing it left the
# control named for the all-absent shape receiving THIS one - and passing,
# because both route to the same row. The two shapes are one defect and
# two facts, and each has a control that drives its own.
_MIXED_SEAM_LOG = ([{'poll': '1', 'request': 'query one'}]
                   + [{'poll': None, 'request': f'query {index}'}
                      for index in range(2, 2 * POLL_WIDTH + 3)])


def test_the_poll_wait_names_a_partially_wired_seam(tmp):
    """A sequence mixing a marker with no marker is a verdict, not a
    `TypeError`.

    Both marks have to be in the message, because which is which is what
    tells a reader whether the watcher published once or never - a
    difference the call count cannot show. The wait is asked for three
    markers and the log carries two, so this ends on the bound rather than
    on a count the two-marker log would satisfy.
    """
    del tmp
    log = _GrowingLog(_MIXED_SEAM_LOG)
    child = _ScriptedChild(alive=True)
    message = None
    try:
        await_polls(log, 3, child, '3 poll(s)')
    except AssertionError as exc:
        message = str(exc)
    assert message is not None, 'a partially wired seam did not fail'
    assert "3 poll(s): the poll markers did not reach 3" in message, message
    assert '2 distinct, sequence 1, None,' in message, message
    assert f'within {len(_MIXED_SEAM_LOG)} gh call(s)' in message, message


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
