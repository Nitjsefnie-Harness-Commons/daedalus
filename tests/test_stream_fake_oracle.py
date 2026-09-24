#!/usr/bin/env python3
"""The bridge fake's own gates, driven without the worker.

The harness is test_stream_backoff's; the asserts below are its spec.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_stream_backoff import BRIDGE, OTHER, SYNC, _drive  # noqa: E402


def _probe():
    return _drive({'scenario': 'fake-probe', 'planned': [SYNC, OTHER],
                   'hosts': [BRIDGE]})


def test_a_declared_route_answers_200_the_first_time(tmp):
    del tmp
    assert _probe()['firstStatus'] == 200


def test_a_declared_route_is_refused_the_second_time(tmp):
    """The count limit is a gate of its own, not a side effect of the
    undeclared-route arm: SYNC is declared once and still refuses."""
    del tmp
    outcome = _probe()
    assert outcome['secondStatus'] == 599, outcome
    assert outcome['refused'][0] == SYNC, outcome


def test_an_undeclared_route_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['undeclaredStatus'] == 599, outcome
    assert outcome['refused'][1] == 'POST /tabs', outcome


def test_a_route_at_an_unpermitted_origin_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['badOriginStatus'] == 599, outcome
    assert outcome['badOrigins'][0] == 'https://elsewhere.example.com', outcome


def test_a_relative_url_is_refused(tmp):
    """No origin is not a permitted origin: a relative target names no
    bridge, so it is as unaccounted for as a foreign one."""
    del tmp
    outcome = _probe()
    assert outcome['relativeStatus'] == 599, outcome
    assert '(no origin)' in outcome['badOrigins'], outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streamfakeoracle_')


if __name__ == '__main__':
    raise SystemExit(main())
