"""Waiting for the bridge's optional MCP front end to settle.

Its own module because `_util` is at its size ceiling, and because this is
the one thing the harness waits for twice: the bridge announces its port
first, then a second optional listener on a thread of its own.
"""
import re
import time

MCP_BOUND = re.compile(r'\[MCP\] streamable-http on ')
MCP_FAILED = re.compile(r'MCP bootstrap failed, so /mcp is not served'
                        r'|\[MCP\] serve crashed')

# The import is deliberately not part of readiness, and a caller waiting for
# it waits for seconds natively and minutes under an instruction counter, so
# the bound is its own rather than the bridge's startup one.
MCP_READY_TIMEOUT = 900


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


def await_mcp_ready(proc, drained, observations, timeout=MCP_READY_TIMEOUT):
    """Block until the front end has settled, and return which way it went.

    The import is billions of instructions and runs beside whatever the
    caller is measuring, so a count taken across it carries whichever slice
    of the import happened to run alongside — a function of the machine's
    speed, not of the tree.

    `observations` is the caller's own failure renderer, passed in so this
    module need not import `_util`, which imports this one.
    """
    started = time.time()
    deadline = started + timeout
    seen = 0
    while True:
        pending = drained[seen:]
        seen += len(pending)
        state = mcp_state(pending)
        if state is not None:
            return state
        if proc.poll() is not None:
            raise RuntimeError(
                'bridge exited while its MCP front end was still starting: '
                + observations(proc, drained, time.time() - started))
        if time.time() > deadline:
            raise RuntimeError(
                f'bridge did not settle its MCP front end in {timeout}s: '
                + observations(proc, drained, time.time() - started))
        time.sleep(0.05)
