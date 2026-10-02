"""Waiting for the bridge's optional MCP front end to settle.

Its own name because `_util` is at its size ceiling and because this is the
one thing the harness waits for twice: the bridge announces its port first,
then a second optional listener on a thread of its own.

The wait itself is in `_child_ready`, beside the drain thread it blocks on —
a wait whose iteration count is a function of wall time is not a
measurement, and the two are one subject rather than two.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _child_ready  # noqa: E402  pylint: disable=wrong-import-position

MCP_BOUND = _child_ready.MCP_BOUND
MCP_FAILED = _child_ready.MCP_FAILED
MCP_READY_TIMEOUT = _child_ready.MCP_READY_TIMEOUT
mcp_state = _child_ready.mcp_state


def await_mcp_ready(proc, drained, observations, timeout=MCP_READY_TIMEOUT):
    """Block until the front end has settled, and return which way it went.

    `observations` is the caller's own failure renderer, passed in so this
    module need not import `_util`, which imports this one. This module does
    not import `_util` either — it is the renderer that names the child's
    state, and the wait is one blocking step on a line the bridge printed.
    """
    started = time.monotonic()
    try:
        return _child_ready.await_state(proc, drained, timeout)
    except RuntimeError as refused:
        waited = time.monotonic() - started
        raise RuntimeError(
            f'{refused}; '
            + observations(proc, drained, waited)) from None
