"""Removes segment jobs idle past the retention; binds no config."""
import os
import shutil
import stat
import time

from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import segment_store


def _candidates(seg_dir_root):
    names = set()
    with os.scandir(seg_dir_root) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                names.add(entry.name)
            elif (entry.is_file(follow_symlinks=False)
                  and entry.name.endswith('.json')):
                names.add(entry.name.removesuffix('.json'))
    # '' (from a stray `.json`) would make the root itself the job directory.
    return sorted(name for name in names
                  if name and not segment_store.unsafe_job_name(name)
                  and not segment_store.reserved_bookkeeping_name(name))


def _mtime(path, kind):
    try:
        found = path.lstat()
    except FileNotFoundError:
        return None
    return found.st_mtime if kind(found.st_mode) else None


def _expire(root, job, retention, now):
    """Remove `job` once its record, marker and directory are all idle."""
    # Joined unresolved: the store's own path helpers follow symlinks.
    record = root / f'{job}.json'
    marker = root / f'.{job}.json.dirty'
    temp = root / f'.{job}.json.tmp'
    job_dir = root / job
    with segment_store.seg_lock_for(job):
        files = {path: _mtime(path, stat.S_ISREG)
                 for path in (record, marker, temp)}
        dir_mtime = _mtime(job_dir, stat.S_ISDIR)
        stamps = [stamp for stamp in (files[record], files[marker], dir_mtime)
                  if stamp is not None]
        if not stamps or now - max(stamps) < retention:
            return
        # The record first: it revokes the capability before a segment goes.
        for path, stamp in files.items():
            if stamp is not None:
                path.unlink(missing_ok=True)
        if dir_mtime is not None:
            shutil.rmtree(job_dir)
    print(f'[SEGMENT] EXPIRED {log_safe(job)}', flush=True)


def sweep(seg_dir_root, retention, now=None):
    """One scan, then each job under its own lock; a job's OSError skips it."""
    now = time.time() if now is None else now
    try:
        jobs = _candidates(seg_dir_root)
    except OSError as why:
        print(f'[SEGMENT] SWEEP-FAILED {log_safe(why)}', flush=True)
        return
    for job in jobs:
        try:
            _expire(seg_dir_root, job, retention, now)
        except OSError as why:
            print(f'[SEGMENT] EXPIRE-FAILED {log_safe(job)}: {log_safe(why)}',
                  flush=True)


def gc_loop(seg_dir_root, retention):
    interval = max(0.05, min(3600.0, retention))
    while True:
        time.sleep(interval)
        try:
            sweep(seg_dir_root, retention)
        except Exception as why:  # one bad sweep must not end the thread
            print(f'[SEGMENT] SWEEP-FAILED {log_safe(why)}', flush=True)
