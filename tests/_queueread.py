"""Bounded reads of bridge command queue entries for CLI-driving suites.

The bridge publishes an entry by renaming its temp over the final name, and
Windows denies an open that lands while the name is still settling. This is
the reader's half of the arrangement `daedalus_bridge/atomic_file.py` makes
on the writer's side. Callers read one eligible entry or an exact entry set.
"""
from _cmdqueue import POLL_DELAY as _SHARED_POLL_DELAY, _poll_queue_reads

POLL_DELAY = _SHARED_POLL_DELAY
DEFAULT_TIMEOUT = 15.0


def queued_command(qdir, what, timeout: float = DEFAULT_TIMEOUT, exclude=()):
    """Parse a bridge command queue's oldest entry, retrying a refusal.

    The oldest eligible entry is returned when several entries exist. A denial
    clears on its own, so the read retries until the bounded timeout; one
    that survives it is raised as itself, because a queue no reader can
    open is the failure the caller exists to report rather than a timeout
    about a queue that never filled. `exclude` contains entry names, not
    paths, that are already handled and must be skipped.
    """
    commands, denied = _poll_queue_reads(
        qdir, None, timeout, ignored_names=exclude, retry_vanished=False,
        check_deadline_before_read=False)
    if commands is not None:
        return commands[0]
    if denied is not None:
        raise denied from None
    raise AssertionError(f'timed out waiting for {what}')


def queued_commands(qdir, what, count, timeout: float = DEFAULT_TIMEOUT):
    """Parse exactly `count` queue entries, retrying transient refusals.

    A refusal clears on its own, so the complete read retries until the
    bounded timeout; one that survives it is raised as itself. A vanished
    entry is not a refusal and is allowed to reach the caller unchanged.
    """
    commands, denied = _poll_queue_reads(
        qdir, count, timeout, retry_vanished=False,
        check_deadline_before_read=False)
    if commands is not None:
        return commands
    if denied is not None:
        raise denied from None
    raise AssertionError(f'timed out waiting for {what}')
