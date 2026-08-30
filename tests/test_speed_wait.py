#!/usr/bin/env python3
"""The correctness wait's runtime behaviour, executed rather than read back.

Shape pins over speed.yml live in test_speed_gate.py. These only mean
anything as a run: a commit whose checks have not registered yet, a read
that fails, a near-miss name, an aggregate that is still going.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _speedharness  # noqa: E402
from _speedharness import (  # noqa: E402
    WAIT_TRIES_UNDER_TEST, run_workflow_script, stub_path, wait_script,
    write_executable,
)


_AGGREGATE = 'Aggregate workflow checks'


def assert_sleep_stubbed(calls, expected):
    """Every bounded wait delay must be handled by the recording double."""
    sleeps = [call for call in calls if call.startswith('sleep ')]
    assert len(sleeps) == expected, (sleeps, calls)


def assert_wait_calls(calls, expected):
    """The API double and delay double each run once per wait attempt."""
    gh_calls = [call for call in calls if not call.startswith('sleep ')]
    assert len(gh_calls) == expected, (gh_calls, calls)
    assert_sleep_stubbed(calls, expected)


def test_the_harness_runs_the_wait_with_a_small_bound(tmp):
    """The behavioral pins run the wait's loop at a small bound.

    Every attempt spawns the stubs, so a leg's cost is attempts x per-spawn
    cost, and per-spawn cost varies by two orders of magnitude across
    platforms — a 45-attempt loop is seconds on Linux and minutes on
    windows-latest. The value is pinned where it is production, in the
    workflow file; here it is whatever the harness substitutes.
    """
    del tmp
    script = wait_script()
    assert 'tries=45' not in script, script
    assert f'tries={WAIT_TRIES_UNDER_TEST}' in script, script


def test_the_sleep_double_shadows_the_path_executable(tmp):
    """The injected Bash function wins even when PATH sleep would fail."""
    stub_path(tmp)
    write_executable(Path(tmp) / 'bin' / 'sleep',
                     '#!/usr/bin/env bash\nexit 1\n')
    calls = Path(tmp) / 'sleep-calls'
    result = run_workflow_script(tmp, 'sleep 60', {
        'STUB_CALLS': str(calls),
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert calls.read_text(encoding='utf-8') == 'sleep 60\n', calls


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


def test_an_empty_stretch_is_waited_out_under_the_overall_bound(tmp):
    """No check runs yet is queueing, not a misconfiguration.

    GitHub registers a commit's check runs as runners free up, so the first
    polls of a perfectly green run can see none, and registration lag is
    queue-dependent — no honest bound fits it. The wait spends the same
    overall patience an aggregate that is present and running gets, and the
    loud ending names that as one reason an aggregate may be absent.
    """
    result, calls = run_wait(tmp, '')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "No completed 'Aggregate workflow checks' check run" \
        in result.stderr, result.stderr
    # The echo lines wrap at the line-length gate, so the message is matched
    # with its wrapping collapsed.
    said = ' '.join(result.stderr.split())
    assert 'has not registered its checks yet' in said, result.stderr
    tries = int(re.search(r'\btries=(\d+)\b', wait_script()).group(1))
    assert_wait_calls(calls, tries)


def test_the_wait_selects_the_aggregate_by_exact_name(tmp):
    """A check run whose name merely contains the aggregate's is not it.

    The selection is equality against the whole name, never a pattern: other
    workflows here carry names that start with the same words, and a
    regex-flavoured match would take one of them for the gate and report an
    aggregate still running where none ever was.
    """
    rows = ''.join(f'{name}\tin_progress\t\n' for name in (
        f'{_AGGREGATE} (fork)', f'{_AGGREGATE} re-run', 'Aggregate'))
    result, calls = run_wait(tmp, rows)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'still running' not in result.stdout, result.stdout
    assert "No completed 'Aggregate workflow checks' check run" \
        in result.stderr, result.stderr
    tries = int(re.search(r'\btries=(\d+)\b', wait_script()).group(1))
    assert_wait_calls(calls, tries)


def test_the_wait_exhausts_its_bound_while_the_aggregate_runs(tmp):
    """An aggregate that is present and still going is waited out, once.

    That is the same patience an absent aggregate gets: the ending is loud
    either way, and the difference between the two lives in the rows the
    polls saw, not in a shorter bound.
    """
    result, calls = run_wait(tmp, f'{_AGGREGATE}\tin_progress\t\n')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "No completed 'Aggregate workflow checks' check run" \
        in result.stderr, result.stderr
    tries = int(re.search(r'\btries=(\d+)\b', wait_script()).group(1))
    assert_wait_calls(calls, tries)


def test_a_read_that_fails_says_so_instead_of_looking_empty(tmp):
    """A request that failed is not an answer of zero check runs.

    Both end the wait eventually; only one is a fact about the commit, and
    the loud ending has to name the read failure as a reason the aggregate
    was never seen, or a dead API looks like a red tree.
    """
    result, calls = run_wait(tmp, '', fail=True)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'could not be read' in result.stderr, result.stderr
    assert 'no check runs on' not in result.stdout, result.stdout
    said = ' '.join(result.stderr.split())
    # The loud ending names the read failure as one reason the aggregate was
    # never seen, so a dead API is not left looking like a red tree.
    assert 'or its checks could not be read' in said, result.stderr
    tries = int(re.search(r'\btries=(\d+)\b', wait_script()).group(1))
    assert_wait_calls(calls, tries)


def test_the_harness_timeout_kills_grandchildren_and_keeps_output(tmp):
    """A timeout keeps evidence and checks tree reaping per platform.

    POSIX records a native grandchild pid, so ``os.kill(pid, 0)`` observes
    that the recorded grandchild no longer exists. Windows records an MSYS
    pid in ``$!``; it does not use that pid as a liveness probe and instead
    observes the ``taskkill`` cleanup diagnostic, not child liveness.
    """
    pid_file = Path(tmp) / 'grandchild.pid'
    script = (
        "printf 'started\\n'; "
        'sleep 15 & echo $! > "$PWD/grandchild.pid"; '
        'wait')

    try:
        run_workflow_script(
            tmp, script, {'BASH_FUNC_sleep%%': ''}, timeout=2)
    except subprocess.TimeoutExpired as failure:
        output_files = getattr(failure, 'output_files', {})
        assert isinstance(output_files, dict) and output_files, failure
        stdout_path = Path(output_files['stdout'])
        assert 'started' in stdout_path.read_text(encoding='utf-8'), (
            stdout_path, failure)
        stdout = getattr(failure, 'stdout', None)
        assert stdout == 'started\n', stdout
        assert getattr(failure, 'output', None) == stdout, failure
        assert getattr(failure, 'stderr', None) == '', failure
        cleanup = getattr(failure, 'cleanup_diagnostic', None)
        assert cleanup, failure
    else:
        raise AssertionError('the workflow unexpectedly completed')

    assert pid_file.exists(), pid_file
    if sys.platform == 'win32':
        assert 'taskkill' in cleanup.lower(), cleanup
        return
    pid = int(pid_file.read_text(encoding='utf-8'))
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f'grandchild {pid} is still alive')


def test_the_harness_bounds_cleanup_when_tree_kill_fails(tmp):
    """A failed tree kill still raises the original timeout with evidence."""
    class FakeProcess:
        pid = 123

        def __init__(self):
            self.waits = []
            self.killed = False

        def wait(self, timeout=None):
            self.waits.append(timeout)
            if len(self.waits) < 3:
                raise subprocess.TimeoutExpired(['fake'], timeout)
            return 0

        def kill(self):
            self.killed = True

    process = FakeProcess()
    with mock.patch.object(_speedharness.subprocess, 'Popen',
                           return_value=process), \
            mock.patch.object(_speedharness, '_kill_process_tree',
                              return_value='simulated tree-kill failure'):
        try:
            run_workflow_script(tmp, 'printf started', {}, timeout=0.01)
        except subprocess.TimeoutExpired as failure:
            assert failure.timeout == 0.01, failure
            cleanup = getattr(failure, 'cleanup_diagnostic', None)
            assert cleanup == (
                'simulated tree-kill failure; '
                'bounded reap timed out; fallback process kill requested; '
                'process reaped after fallback'), cleanup
            assert getattr(failure, 'output_files', None), failure
        else:
            raise AssertionError('the workflow unexpectedly completed')
    assert process.waits == [0.01, 5, 5], process.waits
    assert process.killed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedwait_')


if __name__ == '__main__':
    raise SystemExit(main())
