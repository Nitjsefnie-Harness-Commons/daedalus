#!/usr/bin/env python3
"""The correctness wait's runtime behaviour, executed rather than read back.

Shape pins over speed.yml live in test_speed_gate.py. These only mean
anything as a run: a commit that never gains a check run, a read that
fails, a near-miss name, an aggregate that is still going.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _speedharness import (  # noqa: E402
    run_workflow_script, speed_script, speed_yml, stub_path,
)


_AGGREGATE = 'Aggregate workflow checks'


def wait_script():
    return speed_script(speed_yml(), 'correctness',
                        'Wait for the correctness aggregate')


def run_wait(workdir, rows, sha='a' * 40, fail=False):
    """Run the wait against a stubbed Checks API, recording each call.

    `rows` is the TSV the workflow's own `--jq` would have rendered: one
    name/status/conclusion line per check run on the commit.
    """
    stub_path(workdir)
    calls = Path(workdir) / 'gh-calls'
    checks = Path(workdir) / 'checks.tsv'
    checks.write_text(rows, encoding='utf-8')
    environment = {
        'GH_TOKEN': 'stub',
        'REPO': 'owner/repo',
        'SHA': sha,
        'STUB_CALLS': str(calls),
        'STUB_ROWS': str(checks),
    }
    if fail:
        environment['STUB_FAIL'] = '1'
    result = run_workflow_script(workdir, wait_script(), environment)
    recorded = []
    if calls.exists():
        recorded = calls.read_text(encoding='utf-8').splitlines()
    return result, recorded


def test_the_wait_polls_the_commit_the_checks_attach_to(tmp):
    """The wait queries the SHA its job was handed, and the pass is real.

    The stub records the URL, so a script that stopped reading `SHA` — or
    queried some other commit — fails here, not in a 45-minute live run.
    """
    result, calls = run_wait(
        tmp, f'{_AGGREGATE}\tcompleted\tsuccess\n', sha='b' * 40)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'passed' in result.stdout, result.stdout
    assert len(calls) == 1, calls
    assert f'commits/{"b" * 40}/check-runs' in calls[0], calls
    assert '.check_runs[]' in calls[0], calls


def test_the_wait_refuses_a_commit_that_never_gains_a_check_run(tmp):
    """A stretch of empty polls is a misconfiguration signal, not patience.

    The bound is consecutive and separate from the overall one, which still
    governs an aggregate that is present and running.
    """
    result, calls = run_wait(tmp, '')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'No check runs at all' in result.stderr, result.stderr
    blind = int(re.search(r'\bblind=(\d+)\b', wait_script()).group(1))
    assert len(calls) == blind, (len(calls), blind)
    assert len(calls) < int(
        re.search(r'\btries=(\d+)\b', wait_script()).group(1))


def test_the_wait_selects_the_aggregate_by_exact_name(tmp):
    """A check run whose name merely contains the aggregate's is not it.

    The selection is equality against the whole name, never a pattern: other
    workflows here carry names that start with the same words.
    """
    rows = ''.join(f'{name}\tin_progress\t\n' for name in (
        f'{_AGGREGATE} (fork)', f'{_AGGREGATE} re-run', 'Aggregate'))
    result, calls = run_wait(tmp, rows)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'No check runs at all' in result.stderr, result.stderr
    blind = int(re.search(r'\bblind=(\d+)\b', wait_script()).group(1))
    assert len(calls) == blind, (len(calls), blind)


def test_the_wait_exhausts_its_bound_while_the_aggregate_runs(tmp):
    """An aggregate that is present and still going is waited out, once.

    The overall bound governs that case, a different ending from the
    misconfiguration one.
    """
    result, calls = run_wait(tmp, f'{_AGGREGATE}\tin_progress\t\n')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "No completed 'Aggregate workflow checks' check run" \
        in result.stderr, result.stderr
    tries = int(re.search(r'\btries=(\d+)\b', wait_script()).group(1))
    assert len(calls) == tries, (len(calls), tries)


def test_a_read_that_fails_says_so_instead_of_looking_empty(tmp):
    """A request that failed is not an answer of zero check runs.

    Both end the wait eventually; only one is a fact about the commit.
    """
    result, calls = run_wait(tmp, '', fail=True)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'could not be read' in result.stderr, result.stderr
    assert 'No check runs at all' not in result.stderr, result.stderr
    assert 'no check runs on' not in result.stderr, result.stderr
    tries = int(re.search(r'\btries=(\d+)\b', wait_script()).group(1))
    assert len(calls) == tries, (len(calls), tries)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedwait_')


if __name__ == '__main__':
    raise SystemExit(main())
