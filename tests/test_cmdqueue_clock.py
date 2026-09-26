#!/usr/bin/env python3
"""Virtual-clock controls for test-side command queue readers."""
import contextlib
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


@contextlib.contextmanager
def _wall_time_past_limit(seconds=6.0):
    """Stand in for a machine slow enough to spend _RUNAWAY_WALL in a loop.

    The first read is the guard's own start mark; every later read reports the
    machine `seconds` past it, so a wall bound is decided the moment it is
    consulted rather than by how long this box happens to take.
    """
    original = _cmdqueue.time.perf_counter
    reads = [0]

    def perf_counter():
        reads[0] += 1
        return 0.0 if reads[0] == 1 else seconds

    _cmdqueue.time.perf_counter = perf_counter
    try:
        yield
    finally:
        _cmdqueue.time.perf_counter = original


def test_virtual_clock_allows_no_op_sleeps_before_progress(_tmp):
    with _virtual_cmdqueue_clock(wall_budget=None) as (clock, _events, origin):
        for _ in range(2000):
            clock.sleep(0.0)
        positive = _cmdqueue.POLL_DELAY
        clock.sleep(positive)
    assert clock.monotonic() == origin + positive, (
        clock.monotonic(), origin, positive)


def test_virtual_clock_accumulates_sub_ulp_sleep_requests(_tmp):
    with _virtual_cmdqueue_clock(wall_budget=None) as (clock, _events, origin):
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
    with _virtual_cmdqueue_clock(wall_budget=None) as (clock, events, origin):
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
        with _virtual_cmdqueue_clock(
                max_sleeps, wall_budget=None) as (clock, events, origin):
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
    with _virtual_cmdqueue_clock(wall_budget=None) as (clock, events, origin):
        for _ in range(parts):
            clock.sleep(requested)
    assert len(events) == parts, len(events)
    assert clock.monotonic() == origin + _cmdqueue.POLL_DELAY, (
        clock.monotonic(), origin)


def test_virtual_clock_default_guard_stops_zero_time_runaway(_tmp):
    failure = None
    tripped_at = None
    expected_sleeps = 200_000
    with _virtual_cmdqueue_clock(wall_budget=None) as (clock, events, origin):
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


def test_virtual_clock_stops_nonzero_observable_stall(_tmp):
    failure = None
    tripped_at = None
    expected_sleeps = 200_000
    smallest_positive = math.ulp(0.0)
    with _virtual_cmdqueue_clock(wall_budget=None) as (clock, events, origin):
        try:
            for tripped_at in range(1, 2 * expected_sleeps + 1):
                clock.sleep(smallest_positive)
        except AssertionError as caught:
            failure = caught
    assert isinstance(failure, AssertionError), failure
    assert tripped_at == expected_sleeps + 1, tripped_at
    assert len(events) == expected_sleeps, len(events)
    assert clock.monotonic() == origin, (clock.monotonic(), origin)
    message = str(failure).lower()
    assert 'virtual clock' in message, message
    assert 'progress' in message, message
    assert _has_numeric_token(message, str(expected_sleeps)), message


def test_counted_runaway_outruns_real_wall_time(_tmp):
    """The no-progress guard is the one that stops a counted runaway.

    This drives the control issue 1190 is about under the conditions that
    exposed it — a machine slow enough to spend the module's five-second wall
    bound inside its 200,001 sleeps, where the wall guard raised first,
    appended nothing, and left the control reporting a bare trip count. The
    machine is this double's, so the verdict is the control's and not this
    box's; the control supplies the fix, so what is pinned here is that the
    fix is applied to the control the issue names.
    """
    with _wall_time_past_limit():
        try:
            test_virtual_clock_stops_nonzero_observable_stall(_tmp)
        except AssertionError as counted:
            raise AssertionError(
                f'the counted control failed on a slow machine: {counted}'
            ) from counted


def test_virtual_clock_bounds_wait_by_real_wall_time(_tmp):
    original_perf_counter = _cmdqueue.time.perf_counter
    samples = iter((100.0, 106.0))

    def perf_counter():
        return next(samples)

    failure = None
    _cmdqueue.time.perf_counter = perf_counter
    try:
        with _virtual_cmdqueue_clock() as (clock, events, _origin):
            try:
                clock.sleep(2 ** -33)
            except AssertionError as caught:
                failure = caught
    finally:
        _cmdqueue.time.perf_counter = original_perf_counter
    assert isinstance(failure, AssertionError), failure
    assert events == [], events
    message = str(failure).lower()
    assert 'virtual clock' in message, message
    assert 'wall' in message, message
    assert 'bound' in message, message


def test_per_call_wall_budget_replaces_the_module_default(_tmp):
    """A caller's own budget is the bound that applies, slow machine or not."""
    positive = _cmdqueue.POLL_DELAY
    with _wall_time_past_limit():
        with _virtual_cmdqueue_clock(
                wall_budget=3600.0) as (clock, events, origin):
            clock.sleep(positive)
        assert events == [('sleep', positive)], events
        assert clock.monotonic() == origin + positive, (
            clock.monotonic(), origin, positive)


def test_omitted_wall_budget_is_the_module_default(_tmp):
    """The other margin of the module bound: quiet below it, tripping above.

    The wall bound control above trips on six seconds of reported elapsed
    time; this one must stay quiet at four and a half, so neither margin of
    the five-second bound has moved.
    """
    positive = _cmdqueue.POLL_DELAY
    with _wall_time_past_limit(4.5):
        with _virtual_cmdqueue_clock() as (clock, events, origin):
            clock.sleep(positive)
        assert events == [('sleep', positive)], events
        assert clock.monotonic() == origin + positive, (
            clock.monotonic(), origin, positive)


def test_virtual_clock_rejects_non_finite_sleep_requests(_tmp):
    for requested in (math.nan, math.inf, -math.inf):
        failure = None
        with _virtual_cmdqueue_clock(wall_budget=None) as (clock, events,
                                                           origin):
            try:
                clock.sleep(requested)
            except ValueError as caught:
                failure = caught
        assert isinstance(failure, ValueError), (requested, failure)
        assert events == [], (requested, events)
        assert clock.monotonic() == origin, (requested, clock.monotonic())


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_clock_'))
