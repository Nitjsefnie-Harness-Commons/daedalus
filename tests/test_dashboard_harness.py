#!/usr/bin/env python3
"""Diagnostics from the dashboard suites' Node harness process boundary.

Each stall is driven through a real Node subprocess so the suite checks the
exact evidence returned to Python rather than the helpers' source text.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402


_HOST_REALM_KEEPALIVE = "setInterval(() => {}, 10);\n"


def _harness_failure(source, *arguments, **options):
    try:
        _dashnode.run_dashboard_node(source, *arguments, **options)
    except AssertionError as failure:
        return str(failure)
    raise AssertionError('the failing dashboard harness unexpectedly passed')


def test_phase_records_a_harness_checkpoint(tmp):
    """A phase reaches captured stderr in its stable diagnostic format."""
    del tmp
    result = _dashnode.run_dashboard_node(
        "phase('dashboard harness started');")
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
    failure = _harness_failure(source)
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashharness_')


if __name__ == '__main__':
    raise SystemExit(main())
