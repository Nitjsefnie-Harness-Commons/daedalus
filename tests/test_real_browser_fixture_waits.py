#!/usr/bin/env python3
"""The real-browser fixture's wait clocks, pinned without a browser.

`_reached_worker` answers two different questions on one loop, and the
fixture's production deadlines belong to only one of them. A worker that has
answered no evaluation at all is the machine-contention question, and its
thirty seconds is what keeps a cold runner from being misread as a broken
extension. A worker that answered and then never finished loading is the
extension's own behaviour, and that wait is the one a classification test may
shorten. The tests here stand in for the worker and for time itself, so the
arrival instant of each verdict is asserted as arithmetic rather than
measured.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402


class _FakeClock:
    """A time module whose only moving part is `sleep`."""

    def __init__(self, start):
        self.now = float(start)
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


_ANSWERED_NEVER_READY = (
    None, True, 'the worker answered without its declarations')
_NEVER_ANSWERED = (None, False, 'no service worker target is listed')


def _run_reached_worker(clock, patience, answer):
    """Run `_reached_worker` under `clock`; return (verdict, poll times)."""
    polls = []

    def ready_worker(node, workers):
        del node, workers
        polls.append(clock.now)
        return answer

    def list_targets(port):
        del port
        return []

    with mock.patch.object(_realbrowser, 'ready_worker', ready_worker), \
            mock.patch.object(
                _realbrowser, '_devtools_targets', list_targets), \
            mock.patch.object(_realbrowser, 'time', clock):
        try:
            target = _realbrowser._reached_worker(
                'node-for-control', '/controlled/chromium',
                [{'webSocketDebuggerUrl': 'ws://worker'}], '9222',
                'background.js', patience=patience)
        except Exception as why:  # noqa: BLE001
            return why, polls
    raise AssertionError(
        f'the wait returned {target!r} instead of raising a verdict')


def test_answered_worker_that_never_readies_fails_at_its_own_patience(tmp):
    """Contact is demonstrated, so the verdict is the extension's own."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_reached_worker(clock, 2.0, _ANSWERED_NEVER_READY)
    assert outcome.__class__ is AssertionError, outcome
    assert 'service worker' in str(outcome), outcome
    assert polls == [1000.0, 1000.5, 1001.0, 1001.5], polls
    assert clock.now == 1002.0, clock.now


def test_a_worker_that_never_answers_skips_at_the_full_deadline(tmp):
    """An unanswered worker is the machine's; patience cannot shorten it."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_reached_worker(clock, 2.0, _NEVER_ANSWERED)
    skipped = _realbrowser.BrowserEnvironmentSkipped
    assert outcome.__class__ is skipped, outcome
    message = str(outcome)
    assert 'never let the extension worker be reached' in message, outcome
    assert clock.now == 1030.0, clock.now
    assert len(polls) == 60, polls


def test_the_default_patience_still_fails_an_answered_worker(tmp):
    """Default patience leaves every classification where it is today."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_reached_worker(clock, 30.0, _ANSWERED_NEVER_READY)
    assert outcome.__class__ is AssertionError, outcome
    assert 'service worker' in str(outcome), outcome
    assert clock.now == 1030.0, clock.now
    assert len(polls) == 60, polls


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='fixturewaits_')


if __name__ == '__main__':
    raise SystemExit(main())
