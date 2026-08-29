#!/usr/bin/env python3
"""Pins for the bounded teardown retry behind the shared suite runner."""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _teardown  # noqa: E402
import _util  # noqa: E402

# One sleep per failed attempt but the last, 0.05s doubling to a 2.0s cap.
FULL_SCHEDULE = [0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 2.0]
MAX_ATTEMPTS = 8


def _sleep_recorder(sleeps):
    def sleep(delay):
        sleeps.append(delay)
    return sleep


def _run_passing_suite(test):
    """Run one trivial test through `_util.runner`, output captured."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = _util.runner([test])
    return code, out.getvalue()


def test_transient_refusal_is_retried_until_it_clears(tmp):
    attempts = []

    def cleanup():
        attempts.append(True)
        if len(attempts) == 1:
            raise PermissionError(32, 'in use by another process', tmp)

    sleeps = []
    settled = _teardown.settle(cleanup, sleep=_sleep_recorder(sleeps))
    assert settled
    assert len(attempts) == 2, attempts
    assert sleeps == [0.05], sleeps


def test_permanent_refusal_is_reported_not_raised(tmp):
    attempts = []

    def cleanup():
        attempts.append(True)
        raise PermissionError(32, 'in use by another process', tmp)

    sleeps = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        settled = _teardown.settle(cleanup, sleep=_sleep_recorder(sleeps))
    assert not settled
    assert len(attempts) == MAX_ATTEMPTS, attempts
    assert sleeps == FULL_SCHEDULE, sleeps
    message = out.getvalue()
    assert tmp in message, message
    assert 'PermissionError' in message, message


def test_the_warning_names_the_path_it_was_given(tmp):
    """The path is printed raw, not as `repr()` renders it on Windows."""
    held = r'C:\Temp\held'

    def cleanup():
        raise PermissionError(32, 'in use by another process', held)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _teardown.settle(cleanup, sleep=_sleep_recorder([]))
    assert held in out.getvalue(), out.getvalue()


def test_clear_teardown_reports_success_without_sleeping(tmp):
    calls, sleeps = [], []
    settled = _teardown.settle(lambda: calls.append(True),
                               sleep=_sleep_recorder(sleeps))
    assert settled
    assert calls == [True]
    assert sleeps == []


def test_other_oserror_reaches_the_caller(tmp):
    def cleanup():
        raise FileNotFoundError(2, 'No such file or directory', tmp)

    sleeps = []
    try:
        _teardown.settle(cleanup, sleep=_sleep_recorder(sleeps))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('settle swallowed a FileNotFoundError')
    assert sleeps == []


def test_a_blocked_teardown_does_not_flip_a_passed_suite(tmp):
    def passing(_tmp):
        pass

    handed = []

    def refusing(cleanup):
        handed.append(cleanup)
        return False

    original = _util.settle
    _util.settle = refusing
    try:
        code, text = _run_passing_suite(passing)
    finally:
        _util.settle = original
        for cleanup in handed:
            cleanup()
    assert code == 0, text
    assert '  PASS  passing' in text, text
    assert '\n1/1 passed' in text, text
    assert 'temporary tree left behind' in text, text


def test_the_runner_hands_its_directory_cleanup_to_settle(tmp):
    def passing(_tmp):
        pass

    calls = []
    original = _util.settle

    def recorder(cleanup):
        calls.append(cleanup)
        return original(cleanup)

    _util.settle = recorder
    try:
        code, text = _run_passing_suite(passing)
    finally:
        _util.settle = original
    assert original is _teardown.settle
    assert code == 0, text
    assert len(calls) == 1, calls
    assert calls[0].__func__ is tempfile.TemporaryDirectory.cleanup
    assert isinstance(calls[0].__self__, tempfile.TemporaryDirectory)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
