#!/usr/bin/env python3
"""Retry budgets at the dashboard Node process boundary."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
import test_dashboard_behaviour as behaviour  # noqa: E402
from test_dashboard_behaviour import (  # noqa: E402
    _controlled_run, _result, _timeout)


def _harness(source, bounded_steps=0, module=False):
    return _dashnode.DashboardNodeHarness(
        source, bounded_steps=bounded_steps, module=module)


def _set_filetime(pointer, ticks):
    value = pointer._obj
    value.dwLowDateTime = ticks & 0xffffffff
    value.dwHighDateTime = ticks >> 32


def test_windows_process_cpu_adapter_calls_kernel32_contract(tmp):
    del tmp
    adapter = getattr(_dashnode, '_windows_process_cpu_seconds', None)
    assert adapter is not None, 'Windows process CPU adapter is missing'
    cases = (('success', True, 0, None),
             ('query error', False, 5, 'winerror 5'))
    for name, queried, error, expected_error in cases:
        def get_times(
                _handle, created, exited, kernel, user, result=queried):
            _set_filetime(created, 3)
            _set_filetime(exited, 4)
            _set_filetime(kernel, 0x100000000)
            _set_filetime(user, 10_000_000)
            return result

        query = Mock(side_effect=get_times)
        kernel32 = type('Kernel32', (), {})()
        kernel32.GetProcessTimes = query
        with (
                patch.object(
                    _dashnode.ctypes, 'WinDLL', create=True,
                    return_value=kernel32) as win_dll,
                patch.object(_dashnode.ctypes, 'get_last_error', create=True,
                             return_value=error),
                patch.object(_dashnode.ctypes, 'WinError', create=True,
                             side_effect=lambda code: OSError(
                                 f'winerror {code}')),
        ):
            actual = actual_error = None
            try:
                actual = adapter(
                    type('Process', (), {'_handle': 41})())
            except OSError as failure:
                actual_error = str(failure)
        assert actual_error == expected_error, (name, actual_error)
        expected = (0x100000000 + 10_000_000) / 10_000_000
        assert actual == (expected if queried else None), (name, actual)
        win_dll.assert_called_once_with('kernel32', use_last_error=True)
        assert query.call_count == 1, name
        handle, *times = query.call_args.args
        assert handle == 41, name
        assert all(
            item._obj.__class__ is _dashnode.wintypes.FILETIME
            for item in times), name
        assert query.argtypes == (
            _dashnode.wintypes.HANDLE,
            _dashnode.wintypes.LPFILETIME,
            _dashnode.wintypes.LPFILETIME,
            _dashnode.wintypes.LPFILETIME,
            _dashnode.wintypes.LPFILETIME), name
        assert query.restype is _dashnode.wintypes.BOOL, name


def test_windows_child_cpu_wrapper_preserves_query_error(tmp):
    del tmp

    def process_cpu(_process):
        raise OSError('query denied')

    with (
            patch.object(_dashnode.sys, 'platform', 'win32'),
            patch.object(
                _dashnode, '_windows_process_cpu_seconds', process_cpu),
    ):
        actual = _dashnode._child_cpu_at_timeout(object())

    assert actual == 'unavailable (OSError: query denied)', actual


def test_non_windows_child_cpu_wrapper_is_na_without_adapter(tmp):
    del tmp
    calls = []

    def process_cpu(process):
        calls.append(process)
        return 0.25

    with (
            patch.object(_dashnode.sys, 'platform', 'linux'),
            patch.object(
                _dashnode, '_windows_process_cpu_seconds', process_cpu),
    ):
        actual = _dashnode._child_cpu_at_timeout(object())

    assert actual == 'n/a', actual
    assert calls == [], calls


def test_windows_outer_timeout_records_cpu_before_kill(tmp):
    del tmp
    events = []
    process = Mock(pid=6500, returncode=-9, stdout=None, stderr=None)
    process.communicate.side_effect = (
        subprocess.TimeoutExpired('node.exe', 0), ('', ''))
    process.kill.side_effect = lambda: events.append('kill')

    def process_cpu(_process):
        events.append('cpu')
        return 0.375

    with (
            patch.object(_dashnode.sys, 'platform', 'win32'),
            patch.object(_dashnode.shutil, 'which', return_value='node.exe'),
            patch.object(_dashnode.subprocess, 'Popen', return_value=process),
            patch.object(
                _dashnode, '_windows_process_cpu_seconds', process_cpu,
                create=True),
    ):
        record = None
        try:
            _dashnode._run_dashboard_node_once(_harness(''), attempt=1)
        except _dashnode._DashboardOuterTimeout as failure:
            record = failure.record
        else:
            raise AssertionError('timed-out Windows harness unexpectedly ran')

    assert record is not None, 'outer timeout record was not captured'
    assert events == ['cpu', 'kill'], events
    assert record.child_cpu_at_timeout == '0.375000s', record
    formatted = _dashnode._format_timeout_attempt(record)
    assert 'child CPU at timeout: 0.375000s' in formatted, formatted


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
                _dashnode, '_windows_process_cpu_seconds',
                side_effect=(0.0, 0.5), create=True),
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
    assert [getattr(record, 'child_cpu_at_timeout', None)
            for record in records] == [
        '0.000000s', '0.500000s'], records
    assert 'child CPU at timeout: 0.000000s' in message, message
    assert 'child CPU at timeout: 0.500000s' in message, message
    assert [process.communicate.call_args_list for process in processes] == [
        [call(timeout=10), call(timeout=1)],
        [call(timeout=20), call(timeout=1)],
    ]
    programs = [entry.args[0][3] for entry in popen.call_args_list]
    assert 'const _dashnodeStepTimeoutMs = 5000;' in programs[0]
    assert 'const _dashnodeStepTimeoutMs = 10000;' in programs[1]


def test_windows_declined_retry_is_named_in_the_verdict(tmp):
    del tmp
    failure, events, _ = behaviour._controlled_run(
        'win32', (1101, [behaviour._timeout(),
                         behaviour._timeout('partial', 'error')]),
        (1102, [behaviour._result(0, 'wrong retry')]))
    assert failure.startswith(
        'dashboard node outer timeout after 1 attempt\n'
        "retry declined: the first child's reader cleanup did not finish "
        '(drain outcome: timed out)\n'), failure
    assert 'attempt 1:' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_windows_declined_retry_names_a_drain_that_raised(tmp):
    del tmp

    def raise_drain(_process):
        raise RuntimeError('drain reader gone')

    failure, events, _ = behaviour._controlled_run(
        'win32', (1201, [behaviour._timeout(), raise_drain]),
        (1202, [behaviour._result(0, 'wrong retry')]))
    expected = (
        "retry declined: the first child's reader cleanup did not finish "
        '(drain outcome: raised RuntimeError: drain reader gone)\n')
    assert expected in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_non_windows_verdict_does_not_name_a_declined_retry(tmp):
    del tmp
    failure, events, _ = behaviour._controlled_run(
        'linux', (1301, [behaviour._timeout(), behaviour._result(-9)]),
        (1302, [behaviour._timeout(), behaviour._result(-9)]))
    assert failure.startswith(
        'dashboard node outer timeout after 2 attempts\n'), failure
    assert 'attempt 1:' in failure and 'pid: 1301' in failure, failure
    assert 'retry declined' not in failure, failure
    assert [event[0] for event in events].count('popen') == 2, events


def test_both_attempts_verdict_does_not_name_a_declined_retry(tmp):
    del tmp
    failure, events, _ = behaviour._controlled_run(
        'win32', (1401, [behaviour._timeout(), behaviour._result(-9, 'one')]),
        (1402, [behaviour._timeout(), behaviour._result(-9, 'two')]))
    assert failure.startswith(
        'dashboard node outer timeout after 2 attempts\n'), failure
    assert 'retry declined' not in failure, failure
    assert [event[0] for event in events].count('popen') == 2, events


def test_non_windows_retries_one_silent_stall_then_returns_success(tmp):
    del tmp
    result, events, diagnostic = behaviour._controlled_run(
        'linux', (1501, [behaviour._timeout(),
                         behaviour._result(-9, 'first', 'error')]),
        (1502, [behaviour._result(0, 'second success', 'second stderr')]))
    assert [event[:2] for event in events] == [
        ('popen', 1501), ('communicate', 1501), ('kill', 1501),
        ('communicate', 1501), ('popen', 1502), ('communicate', 1502)], events
    assert result.stdout == 'second success', result
    assert diagnostic.count('\n') == 1, diagnostic
    expected = ('recovered', 'attempt 1', 'pid 1501',
                'drain completed', 'last phase none recorded')
    assert all(part in diagnostic for part in expected), diagnostic


def test_clean_run_writes_no_recovery_diagnostic(tmp):
    """A first-attempt success never announces that a retry recovered."""
    del tmp
    result, events, diagnostic = behaviour._controlled_run(
        'linux', (1701, [behaviour._result(0, 'ok')]))
    assert diagnostic == '', diagnostic
    assert result.stdout == 'ok', result
    assert [event[:2] for event in events] == [
        ('popen', 1701), ('communicate', 1701)], events


def test_non_windows_declined_retry_is_named_in_the_verdict(tmp):
    del tmp
    failure, events, _ = behaviour._controlled_run(
        'linux', (1601, [behaviour._timeout(), behaviour._timeout(
            'partial', 'error')]),
        (1602, [behaviour._result(0, 'wrong retry')]))
    assert failure.startswith(
        'dashboard node outer timeout after 1 attempt\n'
        'retry declined: the post-kill drain did not complete '
        '(drain outcome: timed out)\n'), failure
    assert 'attempt 1:' in failure and 'pid: 1601' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_last_attempt_decline_does_not_name_a_retry_left_to_decline(tmp):
    del tmp
    failure, events, _ = behaviour._controlled_run(
        'linux', (2101, [behaviour._timeout(), behaviour._result(-9)]),
        (2102, [behaviour._timeout(), behaviour._timeout('partial', 'err')]))
    assert failure.startswith(
        'dashboard node outer timeout after 2 attempts\n'), failure
    assert 'retry declined' not in failure, failure
    assert [event[0] for event in events].count('popen') == 2, events


def test_windows_preserves_only_completed_cpython_reader_buffers(tmp):
    # Cleanup settles, so the gate retries; the second attempt also times
    # out so the verdict carries both records for the buffer assertions.
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (1001, [
            _timeout(None, b'early error\n'), _timeout(None, None)], {
                'wait_succeeds': True,
                'reader_buffers': {
                    'stdout': 'prefix middle end',
                    'stderr': '[phase] buffered phase\nbuffered error',
                }}), (1009, [_timeout(), _result(-9, 'second attempt')]))
    launches = [event[0] for event in events].count('popen')
    assert launches == 2, (failure, events)
    assert "stdout: 'prefix middle end'" in failure, failure
    assert failure.count('prefix middle end') == 1, failure
    assert "stderr: 'early error\\n[phase] buffered phase\\n" \
        "buffered error'" in failure
    assert 'last phase: buffered phase' in failure, failure

    sibling, events, _ = _controlled_run(
        'win32', (1002, [_timeout(None, None), _timeout(None, None)], {
            'wait_succeeds': True,
            'reader_buffers': {'stdout': 'completed sibling'},
            'stuck_reader': 'stderr',
        }))
    assert ("stdout: 'completed sibling'; "
            "stderr: '<unrecoverable: reader cancelled after the drain "
            "timed out>'") in sibling, sibling
    assert ('buffer-read', 'stderr') not in events, events
    assert [event[0] for event in events].count('popen') == 1, events


def test_windows_cancelled_reader_is_not_rendered_as_empty(tmp):
    # The cancelled stream recovered nothing, so the record must distinguish
    # it from a child that said nothing; the completed-empty stdout stays ''.
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (1103, [_timeout(None, None), _timeout(None, None)], {
            'wait_succeeds': False,
            'reader_buffers': {'stdout': ''}}))
    assert ('buffer-read', 'stderr') in events, events
    assert ("stdout: ''; "
            "stderr: '<unrecoverable: reader cancelled after the drain "
            "timed out>'") in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_independent_output_sources_keep_repeated_boundary(tmp):
    # Both readers complete with recorded buffers so the pin stays about the
    # merge boundary; the cleanup settles and the retry runs.
    del tmp
    failure, events, _ = _controlled_run('win32', (1003, [
        _timeout(b'leftX'), _timeout(None, None)], {
            'wait_succeeds': True,
            'reader_buffers': {'stdout': 'Xright', 'stderr': ''}}), (1009, [
                _timeout(), _result(-9, 'second attempt')]))
    launches = [event[0] for event in events].count('popen')
    assert launches == 2, (failure, events)
    assert "stdout: 'leftXXright'; stderr: ''" in failure, failure


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashretry_')


if __name__ == '__main__':
    raise SystemExit(main())
