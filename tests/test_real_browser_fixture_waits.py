#!/usr/bin/env python3
"""The real-browser fixture's wait clocks, pinned without a browser.

`_reached_worker` answers two different questions on one loop, and the
fixture's production deadlines belong to only one of them. A worker that has
answered no evaluation at all is the machine-contention question, and its
thirty seconds is what keeps a cold runner from being misread as a broken
extension. A worker that answered and then never finished loading is the
extension's own behaviour, and that wait is the one a classification test may
shorten. The page-ready loop in `_configured_fixture` is on the same side of
that line and its timeout is pinned here too. The tests stand in for the
worker and for time itself, so the arrival instant of each verdict is
asserted as arithmetic rather than measured; the exact poll lists couple to
the fixture's own poll constants (0.5 s in `_reached_worker`, 0.25 s in the
page-ready loop), so a legitimate change to either interval rewrites them.
"""
import contextlib
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
_ANSWER_THEN_VANISHED = (
    None, False, 'the worker answered once and then vanished')
_NEVER_ANSWERED = (None, False, 'no service worker target is listed')


def _run_reached_worker(clock, patience, answers):
    """Run `_reached_worker` under `clock`; return (verdict, poll times).

    `answers` is spent one entry per poll, the last entry repeating.
    """
    polls = []
    pending = list(answers)

    def ready_worker(node, workers):
        del node, workers
        polls.append(clock.now)
        return pending[0] if len(pending) == 1 else pending.pop(0)

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
        except (_realbrowser.BrowserEnvironmentSkipped,
                AssertionError) as why:
            return why, polls
    raise AssertionError(
        f'the wait returned {target!r} instead of raising a verdict')


def _run_configured_fixture(clock, page_ready_timeout):
    """Run `_configured_fixture` under `clock`; return (verdict, polls).

    The configure step answers on its first attempt, so the page-ready loop
    is the only one the control drives, and `polls` records the instant of
    every `__evalPageReady` evaluation.
    """
    polls = []

    def evaluate(node, target, expression):
        del node
        if target != 'ws://page':
            return True
        assert expression == 'globalThis.__evalPageReady === true', expression
        polls.append(clock.now)
        return False

    def navigate(node, target, method, params):
        del node, method, params
        assert target == 'ws://page', target
        return {}

    def answer_worker(node, workers):
        del node, workers
        return None, False, 'the configure loop must not need a worker'

    def list_targets(port):
        del port
        return []

    patchers = (
        mock.patch.object(_realbrowser, 'cdp_eval', evaluate),
        mock.patch.object(_realbrowser, 'cdp_call', navigate),
        mock.patch.object(_realbrowser, 'ready_worker', answer_worker),
        mock.patch.object(_realbrowser, '_devtools_targets', list_targets),
        mock.patch.object(_realbrowser, 'time', clock),
    )
    with contextlib.ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        yielded = []
        try:
            for item in _realbrowser._configured_fixture(
                    'node-for-control', 'http://127.0.0.1:1', 'controltoken',
                    'ws://worker', '9222', 'background.js', 'ws://page',
                    'http://127.0.0.1:2/ready.html',
                    page_ready_timeout=page_ready_timeout):
                yielded.append(item)
        except AssertionError as outcome:
            assert not yielded, yielded
            return outcome, polls
    raise AssertionError('the page-ready loop never raised a verdict')


def test_answered_worker_that_never_readies_fails_at_its_own_patience(tmp):
    """Contact is demonstrated, so the verdict is the extension's own."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_reached_worker(
        clock, 2.0, [_ANSWERED_NEVER_READY])
    assert outcome.__class__ is AssertionError, outcome
    assert 'service worker' in str(outcome), outcome
    assert polls == [1000.0, 1000.5, 1001.0, 1001.5], polls
    assert clock.now == 1002.0, clock.now


def test_a_worker_that_never_answers_skips_at_the_full_deadline(tmp):
    """An unanswered worker is the machine's; patience cannot shorten it."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_reached_worker(clock, 2.0, [_NEVER_ANSWERED])
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
    outcome, polls = _run_reached_worker(
        clock, 30.0, [_ANSWERED_NEVER_READY])
    assert outcome.__class__ is AssertionError, outcome
    assert 'service worker' in str(outcome), outcome
    assert clock.now == 1030.0, clock.now
    assert len(polls) == 60, polls


def test_a_worker_that_answers_once_and_vanishes_still_fails(tmp):
    """`answered` is sticky: the verdict belongs to the first contact."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_reached_worker(
        clock, 2.0, [_ANSWERED_NEVER_READY, _ANSWER_THEN_VANISHED])
    assert outcome.__class__ is AssertionError, outcome
    assert 'service worker' in str(outcome), outcome
    assert polls == [1000.0, 1000.5, 1001.0, 1001.5], polls
    assert clock.now == 1002.0, clock.now


def test_the_page_ready_deadline_comes_from_page_ready_timeout(tmp):
    """`_configured_fixture` waits the caller's timeout, not a literal."""
    del tmp
    clock = _FakeClock(1000.0)
    outcome, polls = _run_configured_fixture(clock, 2.0)
    assert outcome.__class__ is AssertionError, outcome
    assert '__evalPageReady' in str(outcome), outcome
    assert polls == [1000.0, 1000.25, 1000.5, 1000.75, 1001.0, 1001.25,
                     1001.5, 1001.75], polls
    assert clock.now == 1002.0, clock.now


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='fixturewaits_')


if __name__ == '__main__':
    raise SystemExit(main())
