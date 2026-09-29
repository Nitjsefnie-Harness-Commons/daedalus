"""The failure a call that was supposed to raise actually raised.

A control that expects a refusal reads the exception rather than the
verdict, because a control that passes vacuously is the one failure
these controls exist to prevent. Two suites asserted that shape
separately, over the same two exception types.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _call_failure(call):
    try:
        call()
    except (AssertionError, _util.Skipped) as failure:
        return failure
    raise AssertionError('controlled call did not raise')
