#!/usr/bin/env python3
"""Diagnostics from the dashboard suites' Node harness process boundary.

Each stall is driven through a real Node subprocess so the suite checks the
exact evidence returned to Python rather than the helpers' source text.
"""
import os
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


def _harness(source, bounded_steps=0, module=False):
    return _dashnode.DashboardNodeHarness(
        source, bounded_steps=bounded_steps, module=module)


def _harness_failure(harness, *arguments, **options):
    if isinstance(harness, str):
        bounded_steps = options.pop('bounded_steps', 0)
        module = options.pop('module', False)
        harness = _harness(harness, bounded_steps, module)
    try:
        _dashnode.run_dashboard_node(harness, *arguments, **options)
    except AssertionError as failure:
        return str(failure)
    except subprocess.TimeoutExpired as failure:
        return f'bare TimeoutExpired after {failure.timeout}s'
    raise AssertionError('the failing dashboard harness unexpectedly passed')


def _phase_trace(result):
    return re.findall(r'^\[phase\] (.+)$', result.stderr, re.MULTILINE)


def _backstop_seconds(failure):
    match = re.search(
        r'outer backstop timed out after ([0-9.]+)s;', failure)
    assert match, failure
    return float(match.group(1))


def test_phase_records_a_harness_checkpoint(tmp):
    """A phase reaches captured stderr in its stable diagnostic format."""
    del tmp
    result = _dashnode.run_dashboard_node(
        _harness("phase('dashboard harness started');"))
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
    source = r"""
globalThis.clearTimeout = () => {};
bounded(Promise.resolve('settled'), 'successful work', 1500).then(
  (value) => process.stdout.write(value),
);
"""
    started = time.monotonic()
    result = _dashnode.run_dashboard_node(_harness(source))
    elapsed = time.monotonic() - started
    assert result.stdout == 'settled', result
    assert elapsed < 0.75, f'settled bound held Node open for {elapsed:.2f}s'


def test_outer_backstop_bounds_a_grandchild_held_pipe_drain(tmp):
    """An inherited pipe writer cannot defeat the process backstop."""
    del tmp
    source = r"""
const { spawn } = require('child_process');
spawn(process.execPath, ['--eval', 'setTimeout(() => {}, 2000)'], {
  stdio: ['ignore', 'inherit', 'inherit'],
});
phase('grandchild inherited dashboard pipes');
process.stdout.write('grandchild stdout');
setInterval(() => {}, 10);
"""
    started = time.monotonic()
    failure = _harness_failure(
        source, bounded_steps=0, step_timeout=0.4)
    elapsed = time.monotonic() - started
    assert elapsed < 1.25, f'post-kill drain took {elapsed:.2f}s'
    assert 'last phase: grandchild inherited dashboard pipes' in failure
    assert "stdout: 'grandchild stdout'" in failure, failure


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
        encoding='utf-8', errors='replace', timeout=10)
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
    """The outer timeout reserves every step plus one full-step grace."""
    del tmp
    assert hasattr(_dashnode, 'dashboard_child_timeout'), (
        'dashboard_child_timeout did not derive the outer backstop')
    zero_steps = _dashnode.dashboard_child_timeout(0, step_timeout=0.25)
    one_step = _dashnode.dashboard_child_timeout(1, step_timeout=0.25)
    three_steps = _dashnode.dashboard_child_timeout(3, step_timeout=0.25)
    assert (zero_steps, one_step, three_steps) == (0.25, 0.5, 1.0)


def test_shipped_harnesses_pin_their_exact_bounded_step_counts(tmp):
    """Each shipped source carries its one validated timeout count."""
    del tmp
    expected = {
        'content': (behaviour._CONTENT_KEEPALIVE_HARNESS, 0),
        'consume': (behaviour._DASHBOARD_CONSUME_HARNESS, 2),
        'world': (behaviour._DASHBOARD_WORLD_HARNESS, 1),
        'selector': (behaviour._TAB_SELECTOR_HARNESS, 5),
    }
    for name, (harness, bounded_steps) in expected.items():
        assert isinstance(harness, _dashnode.DashboardNodeHarness), name
        assert harness.bounded_steps == bounded_steps, name


def test_harness_metadata_rejects_an_under_declared_bound_count(tmp):
    """One real bound cannot be declared as zero bounded steps."""
    del tmp
    try:
        _dashnode.DashboardNodeHarness(
            "await bounded(Promise.resolve(), 'work', 1);",
            bounded_steps=0, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness declares 0 bounded steps but performs 1')
    else:
        raise AssertionError('under-declared dashboard harness was accepted')


def test_harness_metadata_rejects_an_over_declared_bound_count(tmp):
    """One real bound cannot be declared as two bounded steps."""
    del tmp
    try:
        _dashnode.DashboardNodeHarness(
            "await bounded(Promise.resolve(), 'work', 1);",
            bounded_steps=2, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness declares 2 bounded steps but performs 1')
    else:
        raise AssertionError('over-declared dashboard harness was accepted')


def test_harness_metadata_counts_a_bound_split_by_a_comment(tmp):
    """A comment between `await` and `bounded` cannot hide a real bound."""
    del tmp
    source = "await /* diagnostic label */ bounded(work, 'work', 1);"
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=0, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness declares 0 bounded steps but performs 1')
    else:
        raise AssertionError('comment-split dashboard bound was not counted')


def test_harness_metadata_ignores_a_bound_inside_a_comment(tmp):
    """A commented-out `await bounded` cannot inflate the bound count."""
    del tmp
    source = r"""
await bounded(first, 'first', 1);
/* await bounded(disabled, 'disabled', 1); */
"""
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=2, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness declares 2 bounded steps but performs 1')
    else:
        raise AssertionError('commented dashboard bound was counted')


def test_harness_metadata_refuses_a_template_expression(tmp):
    """Template-expression code is outside the comment blanker's model."""
    del tmp
    source = "const value = `${/* await bounded(x, 'y', 1) */ 1}`;"
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=1, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness bound count cannot inspect '
            'template expression')
    else:
        raise AssertionError('template-expression harness was accepted')


def test_harness_metadata_refuses_a_regex_literal(tmp):
    """Regex source is outside the comment blanker's lexical model."""
    del tmp
    source = r"""
const slashOrStar = /[/*]/;
await bounded(work, 'work', 1);
"""
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=0, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness bound count cannot inspect regex literal')
    else:
        raise AssertionError('regex-literal harness was accepted')


def test_harness_metadata_accepts_division_after_a_keyword_method(tmp):
    """A control-keyword property name does not turn division into regex."""
    del tmp
    source = 'const half = obj.if(value) / 2;'
    harness = _dashnode.DashboardNodeHarness(
        source, bounded_steps=0, module=True)
    assert harness.source == source


def test_harness_metadata_refuses_a_regex_after_a_block(tmp):
    """A regex expression after a statement block fails loudly."""
    del tmp
    source = "{} /abc/; await bounded(work, 'work', 1);"
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=1, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness bound count cannot inspect regex literal')
    else:
        raise AssertionError('post-block regex harness was accepted')


def test_harness_metadata_refuses_an_exported_regex(tmp):
    """A regex introduced by `export default` fails loudly."""
    del tmp
    source = 'export default /abc/;'
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=0, module=True)
    except ValueError as failure:
        assert str(failure) == (
            'dashboard harness bound count cannot inspect regex literal')
    else:
        raise AssertionError('exported regex harness was accepted')


def test_harness_metadata_refuses_regex_after_statement_bodies(tmp):
    """Statement bodies keep the regex lexical goal after their close."""
    del tmp
    sources = (
        'function f() {} /await bounded(foo)/;',
        'class C {} /await bounded(foo)/;',
        'if (ok) {} else {} /await bounded(foo)/;',
        'try {} finally {} /await bounded(foo)/;',
    )
    for source in sources:
        try:
            _dashnode.DashboardNodeHarness(
                source, bounded_steps=0, module=True)
        except ValueError as failure:
            expected = (
                'dashboard harness bound count cannot inspect regex literal')
            if str(failure) != expected:
                raise AssertionError(
                    f'statement-body regex was miscounted: {failure}') \
                    from failure
        else:
            raise AssertionError(
                f'statement-body regex harness was accepted: {source}')


def test_harness_metadata_refuses_regex_expression_operands(tmp):
    """Spread and `extends` introduce expressions that may be regexes."""
    del tmp
    sources = (
        'const values = [... /await bounded(foo)/];',
        'class C extends /await bounded(foo)/ {}',
    )
    for source in sources:
        try:
            _dashnode.DashboardNodeHarness(
                source, bounded_steps=0, module=True)
        except ValueError as failure:
            expected = (
                'dashboard harness bound count cannot inspect regex literal')
            if str(failure) != expected:
                raise AssertionError(
                    f'expression regex was miscounted: {failure}') \
                    from failure
        else:
            raise AssertionError(
                f'expression-operand regex harness was accepted: {source}')


def test_harness_metadata_accepts_private_keyword_method_division(tmp):
    """A private keyword-named method cannot create control syntax."""
    del tmp
    source = (
        'class C { #if(value) { return value; } '
        'half() { return this.#if(4) / 2; } }')
    harness = _dashnode.DashboardNodeHarness(
        source, bounded_steps=0, module=True)
    assert harness.source == source


def test_harness_metadata_keeps_nested_declaration_body_goals(tmp):
    """Nested bodies cannot replace an enclosing declaration goal."""
    del tmp
    sources = (
        'function f(cb = () => {}) {} /await bounded(foo)/;',
        'class C extends (class {}) {} /await bounded(foo)/;',
    )
    for source in sources:
        try:
            _dashnode.DashboardNodeHarness(
                source, bounded_steps=0, module=True)
        except ValueError as failure:
            expected = (
                'dashboard harness bound count cannot inspect regex literal')
            if str(failure) != expected:
                raise AssertionError(
                    f'nested-body regex was miscounted: {failure}') \
                    from failure
        else:
            raise AssertionError(
                f'nested-body regex harness was accepted: {source}')


def test_harness_metadata_preserves_statement_boundaries(tmp):
    """Labels and restricted statements preserve following regex goals."""
    del tmp
    sources = (
        'label: {} /await bounded(foo)/;',
        'debugger\n/await bounded(foo)/;',
        'while (true) { break\n/await bounded(foo)/; }',
    )
    for source in sources:
        try:
            _dashnode.DashboardNodeHarness(
                source, bounded_steps=0, module=True)
        except ValueError as failure:
            expected = (
                'dashboard harness bound count cannot inspect regex literal')
            if str(failure) != expected:
                raise AssertionError(
                    f'statement regex was miscounted: {failure}') \
                    from failure
        else:
            raise AssertionError(
                f'statement regex harness was accepted: {source}')


def test_harness_metadata_limits_async_declaration_prefixes(tmp):
    """Contextual `async` cannot reclassify a later function expression."""
    del tmp
    sources = (
        'async => function() {} / 2;',
        'const async = 1; async + function() {} / 2;',
    )
    for source in sources:
        harness = _dashnode.DashboardNodeHarness(
            source, bounded_steps=0, module=True)
        assert harness.source == source


def test_all_shipped_harnesses_pass_bound_shape_validation(tmp):
    """Supported source shapes in all shipped harnesses remain valid."""
    del tmp
    shipped = (
        behaviour._CONTENT_KEEPALIVE_HARNESS,
        behaviour._DASHBOARD_CONSUME_HARNESS,
        behaviour._DASHBOARD_WORLD_HARNESS,
        behaviour._TAB_SELECTOR_HARNESS,
    )
    rebuilt = tuple(
        _dashnode.DashboardNodeHarness(
            harness.source, harness.bounded_steps, harness.module)
        for harness in shipped
    )
    assert rebuilt == shipped


def test_completed_steps_that_do_not_exit_report_the_last_phase(tmp):
    """The outer backstop distinguishes finished work from a hung step."""
    module = _module(tmp, _HOST_REALM_KEEPALIVE + r"""
export function formatEvalWorld(value) {
  return value;
}
""")
    failure = _harness_failure(
        behaviour._DASHBOARD_WORLD_HARNESS, module,
        step_timeout=0.5)
    assert _backstop_seconds(failure) == 1.0, failure
    assert 'last phase: dashboard harness finished' in failure, failure
    assert '"cdp"' in failure, failure
    assert '[phase] dashboard module imported' in failure, failure


def test_synchronous_stall_before_the_first_phase_says_none_recorded(tmp):
    """A child blocked before its body reports that no phase was emitted."""
    del tmp
    failure = _harness_failure(
        'for (;;) {}', bounded_steps=0, step_timeout=0.1)
    assert _backstop_seconds(failure) == 0.1, failure
    assert 'last phase: none recorded' in failure, failure


def test_last_phase_preserves_regex_metacharacters(tmp):
    """Phase extraction treats a diagnostic label as arbitrary text."""
    del tmp
    failure = _harness_failure(
        "phase('selector [update] (2/3) .*'); setInterval(() => {}, 10);",
        step_timeout=0.3)
    assert 'last phase: selector [update] (2/3) .*;' in failure, failure


def test_last_phase_accepts_an_unterminated_final_line(tmp):
    """A killed child's final phase does not require a trailing newline."""
    del tmp
    failure = _harness_failure(
        "process.stderr.write('[phase] final partial line'); "
        "setInterval(() => {}, 10);", step_timeout=0.3)
    assert 'last phase: final partial line;' in failure, failure


def test_outer_backstop_preserves_named_output_fields(tmp):
    """Captured stdout and stderr keep independently named fields."""
    del tmp
    source = r"""
process.stdout.write('OUT');
process.stderr.write('[phase] output fields\nERR');
setInterval(() => {}, 10);
"""
    failure = _harness_failure(source, step_timeout=0.3)
    assert "stdout: 'OUT'; stderr: '[phase] output fields\\nERR'" in failure


def test_shipped_harnesses_emit_the_complete_phase_trace(tmp):
    """Every shipped harness records all six diagnostic checkpoints."""
    del tmp
    runs = {
        'content': _dashnode.run_dashboard_node(
            behaviour._CONTENT_KEEPALIVE_HARNESS,
            behaviour.ROOT / 'extension' / 'content.js'),
        'consume': _dashnode.run_dashboard_node(
            behaviour._DASHBOARD_CONSUME_HARNESS,
            behaviour.ROOT / 'dashboard' / 'api.js'),
        'world': _dashnode.run_dashboard_node(
            behaviour._DASHBOARD_WORLD_HARNESS,
            behaviour.ROOT / 'dashboard' / 'sections' / '_util.js'),
        'selector': _dashnode.run_dashboard_node(
            behaviour._TAB_SELECTOR_HARNESS,
            behaviour.ROOT / 'dashboard' / 'sections' / '_util.js'),
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
