#!/usr/bin/env python3
"""One per-suite wall-clock bound, and the bounded launch that enforces it.

`run_tests.py` and `scripts/ci/coverage_suites.py` both launch every
suite in `tests/`, so both are ended by a wedge in any one of them. The
bound lives here rather than in either launcher because a number two
files each hold is two premises: widening one leaves the other bounding
the value before, and neither file says so.

The bound is a parameter of the launch, never a value captured when this
module was written, so a control that shrinks the constant at runtime
sees the launcher obey the smaller value.

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
import time

DEFAULT_SUITE_TIMEOUT_S = 900
TIMEOUT_ENV = 'DAEDALUS_SUITE_TIMEOUT'
# One number for every bounded step of a teardown, because the steps share
# the reason it is bounded: a cleanup that waits for ever hands the hang
# back through the error path that was supposed to report it.
CLEANUP_TIMEOUT_S = 10
GRACE_POLL_S = 0.05


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
    """Ask `process`'s whole tree to stop, then insist, and name the outcome.

    Two phases, because neither order alone is right. A SIGKILL first is
    one signal shorter, and it takes away what `pyproject.toml`'s
    `sigterm = true` exists for: a suite that terminates still writes
    what it measured. A SIGTERM first with nothing after it leaves a
    suite that ignores the request running for ever, which is the hang
    this bound exists to end. So the request goes first, a bounded grace
    follows, and the escalation goes out whatever happened in between.

    Never raises. Every outcome is a string rather than a discard: a
    caller that drops it has thrown away the only evidence of what
    happened to a process that had already stopped answering.
    """
    try:
        return _ask_and_insist(process)
    except Exception as error:  # pylint: disable=broad-except
        # A cleanup failure must not replace the expiry the caller is
        # about to report with an unrelated one.
        return f'process-tree kill raised {error!r}'


def _ask_and_insist(process):
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
        return _kill_direct(process, 'child shares the launcher group')
    asked = _signal_group(group, signal.SIGTERM)
    if asked is not None:
        return asked
    # Resolved once, before the direct child is reaped: after that its pid
    # is free to be reused, and `os.getpgid` would name a group that is
    # not this suite's.
    stopped = not _still_running(process, CLEANUP_TIMEOUT_S)
    insisted = _signal_group(group, signal.SIGKILL)
    if stopped:
        # The suite's own exit empties its group, so the escalation is
        # aimed at a group that may no longer exist -- and a group that
        # DOES still exist means something of the suite's outlived the
        # request, which the record has to say rather than leave as an
        # absence between two clauses that both sound complete. A signal
        # that goes out proves a member was there; a ProcessLookupError
        # proves one is not.
        note = insisted or 'the escalation reached what was still in it'
        return f'process group {group} asked to stop and the suite did; {note}'
    if insisted is not None:
        return f'{insisted} after {CLEANUP_TIMEOUT_S} s of grace'
    return (f'process group {group} ignored the request and was killed '
            f'after {CLEANUP_TIMEOUT_S} s of grace')


def _signal_group(group, sig):
    """None when the signal went out; the outcome string when it did not."""
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        return f'process group {group} was already gone'
    except OSError as error:
        return f'process-group {sig.name} failed: {error}'
    return None


def _still_running(process, seconds):
    """Whether the suite is still running, against a deadline.

    The direct child and not the group: the child stays in its own group
    as a zombie until the caller reaps it, so the group never empties
    inside this window and asking about the group would answer "still
    running" every time.
    """
    deadline = time.monotonic() + seconds
    while process.poll() is None:
        if time.monotonic() >= deadline:
            return True
        time.sleep(GRACE_POLL_S)
    return False


def _kill_direct(process, reason):
    """The only signal left when the tree cannot be signalled as a group.

    Doing nothing would leave the suite running and make every line the
    caller writes about it a lie, so the direct child is killed and the
    record says that is all that was killed.
    """
    try:
        process.kill()
    except OSError as error:
        return f'{reason}; direct kill failed: {error}'
    return f'{reason}; only the direct child was killed'


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


def launch_suite(name, argv, *, cwd, output_path, env=None, timeout):
    """Start one suite, bound it, and end its whole tree if it overruns.

    Returns `(returncode, cleanup)`. `cleanup` is empty exactly when the
    suite finished inside the bound, so a caller can tell a wedge from a
    suite that failed on its own by the value alone, and the string names
    what the kill did when there was one.
    """
    process = None
    try:
        # The spawn is inside the guard, not beside it: a failure closing
        # the output file, or an interrupt between the two, must reach the
        # teardown below rather than propagate with the suite running in a
        # session nothing else can reach.
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
        if process is not None:
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
