#!/usr/bin/env python3
"""The bounded post-kill drain every suite cleanup goes through."""
import subprocess
import sys
import time
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402


class _HoldsPipesPastKill:
    """A killed child whose pipes stay open past any bound.

    ``communicate(timeout=...)`` raises with the partial output, the way the
    real thing does when a grandchild holds the inherited pipes; a call with
    no bound models the unbounded drain this module exists to prevent.
    """

    def __init__(self):
        self.pid = 4711
        self.stdout = mock.Mock()
        self.stderr = mock.Mock()
        self.returncode = None

    def kill(self):
        self.returncode = -9

    def communicate(self, timeout=None):
        if timeout is None:
            raise AssertionError('the drain was called without a bound')
        raise subprocess.TimeoutExpired(
            cmd='stub', timeout=timeout,
            output=b'partial-out', stderr=b'partial-err')

    def wait(self, timeout=None):
        assert timeout is not None, 'the reap was called without a bound'
        return -9


def test_drain_with_a_bound_reports_a_drain_that_would_not_end(tmp):
    process = _HoldsPipesPastKill()
    timed_out, out, err = _drain.kill_and_drain(process, drain_timeout=0.05)
    assert timed_out is True
    assert (out, err) == (b'partial-out', b'partial-err')
    for pipe in (process.stdout, process.stderr):
        pipe.close.assert_called_once_with()


def test_drain_returns_the_clean_result_when_the_pipes_close(tmp):
    process = mock.Mock()
    process.pid = 4712
    process.stdout = process.stderr = None
    process.communicate.return_value = ('clean-out', 'clean-err')
    timed_out, out, err = _drain.kill_and_drain(process, drain_timeout=1)
    assert timed_out is False
    assert (out, err) == ('clean-out', 'clean-err')


def test_a_grandchild_holding_pipes_cannot_hang_the_drain(tmp):
    """The real failure: a killed child whose grandchild holds the pipes.

    Against an unbounded drain this test never returns -- the killed child's
    ``communicate()`` waits for the sleeping grandchild to exit. The bound
    turns that into a recorded outcome inside a few seconds.
    """
    client = (
        'import subprocess, sys, time\n'
        'subprocess.Popen([sys.executable, "-c", "import time; '
        'time.sleep(10)"])\n'
        'print("marker", flush=True)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == 'marker'
        started = time.monotonic()
        timed_out, _out, _err = _drain.kill_and_drain(
            process, drain_timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        _drain.kill_and_drain(process)
    assert timed_out is True
    assert elapsed < 60, elapsed
    assert _drain.DRAIN_TIMEOUT_S >= 5


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
