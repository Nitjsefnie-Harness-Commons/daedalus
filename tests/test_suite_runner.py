#!/usr/bin/env python3
"""run_tests.py: what an aggregate verdict is allowed to say.

A suite whose every test skipped verified nothing, and reporting that as a
pass is the one thing the aggregate line must never do — it is what a reader
and CI both key on. These tests run the runner over trees built to produce
each verdict.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


def _runner_tree(tmp, suites, under='.'):
    """A copy of run_tests.py over fabricated suites, run where it stands.

    `under` names a PARENT directory, so one test can build two trees and
    compare what the aggregate says about each. The tree itself is always
    called `tree`: coverage maps `*/tree` back onto this repository — see the
    `[tool.coverage.paths]` note in pyproject.toml — and a tree by any other
    name is measured under a path that no longer exists when the report is
    read, which fails the coverage job rather than the suite.
    """
    root = Path(tmp) / under / 'tree'
    (root / 'tests').mkdir(parents=True)
    shutil.copy2(ROOT / 'run_tests.py', root / 'run_tests.py')
    shutil.copy2(ROOT / 'tests' / '_util.py', root / 'tests' / '_util.py')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run(
        [sys.executable, 'run_tests.py'], cwd=str(root), env=env,
        capture_output=True, text=True, timeout=300)


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


def test_the_overlap_harness_bound_outlasts_its_inner_waits(tmp):
    """The subprocess bound is a backstop, not the first thing to fire.

    Every wait inside the harness is bounded and names what it was waiting
    for; the bound around the whole child says only that a command timed
    out, and carries the entire harness source with it. A Windows leg
    reported exactly that. So the outer bound has to outlast the worst path
    through the inner ones: one wait for the handlers to start, one per
    result, and one per gap when the caller asks to wait between them.
    """
    del tmp
    inner = re.search(r'timeoutMs = (\d+)', _util._BACKGROUND_OVERLAP_HARNESS)
    assert inner, 'the harness no longer declares a per-wait bound'
    inner_ms = int(inner.group(1))
    for order, wait_between in (
            (['a', 'b'], True), (['a', 'b'], False), (['a'], False)):
        waits = 1 + len(order) + (len(order) - 1 if wait_between else 0)
        worst = waits * inner_ms / 1000
        bound = _util.overlap_child_timeout(order, wait_between)
        assert bound > worst, (
            f'{len(order)} commands, wait_between={wait_between}: the child '
            f'is killed at {bound}s while its own waits can run to {worst}s, '
            'so the failure names the whole command instead of the step')


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
