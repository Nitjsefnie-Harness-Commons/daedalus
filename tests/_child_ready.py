"""Waiting for a child to be ready, without counting the wait.

A wait whose iteration count is a function of wall time is not a measurement.
The instruction count it contributes grows with however long the machine took
to get there, and no tolerance absorbs that without also absorbing the
regression the budget exists to catch — the same argument `JOURNEY
DETERMINISM` settled when a polled `/health` was replaced by a wait on the
bridge's own `[MCP]` line.

So every wait here is EVENT-DRIVEN: it blocks on something the child
produced, and the number of iterations is the number of things the child
produced. A deadline is still there and still fails, but it is a bound and
not a driver: nothing loops on it.

Nothing here reads the child's stdout. The drain thread beside it owns that
stream and appends to a list every existing caller already holds, so the
condition lives on the CHILD and is reached through it — a wrapper type
would have meant a caller passing a list got a different object back.
"""
import re
import threading
import time

# The bridge's own announcements. The import is deliberately not part of
# readiness, and a caller waiting for it waits for seconds natively and
# minutes under an instruction counter, so the bound is its own rather than
# the bridge's startup one.
MCP_BOUND = re.compile(r'\[MCP\] streamable-http on ')
MCP_FAILED = re.compile(r'\[MCP\] bootstrap failed, so /mcp is not served'
                        r'|\[MCP\] serve crashed')
MCP_READY_TIMEOUT = 900

# Where the condition is hung on the child, beside the drain thread that
# already is. Two attributes because they are set together and read
# together; the name says which is which.
ARRIVAL = '_daedalus_drain_arrival'
PUMP = '_daedalus_drain_thread'


class Arrival:
    """The pump's side of the pipe, as something a waiter can block on.

    `count` is the number of lines appended so far and `eof` is whether the
    child has closed the stream. A waiter waits for one of them, so a child
    that dies before announcing wakes it instead of leaving it to time out.
    """

    def __init__(self):
        self.condition = threading.Condition()
        self.count = 0
        self.eof = False

    def line(self):
        """One line arrived."""
        with self.condition:
            self.count += 1
            self.condition.notify_all()

    def end(self):
        """The child closed the stream."""
        with self.condition:
            self.eof = True
            self.condition.notify_all()

    def wait_for(self, beyond, deadline):
        """Block until more than `beyond` lines have arrived, or EOF, or
        the deadline passes.

        The predicate is re-read under the lock, so a line that lands
        between the caller's own count and this call returns immediately
        rather than sleeping through a line that is already there.
        """
        with self.condition:
            self.condition.wait_for(
                lambda: self.eof or self.count > beyond,
                timeout=max(0.0, deadline - time.monotonic()))


def drain(proc, collected=None):
    """Start relaying the child's stdout into `collected`, and return it.

    `collected` is the caller's own list when it passes one, so a caller
    that handed a list in still has that list filled — and still holds the
    object it asked for, which is what every reader of it is written
    against.
    """
    collected = [] if collected is None else collected
    arrival = Arrival()
    setattr(proc, ARRIVAL, arrival)

    def pump():
        try:
            for line in proc.stdout:
                collected.append(line)
                arrival.line()
        finally:
            arrival.end()

    thread = threading.Thread(target=pump, daemon=True,
                              name='bridge-stdout-drain')
    setattr(proc, PUMP, thread)
    thread.start()
    return collected


def arrival_of(proc):
    """The `Arrival` this child's drain thread signals, or a dead one.

    A child whose drain this module did not start has no arrival to block
    on, and the wait it would need is the one this module exists to remove.
    Rather than fall back to a sleep, that is a refusal naming the child: a
    caller reaching it is calling a waiter on a stream nobody is relaying,
    which is a bug to read rather than a slow machine to absorb.
    """
    found = getattr(proc, ARRIVAL, None)
    if found is None:
        raise RuntimeError(
            f'pid {proc.pid} has no drain thread, so there is nothing to '
            'wait on; call drain() on it before waiting for its output')
    return found


def mcp_state(lines):
    """`up`, `down`, or None while the front end is still starting.

    The announcement is the signal, not a poll of `/health`: each poll is a
    connection and a `ThreadingHTTPServer` thread, so a polled wait costs a
    count that grows with however long the import took — the wall-clock term
    the wait exists to remove, carrying one of its own. `down` is settled
    too, and is a fixed state rather than a race.
    """
    for line in list(lines):
        if MCP_BOUND.search(line):
            return 'up'
        if MCP_FAILED.search(line):
            return 'down'
    return None


def await_state(proc, drained, timeout=MCP_READY_TIMEOUT):
    """Block until the front end has settled, and return which way it went.

    `observations` is the caller's own failure renderer, passed in by the
    caller of this module's wrapper so this one need not import `_util`,
    which imports this one.

    The import is billions of instructions and runs beside whatever the
    caller is measuring, so a count taken across it carries whichever slice
    of the import happened to run alongside — a function of the machine's
    speed, not of the tree. The wait here is on a LINE, so the number of
    times this loop runs is the number of lines the bridge printed and not
    the length of the import.
    """
    started = time.monotonic()
    deadline = started + timeout
    arrival = arrival_of(proc)
    seen = 0
    while True:
        pending = drained[seen:]
        seen = len(drained)
        state = mcp_state(pending)
        if state is not None:
            return state
        if proc.poll() is not None:
            raise RuntimeError('the child exited while its front end was '
                               'still starting')
        if time.monotonic() > deadline:
            raise RuntimeError(
                f'the child did not settle its front end in {timeout}s')
        arrival.wait_for(seen, deadline)


def await_line(proc, drained, matches, timeout):
    """Block until a captured line matches `matches`, and return that line.

    The announcement is SEARCHED FOR across every line the child has printed
    so far, not assumed to be the first one. A bridge prints whatever its
    platform gives it cause to — a malloc-tuning diagnostic where mallopt is
    not a glibc symbol before it gets that far, a front-end bootstrap failure
    from its own thread at whatever moment its missing dependencies surface
    — and a reader that inspected only the first line would take one of those
    for the announcement and time out on a bridge that came up fine.

    The scan stays bounded in both directions: a child that exits is reported
    the moment it does, and one that stays up without announcing fails at
    `timeout`. Both are `None`, which the caller renders with the child's own
    state, because only the caller knows what its wait was for.
    """
    started = time.monotonic()
    deadline = started + timeout
    arrival = arrival_of(proc)
    seen = 0
    while True:
        pending = drained[seen:]
        seen = len(drained)
        for line in pending:
            found = matches(line)
            if found is not None:
                return found
        if proc.poll() is not None or time.monotonic() > deadline:
            return None
        arrival.wait_for(seen, deadline)