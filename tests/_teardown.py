"""Teardown of a temporary tree a still-held file blocks (issue 313).

`settle` retries: Windows holds a per-test directory past the last test from
outside this process, where no in-process fix reaches. Exhaustion is reported
rather than raised so a suite whose tests passed still passes, and only
PermissionError is retried -- any other exception reaches the caller
unchanged. A tree no teardown finished, from a killed run or a refusal that
outlasted the retries, is reclaimed by the next `OwnedTemporaryDirectory`
made under the same prefix.
"""
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import time
import weakref
from pathlib import Path

ATTEMPTS = 8
BACKOFF_START = 0.05
BACKOFF_CAP = 2.0
_BOUND = importlib.util.spec_from_file_location(
    'teardown_suite_bound',
    Path(__file__).resolve().parents[1] / 'scripts' / 'ci' / 'suite_bound.py')
assert _BOUND and _BOUND.loader
SUITE_BOUND = importlib.util.module_from_spec(_BOUND)
_BOUND.loader.exec_module(SUITE_BOUND)


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
    """Take an owner record's lock without waiting; False if it is held."""
    try:
        if os.name == 'nt':
            msvcrt = importlib.import_module('msvcrt')
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl = importlib.import_module('fcntl')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _owner_gone(directory):
    record = os.path.join(directory, '.owner')
    try:
        if os.path.getsize(record):
            descriptor = os.open(record, os.O_RDWR)
            try:
                return _lock(descriptor)
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        pass
    # No written record: older than records, or its creator is still taking
    # the lock. No suite outlives run_tests.py's bound.
    age = time.time() - os.stat(directory).st_mtime
    return age > SUITE_BOUND.suite_timeout()


def reclaim_tempdirs(prefix, parent):
    """Remove `prefix` + `tempfile`'s 8-character suffix dirs nobody owns."""
    name = re.compile(re.escape(prefix) + '[a-z0-9_]{8}')
    for entry in os.scandir(parent):
        try:
            stale = (name.fullmatch(entry.name)
                     and entry.is_dir(follow_symlinks=False)
                     and _owner_gone(entry.path))
        except OSError:
            continue
        if stale:
            try:
                shutil.rmtree(entry.path)
            except OSError as refusal:
                print(f'  WARN  {entry.path} not reclaimed, a later run '
                      f'retries: {refusal!r}', file=sys.stderr)


class OwnedTemporaryDirectory(tempfile.TemporaryDirectory):
    """A `TemporaryDirectory` the next run removes if this one cannot.

    Creating one first reclaims what dead runs left under its prefix. Its
    `.owner` record is locked until `cleanup`, and the OS drops the lock
    when the process dies, which is how another process tells a leftover
    from a live directory on every OS. The record's descriptor is not
    inherited (PEP 446), and it closes before removal, which Windows needs.
    """

    def __init__(self, prefix, **kwargs):
        reclaim_tempdirs(prefix, kwargs.get('dir') or tempfile.gettempdir())
        super().__init__(prefix=prefix, **kwargs)
        record = os.open(os.path.join(self.name, '.owner'),
                         os.O_CREAT | os.O_RDWR)
        # Empty, so no reclaimer tries it: the lock cannot be contended.
        _lock(record)
        os.write(record, str(os.getpid()).encode())
        # Created after the base finalizer, so it runs first at exit.
        self._release = weakref.finalize(self, os.close, record)

    def cleanup(self):
        self._release()
        super().cleanup()
