"""The waits a watcher-budget case measures its children through.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once, as
the failure-reporting backstop on the single wait with no live process to
give up on, and once more as the bound on a reap that follows a kill and
can only return.
"""
import subprocess
import sys
import threading
import time

from _processtree import cleanup_process_tree
from _watcher_fixtures import IDLE_POLL_BOUND

# The backstop on the one wait that cannot end on a state. 90s is the
# figure tests/test_parent_watch.py already waits a real grandchild's death
# with, and the suites job allows twenty minutes for the whole file, so a
# survivor is named long before the job's own limit ends the run nameless.
BACKSTOP = 90
# A wake-up interval, never a deadline: the waits below end on what the
# process under test did.
POLL = 0.05
# The bound on the reap that follows a cancel, and on the cancel itself.
CANCEL_BOUND = 60
# How many gh calls one poll may log before `await_polls` gives up on the
# index not advancing. Generous by construction: the budget suite's own
# bound on one idle poll is IDLE_POLL_BOUND (1), and the widest loop any
# control measures costs 2 requests a poll
# (test_a_loop_that_repeats_its_last_request_costs_two), so 4 leaves
# headroom above every measured poll - and a poll that did exceed it would
# already have been refused by the idle bound.
POLL_WIDTH = 4


class Stream:
    """One child stream, drained, with its end carried as a state.

    The pump publishes under the condition's lock, so a waiter holding that
    lock while it looks cannot miss a line published between the look and the
    wait.
    """

    def __init__(self, changed=None):
        self.lines = []
        self.changed = (threading.Condition() if changed is None
                        else changed)
        self.ended = False

    def publish(self, line):
        with self.changed:
            self.lines.append(line)
            self.changed.notify_all()

    def pump(self, pipe):
        for line in pipe:
            self.publish(line.rstrip('\n'))
        with self.changed:
            self.ended = True
            self.changed.notify_all()


class ChildProcess:
    """A started process, with the two streams the waits read drained.

    The contract every wait below consumes: `alive()`, `captured()`, a
    `proc` carrying the exit code, and the streams `await_lines` reads. A
    subclass supplies the argv and the environment and whatever else its
    launch needs; the kill is here, because a kill naming one process
    abandons whatever that process had already spawned.

    The child leads a process group of its own, so `stop` is a
    cancellation rather than an abandonment. A group is also what lets a
    graceful signal reach one child and not the runner: on Windows that
    is the new-process-group flag `CTRL_BREAK_EVENT` needs, and on POSIX
    a new session means a signal sent to the pid is never the runner's.
    """

    def __init__(self, argv, env):
        self.argv = argv
        self.proc = subprocess.Popen(
            argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            start_new_session=True,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           if sys.platform.startswith('win') else 0))
        self.out = Stream()
        self.err = Stream()
        for pipe, sink in ((self.proc.stdout, self.out),
                           (self.proc.stderr, self.err)):
            threading.Thread(target=sink.pump, args=(pipe,),
                             daemon=True).start()

    def alive(self):
        return self.proc.poll() is None

    def captured(self):
        """Everything the child printed, for a wait's failure report."""
        return '\n'.join(self.out.lines + self.err.lines)

    def stop(self):
        if self.proc.poll() is None:
            _cancel(self.proc)
        self.proc.wait(timeout=CANCEL_BOUND)
        return self.proc.returncode


def _cancel(proc):
    """End the child's whole tree, through the one module that owns a kill.

    A kill names one process, and the `gh` a watcher had already spawned is
    not it: the orphan keeps running, and keeps appending to the call log a
    measurement is still reading, after the child it belonged to is gone.
    The tree has to go, on both platforms, and `tests/_processtree.py` is
    where that lives.

    **This is deduplication, not a repair.** `ChildProcess` launches with
    `start_new_session=True`, so the child is its own session and group
    leader from `Popen` returning, and the local spelling that passed
    `proc.pid` AS the group id resolved to the same group the owner's
    lookup does - always, and not because of luck. The two spellings
    already agreed; the guard in `tests/test_noderun_deadline.py` is what
    disagreed with them, because a second copy of a kill is a second
    mechanism wearing the same name. What rests on that equivalence, and
    is worth saying where the code relies on it: `start_new_session=True`
    AND the child being unreaped. The second half is load-bearing - a
    reaped pid can be recycled, and a recycled pid is not its own group.

    The `proc.kill()` below is the local copy's own fallback, kept. The
    owner returns a description when a group is already gone and does not
    fall back to a direct kill, so a bare delegation would drop it; here
    it fires on the one state where a direct kill is wanted, the child
    still running, rather than on the owner's wording. It is contained
    for the reason the owner contains its own steps: an uncontained
    cleanup failure is one more thing that can replace the expiry the
    caller is about to report, and this one would replace it with an
    `OSError` from the very call meant to be the last resort.

    **What delegating cost, in time.** `ChildProcess.stop` used to wait
    once, for `CANCEL_BOUND`, and that was the whole bound. The owner
    waits for its own reap, and then `stop` waits again, so the worst
    case is now `2 x CANCEL_BOUND` inside the owner plus `stop`'s own
    `CANCEL_BOUND` - three waits, not one, on a child that will not die.
    The owner returns no description to report, and `stop` returns the
    process's own return code, so nothing reads a reason.
    """
    killed = cleanup_process_tree(proc, CANCEL_BOUND)
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception as error:  # pylint: disable=broad-except
            killed += f'; direct fallback kill raised {error!r}'
    return killed


def await_lines(stream, match, count, what):
    """The first `count` lines of `stream` that match.

    The early exit is a state rather than a duration - a stream at its end
    can never print the line again - and the failure carries everything
    the process did print, which is what says which line arrived instead.
    A process that stays up and stays silent leaves this wait nothing to
    end it, and the hung job naming this wait is the trade the repository
    takes deliberately (the `_await_alive` in
    tests/test_stream_lifecycle.py).
    """
    while True:
        with stream.changed:
            found = [line for line in stream.lines if match(line)]
            if len(found) >= count:
                return found
            if stream.ended:
                raise AssertionError(
                    f'{what}: the stream ended first:\n'
                    + '\n'.join(stream.lines))
            stream.changed.wait()


def await_calls(fake, count, child, what):
    """The call log, once it holds `count` entries.

    The log is a file the watcher children append to, so there is no signal
    to wait on and this polls the record. A child that has exited can make
    no further call, which is the state that ends the wait early, with the
    child's own output in the failure. A child that stays up and never
    calls leaves this wait nothing to end it, and the hung job naming this
    wait is the same trade `await_lines` takes.
    """
    while True:
        calls = fake.calls()
        if len(calls) >= count:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
        time.sleep(POLL)


def await_polls(fake, polls, child, what, width=POLL_WIDTH):
    """The call log, once it carries `polls` distinct poll markers.

    A watcher names its own poll boundary by publishing an index its `gh`
    children inherit, so the log says how many polls ran rather than leaving
    the count to be inferred from the calls. Polls are counted rather than
    calls because a poll's width is data-dependent - a follow-up query and a
    paginated connection both make it wider - and the measurement is the one
    thing that must not assume it.

    The last marker seen names a poll that may still be in flight, so the
    caller reads the ones before it.

    The ceiling is in CALLS, not in seconds, and that is the whole reason it
    can exist. A bound in time would fail a slow or loaded runner that is
    making progress; this one is monotone in evidence, so a slow runner
    reaches it later or not at all. What it does catch is the shape no other
    arm can: a child that stays up, healthy, and republishes the same index
    forever, which neither the distinct count nor `child.alive` can end.
    """
    ceiling = polls * width
    while True:
        calls = fake.calls()
        markers = {call.get('poll') for call in calls}
        if len(markers) >= polls:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
        if len(calls) >= ceiling:
            raise AssertionError(
                f'{what}: the poll index did not advance - {len(calls)} gh '
                f'call(s) logged, markers seen {sorted(markers)}, {polls} '
                f'advancing marker(s) expected within {ceiling} call(s). '
                f'This log cannot tell a frozen index from one very wide '
                f'poll; IDLE_POLL_BOUND ({IDLE_POLL_BOUND}) is what refuses a '
                f'poll wider than an idle one.')
        time.sleep(POLL)


def await_gone(pids, child, what, alive, backstop=BACKSTOP):
    """Wait for those processes to be gone, reporting live state at expiry.

    A wait for a child to DIE is the one wait with no live process to give
    up on: the parent is reaped, the pids are what is being waited for, and
    the only thing that can end the wait is a real survivor - which is the
    defect itself. The bound is therefore a failure report and not a health
    margin: it never sets the passing wall.
    """
    started = time.monotonic()
    while any(alive(pid) for pid in pids):
        if time.monotonic() - started >= backstop:
            survivors = [pid for pid in pids if alive(pid)]
            raise AssertionError(
                f'{what}: pids still alive {survivors}, parent exit '
                f'{child.proc.returncode}:\n{child.captured()}')
        time.sleep(POLL)
