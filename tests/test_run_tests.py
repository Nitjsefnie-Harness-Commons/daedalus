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
_OVERRUN_BOUND_S = 2

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

# Models a bystander a loaded runner could not start in time: the sleep
# exceeds any bound this file uses, so it is killed on every machine, and
# its own output carries a FAILED: line the pin must not read. The decoy
# is not asserted; test_a_suites_own_failed_line_is_not_the_aggregate
# pins the parse.
_SLOW_PASSING_SUITE = (
    'import json, os, time\n'
    "print('FAILED: my own subtest', flush=True)\n"
    'time.sleep(4)\n'
    "summary = os.environ['DAEDALUS_TEST_SUMMARY']\n"
    "json.dump({'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,\n"
    "           'requires': None}, open(summary, 'w'))\n"
    "print('stub pass')\n"
)


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


def _timeout_record(bound):
    """The record the runner writes for a suite it stopped at `bound`."""
    return f'SUITE TIMED OUT after {float(bound)} s (returncode '


def _failed_suites(stdout):
    """The suites the aggregate named as failed, or [] when it named none.

    The aggregate is the runner's last non-empty line; a suite's own
    output arrives earlier, so the scan starts at the end."""
    lines = stdout.splitlines()
    aggregate = next((line for line in reversed(lines) if line.strip()), '')
    if not aggregate.startswith('FAILED: '):
        return []
    return [name.strip()
            for name in aggregate[8:].split(',') if name.strip()]


def test_a_suites_own_failed_line_is_not_the_aggregate(tmp):
    decoy = '=== test_passer.py ===\nFAILED: my own subtest\n\n'
    assert _failed_suites(decoy + 'FAILED: test_staller.py\n') == [
        'test_staller.py']
    assert _failed_suites(decoy + 'FAILED: \n') == []
    assert _failed_suites(decoy + 'OVERALL: PASS (2 suites)\n') == []


def test_an_overrunning_suite_is_named_and_the_run_reports_it(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)})
    assert result.returncode == 1, (result.returncode, result.stdout)
    # The runner's own record, naming the bound it applied.
    assert _timeout_record(_OVERRUN_BOUND_S) in result.stdout, result.stdout
    assert 'test_staller.py' in _failed_suites(result.stdout), result.stdout
    assert '=== test_passer.py ===' in result.stdout, result.stdout


def test_the_staller_is_named_when_the_bystander_misses_the_bound_too(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _SLOW_PASSING_SUITE})
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert _timeout_record(_OVERRUN_BOUND_S) in result.stdout, result.stdout
    assert 'test_staller.py' in _failed_suites(result.stdout), result.stdout
    assert '=== test_passer.py ===' in result.stdout, result.stdout


def test_a_passing_suites_own_output_reaches_stdout(tmp):
    # A bound no trivial child approaches: this cannot turn on speed.
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '60'})
    assert result.returncode == 0, (result.returncode, result.stdout)
    assert '=== test_passer.py ===' in result.stdout, result.stdout
    assert 'stub pass' in result.stdout, result.stdout


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
