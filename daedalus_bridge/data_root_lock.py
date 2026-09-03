"""Exclusive ownership of a bridge data root by one bridge process."""
import importlib
import os
import pathlib

# The lockfile name is fixed so every bridge on a root agrees on one file.
LOCK_NAME = 'bridge.lock'


class DataRootInUse(OSError):
    """Another process already holds the data root."""


def acquire(data_root):
    """Claim `data_root` exclusively, returning the held lock handle.

    The claim lives on the open handle, so the kernel drops it when the
    holder dies and process exit is the release: a crashed bridge leaves a
    plain file behind, never a lock. The file is left in place on release
    too -- unlinking it would let two processes hold different objects
    under one name.
    """
    root = pathlib.Path(data_root)
    handle = os.open(str(root / LOCK_NAME), os.O_CREAT | os.O_RDWR)
    try:
        _claim(handle)
    except OSError:
        os.close(handle)
        raise DataRootInUse(f'data root already in use: {root}') from None
    return handle


def _claim(handle):
    """Take the exclusive lock without waiting, or raise OSError."""
    if os.name == 'nt':
        msvcrt = importlib.import_module('msvcrt')
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        return
    fcntl = importlib.import_module('fcntl')
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def release(handle):
    """Close the held handle, freeing the root for the next bridge."""
    os.close(handle)
