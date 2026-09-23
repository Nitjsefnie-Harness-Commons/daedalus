#!/usr/bin/env python3
"""run_tests.py bounds each suite and names the one that overruns."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT

_PASSING_SUITE = (
    'import json, os\n'
    "summary = os.environ['DAEDALUS_TEST_SUMMARY']\n"
    "json.dump({'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,\n"
    "           'requires': None}, open(summary, 'w'))\n"
    "print('stub pass')\n"
)

_STALLING_SUITE = (
    'import time\nprint("stalling", flush=True)\n'
    'time.sleep(60)\n'
)

# The passer the loaded runner never got to finish: it shares the bound the
# staller exhausts, so the run must name the staller either way.
_SLOW_PASSING_SUITE = _PASSING_SUITE.replace(
    "import json, os\n", "import json, os, time\ntime.sleep(4)\n")


def _sandbox(tmp, suites):
    root = Path(tmp) / 'tree'
    (root / 'tests').mkdir(parents=True)
    shutil.copy(ROOT / 'run_tests.py', root / 'run_tests.py')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    return root


def _run_sandbox(root, timeout_env):
    env = dict(os.environ, **timeout_env)
    return subprocess.run(
        [sys.executable, str(root / 'run_tests.py')],
        cwd=str(root), env=_util.child_coverage('keep', env, cwd=root),
        capture_output=True, text=True, timeout=120)


def _failed_suites(stdout):
    """The suites the run named as failed, from its FAILED: line."""
    for line in stdout.splitlines():
        if line.startswith('FAILED: '):
            return [name.strip() for name in line[8:].split(',')]
    return []


def test_a_suite_that_overruns_is_named_and_the_run_completes(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '2'})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert 'SUITE TIMED OUT' in result.stdout, result.stdout
    assert 'returncode' in result.stdout, result.stdout
    # Membership, not a substring: a bystander that missed the bound is
    # named on the same line, and the run still named the staller.
    assert 'test_staller.py' in _failed_suites(result.stdout), result.stdout
    # The other suite's block still lands: the run did not go silent.
    assert '=== test_passer.py ===' in result.stdout, result.stdout


def test_the_staller_is_named_when_the_bystander_misses_the_bound_too(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _SLOW_PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '2'})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert 'SUITE TIMED OUT' in result.stdout, result.stdout
    assert 'test_staller.py' in _failed_suites(result.stdout), result.stdout
    assert '=== test_passer.py ===' in result.stdout, result.stdout


def test_a_suite_within_its_budget_still_passes(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '60'})
    assert result.returncode == 0, (result.returncode, result.stdout)
    assert 'OVERALL: PASS' in result.stdout, result.stdout


def test_an_invalid_timeout_stops_startup_naming_the_setting(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    for value in ('soon', 'inf', 'INF', '1e400', 'nan', '0', '-1'):
        result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': value})
        assert result.returncode != 0, (value, result.returncode,
                                        result.stdout)
        assert 'DAEDALUS_SUITE_TIMEOUT' in result.stdout + result.stderr, (
            value)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
