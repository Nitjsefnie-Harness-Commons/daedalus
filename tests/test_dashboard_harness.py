#!/usr/bin/env python3
"""Diagnostics from the dashboard suites' Node harness process boundary.

Each stall is driven through a real Node subprocess so the suite checks the
exact evidence returned to Python rather than the helpers' source text.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
import test_dashboard_behaviour as behaviour  # noqa: E402


_HOST_REALM_KEEPALIVE = "setInterval(() => {}, 10);\n"


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


def _harness_failure(source, *arguments, **options):
    try:
        _dashnode.run_dashboard_node(source, *arguments, **options)
    except AssertionError as failure:
        return str(failure)
    except subprocess.TimeoutExpired as failure:
        return f'bare TimeoutExpired after {failure.timeout}s'
    raise AssertionError('the failing dashboard harness unexpectedly passed')


def _phase_trace(result):
    return re.findall(r'^\[phase\] (.+)$', result.stderr, re.MULTILINE)


def test_phase_records_a_harness_checkpoint(tmp):
    """A phase reaches captured stderr in its stable diagnostic format."""
    del tmp
    result = _dashnode.run_dashboard_node(
        "phase('dashboard harness started');", bounded_steps=0)
    assert result.stderr == '[phase] dashboard harness started\n', result


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
    result = _dashnode.run_dashboard_node(source)
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
    result = _dashnode.run_dashboard_node(source)
    assert result.stdout == 'resolved: settled', result


def test_successful_bound_clears_its_real_timeout(tmp):
    """A settled step leaves no diagnostic timer holding Node open."""
    del tmp
    source = r"""
globalThis.clearTimeout = () => {};
bounded(Promise.resolve('settled'), 'successful work', 1500).then(
  (value) => process.stdout.write(value),
);
"""
    started = time.monotonic()
    result = _dashnode.run_dashboard_node(source)
    elapsed = time.monotonic() - started
    assert result.stdout == 'settled', result
    assert elapsed < 0.75, f'settled bound held Node open for {elapsed:.2f}s'


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
    """The outer timeout leaves every declared inner step time to report."""
    del tmp
    assert hasattr(_dashnode, 'dashboard_child_timeout'), (
        'dashboard_child_timeout did not derive the outer backstop')
    one_step = _dashnode.dashboard_child_timeout(1, step_timeout=0.25)
    three_steps = _dashnode.dashboard_child_timeout(3, step_timeout=0.25)
    assert one_step > 0.25, one_step
    assert three_steps > one_step, (one_step, three_steps)


def test_completed_steps_that_do_not_exit_report_the_last_phase(tmp):
    """The outer backstop distinguishes finished work from a hung step."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
export function formatEvalWorld(value) {
  return value;
}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_WORLD_HARNESS, module,
        bounded_steps=1, step_timeout=0.5)
    assert 'outer backstop timed out after 1.0s' in failure, failure
    assert 'last phase: dashboard harness finished' in failure, failure
    assert '"cdp"' in failure, failure
    assert '[phase] dashboard module imported' in failure, failure


def test_synchronous_stall_before_the_first_phase_says_none_recorded(tmp):
    """A child blocked before its body reports that no phase was emitted."""
    del tmp
    failure = _harness_failure(
        'for (;;) {}', bounded_steps=0, step_timeout=0.1)
    assert 'outer backstop timed out after 0.1s' in failure, failure
    assert 'last phase: none recorded' in failure, failure


def test_shipped_harnesses_emit_the_complete_phase_trace(tmp):
    """Every shipped harness records all six diagnostic checkpoints."""
    del tmp
    runs = {
        'content': _dashnode.run_dashboard_node(
            behaviour._CONTENT_KEEPALIVE_HARNESS,
            behaviour.ROOT / 'extension' / 'content.js',
            bounded_steps=0),
        'consume': _dashnode.run_dashboard_node(
            behaviour._DASHBOARD_CONSUME_HARNESS,
            behaviour.ROOT / 'dashboard' / 'api.js', bounded_steps=2),
        'world': _dashnode.run_dashboard_node(
            behaviour._DASHBOARD_WORLD_HARNESS,
            behaviour.ROOT / 'dashboard' / 'sections' / '_util.js',
            bounded_steps=1),
        'selector': _dashnode.run_dashboard_node(
            behaviour._TAB_SELECTOR_HARNESS,
            behaviour.ROOT / 'dashboard' / 'sections' / '_util.js',
            module=True, bounded_steps=5),
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


def test_a_dashboard_module_import_that_never_settles_names_import(tmp):
    """A pending module import reports its inner bound before the backstop."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
await new Promise(() => {});
export async function runCommand() {}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_CONSUME_HARNESS, module,
        bounded_steps=2, step_timeout=0.5)
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
        bounded_steps=2, step_timeout=0.5)
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
        bounded_steps=1, step_timeout=0.5)
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
        behaviour._TAB_SELECTOR_HARNESS, module, module=True,
        bounded_steps=5, step_timeout=0.5)
    assert 'timed out waiting for dashboard module import' in failure, failure
    assert 'outer backstop' not in failure, failure
    assert '[phase] dashboard module import started' in failure, failure


def test_initial_tab_selector_settle_is_bounded(tmp):
    """The selector's initial asynchronous render has its own bound."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 0), module=True,
        bounded_steps=5, step_timeout=0.3)
    assert (
        'timed out waiting for initial tab selector render' in failure
    ), failure
    assert 'outer backstop' not in failure, failure


def test_tab_update_settle_is_bounded(tmp):
    """A tab-updated refresh has its own bound after initial render."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 1), module=True,
        bounded_steps=5, step_timeout=0.3)
    assert 'timed out waiting for tab update refresh' in failure, failure
    assert 'outer backstop' not in failure, failure


def test_tab_unregister_settle_is_bounded(tmp):
    """A tab-unregistered refresh has its own bound after an update."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 2), module=True,
        bounded_steps=5, step_timeout=0.3)
    assert 'timed out waiting for tab unregister refresh' in failure, failure
    assert 'outer backstop' not in failure, failure


def test_tab_sync_settle_is_bounded(tmp):
    """A tabs-synced refresh has its own bound after unregistering a tab."""
    failure = _harness_failure(
        behaviour._TAB_SELECTOR_HARNESS,
        _stalling_selector_module(tmp, 3), module=True,
        bounded_steps=5, step_timeout=0.3)
    assert 'timed out waiting for tab sync refresh' in failure, failure
    assert 'outer backstop' not in failure, failure


def test_shipped_catch_tails_flush_through_leave(tmp):
    """Every asynchronous harness waits for a delayed failure write."""
    cases = (
        ('consume', behaviour._DASHBOARD_CONSUME_HARNESS, False, 2),
        ('world', behaviour._DASHBOARD_WORLD_HARNESS, False, 1),
        ('selector', behaviour._TAB_SELECTOR_HARNESS, True, 5),
    )
    for name, harness, module_mode, steps in cases:
        message = f'flushed {name} harness failure'
        module = _delayed_failure_module(
            tmp, message, f'{name}.mjs')
        failure = _harness_failure(
            harness, module, module=module_mode, bounded_steps=steps,
            step_timeout=0.5)
        assert message in failure, (name, failure)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashharness_')


if __name__ == '__main__':
    raise SystemExit(main())
