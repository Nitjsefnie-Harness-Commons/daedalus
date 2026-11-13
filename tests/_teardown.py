"""Bounded retry for a teardown a still-held file blocks (issue 313).

Windows holds a per-test directory past the last test from outside this
process, where no in-process fix reaches. Exhaustion is reported rather than
raised so a suite whose tests passed still passes, and only PermissionError
is retried -- any other exception reaches the caller unchanged.
"""
import contextlib
import importlib
import os
import re
import runpy
import shutil
import tempfile
import time
import weakref

ATTEMPTS = 8
BACKOFF_START = 0.05
BACKOFF_CAP = 2.0
_BOUND = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'ci')
_SUITE_TIMEOUT = runpy.run_path(_BOUND + '/suite_bound.py')['suite_timeout']


def settle(cleanup, *, sleep=time.sleep):
    """Run cleanup, retrying PermissionError until the attempts run out."""
    delay = BACKOFF_START
    failure: PermissionError
    for attempt in range(ATTEMPTS):
        try:
            cleanup()
        except PermissionError as exc:
            failure = exc
            if attempt == ATTEMPTS - 1:
                break
            sleep(delay)
            delay = min(delay * 2, BACKOFF_CAP)
        else:
            return True
    where = getattr(failure, 'filename', None) or failure
    print(f'  WARN  teardown abandoned after {ATTEMPTS} attempts: {where}: '
          f'{type(failure).__name__}: {failure.strerror or failure}')
    return False


def _lock(descriptor):
    try:
        if os.name == 'nt':
            msvcrt = importlib.import_module('msvcrt')
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl = importlib.import_module('fcntl')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _disown(record):
    # Emptied first, a cleaning owner's tree reads as unrecorded, which only
    # the suite bound ends, not as a dead owner's mid-removal.
    with contextlib.suppress(OSError):
        os.ftruncate(record, 0)
    os.close(record)


def reclaim_tempdirs(prefix, parent):
    """Remove `prefix` + 8-character `tempfile` suffix dirs whose `.owner`
    lock is free; with no record written yet, only past the suite bound."""
    name = re.compile(re.escape(prefix) + '[a-z0-9_]{8}')
    for entry in os.scandir(parent):
        if not (name.fullmatch(entry.name)
                and entry.is_dir(follow_symlinks=False)):
            continue
        record = entry.path + '/.owner'
        try:
            if os.path.isfile(record) and os.path.getsize(record):
                descriptor = os.open(record, os.O_RDWR)
                stale = _lock(descriptor)
                os.close(descriptor)
            else:
                stale = time.time() - entry.stat().st_mtime > _SUITE_TIMEOUT()
            if stale:
                shutil.rmtree(entry.path)
        except FileNotFoundError:
            continue  # another run removed it first
        except OSError as refusal:
            print(f'  WARN  {entry.path} not reclaimed, a later run retries: '
                  f'{refusal!r}')


class OwnedTemporaryDirectory(tempfile.TemporaryDirectory):
    """A `TemporaryDirectory` the next one under its prefix reclaims."""

    def __init__(self, prefix, **kwargs):
        reclaim_tempdirs(prefix, kwargs.get('dir') or tempfile.gettempdir())
        super().__init__(prefix=prefix, **kwargs)
        record = os.open(self.name + '/.owner', os.O_CREAT | os.O_RDWR)
        if _lock(record):  # unlocked, it stays empty: only age can end it
            os.write(record, str(os.getpid()).encode())
        # Runs before the base finalizer: Windows keeps an open file.
        self._release = weakref.finalize(self, _disown, record)

    def cleanup(self):
        self._release()
        try:
            super().cleanup()
        except OSError:
            # Left behind after all: written again, the next run takes it.
            with contextlib.suppress(OSError), open(
                    self.name + '/.owner', 'w', encoding='utf-8') as record:
                record.write('left')
            raise
