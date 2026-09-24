#!/usr/bin/env python3
"""The bridge fake's own gates, driven without the worker.

The harness is test_stream_backoff's. A target naming no origin is as
unaccounted for as a foreign one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_stream_backoff import BRIDGE, OTHER, SYNC, _drive  # noqa: E402

ELSEWHERE = 'https://elsewhere.example.com'
# The probe's two lists, whole: membership would not pin a count, and a
# URL is compared by equality, never asked about as a substring (CodeQL).
EXPECTED_REFUSED = [SYNC, 'POST /tabs']
EXPECTED_ORIGINS = [ELSEWHERE, '(no origin)']


def _probe():
    return _drive({'scenario': 'fake-probe', 'planned': [SYNC, OTHER],
                   'hosts': [BRIDGE]})


def test_a_declared_route_answers_200_the_first_time(tmp):
    del tmp
    assert _probe()['firstStatus'] == 200


def test_a_declared_route_is_refused_the_second_time(tmp):
    """Its own gate: M7 (`planned === 0`) left this green without it."""
    del tmp
    outcome = _probe()
    assert outcome['refused'] == EXPECTED_REFUSED, outcome
    assert outcome['secondStatus'] == 599, outcome


def test_an_undeclared_route_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['undeclaredStatus'] == 599, outcome
    assert outcome['refused'] == EXPECTED_REFUSED, outcome


def test_a_route_at_an_unpermitted_origin_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['badOriginStatus'] == 599, outcome
    assert outcome['badOrigins'] == EXPECTED_ORIGINS, outcome


def test_a_relative_url_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['relativeStatus'] == 599, outcome
    assert outcome['badOrigins'] == EXPECTED_ORIGINS, outcome


def test_an_unpermitted_origin_spends_no_route_allowance(tmp):
    """A refused origin spends no budget, so the next legitimate post
    keeps its 200."""
    del tmp
    outcome = _probe()
    assert outcome['afterBadOriginStatus'] == 200, outcome
    assert outcome['refused'] == EXPECTED_REFUSED, outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streamfakeoracle_')


if __name__ == '__main__':
    raise SystemExit(main())
