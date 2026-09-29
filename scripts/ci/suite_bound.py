#!/usr/bin/env python3
"""One per-suite wall-clock bound, and the bounded launch that enforces it.

`run_tests.py` and `scripts/ci/coverage_suites.py` both launch every
suite in `tests/`, so both are ended by a wedge in any one of them. The
bound lives here rather than in either launcher because a number two
files each hold is two premises: widening one leaves the other bounding
the value before, and neither file says so.

The bound is resolved when a suite is launched, never captured in a
default argument, so a control that shrinks the constant sees the
launcher obey the smaller value.

The direct child is not what a wedge leaves behind: a suite is a
`coverage run` with the suite under it, plus whatever the suite itself
started, so the kill here is of the child's whole TREE. `start_new_session`
is what makes that reachable on POSIX, and it is a no-op on Windows, where
the tree is killed by pid with the tree flag instead.

`tests/_processtree.py` is the same shape for the suites' own children. It
is followed here rather than imported: a shipped launcher must not depend
on a test module, and that file is held by an open pull request.
"""
import math
import os
import signal
import subprocess
import sys

DEFAULT_SUITE_TIMEOUT_S = 900
TIMEOUT_ENV = 'DAEDALUS_SUITE_TIMEOUT'
CLEANUP_TIMEOUT_S = 10


def suite_timeout():
    """The per-suite wall-clock bound, overridable for slow machines.

    A value that is not a finite positive number is refused rather than
    rounded or clamped: `float('inf')` passes a `<= 0` test, and a bound
    of infinity never expires, which is the unbounded wait this bound
    exists to remove.
    """
    raw = os.environ.get(TIMEOUT_ENV)
    if raw is None:
        # Cast, so the record reads the same number whether the bound came
        # from the environment or from the constant above.
        return float(DEFAULT_SUITE_TIMEOUT_S)
    try:
        timeout = float(raw)
    except ValueError:
        raise SystemExit(
            f'{TIMEOUT_ENV}: not a number: {raw!r}') from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise SystemExit(
            f'{TIMEOUT_ENV}: must be a finite positive number; '
            f'got {raw!r}')
    return timeout


def timeout_record(name, bound, returncode, cleanup=''):
    """The line a launcher appends for the suite it stopped at `bound`.

    It names the suite, because a group of output is the only place a
    reader learns which of the concurrent children is the one that wedged.
    """
    record = (f'SUITE TIMED OUT after {bound} s (returncode {returncode!r})'
              f' in {name}')
    if cleanup:
        record += f'; {cleanup}'
    return record + '; its last lines are above\n'


def kill_process_tree(process):
    """Kill `process`'s whole tree and name the outcome, never raising.

    Every outcome is a string rather than a discard: a caller that drops it
    has thrown away the only evidence of what happened to a process that
    had already stopped answering. Every wait this path makes is bounded
    too, or a kill that hangs hands the hang back through the error path.
    """
    if sys.platform.startswith('win'):
        return _taskkill(process)
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return 'process group was already gone before cleanup'
    except OSError as error:
        return f'process-group lookup failed: {error}'
    if group == os.getpgrp():
        # The launch was not given a session of its own, so the group is
        # the launcher's own and signalling it would signal the launcher.
        return 'child shares the launcher group; no group signal sent'
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        return f'process group {group} was already gone'
    except OSError as error:
        return f'process-group kill failed: {error}'
    return f'process group {group} killed'


def _taskkill(process):
    """Kill the tree the way Windows can: by pid, with the tree flag."""
    try:
        result = subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(process.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
            timeout=CLEANUP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f'taskkill timed out after {CLEANUP_TIMEOUT_S}s'
    except OSError as error:
        return f'taskkill failed to run: {error}'
    if result.returncode == 0:
        return 'taskkill completed successfully'
    return f'taskkill failed with exit code {result.returncode}'


def launch_suite(name, argv, *, cwd, output_path, env=None, timeout=None):
    """Start one suite, bound it, and kill its whole tree if it overruns.

    Returns `(returncode, cleanup)`. `cleanup` is empty exactly when the
    suite finished inside the bound, so a caller can tell a wedge from a
    suite that failed on its own by the value alone, and the string names
    what the kill did when there was one.
    """
    if timeout is None:
        timeout = suite_timeout()
    with output_path.open('wb') as output:
        process = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, env=env,
            start_new_session=sys.platform != 'win32',
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           if sys.platform.startswith('win') else 0))
    try:
        return process.wait(timeout=timeout), ''
    except subprocess.TimeoutExpired:
        cleanup = kill_process_tree(process)
    except BaseException:
        # An interrupt inside the bounded wait must not leave the suite
        # running, and must not be replaced by the teardown's own failure.
        kill_process_tree(process)
        try:
            process.wait(timeout=CLEANUP_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    try:
        process.wait(timeout=CLEANUP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        cleanup += '; bounded reap timed out'
    except OSError as error:
        cleanup += f'; bounded reap failed: {error}'
    else:
        cleanup += '; process reaped'
    return process.returncode, cleanup
