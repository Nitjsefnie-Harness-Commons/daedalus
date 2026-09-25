"""Terminate a child's whole process tree and report what the kill did.

Lives beside the callers rather than in either of them: `tests/_noderun.py`
exists for the same reason — two modules needed one launcher and importing
each other was a cycle. The cleanup bound is a parameter because the two
sites do not share a reason for one number: the speed harness bounds a shell
it drained, the Node gate bounds a child it launched, and a shared constant
would tie the two to each other's reasoning.

Every outcome is named. A caller that discards this string has thrown away
the only evidence of what happened to a process that had already stopped
answering, which is the case the string exists for.
"""
import os
import signal
import subprocess
import sys


def cleanup_process_tree(process, cleanup_timeout):
    """Kill `process`'s tree, reap it, and return what each step did.

    `start_new_session=True` on the launch is what makes the POSIX half
    possible, and it is applied only where it exists: on Windows the process
    group is not a thing `os` can signal, so the tree is killed through
    `taskkill /T`, which takes the whole tree by pid instead.
    """
    return _reap(process, _kill_tree(process, cleanup_timeout),
                 cleanup_timeout)


def _kill_tree(process, cleanup_timeout):
    """Terminate the tree and describe the outcome, never raising."""
    if sys.platform == 'win32':
        return _taskkill(process, cleanup_timeout)
    try:
        process_group = os.getpgid(process.pid)
    except ProcessLookupError:
        return 'process group was already gone before cleanup'
    except OSError as error:
        return f'process-group lookup failed: {error}'
    try:
        if process_group == os.getpgrp():
            process.kill()
            return 'direct process kill requested for the current group'
        os.killpg(process_group, signal.SIGKILL)
        return f'process group {process_group} killed'
    except ProcessLookupError:
        return f'process group {process_group} was already gone'
    except OSError as error:
        return f'process-group kill failed: {error}'


def _taskkill(process, cleanup_timeout):
    """Kill the tree the way Windows can: by pid, with the tree flag."""
    try:
        result = subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(process.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:
        return f'taskkill timed out after {cleanup_timeout}s'
    except OSError as error:
        return f'taskkill failed to run: {error}'
    if result.returncode == 0:
        return 'taskkill completed successfully'
    return f'taskkill failed with exit code {result.returncode}'


def _reap(process, cleanup, cleanup_timeout):
    """Reap without restoring the unbounded wait the cleanup just replaced."""
    try:
        process.wait(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:
        cleanup += '; bounded reap timed out'
    except OSError as error:
        cleanup += f'; bounded reap failed: {error}'
    else:
        return cleanup + '; process reaped'
    try:
        process.kill()
    except ProcessLookupError:
        cleanup += '; fallback process was already gone'
    except OSError as error:
        cleanup += f'; fallback process kill failed: {error}'
    else:
        cleanup += '; fallback process kill requested'
    try:
        process.wait(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:
        return cleanup + '; process reap still timed out'
    except OSError as error:
        return cleanup + f'; process reap failed: {error}'
    return cleanup + '; process reaped after fallback'
