"""Atomic filesystem replacement shared by bridge storage owners."""
import os
import time


_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 0.02


def _retrying(perform):
    """Run `perform`, retrying a transient Windows sharing violation.

    Windows refuses an open, write or replace while any handle is open on
    the file, and that handle need not be the bridge's -- a scanner that
    opens a file the moment it appears is enough. It clears on its own
    within milliseconds, so without a retry the bridge answers 500 for a
    write that was about to succeed and discards data a caller already
    produced.

    Only PermissionError is retried. A write refused because the volume is
    read-only or the disk is full is not going to start working, and waiting
    on it would delay the error that explains what happened instead of
    fixing anything.

    The sleeps run while the caller holds whatever locks it holds, so the
    wait is bounded at roughly `_RETRY_ATTEMPTS * _RETRY_DELAY` (80 ms) per
    retrying call.
    """
    for remaining in range(_RETRY_ATTEMPTS - 1, -1, -1):
        try:
            perform()
            return
        except PermissionError:
            if not remaining:
                raise
            time.sleep(_RETRY_DELAY)


def replace_atomically(src, dst):
    """Publish `src` over `dst`, retrying a transient sharing violation."""
    _retrying(lambda: os.replace(src, dst))


def write_bytes_retrying(path, data):
    """Write bytes to `path`, retrying a transient sharing violation."""
    _retrying(lambda: path.write_bytes(data))


def write_text_retrying(path, data, encoding='utf-8'):
    """Write text to `path`, retrying a transient sharing violation."""
    _retrying(lambda: path.write_text(data, encoding=encoding))
