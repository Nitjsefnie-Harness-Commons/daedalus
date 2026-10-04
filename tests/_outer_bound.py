"""A bound on a call that can wedge, held from OUTSIDE it.

A control written for a hang detector cannot use the detector: the machinery
that would have ended the wait is the machinery under test, so against a
launcher reverted to an unbounded one the control hangs and reports nothing.
What this module hands out is the escape that needs — a liveness escape for
the process the wait depends on, plus a failure naming what never arrived,
because a wait only the suite ceiling ends says which job timed out and
nothing about which assertion.

Three properties are load-bearing:

**The bound is outside the call.** It runs on its own thread and the block it
wraps is never interrupted. A bound that raised INTO the blocked thread would
have to be a signal, and a signal is what `windows-latest` does not have:
`signal.SIGALRM` and `signal.setitimer` do not exist there, so the two
controls that armed them raised `AttributeError` on the spot and four legs
of a twelve-cell matrix were red — the four `windows-latest` cells of
`scripts/ci/classify_changes.py`'s `FULL_MATRIX`, three operating systems by
four interpreters.

**It ends the wait rather than reporting it.** The child is killed, so a
`Popen.wait()` with no timeout returns and the block finishes. A bound that
only raised left the child running — the orphan the launcher spends
`tests/_processtree.py` existing to prevent.

**The kill needs a pid, and a wedged call cannot report one.** It is read from
a file the child writes itself. A child that never wrote one leaves the bound
nothing to kill: it still fails, and says which of the two shapes it was.
"""
import json
import threading
import time
from pathlib import Path

from _processtree import kill_process_tree

# The kill's own bound. It ends a process that has already stopped answering,
# so it is short and it is its own number: a caller's bound is the subject of
# the control that supplied it, and scaling this by it would tie the two.
KILL_BOUND_S = 5
# How long the bound keeps looking for a pid it did not find on the first
# read. A child slower to reach its first statement than the bound is to pass
# is the ordinary case on a loaded runner, and a bound that gave up at the
# first miss would leave exactly the wedge it exists to end.
ANNOUNCE_GRACE_S = KILL_BOUND_S
ANNOUNCE_POLL_S = 0.05


def announcing_pid(pid_file):
    """The statement a child runs to write its own pid where a bound reads it.

    Beside the bound rather than spelled by each control, because the file
    the bound reads and the statement that fills it are ONE contract: a
    control spelling its own could spell one the bound cannot read, and the
    bound would then report that it killed nothing while the child ran on.

    `json.dumps` for the path rather than an f-string, because a Windows path
    carries backslashes that are escape sequences in a JavaScript string.
    """
    return ('require("fs").writeFileSync('
            f'{json.dumps(str(pid_file))}, String(process.pid));')


class OuterBoundExpired(TimeoutError):
    """The bound on a wedged call passed; the child was ended first.

    A `TimeoutError` so a control can keep the handler it already had, but a
    class of its own so a bound that passed is never mistaken for a bare
    `TimeoutExpired` a launcher raised for a different reason.
    """


def outer_bound(bound_s, pid_file, what):
    """Hold `bound_s` on the block, from a thread that is not inside it.

    `pid_file` is where the child under test writes its own pid, and `what`
    names it in the failure — a report that says "the bound passed" without
    saying what was still waiting names no control.
    """
    return _OuterBound(bound_s, Path(pid_file), what)


class _OuterBound:
    """The bound's own state: a cancel, a thread, and what it found."""

    def __init__(self, bound_s, pid_file, what):
        self._bound_s = bound_s
        self._pid_file = pid_file
        self._what = what
        self._cancelled = threading.Event()
        self._fired = None
        # Built here and started on entry, so the attribute is a thread from
        # the moment `__exit__` can reach it.
        self._watcher = threading.Thread(
            target=self._watch, name='outer-bound', daemon=True)

    def __enter__(self):
        self._watcher.start()
        return self

    def __exit__(self, _kind, failure, _traceback):
        self._cancelled.set()
        self._watcher.join()
        if self._fired is None:
            return False
        # A block that raised after the kill raised it in consequence of the
        # kill, so the bound's own report is the one a reader needs; the
        # block's failure travels as the cause rather than being lost.
        raise OuterBoundExpired(self._fired) from failure

    def _watch(self):
        if self._cancelled.wait(self._bound_s):
            return
        self._fired = self._end_the_wait()

    def _end_the_wait(self):
        """Kill the child that is holding the wait, and say what was done."""
        pid = _announced_pid(self._pid_file)
        if pid is None:
            return (f'the bound of {self._bound_s}s passed with {self._what} '
                    f'still waiting and no pid announced at '
                    f'{self._pid_file.name}, so nothing was killed')
        killed = kill_process_tree(pid, KILL_BOUND_S)
        return (f'the bound of {self._bound_s}s passed: {self._what} was '
                f'still waiting, so the child it launched (pid {pid}) was '
                f'killed to end the wait — {killed}')


def _announced_pid(pid_file):
    """The pid a child wrote, or None when it never wrote one.

    Polled across `ANNOUNCE_GRACE_S` rather than read once. The bound has
    already fired by the time this runs, and the only thing it can still do
    for the block is end the wait; giving up on the first miss trades a slow
    start for a hang.
    """
    deadline = time.monotonic() + ANNOUNCE_GRACE_S
    while True:
        try:
            return int(pid_file.read_text(encoding='utf-8').split()[0])
        except (OSError, ValueError, IndexError):
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(ANNOUNCE_POLL_S)
