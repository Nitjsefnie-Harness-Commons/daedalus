#!/usr/bin/env python3
"""Windows retry budgets at the dashboard Node process boundary."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402


def _harness(source, bounded_steps=0, module=False):
    return _dashnode.DashboardNodeHarness(
        source, bounded_steps=bounded_steps, module=module)


def test_windows_retry_escalates_inner_and_outer_timeout_budgets(tmp):
    """Keep attempt 2 from only changing which timeout reports the stall."""
    del tmp
    processes = []
    records = []
    for pid in (7000, 7001):
        process = Mock(
            pid=pid, returncode=1, stdout=None, stderr=None)
        process.communicate.side_effect = (
            subprocess.TimeoutExpired('node.exe', 0), ('', ''))
        processes.append(process)

    real_format = _dashnode._format_timeout_attempt

    def capture_record(record):
        records.append(record)
        return real_format(record)

    with (
            patch.object(_dashnode.sys, 'platform', 'win32'),
            patch.object(_dashnode.shutil, 'which', return_value='node.exe'),
            patch.object(
                _dashnode.subprocess, 'Popen', side_effect=processes) as popen,
            patch.object(
                _dashnode, '_format_timeout_attempt', capture_record),
    ):
        try:
            _dashnode.run_dashboard_node(
                _harness(
                    "await bounded(Promise.resolve(), 'work', "
                    "_dashnodeStepTimeoutMs);",
                    bounded_steps=1, module=True))
        except AssertionError as failure:
            message = str(failure)
        else:
            message = 'timed-out Windows harness unexpectedly ran'

    assert message.startswith(
        'dashboard node outer timeout after 2 attempts\n'), message
    assert popen.call_count == 2, popen.call_args_list
    assert [record.attempt for record in records] == [1, 2], records
    assert [record.timeout_s for record in records] == [10, 20], records
    assert [record.drain_outcome for record in records] == [
        'completed', 'completed'], records
    assert [process.communicate.call_args_list for process in processes] == [
        [call(timeout=10), call(timeout=1)],
        [call(timeout=20), call(timeout=1)],
    ]
    programs = [entry.args[0][3] for entry in popen.call_args_list]
    assert 'const _dashnodeStepTimeoutMs = 5000;' in programs[0]
    assert 'const _dashnodeStepTimeoutMs = 10000;' in programs[1]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashretry_')


if __name__ == '__main__':
    raise SystemExit(main())
