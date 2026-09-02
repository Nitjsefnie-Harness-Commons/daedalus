"""A bounded post-kill drain for child processes the suites start.

A drain after ``kill()`` returns when the pipes close, not when the process
dies: a grandchild that inherited them keeps them open, so an unbounded drain
blocks forever and the suite holding it stops producing output rather than
failing. Every post-kill drain in the suites goes through :func:`drain` here,
which bounds the wait and reports one that would not end.
"""

import subprocess
import sys

DRAIN_TIMEOUT_S = 10.0


def kill_and_drain(process, drain_timeout=DRAIN_TIMEOUT_S):
    """Kill ``process``, drain its pipes under ``drain_timeout``, never raise.

    Returns ``(drain_timed_out, stdout, stderr)``. A drain that outlives the
    bound has its pipes force-closed and the process gets one bounded wait;
    a process that outlives even that stays unreaped. Whatever the drain had
    already read comes back either way, and the timeout is reported on
    stderr -- visible in suite output -- instead of raised, so a ``finally``
    block keeps the failure that reached it. ``stdout``/``stderr`` follow
    the pipe mode the process was opened with, except that a timed-out drain
    may hand back bytes where a clean drain hands back str, so a caller that
    formats them normalizes first.
    """
    process.kill()
    try:
        out, err = process.communicate(timeout=drain_timeout)
        return False, out, err
    except subprocess.TimeoutExpired as failure:
        out, err = failure.stdout, failure.stderr
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        try:
            process.wait(timeout=drain_timeout)
        except subprocess.TimeoutExpired:
            # The reaping failure is reported below; raising here would
            # replace the failure that reached a finally block.
            pass
        print(f'kill_and_drain: drain did not end within {drain_timeout}s; '
              f'pid {process.pid}, returncode {process.returncode!r}',
              file=sys.stderr)
        return True, out, err
