"""Bounded read of one bridge command queue entry, for a suite driving a CLI.

The bridge publishes an entry by renaming its temp over the final name, and
Windows denies an open that lands while the name is still settling. This is
the reader's half of the arrangement `daedalus_bridge/atomic_file.py` makes
on the writer's side.
"""
import json
import time

POLL_DELAY = 0.05
DEFAULT_TIMEOUT = 15.0


def queued_command(qdir, what, timeout: float = DEFAULT_TIMEOUT):
    """Parse a bridge command queue's oldest entry, retrying a refusal.

    The caller guarantees a single-entry queue — the one command it is
    waiting on — and of several entries the oldest is returned. A denial
    clears on its own, so the read retries until the bounded timeout; one
    that survives it is raised as itself, because a queue no reader can
    open is the failure the caller exists to report rather than a timeout
    about a queue that never filled.
    """
    outcome = {}
    deadline = time.monotonic() + timeout
    while True:
        entries = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
        if entries:
            try:
                outcome['command'] = json.loads(
                    entries[0].read_text(encoding='utf-8'))
                return outcome['command']
            except PermissionError as denied:
                outcome['denied'] = denied
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_DELAY, remaining))
    if 'denied' in outcome:
        raise outcome['denied'] from None
    raise AssertionError(f'timed out waiting for {what}')
