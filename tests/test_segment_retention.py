#!/usr/bin/env python3
"""The sweep that removes segment jobs idle past the retention.

Jobs are laid out with `segment_store`'s own path helpers, so the sweep must
remove the names the store writes; mtimes are set with `os.utime`, and the
clock is handed in except where its default is the subject.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ORIGIN = 1_000_000_000
RETENTION = 1000
LONG_AGO = ORIGIN + 10 * RETENTION


def _retention(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'segment_retention.py', name)


def _job(root, job, record: int | None = ORIGIN,
         marker: int | None = ORIGIN, temp: int | None = ORIGIN,
         directory: int | None = ORIGIN):
    """Lay `job` out at the given mtimes; None leaves that part absent."""
    store = _retention('segret_layout').segment_store
    parts = {}
    if directory is not None:
        (root / job).mkdir()
        segment = root / job / '000000.ts'
        segment.write_bytes(b'ts')
        os.utime(segment, (directory, directory))
        parts[root / job] = directory
    for path, stamp in ((store.record_path(root, job), record),
                        (store._dirty_path(root, job), marker),
                        (store.record_temp_path(root, job), temp)):
        if stamp is not None:
            path.write_text('{}', encoding='utf-8')
            parts[path] = stamp
    for path, stamp in parts.items():
        os.utime(path, (stamp, stamp))
    return sorted(parts)


def _present(paths):
    return [path for path in paths if os.path.lexists(path)]


def test_an_idle_job_is_removed_and_a_fresh_one_is_kept(tmp):
    """Record, marker, temp and directory all go; a fresh job keeps all."""
    tmp = Path(tmp)
    retention = _retention('segret_idle')
    idle = _job(tmp, 'idle-job')
    now = LONG_AGO
    fresh = _job(tmp, 'fresh-job', now, now, now, now)
    retention.sweep(tmp, RETENTION, now=now)
    assert _present(idle) == [], _present(idle)
    assert _present(fresh) == fresh, _present(fresh)


def test_the_newest_of_record_marker_and_directory_is_the_activity(tmp):
    """One fresh part keeps an otherwise idle job, whichever part it is."""
    tmp = Path(tmp)
    retention = _retention('segret_newest')
    now = LONG_AGO
    kept = [_job(tmp, 'record-fresh', record=now),
            _job(tmp, 'marker-fresh', marker=now),
            _job(tmp, 'directory-fresh', directory=now)]
    retention.sweep(tmp, RETENTION, now=now)
    for paths in kept:
        assert _present(paths) == paths, _present(paths)


def test_a_job_exactly_the_retention_old_is_removed(tmp):
    """`now - activity >= retention` expires; one second younger is kept."""
    tmp = Path(tmp)
    retention = _retention('segret_boundary')
    expired = _job(tmp, 'at-retention')
    younger = _job(tmp, 'one-second-younger', *(ORIGIN + 1,) * 4)
    retention.sweep(tmp, RETENTION, now=ORIGIN + RETENTION)
    assert _present(expired) == [], _present(expired)
    assert _present(younger) == younger, _present(younger)


class _RefreshingLock:
    """Stands in for the job's lock: taking it is a write landing."""

    def __init__(self, path, stamp):
        self.path, self.stamp = path, stamp

    def __enter__(self):
        os.utime(self.path, (self.stamp, self.stamp))
        return self

    def __exit__(self, *exc):
        return False


def test_the_age_is_read_again_under_the_jobs_own_lock(tmp):
    """A write landing while the sweep waits for the lock keeps the job."""
    tmp = Path(tmp)
    retention = _retention('segret_locked')
    store = retention.segment_store
    job = 'locked.job'
    paths = _job(tmp, job, marker=None, temp=None)
    asked = []

    def lock_for(name):
        asked.append(name)
        return _RefreshingLock(store.record_path(tmp, job), LONG_AGO)

    real = store.seg_lock_for
    store.seg_lock_for = lock_for
    try:
        retention.sweep(tmp, RETENTION, now=LONG_AGO)
    finally:
        store.seg_lock_for = real
    assert asked == [job], asked
    assert _present(paths) == paths, _present(paths)


def test_only_safe_job_names_are_ever_candidates(tmp):
    """A stray `.json`, a reserved name and an unsafe name are never jobs."""
    tmp = Path(tmp)
    retention = _retention('segret_names')
    live = _job(tmp, 'live-job', *(LONG_AGO,) * 4)
    (tmp / '.json').write_text('{}', encoding='utf-8')
    foreign = [tmp / '.json', tmp / '.x.json.dirty', tmp / ('\u20ac' * 80)]
    for path in foreign[1:]:
        path.mkdir()
    for path in foreign + [tmp]:
        os.utime(path, (ORIGIN, ORIGIN))
    retention.sweep(tmp, RETENTION, now=LONG_AGO)
    assert _present(live) == live, _present(live)
    assert _present(foreign) == foreign, _present(foreign)


class _HeldLock:
    """Stands in for the job's lock and says whether it is held."""

    held = False

    def __enter__(self):
        self.held = True
        return self

    def __exit__(self, *exc):
        self.held = False
        return False


def test_every_removal_happens_inside_the_deciding_hold(tmp):
    """Record, marker, temp, then directory, each while the lock is held."""
    tmp = Path(tmp)
    retention = _retention('segret_held')
    store = retention.segment_store
    paths = _job(tmp, 'held-job')
    lock, removed = _HeldLock(), []
    real_unlink, real_rmtree, real_lock_for = (
        Path.unlink, shutil.rmtree, store.seg_lock_for)

    def unlink(path, *args, **kwargs):
        removed.append((path.name, lock.held))
        return real_unlink(path, *args, **kwargs)

    def rmtree(path, *args, **kwargs):
        removed.append((Path(path).name, lock.held))
        return real_rmtree(path, *args, **kwargs)

    setattr(Path, 'unlink', unlink)
    shutil.rmtree, store.seg_lock_for = rmtree, lambda _name: lock
    try:
        retention.sweep(tmp, RETENTION, now=LONG_AGO)
    finally:
        setattr(Path, 'unlink', real_unlink)
        shutil.rmtree, store.seg_lock_for = real_rmtree, real_lock_for
    assert removed == [('held-job.json', True), ('.held-job.json.dirty', True),
                       ('.held-job.json.tmp', True), ('held-job', True)]
    assert _present(paths) == [], _present(paths)


def test_a_half_removed_job_is_collected_from_either_half(tmp):
    """A directory without its record, and a record without its directory."""
    tmp = Path(tmp)
    retention = _retention('segret_halves')
    orphans = (_job(tmp, 'directory-only', None, None, None)
               + _job(tmp, 'record-only', directory=None))
    retention.sweep(tmp, RETENTION, now=LONG_AGO)
    assert _present(orphans) == [], _present(orphans)


def _symlink_or_skip(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as why:
        _util.skip(f'this filesystem will not hold a symlink: {why}')


def test_a_symlinked_job_name_is_never_followed_or_removed(tmp):
    """The idle records beside two links go; the links and targets stay."""
    tmp = Path(tmp)
    retention = _retention('segret_symlink')
    root = tmp / 'segments'
    root.mkdir()
    outside = tmp / 'outside'
    outside.mkdir()
    (outside / 'keep.ts').write_bytes(b'ts')
    victim = _job(root, 'victim', record=LONG_AGO)
    _symlink_or_skip(root / 'outward', outside)
    _symlink_or_skip(root / 'inward', root / 'victim')
    records = (_job(root, 'outward', directory=None)
               + _job(root, 'inward', directory=None))
    retention.sweep(root, RETENTION, now=LONG_AGO)
    assert _present(records) == [], _present(records)
    assert (root / 'outward').is_symlink() and (root / 'inward').is_symlink()
    assert (outside / 'keep.ts').is_file()
    assert _present(victim) == victim, _present(victim)


def test_one_job_that_cannot_be_removed_does_not_stop_the_sweep(tmp):
    """The next expired job is still removed after an OSError."""
    tmp = Path(tmp)
    retention = _retention('segret_fault')
    failing = _job(tmp, 'a-cannot-remove')
    removable = _job(tmp, 'b-removable')
    refused = []
    real = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path).name == 'a-cannot-remove':
            refused.append(Path(path).name)
            raise OSError('injected rmtree failure')
        return real(path, *args, **kwargs)

    shutil.rmtree = rmtree
    try:
        retention.sweep(tmp, RETENTION, now=LONG_AGO)
    finally:
        shutil.rmtree = real
    assert refused == ['a-cannot-remove'], refused
    assert _present(failing) == [tmp / 'a-cannot-remove'], _present(failing)
    assert _present(removable) == [], _present(removable)


def test_a_root_that_cannot_be_scanned_is_answered_not_raised(tmp):
    """The sweep thread survives a segments root it cannot list."""
    _retention('segret_absent').sweep(Path(tmp) / 'absent', RETENTION)


def test_without_a_given_clock_the_sweep_reads_the_wall_clock(tmp):
    """Unset `now` is the time of the sweep: decades old goes, new stays."""
    tmp = Path(tmp)
    retention = _retention('segret_clock')
    ancient = _job(tmp, 'ancient-job')
    store = retention.segment_store
    (tmp / 'new-job').mkdir()
    store.record_path(tmp, 'new-job').write_text('{}', encoding='utf-8')
    retention.sweep(tmp, 3600)
    assert _present(ancient) == [], _present(ancient)
    assert (tmp / 'new-job').is_dir(), sorted(tmp.iterdir())


class _LoopEnd(BaseException):
    """Ends `gc_loop` from its sleep, past any handler around the sweep."""


def _gc_calls(given, sweeps, failing=0):
    """`gc_loop`'s calls over `sweeps` sweeps; the first `failing` raise."""
    retention = _retention('segret_gc_loop')
    calls = []

    def sleep(seconds):
        if sum(call[0] == 'sweep' for call in calls) == sweeps:
            raise _LoopEnd
        calls.append(('sleep', seconds))

    def sweep(directory, seconds):
        calls.append(('sweep', directory, seconds))
        if sum(call[0] == 'sweep' for call in calls) <= failing:
            raise RuntimeError('injected sweep failure')

    clock = type('Clock', (), {'sleep': staticmethod(sleep)})
    setattr(retention, 'time', clock)
    setattr(retention, 'sweep', sweep)
    try:
        retention.gc_loop(Path('segments'), given)
    except _LoopEnd:
        pass
    return calls


def test_gc_loop_forwards_root_and_retention_after_sleep(_tmp):
    """At least hourly, at most every 0.05 s, the sweep given both values."""
    root = Path('segments')
    assert _gc_calls(7200, 1) == [('sleep', 3600.0), ('sweep', root, 7200)]
    assert _gc_calls(0.01, 1) == [('sleep', 0.05), ('sweep', root, 0.01)]


def test_a_failing_sweep_does_not_end_the_gc_loop(_tmp):
    """The thread outlives one sweep raising: the next sweep still runs."""
    root = Path('segments')
    assert _gc_calls(60, 2, failing=1) == [
        ('sleep', 60.0), ('sweep', root, 60)] * 2


_RECORDING_GC_LOOP = """import json, os, sys
sys.path.insert(0, {root!r})
from daedalus_bridge import segment_retention
def gc_loop(seg_dir_root, retention):
    with open({record!r} + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump([str(seg_dir_root), retention], handle)
    os.replace({record!r} + '.tmp', {record!r})
segment_retention.gc_loop = gc_loop
"""


def test_the_bridge_starts_the_sweep_on_its_segments_root_and_setting(tmp):
    """`server.py` hands the sweep thread its own root and the setting."""
    seam = Path(tmp) / 'seam'
    seam.mkdir()
    record = seam / 'gc-loop-args.json'
    (seam / 'sitecustomize.py').write_text(_RECORDING_GC_LOOP.format(
        root=str(_util.ROOT), record=str(record)), encoding='utf-8')
    env = {'DAEDALUS_SEGMENT_JOB_RETENTION': '7.5', 'PYTHONPATH': str(seam)}
    with _util.bridge(tmp, env=env) as (_base, docroot):
        # Unbounded: only a bridge that never starts the thread waits here.
        while not record.exists():
            time.sleep(0.05)
    assert json.loads(record.read_text(encoding='utf-8')) == [
        str(Path(docroot) / 'segments'), 7.5]


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals()),
                                  tmp_prefix='segret_'))
