"""The waits a watcher-budget case measures its children through.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once, as
the failure-reporting backstop on the single wait with no live process to
give up on, and once more as the bound on a reap that follows a kill and
can only return.
"""
import os
import signal
import subprocess
import sys
import threading
import time

# The backstop on the one wait that cannot end on a state. 90s is the
# figure tests/test_parent_watch.py already waits a real grandchild's death
# with, and the suites job allows twenty minutes for the whole file, so a
# survivor is named long before the job's own limit ends the run nameless.
BACKSTOP = 90
# A wake-up interval, never a deadline: the waits below end on what the
# process under test did.
POLL = 0.05


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
        self.proc.wait(timeout=60)
        return self.proc.returncode


def _cancel(proc):
    """Signal the whole group the child leads, so nothing outlives it.

    A kill names one process, and the `gh` a watcher had already spawned
    is not it: the orphan keeps running, and keeps appending to the call
    log a measurement is still reading, after the child it belonged to is
    gone. The group is every process the child started, so signalling it
    cancels the work. `start_new_session` made the child its own group
    leader, so the group id is its pid - and the child is unreaped here,
    so that pid is still its own and cannot have been handed to anyone
    else. A group that does not exist is a child that has not reached
    `setsid` yet, and naming the child alone is all there is to do.
    """
    if sys.platform.startswith('win'):
        # Windows has no group to signal, so the tree is named instead.
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        proc.kill()


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


def await_polls(fake, polls, child, what):
    """The call log, once it carries `polls` distinct poll markers.

    A watcher names its own poll boundary by publishing an index its `gh`
    children inherit, so the log says how many polls ran rather than leaving
    the count to be inferred from the calls. Polls are counted rather than
    calls because a poll's width is data-dependent - a follow-up query and a
    paginated connection both make it wider - and the measurement is the one
    thing that must not assume it.

    The last marker seen names a poll that may still be in flight, so the
    caller reads the ones before it. The same trade as `await_calls` holds: a
    child that stays up and never publishes leaves this wait nothing to end
    it.
    """
    while True:
        calls = fake.calls()
        if len({call.get('poll') for call in calls}) >= polls:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
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
