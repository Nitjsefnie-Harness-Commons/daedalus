"""A bounded post-kill drain for the child processes the suites start.

A drain after ``kill()`` returns when the pipes close, not when the process
dies: a grandchild that inherited them keeps them open, so an unbounded drain
blocks forever and the suite holding it stops producing output rather than
failing.
"""

import subprocess
import sys

DRAIN_TIMEOUT_S = 10.0


def kill_and_drain(process, drain_timeout=DRAIN_TIMEOUT_S):
    """Kill, drain under ``drain_timeout``, never raise.

    Returns ``(drain_timed_out, stdout, stderr)``. A drain past the bound is
    reported on stderr rather than raised, so a ``finally`` block keeps the
    failure that reached it. ``stdout``/``stderr`` come back in the pipe mode
    the process was opened with, except that a timed-out drain hands back
    bytes -- or None for a pipe with nothing unread.
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
