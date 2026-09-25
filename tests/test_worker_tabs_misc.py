#!/usr/bin/env python3
"""The two handlers in extension/worker/tabs.js that call no chrome API.

handleFetchTimings reads the _fetchTimings ring and the _hasNativeToBase64
const the boot evaluated; handleExtReload posts its answer and then schedules
chrome.runtime.reload through a timer. Each test asserts the posted
postResult payload (the handler's answer) and the chrome calls it made, so a
handler that returned a right answer through the wrong calls fails.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _tabs_harness import apis, command, run_tabs  # noqa: E402

RUNTIME_RELOAD = 'runtime.reload'

# Read from extension/background.js (`const VERSION = '0.26.1';`). Pinned as
# an independent literal, NOT the harness readout: comparing the posted
# version to the readout would move with a real bump and stay green forever.
VERSION = '0.26.1'

# Distinctive ring entries. These are world state the handler reads back out
# of _fetchTimings, never values it received and must echo, so a substituted
# or fabricated entry is observable.
TIMINGS_2 = [
    {'url': 'https://alpha.example.com/a', 'method': 'GET',
     'status': 200, 'bodySize': 11, 'ms_total': 12.5,
     'ts': 1700000000000},
    {'url': 'https://beta.example.com/b', 'method': 'POST',
     'status': 503, 'bodySize': 0, 'ms_total': 34.0,
     'ts': 1700000001000},
]
TIMINGS_3 = TIMINGS_2 + [
    {'url': 'https://gamma.example.com/c', 'method': 'GET',
     'status': 200, 'bodySize': 7, 'ms_total': 5.5,
     'ts': 1700000002000},
]


def _posted_results(outcome):
    """Every posted result, in dispatch order, each a clean extension post."""
    assert all(o == {'settled': 'resolved'}
               for o in outcome['outcomes']), outcome
    assert len(outcome['posted']) == len(outcome['outcomes']), outcome
    results = []
    for post in outcome['posted']:
        assert post['error'] is None, post
        assert post['tabId'] == 'extension', post
        results.append(post['result'])
    return results


def _result(outcome):
    results = _posted_results(outcome)
    assert len(results) == 1, outcome
    return results[0]


# ─── handleFetchTimings ───

def test_fetch_timings_posts_the_empty_ring(tmp):
    del tmp
    outcome = run_tabs([command(type='fetch-timings')],
                       hasNativeToBase64=True)
    result = _result(outcome)
    assert result['timings'] == [], outcome
    assert result['count'] == 0, outcome
    # Pinned realm carries the native toBase64, so the boot's own const is
    # true; a hardcoded or inverted answer dies here.
    assert result['hasNativeToBase64'] is True, outcome
    assert outcome['hasNativeToBase64'] is True, outcome


def test_fetch_timings_posts_the_populated_ring(tmp):
    del tmp
    outcome = run_tabs([command(type='fetch-timings')],
                       fetchTimings=TIMINGS_2,
                       hasNativeToBase64=False)
    result = _result(outcome)
    assert result['timings'] == TIMINGS_2, outcome
    assert result['count'] == 2, outcome
    # Realm pinned without the native toBase64, so the const is false.
    assert result['hasNativeToBase64'] is False, outcome


def test_fetch_timings_count_agrees_with_the_timings_it_ships(tmp):
    del tmp
    outcome = run_tabs([command(type='fetch-timings')],
                       fetchTimings=TIMINGS_3)
    result = _result(outcome)
    assert result['timings'] == TIMINGS_3, outcome
    assert result['count'] == len(result['timings']) == 3, outcome


def test_fetch_timings_reset_clears_the_ring_but_ships_the_copy(tmp):
    del tmp
    outcome = run_tabs([
        command(type='fetch-timings', reset=True),
        command(type='fetch-timings'),
    ], fetchTimings=TIMINGS_2)
    reads = _posted_results(outcome)
    # The copy is taken before the clear, so the reset still ships the ring.
    assert reads[0]['timings'] == TIMINGS_2, outcome
    assert reads[0]['count'] == 2, outcome
    # The next read sees the cleared ring: the reset actually emptied it.
    assert reads[1]['timings'] == [], outcome
    assert reads[1]['count'] == 0, outcome


# ─── handleExtReload ───

def test_ext_reload_defers_the_reload_and_posts_the_version(tmp):
    del tmp
    # Timers inert (runTimers unset): the posted answer still lands, and no
    # runtime.reload call is recorded because the reload rides a timer. A
    # handler that called chrome.runtime.reload directly dies on the apis
    # assertion.
    outcome = run_tabs([command(type='ext-reload')])
    assert _result(outcome) == {
        'reloading': True, 'version': VERSION}, outcome
    assert apis(outcome, RUNTIME_RELOAD) == [], outcome


def test_ext_reload_runs_the_reload_when_the_timer_fires(tmp):
    del tmp
    # The stand-in runs the deferred callback in the same tick, so the
    # reload is observable with no wall-clock margin and no sleep.
    outcome = run_tabs([command(type='ext-reload')], runTimers=True)
    assert _result(outcome) == {
        'reloading': True, 'version': VERSION}, outcome
    assert apis(outcome, RUNTIME_RELOAD) == [
        [RUNTIME_RELOAD, []]], outcome
    # The version the boot actually installed is the pinned literal.
    assert outcome['version'] == VERSION, outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='tabsmisc_')


if __name__ == '__main__':
    raise SystemExit(main())
