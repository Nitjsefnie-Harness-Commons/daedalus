"""Terminate a child's whole process tree and report what the kill did.

Beside the callers rather than in either of them (`tests/_noderun.py`
imports it too): two modules needed one launcher, and mutual import was
a cycle.
"""
import ctypes
import os
import signal
import subprocess
import sys
import time


# A killed process stays in the table until reaped, and a tree kill does
# not land the instant it is issued, so the receipt polls to its bound.
SETTLE_S = 5
SETTLE_POLL_S = 0.05
# The least right Windows grants that still answers the question.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# The ONLY null-handle code that means there was no such pid.
_ERROR_INVALID_PARAMETER = 87
# `STILL_ACTIVE` (259): the one code `GetExitCodeProcess` reports for running.
_STILL_ACTIVE = 259


def process_is_gone(pid, settle_s=SETTLE_S):
    """Whether `pid` is no longer a live process, polled to a bound.

    Non-destructive: POSIX `os.kill(pid, 0)` sends no signal; Windows'
    opens the process it asks about, so Windows asks the API instead.
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
    """Whether Windows still holds `pid`, asked of the API, never a probe."""
    return _open_handle_says_live(_windows_kernel32(), pid)


def _windows_kernel32():
    """`kernel32` with `use_last_error=True`, so a null handle's error code
    is retrievable; `tests/test_parent_watch.py` opens it the same way."""
    win_dll = getattr(ctypes, 'WinDLL')
    kernel32 = win_dll('kernel32', use_last_error=True)
    # Pointer-sized, or a 64-bit handle truncates to null — see below.
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int,
                                     ctypes.c_ulong)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    return kernel32


def _open_handle_says_live(kernel32, pid):
    """Whether this `OpenProcess` names a process that still exists.

    A handle is NOT a yes: Windows keeps a terminated process's object
    open while any handle survives (`taskkill /F` leaves one), so the
    question is asked of the HANDLE, through `GetExitCodeProcess`; a
    null handle is not a no either, and the code behind it decides:

    * `ERROR_INVALID_PARAMETER` (87) — the only code that positively means
      THIS PID WAS NEVER THERE. The one value that reports gone.
    * `ERROR_ACCESS_DENIED` (5) — the pid is running and privileged.
    * anything else is no evidence of anything, so it reports LIVE.

    The default is live, not gone: a receipt that invents a kill is the
    one failure this mechanism exists to prevent; an unclassified code
    costs a timeout, the cheap direction.
    """
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            # The process exists and its exit code is unreadable, which is no
            # evidence that it is running: a receipt must not report a kill
            # it did not see, and must not invent one either.
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_group(process):
    """The group id of a process, or None where there is no group.

    The lookup a caller cannot make later: a reaped pid has no entry.
    `None` off POSIX, asked before `os.getpgid` is named -- Windows has
    no such name.
    """
    if sys.platform == 'win32':
        return None
    return os.getpgid(process.pid)


def cleanup_process_tree(process, cleanup_timeout):
    """Kill `process`'s tree, reap it, and return what each step did.

    The reap stays here: this module cannot wait on a process it did
    not launch.
    """
    try:
        killed = _kill_tree(process, cleanup_timeout)
    except Exception as error:  # pylint: disable=broad-except
        # Keep a cleanup failure from masking the expiry the caller is
        # about to report with an unrelated one.
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

    A pid rather than a `Popen`: `tests/_outer_bound.py` reads a pid a
    wedged child announced, and reaping is `cleanup_process_tree`'s
    half. Two phases on Windows: a `CTRL_BREAK_EVENT` request to the
    group the launch's `CREATE_NEW_PROCESS_GROUP` flag created, the
    `process_is_gone` bound as the grace, then `taskkill /F /T` whatever
    the request did.
    """
    if sys.platform == 'win32':
        return _ask_and_insist_windows(pid, cleanup_timeout)
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


def taskkill_argv(pid):
    """The exact argv a Windows tree kill sends: the tree's one spelling."""
    return ['taskkill', '/F', '/T', '/PID', str(pid)]


def _ask_and_insist_windows(pid, cleanup_timeout):
    """Ask the tree to stop, resolve the grace once, insist, name both.

    A failed request does not cancel the escalation: unlike the POSIX
    group lookup, the phases are addressed differently here.
    """
    asked = _ctrl_break(pid)
    stopped = process_is_gone(pid, cleanup_timeout)
    insisted = _taskkill(pid, cleanup_timeout, stopped)
    if asked is not None:
        note = insisted or 'the escalation reached what was still in it'
        return f'{asked}; {note}'
    if stopped:
        note = insisted or 'the escalation reached what was still in it'
        return f'process tree {pid} asked to stop and the tree did; {note}'
    if insisted is not None:
        return (f'process tree {pid} ignored the request, and after '
                f'{cleanup_timeout} s of grace: {insisted}')
    return (f'process tree {pid} ignored the request and was killed by '
            f'taskkill /F after {cleanup_timeout} s of grace')


def _ctrl_break(pid):
    """None when the request went out; the outcome string when it did not."""
    try:
        # A Windows-only `signal` member; the platform fork above sent
        # POSIX elsewhere, so every interpreter reaching this line has it.
        # pylint: disable-next=no-member
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    except ProcessLookupError:
        return f'process tree {pid} was already gone'
    except OSError as error:
        return f'CTRL_BREAK_EVENT failed: {error}'
    return None


def _taskkill(pid, cleanup_timeout, stopped):
    """The escalation, reading its exit code against what the tree did.

    After an answered request the pid is usually gone already, so a
    nonzero code says the tree ended at the request; beside a tree that
    ignored the request it says the tree may still be running.
    """
    try:
        result = subprocess.run(
            taskkill_argv(pid),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:
        return (f'taskkill /F gave up after {cleanup_timeout}s, so the '
                'tree may still be running')
    except OSError as error:
        return f'taskkill /F could not run: {error}'
    if result.returncode == 0:
        return None
    if stopped:
        return (f'the escalation found the tree already gone '
                f'(taskkill exited {result.returncode})')
    return (f'taskkill /F exited {result.returncode}, so the tree may '
            'still be running')


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
