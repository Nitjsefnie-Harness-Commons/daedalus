#!/usr/bin/env python3
"""run_tests.py: what an aggregate verdict is allowed to say.

A suite whose every test skipped verified nothing, and reporting that as a
pass is the one thing the aggregate line must never do — it is what a reader
and CI both key on. These tests run the runner over trees built to produce
each verdict.
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

SUITE_BOUND = _util.load(_util.ROOT / 'scripts' / 'ci' / 'suite_bound.py')


_ALL_SKIPPED_SUITE = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util


def test_needs_something_absent(d):
    _util.skip('nothing to run against here')


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
"""


_DEPENDENT_SUITE = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util


def test_needs_a_browser(d):
    _util.skip('no browser here')


raise SystemExit(_util.runner(_util.collect(dict(globals())),
                              requires='a real browser'))
"""


_PASSING_SUITE = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util


def test_arithmetic(d):
    assert 1 + 1 == 2


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
"""


_PHANTOM_SUITE = """print("ModuleNotFoundError: No module named '_phantom'")
raise SystemExit(3)
"""


_RENDEZVOUS_SUITE = """import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util


def test_rendezvous(d):
    marks = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'marks')
    os.makedirs(marks, exist_ok=True)
    marker = os.path.join(marks, os.path.basename(__file__))
    with open(marker, 'w', encoding='utf-8'):
        pass
    deadline = time.monotonic() + 30
    while len(os.listdir(marks)) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    if len(os.listdir(marks)) < 2:
        raise AssertionError('no sibling suite was running concurrently')


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
"""


_SLOW_PASSING_SUITE = """import json, os, time

time.sleep(5)
summary = {
    'total': 1,
    'passed': 1,
    'skipped': 0,
    'failed': 0,
    'requires': None,
}
with open(os.environ['DAEDALUS_TEST_SUMMARY'], 'w',
          encoding='utf-8') as destination:
    json.dump(summary, destination)
"""


_LONG_PASSING_SUITE = """import json, os, time

time.sleep(60)
summary = {
    'total': 1,
    'passed': 1,
    'skipped': 0,
    'failed': 0,
    'requires': None,
}
with open(os.environ['DAEDALUS_TEST_SUMMARY'], 'w',
          encoding='utf-8') as destination:
    json.dump(summary, destination)
"""


_HIGH_CPU_SITE = """import os
os.cpu_count = lambda: 64
"""


_BAD_EXECUTABLE_SITE = """import os, sys
sys.executable = os.path.dirname(__file__)
"""


def _invalid_output_suite(passed, failed, returncode):
    return f"""import json, os

os.write(2, bytes([255, 10]))
summary = {{
    'total': 1,
    'passed': {passed},
    'skipped': 0,
    'failed': {failed},
    'requires': None,
}}
with open(os.environ['DAEDALUS_TEST_SUMMARY'], 'w',
          encoding='utf-8') as destination:
    json.dump(summary, destination)
raise SystemExit({returncode})
"""


# The sentence a child prints when the clone cannot resolve a helper: the
# module named is the file the tuple forgot.
_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named '(_\w+)'")


def _runner_tree(tmp, suites, runner_encoding=None, sitecustomize=None,
                 before_exec=None, omit=()):
    """A copy of run_tests.py over fabricated suites, run where it stands.

    `omit` leaves helpers out of the clone, which is how a suite really comes
    to die on an import. The tree is always called `tree`: coverage maps
    `*/tree` back onto this repository — see `tests/_coverage_guard.py` — and
    a tree by any other name fails the coverage job. Generated suites and the
    startup stub stay under `tests`, which coverage omits.
    """
    root = Path(tmp) / 'tree'
    (root / 'tests').mkdir(parents=True)
    (root / 'daedalus_bridge').mkdir()
    (root / 'scripts' / 'ci').mkdir(parents=True)
    shutil.copy2(ROOT / 'run_tests.py', root / 'run_tests.py')
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                 root / 'scripts' / 'ci' / 'suite_bound.py')
    # `_util` imports every one of these, so omitting one kills every suite.
    for helper in ('_util.py', '_mcp_ready.py', '_child_ready.py',
                   '_completion.py', '_teardown.py', '_log_safe_cases.py',
                   '_child_boot_env.py'):
        if helper not in omit:
            shutil.copy2(ROOT / 'tests' / helper, root / 'tests' / helper)
    shutil.copy2(ROOT / 'daedalus_bridge' / 'parent_watch.py',
                 root / 'daedalus_bridge' / 'parent_watch.py')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    if sitecustomize:
        (root / 'tests' / 'sitecustomize.py').write_text(
            sitecustomize, encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if sitecustomize:
        inherited_path = env.get('PYTHONPATH')
        env['PYTHONPATH'] = str(root / 'tests')
        if inherited_path:
            env['PYTHONPATH'] += os.pathsep + inherited_path
    if runner_encoding:
        env['PYTHONIOENCODING'] = runner_encoding
    result = subprocess.run(
        [sys.executable, 'run_tests.py'], cwd=str(root),
        env=_util.child_coverage('keep', env, cwd=root),
        capture_output=True, text=True,
        encoding=runner_encoding or 'utf-8', timeout=300,
        preexec_fn=before_exec)
    if result.returncode != 0:
        absent = sorted({name for name in _MISSING_MODULE.findall(
            result.stdout + result.stderr)
            if (ROOT / 'tests' / f'{name}.py').exists()})
        if absent:
            raise AssertionError(
                f'the synthetic tree could not resolve {", ".join(absent)}: '
                f'add {", ".join(n + ".py" for n in absent)} '
                'to the helper tuple in _runner_tree')
    return result


def test_a_suite_that_ran_no_coverage_is_not_an_overall_pass(tmp):
    """A run that executed nothing must not read as a verified one."""
    result = _runner_tree(tmp, {
        'test_all_skipped.py': _ALL_SKIPPED_SUITE,
        'test_passing.py': _PASSING_SUITE,
    })
    assert 'OVERALL: PASS' not in result.stdout, result.stdout
    assert 'test_all_skipped.py' in result.stdout, result.stdout
    assert result.returncode != 0, (result.returncode, result.stdout)


def test_a_suite_that_named_what_it_needs_is_unrun_rather_than_empty(tmp):
    """A browser suite on a machine with no browser is not a broken suite."""
    result = _runner_tree(tmp, {
        'test_dependent.py': _DEPENDENT_SUITE,
        'test_passing.py': _PASSING_SUITE,
    })
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert 'NOT RUN HERE: test_dependent.py' in result.stdout, result.stdout
    assert 'needs a real browser' in result.stdout, result.stdout
    assert result.returncode == 0, (result.returncode, result.stdout)


def test_the_aggregate_carries_the_totals_it_verified(tmp):
    """A pass says how much was run and how much was skipped."""
    result = _runner_tree(tmp, {'test_passing.py': _PASSING_SUITE})
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert '1 passed' in result.stdout.rsplit('OVERALL', 1)[-1], result.stdout


def test_a_helper_missing_from_the_clone_is_named_in_the_failure(tmp):
    """A stale helper tuple is a fixture failure naming the file to add."""
    for omitted in ('_teardown.py', '_child_boot_env.py'):
        failure = None
        try:
            _runner_tree(os.path.join(tmp, os.path.splitext(omitted)[0]),
                         {'test_passing.py': _PASSING_SUITE,
                          'test_phantom.py': _PHANTOM_SUITE},
                         omit=(omitted,))
        except AssertionError as raised:
            failure = str(raised)
        assert failure and f'add {omitted} to the helper tuple' in failure, (
            f'{omitted}: {failure}')
        assert '_phantom' not in failure, f'{omitted}: {failure}'
    outcome = _runner_tree(os.path.join(tmp, 'phantom'),
                           {'test_phantom.py': _PHANTOM_SUITE})
    assert outcome.returncode != 0, (outcome.returncode, outcome.stdout)


def test_suites_run_concurrently(tmp):
    """Each suite must observe its sibling while both are still running."""
    if (os.cpu_count() or 1) < 2:
        _util.skip('parallel suite test requires at least two CPUs')
    result = _runner_tree(tmp, {
        'test_rendezvous_a.py': _RENDEZVOUS_SUITE,
        'test_rendezvous_b.py': _RENDEZVOUS_SUITE,
    })
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)


def test_undecodable_output_does_not_hide_a_failed_verdict(tmp):
    """Raw child bytes must not prevent the aggregate failure report."""
    result = _runner_tree(tmp, {
        'test_invalid_failure.py': _invalid_output_suite(0, 1, 1),
    })
    assert 'Traceback' not in result.stderr, result.stderr
    assert '=== test_invalid_failure.py ===' in result.stdout, result.stdout
    assert '\ufffd' in result.stdout, result.stdout
    assert 'FAILED: test_invalid_failure.py' in result.stdout, result.stdout
    assert result.returncode != 0, (result.returncode, result.stdout)


def test_undecodable_output_does_not_hide_a_passing_verdict(tmp):
    """Raw child bytes must not prevent the aggregate passing report."""
    result = _runner_tree(tmp, {
        'test_invalid_pass.py': _invalid_output_suite(1, 0, 0),
    })
    assert 'Traceback' not in result.stderr, result.stderr
    assert '=== test_invalid_pass.py ===' in result.stdout, result.stdout
    assert '\ufffd' in result.stdout, result.stdout
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert result.returncode == 0, (result.returncode, result.stdout)


def test_legacy_stdout_does_not_hide_a_failed_verdict(tmp):
    """A legacy runner stream must degrade invalid child output."""
    result = _runner_tree(tmp, {
        'test_invalid_failure.py': _invalid_output_suite(0, 1, 1),
    }, runner_encoding='cp1252')
    assert 'Traceback' not in result.stderr, result.stderr
    assert '=== test_invalid_failure.py ===' in result.stdout, result.stdout
    assert '\n?\n' in result.stdout, result.stdout
    assert 'FAILED: test_invalid_failure.py' in result.stdout, result.stdout
    assert result.returncode != 0, (result.returncode, result.stdout)


def test_legacy_stdout_does_not_hide_a_passing_verdict(tmp):
    """A legacy runner stream must still report an aggregate pass."""
    result = _runner_tree(tmp, {
        'test_invalid_pass.py': _invalid_output_suite(1, 0, 0),
    }, runner_encoding='cp1252')
    assert 'Traceback' not in result.stderr, result.stderr
    assert '=== test_invalid_pass.py ===' in result.stdout, result.stdout
    assert '\n?\n' in result.stdout, result.stdout
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert result.returncode == 0, (result.returncode, result.stdout)


def test_output_capture_survives_a_low_descriptor_limit(tmp):
    """Healthy concurrent suites must fit under a modest descriptor limit."""
    if resource is None or not hasattr(resource, 'RLIMIT_NOFILE'):
        _util.skip('RLIMIT_NOFILE is unavailable on this platform')
    hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if hard_limit != resource.RLIM_INFINITY and hard_limit < 64:
        _util.skip('RLIMIT_NOFILE hard limit is below the test limit')

    def limit_descriptors():
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    suites = {
        f'test_descriptor_{number:02d}.py': _SLOW_PASSING_SUITE
        for number in range(60)
    }
    result = _runner_tree(
        tmp, suites, sitecustomize=_HIGH_CPU_SITE,
        before_exec=limit_descriptors)
    assert 'Traceback' not in result.stderr, result.stderr
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert result.returncode == 0, (result.returncode, result.stdout)


def test_a_launch_failure_is_aggregated(tmp):
    """One child launch error must not erase the aggregate failure verdict."""
    result = _runner_tree(
        tmp, {'test_minimal.py': _PASSING_SUITE},
        sitecustomize=_BAD_EXECUTABLE_SITE)
    assert 'Traceback' not in result.stderr, result.stderr
    assert '=== test_minimal.py ===' in result.stdout, result.stdout
    assert 'LAUNCH FAILED:' in result.stdout, result.stdout
    assert 'FAILED: test_minimal.py' in result.stdout, result.stdout
    assert result.returncode != 0, (result.returncode, result.stdout)


def test_output_close_failure_reaps_the_spawned_suite(tmp):
    """A post-spawn output error must not leave its child alive."""
    tree = Path(tmp) / 'tree'
    suite = tree / 'tests' / 'test_long_pass.py'
    suite.parent.mkdir(parents=True)
    suite.write_text(_LONG_PASSING_SUITE, encoding='utf-8')
    summaries = tree / 'summaries'
    summaries.mkdir()
    spec = importlib.util.spec_from_file_location(
        'runner_with_close_failure', ROOT / 'run_tests.py')
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    original_open = runner.Path.open
    original_popen = runner.subprocess.Popen
    spawned = []

    class CloseFailure:
        """Close the real handle, then reproduce its failing context exit."""

        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self.output

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            self.output.close()
            raise OSError('injected output close failure')

    def failing_output_open(path, *args, **kwargs):
        output = original_open(path, *args, **kwargs)
        if path.suffix == '.output':
            return CloseFailure(output)
        return output

    def recording_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    error = None
    reaped = None
    runner.Path.open = failing_output_open
    runner.subprocess.Popen = recording_popen
    try:
        try:
            runner._run_suite(
                suite, summaries, runner.DEFAULT_SUITE_TIMEOUT_S)
        except OSError as exc:
            error = exc
    finally:
        runner.Path.open = original_open
        runner.subprocess.Popen = original_popen
        if spawned:
            try:
                spawned[0].wait(timeout=0)
                reaped = True
            except subprocess.TimeoutExpired:
                reaped = False
                spawned[0].terminate()
                try:
                    spawned[0].wait(timeout=SUITE_BOUND.CLEANUP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    spawned[0].kill()
                    spawned[0].wait(timeout=SUITE_BOUND.CLEANUP_TIMEOUT_S)

    assert error is not None, 'the injected close failure was not raised'
    assert reaped, 'spawned suite survived the output close failure'


class _SurvivesEverySignal:
    """A suite process no signal takes, and that never reports a status."""

    def __init__(self):
        self.pid = 4714
        self.returncode = None
        self.signalled = []

    def terminate(self):
        self.signalled.append('terminate')

    def kill(self):
        self.signalled.append('kill')

    def wait(self, timeout=None):
        assert timeout is not None, 'the reap was called without a bound'
        raise subprocess.TimeoutExpired(cmd='run_tests.py', timeout=timeout)


def test_a_suite_surviving_sigkill_is_reported_not_raised(tmp):
    """A child no signal reaps is written off in a note, never re-raised.

    A raise from the last-resort reap would replace the failure that reached
    this helper, and an unbounded wait would hold the suite worker forever.
    """
    del tmp
    spec = importlib.util.spec_from_file_location(
        'runner_surviving_sigkill', ROOT / 'run_tests.py')
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    process = _SurvivesEverySignal()
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        runner._terminate_and_reap(process)
    note = captured.getvalue()
    assert process.signalled == ['terminate', 'kill'], process.signalled
    assert 'survived SIGKILL' in note, note
    assert '4714' in note, note
    assert process.returncode is None, process.returncode


def test_the_overlap_harness_bound_outlasts_its_inner_waits(tmp):
    """The subprocess bound leaves slack beyond its bounded inner waits.

    Only the result POST wait is unbounded, so the inequality reserves one
    interval per POST. A stuck POST still reaches the whole-command timeout,
    not an inner deadline.
    """
    del tmp
    inner = _overlap._OVERLAP_INNER_WAIT_S
    for order, wait_between in (
            (['a', 'b'], True), (['a', 'b'], False), (['a'], False)):
        waits = 3 + len(order) + (len(order) - 1 if wait_between else 0)
        worst = waits * inner
        bound = _overlap.overlap_child_timeout(
            order, wait_between, inner)
        assert bound > worst, (
            f'{len(order)} commands, wait_between={wait_between}: '
            f'{worst}s of bounded waits against a {bound}s backstop')


def test_the_overlap_harness_backstop_takes_outer_slack(tmp):
    """Outer slack widens the child backstop without moving an inner bound:
    it is added once, after every bounded wait, costing no wall time when the
    child ends on its own schedule.
    """
    del tmp
    bound = _overlap.overlap_child_timeout(['a'], False, 1, outer_slack=7)
    assert bound == 12, (
        f'outer_slack lands after every bounded wait: got {bound}')
    default = _overlap.overlap_child_timeout(['a'], False, 1)
    assert default == 5, (
        f'the no-slack backstop over one result is unchanged: got {default}')


def test_the_runner_reports_a_failure_a_console_cannot_encode(tmp):
    """A failure the console cannot spell must still be reported."""
    del tmp
    program = (
        'import sys\n'
        'sys.path.insert(0, "tests")\n'
        'import _util\n'
        'def test_arrow(d):\n'
        '    assert False, "wanted \u2192 got \u2190 caf\u00e9"\n'
        'raise SystemExit(_util.runner([test_arrow]))\n')
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'cp1252'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run(
        [sys.executable, '-c', program], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding='cp1252', timeout=60)
    assert 'UnicodeEncodeError' not in result.stderr, result.stderr
    assert 'Traceback' not in result.stderr, result.stderr
    assert 'FAIL  test_arrow' in result.stdout, result.stdout
    assert '0/1 passed' in result.stdout, result.stdout
    assert result.returncode == 1, (result.returncode, result.stdout)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='suiterunner_')


if __name__ == '__main__':
    raise SystemExit(main())
