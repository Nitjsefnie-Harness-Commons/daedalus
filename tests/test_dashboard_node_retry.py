#!/usr/bin/env python3
"""Retry and bound budgets at the dashboard Node process boundary."""
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from test_dashboard_behaviour import (  # noqa: E402
    _controlled_run, _result, _timeout)


# A child idling behind a bound spends milliseconds on timer wakeups, while
# one spinning the loop spends the whole wait. A quarter of the wall time
# separates them without failing on a runner that descheduled the child.
_IDLE_BOUND_CPU_SHARE = 0.25


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


def test_non_windows_declines_even_when_close_and_reap_settle(tmp):
    # The isolating control for the retry gate's platform limb: only Windows
    # may retry on a cleanup recovery, so a POSIX close and reap that settle
    # after a timed-out drain still declines.
    del tmp
    failure, events, _ = _controlled_run(
        'linux', (2201, [_timeout(), _timeout('partial', 'error')],
                  {'wait_succeeds': True}),
        (2202, [_result(0, 'wrong retry')]))
    launches = [event[0] for event in events].count('popen')
    assert launches == 1, (failure, events)
    assert failure.startswith(
        'dashboard node outer timeout after 1 attempt\n'
        'retry declined: the post-kill drain did not complete '
        '(drain outcome: timed out)\n'), failure


def test_windows_declined_retry_is_named_in_the_verdict(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (1101, [_timeout(),
                         _timeout('partial', 'error')]),
        (1102, [_result(0, 'wrong retry')]))
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

    failure, events, _ = _controlled_run(
        'win32', (1201, [_timeout(), raise_drain]),
        (1202, [_result(0, 'wrong retry')]))
    expected = (
        "retry declined: the first child's reader cleanup did not finish "
        '(drain outcome: raised RuntimeError: drain reader gone)\n')
    assert expected in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_windows_retries_when_drain_raised_but_cleanup_settled(tmp):
    # The gate's other win32 limb: a drain that raises mid-read with a
    # cleanup that settles takes the platform's one transient-stall retry.
    del tmp

    def raise_drain(_process):
        raise RuntimeError('drain reader gone')

    result, events, _ = _controlled_run(
        'win32', (2301, [_timeout(), raise_drain], {'wait_succeeds': True}),
        (2302, [_result(0, 'recovered')]))
    launches = [event[0] for event in events].count('popen')
    assert launches == 2, (result, events)
    assert result.stdout == 'recovered', result


def test_non_windows_verdict_does_not_name_a_declined_retry(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'linux', (1301, [_timeout(), _result(-9)]),
        (1302, [_timeout(), _result(-9)]))
    assert failure.startswith(
        'dashboard node outer timeout after 2 attempts\n'), failure
    assert 'attempt 1:' in failure and 'pid: 1301' in failure, failure
    assert 'retry declined' not in failure, failure
    assert [event[0] for event in events].count('popen') == 2, events


def test_both_attempts_verdict_does_not_name_a_declined_retry(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (1401, [_timeout(), _result(-9, 'one')]),
        (1402, [_timeout(), _result(-9, 'two')]))
    assert failure.startswith(
        'dashboard node outer timeout after 2 attempts\n'), failure
    assert 'retry declined' not in failure, failure
    assert [event[0] for event in events].count('popen') == 2, events


def test_non_windows_retries_one_silent_stall_then_returns_success(tmp):
    del tmp
    result, events, diagnostic = _controlled_run(
        'linux', (1501, [_timeout(),
                         _result(-9, 'first', 'error')]),
        (1502, [_result(0, 'second success', 'second stderr')]))
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
    result, events, diagnostic = _controlled_run(
        'linux', (1701, [_result(0, 'ok')]))
    assert diagnostic == '', diagnostic
    assert result.stdout == 'ok', result
    assert [event[:2] for event in events] == [
        ('popen', 1701), ('communicate', 1701)], events


def test_non_windows_declined_retry_is_named_in_the_verdict(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'linux', (1601, [_timeout(), _timeout(
            'partial', 'error')]),
        (1602, [_result(0, 'wrong retry')]))
    assert failure.startswith(
        'dashboard node outer timeout after 1 attempt\n'
        'retry declined: the post-kill drain did not complete '
        '(drain outcome: timed out)\n'), failure
    assert 'attempt 1:' in failure and 'pid: 1601' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_last_attempt_decline_does_not_name_a_retry_left_to_decline(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'linux', (2101, [_timeout(), _result(-9)]),
        (2102, [_timeout(), _timeout('partial', 'err')]))
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


def test_windows_cancelled_reader_with_recovered_buffer_keeps_bytes(tmp):
    # CancelSynchronousIo can land after the read already reached EOF, so a
    # cancelled reader may still hand over its buffer; the recovered bytes
    # win over the marker.
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (2401, [_timeout(None, None), _timeout(None, None)], {
            'wait_succeeds': False,
            'late_buffers': {'stdout': 'late recovered'}}))
    launches = [event[0] for event in events].count('popen')
    assert launches == 1, (failure, events)
    assert ("stdout: 'late recovered'; "
            "stderr: '<unrecoverable: reader cancelled after the drain "
            "timed out>'") in failure, failure


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


# The bound credits one sample with the gap it measures, capped at twice
# its 100 ms sampler interval. Both numbers are spelled out here instead of
# being read back from the prelude, so moving either one fails this suite
# rather than travelling into the expectation with it.
_BOUND_SAMPLE_MS = 100
_BOUND_CREDIT_CAP_MS = 200

# Work that freezes the loop for 900 ms - past the credit cap and past a
# sampler deadline - and settles 100 ms after the freeze ends, so the
# sample the freeze delayed is taken instead of being cleared by a
# settlement landing in the same event-loop turn.
_FROZEN_WORK = r"""
const work = new Promise((resolve) => {
  _dashnodeSetTimeout(() => resolve('settled'), 1000);
  _dashnodeSetTimeout(() => {
    const until = Date.now() + 900;
    while (Date.now() < until) {}
  }, 0);
});
"""


def _bound_outcome(background, label, timeout_ms):
    """Settle one bound over `work` while `background` holds the loop.

    The stderr write before the exit is a flush: pipe writes are
    asynchronous on macOS, so the bound's own record has to complete
    before `process.exit` drops whatever is still queued behind it.
    """
    source = background + f"""
(async () => {{
  let outcome = 'resolved';
  try {{
    await bounded(work, {label!r}, {timeout_ms});
  }} catch (error) {{ outcome = error.message; }}
  process.stderr.write('\\n', () => process.stdout.write(
    outcome, () => process.exit(0)));
}})();
"""
    return _dashnode.run_dashboard_node(_harness(source, bounded_steps=1))


def _bound_record(result):
    """The crediting record the one bound in a child wrote when it settled."""
    records = re.findall(r'^\[bound\] (.+)$', result.stderr, re.MULTILINE)
    assert len(records) == 1, (records, result.stderr)
    return json.loads(records[0])


def test_bounded_outlasts_a_freeze_shorter_than_its_bound(tmp):
    """A freeze inside a bound is waited out, not spent as the bound."""
    del tmp
    result = _bound_outcome(r"""
const work = new Promise((resolve) => {
  _dashnodeSetTimeout(() => resolve('settled'), 3500);
  _dashnodeSetTimeout(() => {
    const until = Date.now() + 1300;
    while (Date.now() < until) {}
  }, 1600);
});
""", 'work behind one freeze', 3000)
    assert result.stdout == 'resolved', result


def test_bounded_keeps_a_hung_step_label_while_its_budget_is_spent(tmp):
    """A hung step names itself while the loop still spends its budget.

    The record is what makes that condition visible: every sample here
    lands past the credit cap, so the loop is genuinely starved, and the
    label survives only because the budget is spent anyway. Starvation
    deep enough to stop the budget being spent before the process
    backstop fires is reported by that backstop instead of by this label.
    """
    del tmp
    result = _bound_outcome(r"""
const work = new Promise(() => {});
setImmediate(function starve() {
  const until = Date.now() + 400;
  while (Date.now() < until) {}
  _dashnodeSetTimeout(starve, 40);
});
""", 'a step that hangs', 1000)
    assert result.stdout == 'timed out waiting for a step that hangs', result
    record = _bound_record(result)
    assert record['maxCreditMs'] == _BOUND_CREDIT_CAP_MS, record
    assert record['servicedMs'] >= 1000, record


def test_bounded_rejects_work_slower_than_its_serviced_budget(tmp):
    """Work handed its whole budget still rejects under its own label."""
    del tmp
    result = _bound_outcome(r"""
const work = new Promise((resolve) => {
  const until = Date.now() + 20000;
  const grind = () => {
    const chunk = Date.now() + 400;
    while (Date.now() < chunk) {}
    if (Date.now() < until) _dashnodeSetTimeout(grind, 10);
    else resolve('settled');
  };
  _dashnodeSetTimeout(grind, 10);
});
""", 'slow chunked work', 1000)
    assert result.stdout == 'timed out waiting for slow chunked work', result


def test_bound_credits_a_frozen_sample_with_its_cap(tmp):
    """A freeze is credited the cap, never the stretch it actually ran."""
    del tmp
    result = _bound_outcome(
        _FROZEN_WORK, 'work behind a long freeze', 3000)
    assert result.stdout == 'resolved', result
    record = _bound_record(result)
    assert record['samples'] >= 1, record
    assert record['maxCreditMs'] == _BOUND_CREDIT_CAP_MS, record
    assert record['servicedMs'] <= (
        record['samples'] * _BOUND_CREDIT_CAP_MS), record


def test_bound_spends_its_budget_in_sampler_sized_credits(tmp):
    """An unfrozen loop's serviced total brackets its own sample count.

    A timer cannot fire before its delay, so every credit an unfrozen
    loop delivers is at least one sampler interval, and no credit is ever
    more than the cap.
    """
    del tmp
    result = _bound_outcome(
        'const work = new Promise(() => {});\n',
        'work nothing settles', 1000)
    assert result.stdout == (
        'timed out waiting for work nothing settles'), result
    record = _bound_record(result)
    samples, serviced = record['samples'], record['servicedMs']
    assert serviced >= 1000, record
    assert serviced >= samples * _BOUND_SAMPLE_MS, record
    assert serviced <= samples * _BOUND_CREDIT_CAP_MS, record
    assert record['maxCreditMs'] <= _BOUND_CREDIT_CAP_MS, record


def test_bounded_outlasts_a_freeze_longer_than_its_bound(tmp):
    """A freeze outrunning the whole bound is still not spent as it.

    The bound is above twice the sampler interval on purpose. A freeze
    delivers one late sample however long it lasted, and that sample
    carries at most the cap of two intervals, so a bound at or below the
    cap can be spent in full by the single sample the freeze delivers and
    rejects instead of waiting the freeze out.
    """
    del tmp
    result = _bound_outcome(
        _FROZEN_WORK, 'work behind a freeze past its bound', 600)
    assert result.stdout == 'resolved', result


def test_bounded_waits_out_a_slow_step_without_spinning(tmp):
    """A bound over idle work costs timer wakeups, not a busy core."""
    del tmp
    source = r"""
const startedAt = Date.now();
const cpuStart = process.cpuUsage();
const work = new Promise((resolve) => {
  _dashnodeSetTimeout(() => resolve('settled'), 1000);
});
bounded(work, 'work that settles only after a real delay', 4000).then(
  (value) => {
    const cpu = process.cpuUsage(cpuStart);
    process.stdout.write(JSON.stringify({
      value,
      cpuMs: (cpu.user + cpu.system) * 0.001,
      waitedMs: Date.now() - startedAt,
    }));
  },
  (error) => process.stdout.write('rejected: ' + error.message),
);
"""
    result = _dashnode.run_dashboard_node(_harness(source))
    assert result.stdout.startswith('{'), result
    report = json.loads(result.stdout)
    assert report['value'] == 'settled', result
    budget = report['waitedMs'] * _IDLE_BOUND_CPU_SHARE
    assert report['cpuMs'] < budget, (report, budget)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashretry_')


if __name__ == '__main__':
    raise SystemExit(main())
