#!/usr/bin/env python3
"""What the per-poll figure reads, over the three timings a host can impose.

The figure is a property of a watcher's request sequence, so the controls
here drive the real helper with one sequence timed three ways and refuse
the windows that are not one number. None of them waits for a duration and
none asserts a wall-clock margin: a test that passes because the machine was
fast enough is the intermittency this measure was rewritten to remove, one
level down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _watcher_polls import await_polls, per_poll  # noqa: E402

# The base comment watcher's one poll: the four REST surfaces it reads, in
# the order the call log records them - the pull request itself first, its
# three comment surfaces after it. The order is what makes a run of them a
# poll and not a set, so a fixture that reordered it would leave an
# order-sensitive measure uncaught.
BASE_POLL = ['repos/o/r/pulls/195', 'repos/o/r/pulls/195/reviews',
             'repos/o/r/pulls/195/comments', 'repos/o/r/issues/195/comments']

# A double that never terminates turns a missing liveness escape into a job
# timeout, so the escape's own control ends by name instead.
RUNAWAY_CALL_LIMIT = 1000


class _SilentLog:
    """A call log that never establishes a width, and says so by name."""

    def __init__(self):
        self.reads = 0

    def calls(self):
        self.reads += 1
        if self.reads > RUNAWAY_CALL_LIMIT:
            raise AssertionError('log double exceeded call limit')
        return []


class _ExitedChild:
    """A child that has exited: it can make no further call."""

    def __init__(self, output):
        self._output = output

    def alive(self):
        return False

    def captured(self):
        return self._output


def _logged(requests, gaps=None, step=0.01):
    """The call log a watcher appends, timed by its inter-call gaps.

    A gap is the wait between two consecutive calls, so a poll the host
    stretched reaches the log as one long gap and nothing else.
    """
    calls, t = [], 0.0
    for index, request in enumerate(requests):
        if index:
            t += step if gaps is None else gaps[index - 1]
        calls.append({'request': request, 't': t})
    return calls


# Two whole polls of that sequence, timed three ways: evenly, with one long
# gap inside a poll, and with every intra-poll gap long. The one long gap in
# the first row is the boundary between the two polls.
_TIMINGS = (
    [.01, .01, .01, 2, .01, .01, .01],
    [.01, .01, 3.0, 2, .01, .01, .01],
    [3.0, .01, 3.0, 2, 3.0, .01, .01],
)


def test_the_figure_does_not_move_when_one_poll_is_slow(tmp):
    """What one poll costs is a property of the request sequence.

    A host that stretches one call inside a poll moves the timestamps and
    nothing else, so every timing of one sequence reads the same. The
    expected width is read off the sequence itself, so this control cannot
    pass by quoting back a constant its own fixture also carries.
    """
    del tmp
    figures = [per_poll(_logged(BASE_POLL * 2, gaps)) for gaps in _TIMINGS]
    assert figures == [len(BASE_POLL)] * len(_TIMINGS), figures


def _refusal(requests):
    """The message a refused window reports, or None when it answered."""
    try:
        per_poll(_logged(requests))
    except AssertionError as exc:
        return str(exc)
    return None


def test_a_window_that_establishes_no_width_is_refused(tmp):
    """A number reported where there is none still reads as a measurement.

    A single call, two different calls, a warm-up poll that differs from
    the polls after it, and a poll that changes shape once a width is
    established are four refusals, each naming what it recorded.
    """
    del tmp
    rows = (
        (BASE_POLL[:1], 'a single call'),
        (BASE_POLL[:2], 'two different requests'),
        (['warmup'] + BASE_POLL * 2, 'a warm-up poll of another shape'),
        (BASE_POLL * 2 + ['other', 'shape', 'entirely', 'now'],
         'a poll that changes shape'),
    )
    for requests, why in rows:
        message = _refusal(requests)
        assert message is not None, f'{why} was reported as a number'
        assert requests[0][:40] in message, (why, message)


def test_a_trailing_partial_poll_is_trimmed_rather_than_refused(tmp):
    """The log is read some time after the wait ends, so a ninth call is
    already on disk. A window stopping part-way through a poll is trimmed
    to whole polls; refusing it trades this flake for a sharper one.
    """
    del tmp
    for extra in (1, 2, 3):
        trailing = BASE_POLL * 2 + BASE_POLL[:extra]
        assert per_poll(_logged(trailing)) == len(BASE_POLL), (
            extra, trailing)


def test_the_poll_wait_gives_up_by_name_when_the_child_exits(tmp):
    """A child that has exited ends the wait, and the failure says which.

    This branch is the only thing between a broken watcher and a wait that
    runs to the job's limit, so the control drives it with a child that can
    make no further call and reads the wait's own name and the child's
    output back out of the failure. The log double ends by name past its own
    limit, so a wait that ignored the child would fail here rather than at
    the job's timeout.
    """
    del tmp
    child = _ExitedChild('gh: no fixture carries the query')
    message = None
    try:
        await_polls(_SilentLog(), child, 'the comment watcher to poll twice')
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError('a wait on an exited child did not fail')
    assert message is not None
    assert 'the comment watcher to poll twice' in message, message
    assert 'gh: no fixture carries the query' in message, message


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchpolls_')


if __name__ == '__main__':
    raise SystemExit(main())
