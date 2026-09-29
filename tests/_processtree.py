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

The RECEIPT lives here for the same reason the kill does: `process_is_gone`
is the only reading of a kill that is not the string the kill wrote about
it, and a module that owns the kill is the one that can be asked whether it
happened.
"""
import ctypes
import os
import signal
import subprocess
import sys
import time


# The receipt's own two figures. A killed process stays in the table until
# something reaps it, so one read of a just-killed grandchild calls it alive;
# and a tree kill does not take effect the instant it is issued, so one read
# of a survivor calls it gone.
SETTLE_S = 5
SETTLE_POLL_S = 0.05
# `PROCESS_QUERY_LIMITED_INFORMATION` is the least right Windows grants
# that still answers the question; a broader mask asks for more access
# than a receipt has any business holding.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# `ERROR_ACCESS_DENIED` — the null handle that means a process is
# RUNNING and this one may not open it. `ERROR_INVALID_PARAMETER`
# is the other null handle and means the pid is not there.
_ERROR_ACCESS_DENIED = 5
# `ERROR_INVALID_PARAMETER` — the null handle that means there was no
# such pid. The pair is the whole classification.
_ERROR_INVALID_PARAMETER = 87


def process_is_gone(pid, settle_s=SETTLE_S):
    """Whether `pid` is no longer a live process, polled to a bound.

    The receipt for a kill, and beside the kill rather than in a control,
    because a probe that reports what the kill did is only evidence if it
    can report the other thing too.

    Non-destructive on every platform, which is the whole of it: a probe
    that ends the process it is probing is not a probe. POSIX
    `os.kill(pid, 0)` sends no signal — it raises for a pid the kernel has
    reaped and returns for a live one. Windows has no such call, and
    `os.kill(pid, 0)` there opens the process it is asked about and can end
    it, so the answer is asked of the API instead. That is the split
    `tests/test_speedharness.py` records, and it is why this is not a
    one-liner over `os.kill`.
    """
    deadline = time.monotonic() + settle_s
    while True:
        if not _process_is_live(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(SETTLE_POLL_S)


def _process_is_live(pid):
    """One read of liveness, per platform, and never a signal."""
    if sys.platform == 'win32':
        return _windows_is_live(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        # It exists and belongs to somebody else, which is alive.
        return True
    except OSError:
        return False
    return True


def _windows_is_live(pid):
    """Whether Windows still holds `pid`, asked of the API rather than a probe.

    `os.kill(pid, 0)` is not that question here: it opens the process it is
    asked about and can end it, so a receipt written over it is a probe that
    changes what it measures. The API answers in BOTH directions — a handle,
    or an error code, because a null handle is two conditions, not one.
    """
    return _open_handle_says_live(_windows_kernel32(), pid)


def _windows_kernel32():
    """`kernel32`, loaded so that a failure can be read.

    `ctypes.windll` is this library with `use_last_error=False`, and without
    that the error code behind a null handle is not retrievable.
    `tests/test_parent_watch.py` opens it the same way.
    """
    win_dll = getattr(ctypes, 'WinDLL')
    kernel32 = win_dll('kernel32', use_last_error=True)
    # Pointer-sized, or a 64-bit handle truncates to null — see below.
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int,
                                     ctypes.c_ulong)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    return kernel32


def _open_handle_says_live(kernel32, pid):
    """Whether this `OpenProcess` names a process that still exists.

    A handle is a yes. A null handle is NOT a no: `ERROR_INVALID_PARAMETER`
    is a pid that was never there, and `ERROR_ACCESS_DENIED` is a pid that
    is running and that this process may not open — a privileged child, which
    is what a tree kill leaves behind. Reading the second as the first
    reports a kill that did not happen.

    `kernel32` is a parameter so this is drivable off Windows, which is the
    only place the real loader runs.
    """
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == _ERROR_ACCESS_DENIED


def process_group(process):
    """The group id of a process, or None where there is no group.

    The lookup a caller cannot make later: a reaped pid has no entry, so
    a teardown that reaches this after the reap has nothing to derive a
    group from. Capture here, kill with `cleanup_process_group`.

    `None` off POSIX, and the platform is asked BEFORE the name is
    spelled: `os.getpgid` does not exist on Windows, and naming it in
    the body of a function whose callers are not all guarded raises
    `AttributeError` there rather than answering anything. A caller that
    passes `None` on to `cleanup_process_group` gets a no-op that says
    why, which is the honest outcome — there is nothing to name.
    """
    if sys.platform == 'win32':
        return None
    return os.getpgid(process.pid)


def cleanup_process_group(group, cleanup_timeout):
    """Kill a process group by an id, and report what the kill did.

    The sibling of `cleanup_process_tree` for the case that function
    cannot serve: a caller holding a group it captured before reaping its
    leader. A group exists exactly while one of its members does, so the
    zero signal first is what says whether there is anything left to kill
    without signalling a number the kernel may since have handed on.

    POSIX only, and said here rather than only in the sibling: there is no
    process group on Windows at all, so a `None` from `process_group`
    arrives here and is a no-op. A reaped pid also leaves `taskkill`
    nothing to name, so the caller keeps whatever teardown it already had
    — which means anything the group would have reached is left to the
    caller on that platform, and that is worth knowing rather than
    discovering.
    """
    if group is None:
        return ('no process group to kill: this platform has none, and a '
                'reaped leader leaves a tree kill nothing to name')
    if sys.platform == 'win32':
        return 'process group signals do not exist on Windows'
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return 'process group was already gone before cleanup'
    except OSError as error:
        return f'process-group lookup failed: {error}'
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        return f'process group {group} was already gone'
    except OSError as error:
        return f'process-group kill failed: {error}'
    return f'process group {group} killed'


def cleanup_process_tree(process, cleanup_timeout):
    """Kill `process`'s tree, reap it, and return what each step did.

    The kill itself is `kill_process_tree`, which a caller that has only a pid
    can reach; the reap stays here, because a process this module did not
    launch is not one it can wait on.
    """
    try:
        killed = _kill_tree(process, cleanup_timeout)
    except Exception as error:  # pylint: disable=broad-except
        # Keep a cleanup failure from masking the expiry the caller is
        # about to report. An unexpected exception here would replace a
        # named, classified failure with an unrelated one, which is the
        # one thing the caller's error class exists to prevent.
        killed = f'process-tree kill raised {error!r}'
    try:
        return _reap(process, killed, cleanup_timeout)
    except Exception as error:  # pylint: disable=broad-except
        return f'{killed}; cleanup reap raised {error!r}'


def _kill_tree(process, cleanup_timeout):
    """Terminate the tree and describe the outcome, never raising."""
    return kill_process_tree(process.pid, cleanup_timeout)


def kill_process_tree(pid, cleanup_timeout):
    """Terminate the tree rooted at `pid` and describe what the kill did.

    A pid rather than a `Popen`, because a caller that never launched the
    process still has to end it: `tests/_outer_bound.py` reads a pid a
    wedged child announced and cannot reap what it did not start. Reaping is
    the caller's half and stays in `cleanup_process_tree`, which owns a handle
    it can wait on.

    `start_new_session=True` on the launch is what makes the POSIX half
    possible, and it is applied only where it exists: on Windows the process
    group is not a thing `os` can signal, so the tree is killed through
    `taskkill /T`, which takes the whole tree by pid instead.
    """
    if sys.platform == 'win32':
        return _taskkill(pid, cleanup_timeout)
    try:
        process_group = os.getpgid(pid)
    except ProcessLookupError:
        return 'process group was already gone before cleanup'
    except OSError as error:
        return f'process-group lookup failed: {error}'
    try:
        if process_group == os.getpgrp():
            os.kill(pid, signal.SIGKILL)
            return 'direct process kill requested for the current group'
        os.killpg(process_group, signal.SIGKILL)
        return f'process group {process_group} killed'
    except ProcessLookupError:
        return f'process group {process_group} was already gone'
    except OSError as error:
        return f'process-group kill failed: {error}'


def _taskkill(pid, cleanup_timeout):
    """Kill the tree the way Windows can: by pid, with the tree flag."""
    try:
        result = subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
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
