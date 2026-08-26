"""Atomic filesystem replacement shared by bridge storage owners."""
import os
import time


_REPLACE_ATTEMPTS = 5
_REPLACE_RETRY_DELAY = 0.02


def replace_atomically(src, dst):
    """Publish `src` over `dst`, retrying a transient sharing violation.

    Windows refuses a replace while any handle is open on the target, and that
    handle need not be the bridge's -- a scanner that opens a file the moment
    it appears is enough. It clears on its own within milliseconds, so without
    a retry the bridge answers 500 for a write that was about to succeed and
    discards data a caller already produced.

    Only PermissionError is retried. A replace refused because the volume is
    read-only or the disk is full is not going to start working, and waiting
    on it would delay the error that explains what happened instead of fixing
    anything.
    """
    for remaining in range(_REPLACE_ATTEMPTS - 1, -1, -1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not remaining:
                raise
            time.sleep(_REPLACE_RETRY_DELAY)
