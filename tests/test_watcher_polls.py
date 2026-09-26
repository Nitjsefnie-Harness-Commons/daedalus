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
from _watcher_polls import per_poll  # noqa: E402

# The base comment watcher's one poll: the four REST surfaces it reads, in
# order. The order is what makes a run of them a poll and not a set.
BASE_POLL = ['repos/o/r/pulls/195/reviews', 'repos/o/r/pulls/195/comments',
             'repos/o/r/issues/195/comments', 'repos/o/r/pulls/195']


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
    for extra in (1, 3):
        trailing = BASE_POLL * 2 + BASE_POLL[:extra]
        assert per_poll(_logged(trailing)) == len(BASE_POLL), (
            extra, trailing)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchpolls_')


if __name__ == '__main__':
    raise SystemExit(main())
