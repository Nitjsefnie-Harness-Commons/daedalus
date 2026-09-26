"""What one poll of a watcher costs, decided by the request sequence alone.

A watcher repeats one ordered sequence of requests every poll, so the width
of the sequence the recorded window repeats IS the per-poll figure. The
clock decides nothing: a host that stretches a call inside a poll moves the
timestamps and nothing else, and a figure that read those gaps reported
fewer calls per poll the slower the machine got.

Not a suite itself - run_tests.py only loads `test_*.py`. Its controls live
in tests/test_watcher_polls.py.
"""
import time

from _watcher_waits import POLL


def repeated_width(requests):
    """The shortest prefix of the window the window repeats in full.

    Only the first two complete repetitions decide the width: whether the
    rest of the window still agrees at that width is `per_poll`'s to refuse
    on, and a window too short to hold two repetitions has no width yet.
    """
    for width in range(1, len(requests) // 2 + 1):
        if requests[:width] == requests[width:2 * width]:
            return width
    return None


def _refuse(requests, why):
    """The refusal a window that is not one number gets.

    The recorded sequence is in the message because a figure read off a log
    nobody can check is a claim, and a helper that answered anyway would
    satisfy every assertion over it.
    """
    return AssertionError(f'no poll width to report: {why}; recorded '
                          f'{[request[:40] for request in requests]}')


def per_poll(calls):
    """Calls one poll spends: the width of the sequence the window repeats.

    A trailing partial poll is trimmed, because the log is read some time
    after the wait returns and a ninth call is already on disk. A window
    that establishes no width, or whose later polls change shape, is
    reported rather than averaged, because neither is one number.
    """
    requests = [call['request'] for call in calls]
    width = repeated_width(requests)
    if width is None:
        raise _refuse(requests, 'no prefix of it repeats in full')
    for start in range(2 * width, len(requests) - width + 1, width):
        if requests[start:start + width] != requests[:width]:
            raise _refuse(requests,
                          f'the poll at call {start} is another shape')
    polls, _ = divmod(len(requests), width)
    # The numerator counts the whole polls alone: a trailing partial one is
    # trimmed, because the log is read some time after the wait returns.
    whole = polls * width
    return whole // polls


def await_polls(fake, child, what):
    """The call log, once it holds two complete repetitions of one poll.

    The liveness contract of `waits.await_calls`, restated rather than
    inherited silently: the log is a file the children append to, so it is
    polled; a child that has exited can make no further call, which ends the
    wait with its own output in the failure; and no time bound sits on the
    passing path. Two repetitions rather than one, so the width the
    measurement then reads is confirmed rather than a single coincidence.
    """
    while True:
        calls = fake.calls()
        if repeated_width([call['request'] for call in calls]) is not None:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
        time.sleep(POLL)
