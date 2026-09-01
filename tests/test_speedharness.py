#!/usr/bin/env python3
"""The workflow-script runner's own runtime behaviour, executed not read back.

`run_workflow_script` is the harness every behavioural pin over the speed
measurement runs through, so a timeout that lost its evidence or a cleanup
that outlived its bound would break the pins quietly. The speed gate's own
shape lives in test_speed_gate.py.
"""
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _speedharness  # noqa: E402
import _util  # noqa: E402


def test_the_harness_timeout_kills_grandchildren_and_keeps_output(tmp):
    """A timeout keeps evidence and checks tree reaping per platform.

    POSIX records a native grandchild pid, so ``os.kill(pid, 0)`` observes
    that the recorded grandchild no longer exists. Windows records an MSYS
    pid in ``$!``; it does not use that pid as a liveness probe and instead
    observes the ``taskkill`` cleanup diagnostic, not child liveness.
    """
    pid_file = Path(tmp) / 'grandchild.pid'
    script = (
        "printf 'started\\n'; "
        'sleep 15 & echo $! > "$PWD/grandchild.pid"; '
        'wait')

    try:
        _speedharness.run_workflow_script(tmp, script, {}, timeout=2)
    except subprocess.TimeoutExpired as failure:
        output_files = getattr(failure, 'output_files', {})
        assert isinstance(output_files, dict) and output_files, failure
        stdout_path = Path(output_files['stdout'])
        assert 'started' in stdout_path.read_text(encoding='utf-8'), (
            stdout_path, failure)
        stdout = getattr(failure, 'stdout', None)
        assert stdout == 'started\n', stdout
        assert getattr(failure, 'output', None) == stdout, failure
        assert getattr(failure, 'stderr', None) == '', failure
        cleanup = getattr(failure, 'cleanup_diagnostic', None)
        assert cleanup, failure
    else:
        raise AssertionError('the workflow unexpectedly completed')

    assert pid_file.exists(), pid_file
    if sys.platform == 'win32':
        assert 'taskkill' in cleanup.lower(), cleanup
        return
    pid = int(pid_file.read_text(encoding='utf-8'))
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f'grandchild {pid} is still alive')


def test_the_harness_bounds_cleanup_when_tree_kill_fails(tmp):
    """A failed tree kill still raises the original timeout with evidence."""

    class FakeProcess:
        pid = 123

        def __init__(self):
            self.waits = []
            self.killed = False

        def wait(self, timeout=None):
            self.waits.append(timeout)
            if len(self.waits) < 3:
                raise subprocess.TimeoutExpired(['fake'], timeout)
            return 0

        def kill(self):
            self.killed = True

    process = FakeProcess()
    with mock.patch.object(_speedharness.subprocess, 'Popen',
                           return_value=process), \
            mock.patch.object(_speedharness, '_kill_process_tree',
                              return_value='simulated tree-kill failure'):
        try:
            _speedharness.run_workflow_script(
                tmp, 'printf started', {}, timeout=0.01)
        except subprocess.TimeoutExpired as failure:
            assert failure.timeout == 0.01, failure
            cleanup = getattr(failure, 'cleanup_diagnostic', None)
            assert cleanup == (
                'simulated tree-kill failure; '
                'bounded reap timed out; fallback process kill requested; '
                'process reaped after fallback'), cleanup
            assert getattr(failure, 'output_files', None), failure
        else:
            raise AssertionError('the workflow unexpectedly completed')
    assert process.waits == [0.01, 5, 5], process.waits
    assert process.killed


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='speedharness_')


if __name__ == '__main__':
    raise SystemExit(main())
