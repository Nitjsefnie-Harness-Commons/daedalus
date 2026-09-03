#!/usr/bin/env python3
"""Diagnostics from the dashboard suites' Node harness process boundary.

Each stall is driven through a real Node subprocess so the suite checks the
exact evidence returned to Python rather than the helpers' source text.
"""
import inspect
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
import test_dashboard_accessibility as accessibility  # noqa: E402
import test_dashboard_behaviour as behaviour  # noqa: E402


_HOST_REALM_KEEPALIVE = "setInterval(() => {}, 10);\n"
# Loaded Node startup peaked at 1.126s across 120 samples. Tests that only
# need the backstop or drain boundary keep 1.5s; they do not inspect child
# output, so a slow start cannot change their verdict.
_PROCESS_STARTUP_ALLOWANCE_S = 1.5
# Tests that must recover child phase or output use 4.0s, a 3.55x margin over
# the measured maximum, to cover the longer tail under CI contention.
_OUTPUT_PROCESS_STARTUP_ALLOWANCE_S = 4.0


def _module(tmp, source, name='dashboard-module.js'):
    path = Path(tmp) / name
    path.write_text(source, encoding='utf-8')
    return path


def _stalling_selector_module(tmp, stall_at):
    source = _HOST_REALM_KEEPALIVE + f"""
function stall() {{
  globalThis.setTimeout = () => 1;
}}

export function bindTabSelector(_select, options) {{
  let eventCount = 0;
  if ({stall_at} === 0) stall();
  options.bus.on(() => {{
    eventCount += 1;
    if (eventCount === {stall_at}) stall();
  }});
}}
"""
    return _module(tmp, source, name='selector.mjs')


def _delayed_failure_module(tmp, message, name):
    source = f"""
const write = process.stderr.write.bind(process.stderr);
process.stderr.write = (text, callback) => {{
  const timer = setInterval(() => {{
    clearInterval(timer);
    write(text, callback);
  }}, 50);
  return false;
}};
console.error = (error) => {{
  const text = (error.stack || String(error)) + '\\n';
  process.stderr.write(text);
}};
throw new Error({message!r});
"""
    return _module(tmp, source, name=name)


def _harness(source, bounded_steps=0, module=False):
    return _dashnode.DashboardNodeHarness(
        source, bounded_steps=bounded_steps, module=module)


def _harness_failure(harness, *arguments, retry=True, **options):
    """Drive the production retry entry; retry=False drives one attempt."""
    if isinstance(harness, str):
        bounded_steps = options.pop('bounded_steps', 0)
        module = options.pop('module', False)
        harness = _harness(harness, bounded_steps, module)
    if arguments:
        harness = replace(harness, arguments=arguments)
    step_timeout = options.pop(
        'step_timeout', _dashnode._DASHBOARD_STEP_TIMEOUT_S)
    process_grace = options.pop(
        'process_grace', _dashnode._DASHBOARD_PROCESS_GRACE_S)
    assert not options, options
    real_step_timeout = _dashnode._DASHBOARD_STEP_TIMEOUT_S
    real_process_grace = _dashnode._DASHBOARD_PROCESS_GRACE_S
    try:
        _dashnode._DASHBOARD_STEP_TIMEOUT_S = step_timeout
        _dashnode._DASHBOARD_PROCESS_GRACE_S = process_grace
        if retry:
            _dashnode.run_dashboard_node(harness)
        else:
            _dashnode._run_dashboard_node_once(harness, attempt=1)
    except _dashnode._DashboardOuterTimeout as failure:
        return _dashnode._format_timeout_attempt(failure.record)
    except (subprocess.TimeoutExpired, AssertionError) as failure:
        return str(failure)
    finally:
        _dashnode._DASHBOARD_STEP_TIMEOUT_S = real_step_timeout
        _dashnode._DASHBOARD_PROCESS_GRACE_S = real_process_grace
    raise AssertionError('the failing dashboard harness unexpectedly passed')


def _phase_trace(result):
    return re.findall(r'^\[phase\] (.+)$', result.stderr, re.MULTILINE)


def _backstop_seconds(failure):
    match = re.search(
        r'outer backstop timed out after ([0-9.]+)s;', failure)
    assert match, failure
    return float(match.group(1))


def _drain_seconds(failure):
    match = re.search(r'drain took ([0-9.]+)s;', failure)
    assert match, failure
    return float(match.group(1))


def test_windows_cancel_adapter_calls_kernel32_contract(tmp):
    del tmp
    cases = (('success', 41, True, True, 0, None),
             ('not found', 41, False, True, 1168, None),
             ('cancel error', 41, False, True, 5, 'winerror 5'),
             ('open error', 0, True, True, 5, 'winerror 5'),
             ('close failure', 41, True, False, 0, None))
    for name, handle, canceled, closed, error, expected_error in cases:
        open_thread = Mock(return_value=handle)
        cancel_io = Mock(return_value=canceled)
        close_handle = Mock(return_value=closed)
        kernel32 = type('Kernel32', (), {})()
        kernel32.OpenThread = open_thread
        kernel32.CancelSynchronousIo = cancel_io
        kernel32.CloseHandle = close_handle
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
            actual_error = None
            try:
                _dashnode._cancel_windows_synchronous_io(
                    type('Reader', (), {'native_id': 7123})())
            except OSError as failure:
                actual_error = str(failure)
        assert actual_error == expected_error, (name, actual_error)
        win_dll.assert_called_once_with('kernel32', use_last_error=True)
        assert open_thread.call_args_list == [
            call(0x0001, False, 7123)], name
        assert open_thread.argtypes == (
            _dashnode.wintypes.DWORD, _dashnode.wintypes.BOOL,
            _dashnode.wintypes.DWORD), name
        assert open_thread.restype is _dashnode.wintypes.HANDLE, name
        assert cancel_io.argtypes == (_dashnode.wintypes.HANDLE,), name
        assert cancel_io.restype is _dashnode.wintypes.BOOL, name
        assert close_handle.argtypes == (_dashnode.wintypes.HANDLE,), name
        assert close_handle.restype is _dashnode.wintypes.BOOL, name
        assert cancel_io.call_args_list == (
            [call(handle)] if handle else []), name
        assert close_handle.call_args_list == (
            [call(handle)] if handle else []), name


def test_phase_records_a_harness_checkpoint(tmp):
    """A phase reaches captured stderr in its stable diagnostic format."""
    del tmp
    result = _dashnode.run_dashboard_node(
        _harness("phase('dashboard harness started');"))
    assert result.stderr == '[phase] dashboard harness started\n', result


def test_runner_keeps_its_one_harness_public_signature(tmp):
    del tmp
    signature = inspect.signature(_dashnode.run_dashboard_node)
    assert list(signature.parameters) == ['harness'], signature
    assert signature.parameters['harness'].annotation is (
        _dashnode.DashboardNodeHarness), signature
    assert signature.return_annotation == (
        subprocess.CompletedProcess[str]), signature


def test_one_launch_helper_has_attempt_as_keyword_only(tmp):
    del tmp
    helper = getattr(_dashnode, '_run_dashboard_node_once', None)
    assert helper is not None, '_run_dashboard_node_once is missing'
    signature = inspect.signature(helper)
    assert list(signature.parameters) == ['harness', 'attempt'], signature
    assert signature.parameters['harness'].annotation is (
        _dashnode.DashboardNodeHarness), signature
    assert signature.parameters['attempt'].kind is (
        inspect.Parameter.KEYWORD_ONLY), signature
    assert signature.return_annotation == (
        subprocess.CompletedProcess[str]), signature


def test_bounded_names_a_step_that_never_settles(tmp):
    """A hung promise is rejected with the bounded step's own label."""
    del tmp
    source = _HOST_REALM_KEEPALIVE + r"""
bounded(new Promise(() => {}), 'the dashboard module to import', 20)
  .catch((error) => {
    process.stdout.write(error.message);
    process.exit(0);
  });
"""
    result = _dashnode.run_dashboard_node(_harness(source))
    assert result.stdout == (
        'timed out waiting for the dashboard module to import'), result


def test_bounded_keeps_the_real_timeout_after_a_harness_stubs_it(tmp):
    """A harness timer stub cannot make the diagnostic bound fire now."""
    del tmp
    source = r"""
const hostSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback) => { callback(); return 0; };
const work = new Promise((resolve) => {
  hostSetTimeout(() => resolve('settled'), 20);
});
bounded(work, 'work using a timer stub', 100).then(
  (value) => process.stdout.write('resolved: ' + value),
  (error) => process.stdout.write('rejected: ' + error.message),
);
"""
    result = _dashnode.run_dashboard_node(_harness(source))
    assert result.stdout == 'resolved: settled', result


def test_successful_bound_clears_its_real_timeout(tmp):
    """A settled step leaves no diagnostic timer holding Node open."""
    del tmp
    # 120000 ms dwarfs the suite's 5s process backstop, so an uncleared
    # timer is caught by that backstop as a discrete failure instead of
    # racing a wall-clock margin.
    source = r"""
globalThis.clearTimeout = () => {};
bounded(Promise.resolve('settled'), 'successful work', 120000).then(
  (value) => process.stdout.write(value),
);
"""
    result = _dashnode.run_dashboard_node(_harness(source))
    assert result.stdout == 'settled', result


def test_outer_backstop_bounds_a_grandchild_held_pipe_drain(tmp):
    """An inherited pipe writer cannot defeat the process backstop."""
    del tmp
    source = r"""
const { spawn } = require('child_process');
spawn(process.execPath, ['--eval', 'setTimeout(() => {}, 7000)'], {
  stdio: ['ignore', 'inherit', 'inherit'],
});
phase('grandchild inherited dashboard pipes');
process.stdout.write('grandchild stdout');
setInterval(() => {}, 10);
"""
    failure = _harness_failure(
        source, bounded_steps=0,
        process_grace=_OUTPUT_PROCESS_STARTUP_ALLOWANCE_S, retry=False)
    assert _backstop_seconds(failure) == 4.0, failure
    assert 'attempt 1:' in failure, (
        'a retry=False failure reports its one attempt record')
    assert 'dashboard node outer timeout after' not in failure, (
        'a retry=False failure is not the retry-loop verdict')
    drain_seconds = _drain_seconds(failure)
    assert drain_seconds < 1.5, (
        f'dashboard drain took {drain_seconds:.3f}s')


def test_process_creation_delay_does_not_inflate_drain_time(tmp):
    """Drain timing begins only after a delayed child reaches its backstop."""
    del tmp
    real_popen = _dashnode.subprocess.Popen

    def delayed_popen(*args, **kwargs):
        time.sleep(0.7)
        return real_popen(*args, **kwargs)

    _dashnode.subprocess.Popen = delayed_popen
    try:
        failure = _harness_failure(
            "phase('delayed process started'); setInterval(() => {}, 10);",
            process_grace=_PROCESS_STARTUP_ALLOWANCE_S, retry=False)
    finally:
        _dashnode.subprocess.Popen = real_popen
    assert _backstop_seconds(failure) == 1.5, failure
    assert 'attempt 1:' in failure, (
        'a retry=False failure reports its one attempt record')
    assert 'dashboard node outer timeout after' not in failure, (
        'a retry=False failure is not the retry-loop verdict')
    drain_seconds = _drain_seconds(failure)
    assert drain_seconds < 0.5, (
        f'dashboard drain took {drain_seconds:.3f}s')
    assert 'drain timed out: no' in failure, failure


def test_node_output_is_decoded_as_utf8_under_an_ascii_locale(tmp):
    """Node's UTF-8 pipes do not depend on Python's host locale."""
    del tmp
    probe = r'''
import sys
sys.path.insert(0, 'tests')
import _dashnode

source = """
process.stdout.write(Buffer.from('7374646f757420636166c3a9ff', 'hex'));
process.stderr.write(Buffer.from('73746465727220636166c3a9ff', 'hex'));
"""
harness = _dashnode.DashboardNodeHarness(source, bounded_steps=0)
result = _dashnode.run_dashboard_node(harness)
assert result.stdout == 'stdout caf\u00e9\ufffd', result
assert result.stderr == 'stderr caf\u00e9\ufffd', result
print('decoded utf-8')
'''
    environment = dict(os.environ)
    environment.update({
        'LC_ALL': 'C',
        'PYTHONCOERCECLOCALE': '0',
        'PYTHONUTF8': '0',
    })
    result = subprocess.run(
        [sys.executable, '-c', probe], cwd=behaviour.ROOT,
        env=environment, capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        timeout=10 + _PROCESS_STARTUP_ALLOWANCE_S)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'decoded utf-8\n', result


def test_leave_flushes_the_error_before_exiting(tmp):
    """A delayed pipe write completes before leave terminates the child."""
    del tmp
    source = r"""
const write = process.stderr.write.bind(process.stderr);
process.stderr.write = (text, callback) => {
  setTimeout(() => write(text, callback), 50);
  return false;
};
try {
  throw new Error('flushed dashboard failure');
} catch (error) {
  leave(error);
}
"""
    failure = _harness_failure(source, bounded_steps=0)
    assert 'flushed dashboard failure' in failure, failure


def test_backstop_grows_with_the_bounded_step_count(tmp):
    """The outer timeout adds independent step and process allowances."""
    del tmp
    assert hasattr(_dashnode, 'dashboard_child_timeout'), (
        'dashboard_child_timeout did not derive the outer backstop')
    try:
        zero_steps = _dashnode.dashboard_child_timeout(
            0, step_timeout=0.25, process_grace=0.4)
        one_step = _dashnode.dashboard_child_timeout(
            1, step_timeout=0.25, process_grace=0.4)
        three_steps = _dashnode.dashboard_child_timeout(
            3, step_timeout=0.25, process_grace=0.4)
    except TypeError as failure:
        raise AssertionError(
            'dashboard backstop has no independent process grace') from failure
    assert (zero_steps, one_step, three_steps) == (0.4, 0.65, 1.15)


def test_completed_steps_that_do_not_exit_report_the_last_phase(tmp):
    """The outer backstop distinguishes finished work from a hung step."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
export function formatEvalWorld(value) {
  return value;
}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_WORLD_HARNESS, module,
        step_timeout=0.5,
        process_grace=_OUTPUT_PROCESS_STARTUP_ALLOWANCE_S, retry=False)
    assert _backstop_seconds(failure) == 4.5, failure
    assert 'attempt 1:' in failure, (
        'a retry=False failure reports its one attempt record')
    assert 'dashboard node outer timeout after' not in failure, (
        'a retry=False failure is not the retry-loop verdict')
    assert 'last phase: dashboard harness finished' in failure, failure
    assert '"cdp"' in failure, failure
    assert '[phase] dashboard module imported' in failure, failure


def test_synchronous_stall_before_the_first_phase_says_none_recorded(tmp):
    """A child blocked before its body reports that no phase was emitted."""
    del tmp
    failure = _harness_failure(
        'for (;;) {}', bounded_steps=0,
        process_grace=_PROCESS_STARTUP_ALLOWANCE_S, retry=False)
    assert _backstop_seconds(failure) == 1.5, failure
    assert 'attempt 1:' in failure, (
        'a retry=False failure reports its one attempt record')
    assert 'dashboard node outer timeout after' not in failure, (
        'a retry=False failure is not the retry-loop verdict')
    assert 'last phase: none recorded' in failure, failure


def test_outer_backstop_preserves_phase_and_named_output_fields(tmp):
    """One stalled child preserves phase syntax, EOF and named streams."""
    del tmp
    source = r"""
process.stdout.write('OUT');
process.stderr.write('ERR\n[phase] selector [update] (2/3) .*');
setInterval(() => {}, 10);
"""
    failure = _harness_failure(
        source, process_grace=_OUTPUT_PROCESS_STARTUP_ALLOWANCE_S, retry=False)
    assert _backstop_seconds(failure) == 4.0, failure
    assert 'attempt 1:' in failure, (
        'a retry=False failure reports its one attempt record')
    assert 'dashboard node outer timeout after' not in failure, (
        'a retry=False failure is not the retry-loop verdict')
    assert 'last phase: selector [update] (2/3) .*;' in failure, failure
    assert (
        "stdout: 'OUT'; stderr: 'ERR\\n[phase] selector [update] (2/3) .*'"
        in failure
    ), failure


def test_shipped_harnesses_emit_the_complete_phase_trace(tmp):
    """Every shipped harness records all six diagnostic checkpoints."""
    del tmp
    runs = {
        'content': _dashnode.run_dashboard_node(
            behaviour._CONTENT_KEEPALIVE_HARNESS),
        'consume': _dashnode.run_dashboard_node(
            behaviour._DASHBOARD_CONSUME_HARNESS),
        'world': _dashnode.run_dashboard_node(
            behaviour._DASHBOARD_WORLD_HARNESS),
        'selector': _dashnode.run_dashboard_node(
            behaviour._TAB_SELECTOR_HARNESS),
        'field': _dashnode.run_dashboard_node(
            accessibility._FIELD_HARNESS),
    }
    expected = [
        'dashboard harness started',
        'dashboard module import started',
        'dashboard module imported',
        'dashboard call started',
        'dashboard call settled',
        'dashboard harness finished',
    ]
    actual = {name: _phase_trace(result) for name, result in runs.items()}
    assert actual == {name: expected for name in runs}, actual


def test_accessibility_field_case_uses_the_shared_runner(tmp):
    del tmp

    class SharedRunnerReached(Exception):
        pass

    def stop_at_shared_runner(_harness):
        raise SharedRunnerReached

    with patch.object(
            _dashnode, 'run_dashboard_node', stop_at_shared_runner):
        try:
            accessibility.test_field_associates_every_label_with_its_control(
                None)
        except SharedRunnerReached:
            pass  # The sentinel proves the shared boundary was reached.
        else:
            raise AssertionError(
                'accessibility field case bypassed run_dashboard_node')


def test_a_dashboard_module_import_that_never_settles_names_import(tmp):
    """A pending module import reports its inner bound before the backstop."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
await new Promise(() => {});
export async function runCommand() {}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_CONSUME_HARNESS, module,
        step_timeout=0.5)
    assert 'timed out waiting for dashboard module import' in failure, failure
    assert 'outer backstop' not in failure, failure
    assert '[phase] dashboard harness started' in failure, failure
    assert '[phase] dashboard module import started' in failure, failure


def test_a_dashboard_call_that_never_settles_names_the_call(tmp):
    """A pending API call reports its own bound, not the import bound."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
export function runCommand() {
  return new Promise(() => {});
}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_CONSUME_HARNESS, module,
        step_timeout=0.5)
    assert 'timed out waiting for dashboard call' in failure, failure
    assert 'timed out waiting for dashboard module import' not in failure
    assert 'outer backstop' not in failure, failure
    assert '[phase] dashboard module imported' in failure, failure
    assert '[phase] dashboard call started' in failure, failure


def test_world_formatter_import_that_never_settles_names_import(tmp):
    """The formatter harness bounds its own pending dashboard import."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
await new Promise(() => {});
export function formatEvalWorld() {}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_WORLD_HARNESS, module,
        step_timeout=0.5)
    assert 'timed out waiting for dashboard module import' in failure, failure
    assert 'outer backstop' not in failure, failure
    assert '[phase] dashboard module import started' in failure, failure


def test_tab_selector_import_that_never_settles_names_import(tmp):
    """The selector harness bounds its pending dashboard module import."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
await new Promise(() => {});
export function bindTabSelector() {}
""", name='selector.mjs')
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS, module, step_timeout=0.5)
    assert 'timed out waiting for dashboard module import' in failure, failure
    assert 'outer backstop' not in failure, failure
    assert '[phase] dashboard module import started' in failure, failure


def test_initial_tab_selector_settle_is_bounded(tmp):
    """The selector's initial asynchronous render has its own bound."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 0), step_timeout=0.3)
    assert (
        'timed out waiting for initial tab selector render' in failure
    ), failure
    assert 'outer backstop' not in failure, failure


def test_tab_update_settle_is_bounded(tmp):
    """A tab-updated refresh has its own bound after initial render."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 1), step_timeout=0.3)
    assert 'timed out waiting for tab update refresh' in failure, failure
    assert 'outer backstop' not in failure, failure


def test_tab_unregister_settle_is_bounded(tmp):
    """A tab-unregistered refresh has its own bound after an update."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 2), step_timeout=0.3)
    assert 'timed out waiting for tab unregister refresh' in failure, failure
    assert 'outer backstop' not in failure, failure


def test_tab_sync_settle_is_bounded(tmp):
    """A tabs-synced refresh has its own bound after unregistering a tab."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 3), step_timeout=0.3)
    assert 'timed out waiting for tab sync refresh' in failure, failure
    assert 'outer backstop' not in failure, failure


def test_shipped_catch_tails_flush_through_leave(tmp):
    """Every asynchronous harness waits for a delayed failure write."""
    cases = (
        ('consume', behaviour._DASHBOARD_CONSUME_HARNESS),
        ('world', behaviour._DASHBOARD_WORLD_HARNESS),
        ('selector', behaviour._TAB_SELECTOR_HARNESS),
    )
    for name, harness in cases:
        message = f'flushed {name} harness failure'
        module = _delayed_failure_module(
            tmp, message, f'{name}.mjs')
        failure = _harness_failure(
            harness, module, step_timeout=0.5)
        assert message in failure, (name, failure)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashharness_')


if __name__ == '__main__':
    raise SystemExit(main())
