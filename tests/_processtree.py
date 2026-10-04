"""Terminate a child's whole process tree and report what the kill did.

Lives beside the callers rather than in either of them, as
`tests/_noderun.py` does: two modules needed one launcher and importing
each other was a cycle. The cleanup bound is a parameter because the two
sites do not share a reason for one number. Every outcome is named: a
caller that discards this string has thrown away the only evidence of
what happened to a process that had already stopped answering. The
RECEIPT (`process_is_gone`) lives here for the same reason the kill
does: it is the only reading of a kill that is not the string the kill
wrote about it.
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
# The least right Windows grants that still answers the question.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# The ONLY null-handle code that means there was no such pid.
_ERROR_INVALID_PARAMETER = 87
# `STILL_ACTIVE` (259) is the one code `GetExitCodeProcess` reports for a
# process still running; every other value is an exit already published.
_STILL_ACTIVE = 259


def process_is_gone(pid, settle_s=SETTLE_S):
    """Whether `pid` is no longer a live process, polled to a bound.

    The receipt for a kill, and non-destructive on every platform: POSIX
    `os.kill(pid, 0)` sends no signal, while on Windows it opens the
    process it is asked about and can end it, so there the answer is
    asked of the API instead. That split is why this is not a one-liner
    over `os.kill`.
    """
    deadline = time.monotonic() + settle_s
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
    """Whether Windows still holds `pid`, asked of the API, not a probe.

    `os.kill(pid, 0)` here can end the process it asks about; the API
    question below answers in BOTH directions — a handle, or an error
    code, because a null handle is two conditions, not one.
    """
    return _open_handle_says_live(_windows_kernel32(), pid)


def _windows_kernel32():
    """`kernel32` loaded with `use_last_error=True`, so the error code
    behind a null handle is retrievable; `tests/test_parent_watch.py`
    opens it the same way."""
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
    open while any handle to it survives, and `taskkill /F` leaves one,
    so the question is asked of the HANDLE, through `GetExitCodeProcess`.
    A null handle is not a no either, and which way it fails depends on
    the code behind it:

    * `ERROR_INVALID_PARAMETER` (87) — the only code that positively means
      THIS PID WAS NEVER THERE. The one value that reports gone.
    * `ERROR_ACCESS_DENIED` (5) — the pid is running and this process may
      not open it. A privileged child, which is what a tree kill leaves.
    * anything else — `ERROR_INVALID_HANDLE` (6) and every code the API may
      grow — is not evidence of anything, so it reports LIVE.

    The default is live, not gone: a receipt that invents a kill is the
    one failure this mechanism exists to prevent, and an unclassified
    code costs a timeout, the cheap direction. `kernel32` is a parameter
    so this is drivable off Windows, the only place the real loader runs.
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
    `None` off POSIX, and the platform is asked BEFORE the name is
    spelled, because `os.getpgid` does not exist on Windows and naming it
    unguarded raises `AttributeError` there. A caller that passes `None`
    on to `cleanup_process_group` gets a no-op that says why.
    """
    if sys.platform == 'win32':
        return None
    return os.getpgid(process.pid)


def cleanup_process_group(group, cleanup_timeout):
    """Kill a process group by an id, and report what the kill did.

    The sibling of `cleanup_process_tree` for a caller holding a group it
    captured before reaping its leader. The zero signal first says
    whether there is anything left to kill without signalling a number
    the kernel may since have handed on. POSIX only, and a reaped pid
    leaves `taskkill` nothing to name, so the caller keeps whatever
    teardown it already had — anything the group would have reached is
    left to the caller on that platform, worth knowing rather than
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

    The kill itself is `kill_process_tree`, which a caller that has only
    a pid can reach; the reap stays here, because a process this module
    did not launch is not one it can wait on.
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

    A pid rather than a `Popen`, because a caller that never launched the
    process still has to end it: `tests/_outer_bound.py` reads a pid a
    wedged child announced and cannot reap what it did not start. Reaping
    is the caller's half and stays in `cleanup_process_tree`.

    Two phases on Windows, as on POSIX: a forced kill first takes away
    the request a child could still answer, and a request alone leaves a
    child that ignores it running for ever. The request is
    `CTRL_BREAK_EVENT`, aimed at the group the launch's
    `CREATE_NEW_PROCESS_GROUP` flag created (the forks in
    `tests/_speedharness.py` and `tests/_noderun.py` set it), the grace
    is the module's own `process_is_gone` bound, and the escalation is
    `taskkill /F /T` -- the tree by pid, where the group is not a thing
    `os` can signal.
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
    """The exact argv a Windows tree kill sends.

    The tree's one spelling of it, and a function rather than a constant
    so a control that asserts what a launcher sent names the same builder
    instead of writing a second copy of these flags beside the assertion.
    """
    return ['taskkill', '/F', '/T', '/PID', str(pid)]


def _ask_and_insist_windows(pid, cleanup_timeout):
    """Ask the tree to stop, resolve the grace once, insist, name both.

    The escalation goes out whatever the request did: the two phases are
    addressed differently on this platform, so a failed request does not
    cancel the escalation the way a failed group lookup does on POSIX.
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
        # The event is a Windows-only `signal` member, and every
        # interpreter that reaches this line has it: the platform fork
        # sent POSIX elsewhere before this ran.
        # pylint: disable-next=no-member
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    except ProcessLookupError:
        return f'process tree {pid} was already gone'
    except OSError as error:
        return f'CTRL_BREAK_EVENT failed: {error}'
    return None


def _taskkill(pid, cleanup_timeout, stopped):
    """The escalation, and the honest reading of the exit code it returned.

    `/F` goes out whatever the request did, and what its exit code says
    depends on what the tree did with the request: after an answered one
    the pid is usually gone already, so a nonzero code there says the
    tree ended at the request, while beside a tree that ignored the
    request it says the tree may still be running — the record keeps the
    two apart so a stopped tree is never reported as a live one. Returns
    None when the terminations went out, and the outcome string else.
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
