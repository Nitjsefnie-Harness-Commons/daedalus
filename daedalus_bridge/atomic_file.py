"""Atomic filesystem replacement shared by bridge storage owners."""
import os
import time
from typing import Callable, TypeVar


_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 0.02

_Result = TypeVar('_Result')


def _retrying(perform: Callable[[], _Result]) -> _Result:
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
    retrying call. The last attempt stands outside the loop because a
    refusal to it is the answer, not another wait.
    """
    for _ in range(_RETRY_ATTEMPTS - 1):
        try:
            return perform()
        except PermissionError:
            time.sleep(_RETRY_DELAY)
    return perform()


def replace_atomically(src, dst):
    """Publish `src` over `dst`, retrying a transient sharing violation."""
    _retrying(lambda: os.replace(src, dst))


def write_bytes_retrying(path, data):
    """Write bytes to `path`, retrying a transient sharing violation."""
    _retrying(lambda: path.write_bytes(data))


def write_text_retrying(path, data, encoding='utf-8'):
    """Write text to `path`, retrying a transient sharing violation."""
    _retrying(lambda: path.write_text(data, encoding=encoding))


def unlink_retrying(path):
    """Remove `path`, retrying a transient sharing violation.

    A path that is already gone is not an error: `missing_ok=True`, so the
    removal stays idempotent for a caller that checked for the file first.
    """
    _retrying(lambda: path.unlink(missing_ok=True))


def read_text_retrying(path, encoding='utf-8'):
    """Read text from `path`, retrying a transient sharing violation."""
    return _retrying(lambda: path.read_text(encoding=encoding))
