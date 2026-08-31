#!/usr/bin/env python3
"""Virtual-clock controls for test-side command queue readers."""
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cmdqueue  # noqa: E402
from _cmdqueue_faults import (  # noqa: E402
    _RUNAWAY_ELAPSED,
    _virtual_cmdqueue_clock,
)


def _has_numeric_token(message, value):
    pattern = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    return value in re.findall(pattern, message)


def test_numeric_token_requires_the_whole_literal(_tmp):
    cases = (
        ('50.000', '50.000s', ('150.000', '-50.000', '+50.000',
                               '50.000e1', '50.000E+1')),
        ('10', '10 sleeps', ('100', '-10', '+10', '10e1', '10E-1')),
        ('0', '0 sleeps', ()))
    for value, accepted, rejected in cases:
        assert _has_numeric_token(accepted, value), (accepted, value)
        assert not any(
            _has_numeric_token(item, value) for item in rejected), rejected


def _identifies_runaway(message):
    return (
        'runaway' in message
        or ('elapsed' in message
            and any(word in message for word in ('guard', 'limit')))
        or ('origin' in message
            and any(word in message for word in ('past', 'beyond'))))


def test_virtual_clock_allows_no_op_sleeps_before_progress(_tmp):
    with _virtual_cmdqueue_clock() as (clock, _events, origin):
        for _ in range(2000):
            clock.sleep(0.0)
        positive = _cmdqueue.POLL_DELAY
        clock.sleep(positive)
    assert clock.monotonic() == origin + positive, (
        clock.monotonic(), origin, positive)


def test_virtual_clock_accumulates_sub_ulp_sleep_requests(_tmp):
    with _virtual_cmdqueue_clock() as (clock, _events, origin):
        requested = math.ulp(origin)
        third = requested / 3
        clock.sleep(third)
        clock.sleep(third)
        clock.sleep(requested - 2 * third)
    assert clock.monotonic() == origin + requested, (
        clock.monotonic(), origin, requested)


def test_virtual_clock_bounds_a_wait_that_never_ends(_tmp):
    failure = None
    tripped_at = None
    # Bounded so that removing the guard fails this control instead of
    # hanging it; the bound is twice the sleeps the guard needs to trip.
    attempts = int(2000 * _RUNAWAY_ELAPSED / _cmdqueue.POLL_DELAY / 1000)
    with _virtual_cmdqueue_clock() as (clock, events, origin):
        try:
            for tripped_at in range(1, attempts + 1):
                clock.sleep(_cmdqueue.POLL_DELAY)
        except AssertionError as caught:
            failure = caught
    expected_sleeps = int(_RUNAWAY_ELAPSED / _cmdqueue.POLL_DELAY)
    elapsed = clock.monotonic() - origin
    assert isinstance(failure, AssertionError), failure
    assert tripped_at == expected_sleeps, (tripped_at, expected_sleeps)
    assert events == [
        ('sleep', _cmdqueue.POLL_DELAY)] * (expected_sleeps - 1), events
    expected_elapsed = (expected_sleeps - 1) * _cmdqueue.POLL_DELAY
    assert abs(elapsed - expected_elapsed) < 1e-6, elapsed
    message = str(failure).lower()
    assert 'virtual clock' in message, message
    assert _identifies_runaway(message), message
    assert _has_numeric_token(
        message, f'{_RUNAWAY_ELAPSED:.3f}'), message


def test_virtual_clock_keeps_explicit_sleep_ceilings(_tmp):
    def refusal(max_sleeps):
        failure = None
        tripped_at = None
        with _virtual_cmdqueue_clock(max_sleeps) as (clock, events, origin):
            try:
                for tripped_at in range(max_sleeps + 1):
                    clock.sleep(0.0)
            except AssertionError as caught:
                failure = caught
        return failure, tripped_at, events, clock.monotonic() - origin

    for max_sleeps in (0, 10):
        failure, tripped_at, events, elapsed = refusal(max_sleeps)
        assert isinstance(failure, AssertionError), failure
        assert tripped_at == max_sleeps, (tripped_at, max_sleeps)
        assert events == [('sleep', 0.0)] * max_sleeps, events
        assert elapsed == 0.0, elapsed
        message = str(failure).lower()
        assert 'virtual clock' in message, message
        assert 'sleep' in message, message
        assert any(word in message for word in (
            'ceiling', 'maximum', 'limit', 'exceeded')), message
        assert _has_numeric_token(message, str(max_sleeps)), message


def test_virtual_clock_default_guard_ignores_positive_sleep_count(_tmp):
    parts = 100_001
    requested = _cmdqueue.POLL_DELAY / parts
    with _virtual_cmdqueue_clock() as (clock, events, origin):
        for _ in range(parts):
            clock.sleep(requested)
    assert len(events) == parts, len(events)
    assert clock.monotonic() == origin + _cmdqueue.POLL_DELAY, (
        clock.monotonic(), origin)


def test_virtual_clock_default_guard_stops_zero_time_runaway(_tmp):
    failure = None
    tripped_at = None
    expected_sleeps = 200_000
    with _virtual_cmdqueue_clock() as (clock, events, origin):
        try:
            for tripped_at in range(expected_sleeps + 1):
                clock.sleep(0.0)
        except AssertionError as caught:
            failure = caught
    assert isinstance(failure, AssertionError), failure
    assert tripped_at == expected_sleeps, (tripped_at, expected_sleeps)
    assert len(events) == expected_sleeps, len(events)
    assert clock.monotonic() == origin, (clock.monotonic(), origin)
    message = str(failure).lower()
    assert 'virtual clock' in message, message
    assert 'progress' in message, message
    assert _has_numeric_token(message, str(expected_sleeps)), message


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_clock_'))
