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


def _sandbox(tmp, suites):
    root = Path(tmp) / 'tree'
    (root / 'tests').mkdir(parents=True)
    shutil.copy(ROOT / 'run_tests.py', root / 'run_tests.py')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    return root


def _run_sandbox(root, timeout_env):
    return subprocess.run(
        [sys.executable, str(root / 'run_tests.py')],
        cwd=str(root), env=dict(os.environ, **timeout_env),
        capture_output=True, text=True, timeout=120)


def test_a_suite_that_overruns_is_named_and_the_run_completes(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '2'})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert 'SUITE TIMED OUT' in result.stdout, result.stdout
    assert 'returncode' in result.stdout, result.stdout
    assert 'FAILED: test_staller.py' in result.stdout, result.stdout
    # The other suite's block still lands: the run did not go silent.
    assert '=== test_passer.py ===' in result.stdout, result.stdout
    assert 'stub pass' in result.stdout, result.stdout


def test_a_suite_within_its_budget_still_passes(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '60'})
    assert result.returncode == 0, (result.returncode, result.stdout)
    assert 'OVERALL: PASS' in result.stdout, result.stdout


def test_an_invalid_timeout_stops_startup_naming_the_setting(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    for value in ('soon', 'inf', 'INF', '1e400', 'nan'):
        result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': value})
        assert result.returncode != 0, (value, result.returncode,
                                        result.stdout)
        assert 'DAEDALUS_SUITE_TIMEOUT' in result.stdout + result.stderr, (
            value)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
