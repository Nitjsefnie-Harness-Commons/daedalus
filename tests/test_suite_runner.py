#!/usr/bin/env python3
"""run_tests.py: what an aggregate verdict is allowed to say.

A suite whose every test skipped verified nothing, and reporting that as a
pass is the one thing the aggregate line must never do — it is what a reader
and CI both key on. These tests run the runner over trees built to produce
each verdict.
"""
import importlib.util
import os
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


def _runner_tree(tmp, suites, under='.', runner_encoding=None,
                 sitecustomize=None, before_exec=None):
    """A copy of run_tests.py over fabricated suites, run where it stands.

    `under` names a PARENT directory, so one test can build two trees and
    compare what the aggregate says about each. The tree itself is always
    called `tree`: coverage maps `*/tree` back onto this repository — see the
    `[tool.coverage.paths]` note in pyproject.toml — and a tree by any other
    name is measured under a path that no longer exists when the report is
    read, which fails the coverage job rather than the suite. Generated suites
    and the startup stub stay under `tests`, which coverage omits.
    """
    root = Path(tmp) / under / 'tree'
    (root / 'tests').mkdir(parents=True)
    shutil.copy2(ROOT / 'run_tests.py', root / 'run_tests.py')
    shutil.copy2(ROOT / 'tests' / '_util.py', root / 'tests' / '_util.py')
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
    return subprocess.run(
        [sys.executable, 'run_tests.py'], cwd=str(root), env=env,
        capture_output=True, text=True,
        encoding=runner_encoding or 'utf-8', timeout=300,
        preexec_fn=before_exec)


def test_a_suite_that_ran_no_coverage_is_not_an_overall_pass(tmp):
    """A run that executed nothing must not read as a verified one.

    Per-suite lines carried the skip counts, but the aggregate was a boolean
    over exit codes, so a suite whose every test skipped — no browser, no
    dependencies — was indistinguishable from a verified one at exactly the
    line a reader and CI both key on.
    """
    result = _runner_tree(tmp, {
        'test_all_skipped.py': _ALL_SKIPPED_SUITE,
        'test_passing.py': _PASSING_SUITE,
    })
    assert 'OVERALL: PASS' not in result.stdout, result.stdout
    assert 'test_all_skipped.py' in result.stdout, result.stdout
    assert result.returncode != 0, (result.returncode, result.stdout)


def test_a_suite_that_named_what_it_needs_is_unrun_rather_than_empty(tmp):
    """A browser suite on a machine with no browser is not a broken suite.

    The rule above exists because a suite whose every test skipped cannot be
    told apart from one that is broken. A suite that says which external
    dependency it needs IS distinguishable, so the aggregate names it as not
    run here and still passes — while a suite that says nothing keeps failing
    the run, which is what the second half of this asserts.
    """
    result = _runner_tree(tmp, {
        'test_dependent.py': _DEPENDENT_SUITE,
        'test_passing.py': _PASSING_SUITE,
    })
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert 'NOT RUN HERE: test_dependent.py' in result.stdout, result.stdout
    assert 'needs a real browser' in result.stdout, result.stdout
    assert result.returncode == 0, (result.returncode, result.stdout)

    undeclared = _runner_tree(tmp, {
        'test_all_skipped.py': _ALL_SKIPPED_SUITE,
        'test_passing.py': _PASSING_SUITE,
    }, under='undeclared')
    assert 'OVERALL: PASS' not in undeclared.stdout, undeclared.stdout
    assert undeclared.returncode != 0, undeclared.stdout


def test_the_aggregate_carries_the_totals_it_verified(tmp):
    """A pass says how much was run and how much was skipped."""
    result = _runner_tree(tmp, {'test_passing.py': _PASSING_SUITE})
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert '1 passed' in result.stdout.rsplit('OVERALL', 1)[-1], result.stdout


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
            runner._run_suite(suite, summaries)
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
                    spawned[0].wait(timeout=10)
                except subprocess.TimeoutExpired:
                    spawned[0].kill()
                    spawned[0].wait(timeout=10)

    assert error is not None, 'the injected close failure was not raised'
    assert reaped, 'spawned suite survived the output close failure'


def test_the_overlap_harness_bound_outlasts_its_inner_waits(tmp):
    """The subprocess bound leaves slack beyond its bounded inner waits.

    Every wait names what it was waiting for and has its own bound except the
    result POST wait, whose incidental round-trip is bounded only by the child
    backstop. The inequality below sizes slack beyond the bounded waits for
    config load, handler startup, requested gaps, and dispatch settlement,
    while reserving one interval per result POST. It does not prove every path
    reports before the backstop; a stuck result POST reaches the whole-command
    timeout instead of an inner deadline.
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
            f'{len(order)} commands, wait_between={wait_between}: the '
            f'bounded-wait budget and result allowances total {worst}s '
            f'against a {bound}s child backstop, leaving slack; an unbounded '
            'result POST can still reach the child backstop')


def test_the_runner_reports_a_failure_a_console_cannot_encode(tmp):
    """A failure the console cannot spell must still be reported.

    The detail carries whatever the test was comparing, and on a legacy code
    page `print` raises rather than degrading. One failing assertion holding
    an arrow used to abort the whole file with UnicodeEncodeError, so the
    report was lost AND every test after it in that file never ran. A runner
    that cannot say what went wrong is worse than the thing that went wrong.
    """
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
