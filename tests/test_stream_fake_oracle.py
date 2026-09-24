#!/usr/bin/env python3
"""The bridge fake's own refusal oracle, separate from the worker.

tests/test_stream_backoff.py's fake answers an unmodelled non-stream
request with a 599 and records it. This suite pins that property by
driving the fake directly — a planned route answers 200, an unplanned one
answers 599, and both land in the recording — so the oracle is proven
without a worker path in between.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_stream_backoff import SYNC, _drive  # noqa: E402


def test_the_fake_refuses_an_unplanned_request(tmp):
    del tmp
    outcome = _drive({'scenario': 'fake-probe', 'planned': [SYNC],
                      'plannedRoute': '/sync-tabs',
                      'unplannedRoute': '/tabs'})
    assert outcome['plannedStatus'] == 200, outcome
    assert outcome['unplannedStatus'] == 599, outcome
    assert outcome['refused'] == ['POST /tabs'], outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streamfakeoracle_')


if __name__ == '__main__':
    raise SystemExit(main())
