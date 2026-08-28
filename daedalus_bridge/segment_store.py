"""Segment job records and HLS segment usage accounting."""
import os, json, hmac, secrets, threading, time

from daedalus_bridge import atomic_file
from daedalus_bridge.config import (
    DEBUG_TIMING, MAX_SEGMENT_INDEX, MAX_SEGMENT_JOB_SIZE,
    MAX_SEGMENTS_PER_JOB, SEG_DIR,
)
from daedalus_bridge import path_safety


# One flat job namespace under the data root: segments/<job>/ holds the .ts
# files, and segments/<job>.json beside the directory records the owning token,
# minted capability, and fixed index/count/byte quotas. The page-JavaScript
# relay presents the capability (sig) rather than the bridge token, because
# anything that script carries the visited page can read.
seg_lock = threading.Lock()


def record_path(job):
    """The record beside a job's directory, refused if it lands outside.

    Raises ValueError like `under`. Every route reaching here has already
    answered for a bad job name, so a containment failure joins that answer
    rather than becoming a storage error.
    """
    return path_safety.under(SEG_DIR, f'{job}.json')


class SegmentRecordError(Exception):
    """A job record exists but could not be read as one."""


def load_record(job):
    """Return `job`'s JSON object, or None when there is no record at all.

    A record that exists and cannot be read raises instead of arriving as
    None: the mint reads None as "this job does not exist yet" and writes a
    fresh owner and capability over whatever is there, so collapsing the two
    turned local corruption into a destroyed resume identity reported as a
    successful mint.
    """
    path = record_path(job)
    if not path.is_file():
        # No record file here: nothing at that name, or the dotted-name
        # collision where this job's record path is another job's directory
        # (or sits below its record file), which the mint answers as an
        # unavailable name. Asked as a question about the path rather than
        # by exception type, because the type differs per platform: reading
        # a directory raises IsADirectoryError on Linux and PermissionError
        # on Windows, and a check that names types turns one platform's
        # spelling into a storage failure on another.
        return None
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except OSError as why:
        raise SegmentRecordError('record unreadable') from why
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError) as why:
        raise SegmentRecordError('record is not JSON') from why
    if not isinstance(record, dict):
        raise SegmentRecordError('record is not an object')
    return record


def record_for_sig(job, sig):
    """Return `job` metadata when `sig` matches its minted capability.

    compare_digest raises TypeError on non-ASCII str input, and the sig arrives
    as a query string, so both sides are gated before the comparison.
    """
    try:
        record = load_record(job)
    except SegmentRecordError:
        # Fail closed: without a readable record nothing can be authorized,
        # and this path never writes one, so the corrupt record survives for
        # the mint to answer for.
        return None
    expected = record.get('sig', '') if record else ''
    if not isinstance(expected, str) or not expected or not expected.isascii():
        return None
    if not sig or not sig.isascii():
        return None
    return record if hmac.compare_digest(expected, sig) else None


def sig_ok(job, sig):
    """Constant-time check of `sig` against the capability minted for `job`."""
    return record_for_sig(job, sig) is not None


def quota(record):
    """Return trusted (max index, file count, bytes), or None if malformed."""
    max_index = record.get('max_segment_index')
    max_count = record.get('max_segment_count')
    max_bytes = record.get('max_bytes')
    if (not isinstance(max_index, int) or isinstance(max_index, bool)
            or max_index < 0):
        return None
    if (not isinstance(max_count, int) or isinstance(max_count, bool)
            or max_count < 0):
        return None
    if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
            or max_bytes < 0):
        return None
    return max_index, max_count, max_bytes


def usage(record):
    """Return the record's (count, bytes) totals, or None when absent.

    None means "not recorded yet", which is the lazy-migration signal: a job
    minted before totals were kept has none, and one recount converts it. It
    is deliberately not zero, because zero is also what an empty job records
    and the two must not be confused.
    """
    count = record.get('stored_count')
    stored = record.get('stored_bytes')
    # Checked one at a time rather than in a loop over both, matching
    # _segment_quota above: a loop hides the narrowing from a type checker,
    # which then reads the returned pair as possibly None all the way into
    # the arithmetic that spends it.
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    if not isinstance(stored, int) or isinstance(stored, bool) or stored < 0:
        return None
    return count, stored


def recount(seg_dir):
    """Count and measure a job's stored segments by reading the directory.

    The expensive path, kept for exactly two callers: converting a job whose
    record predates the totals, and the sweep that removes temps a crash left
    behind. It is off the per-segment path, which is the whole point.

    Returns None when the directory cannot be enumerated, so every caller
    answers that in its own terms rather than letting the exception escape.
    """
    count = 0
    stored = 0
    try:
        entries = list(seg_dir.iterdir())
    except FileNotFoundError:
        return 0, 0
    except OSError:
        # Not a directory at all, or unreadable. A job name may contain a
        # dot, so one job's directory is another's record file: enumerating
        # it raises, and the answer to that is the caller's existing refusal,
        # not an exception escaping into a dropped connection.
        return None
    for path in entries:
        if path.name.startswith('.') and path.name.endswith('.ts.tmp'):
            try:
                path.unlink()
            except OSError:
                # A temp that will not go is not worth failing a write over;
                # it is invisible to the .ts accounting either way.
                pass
            continue
        if path.suffix != '.ts':
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if not stat.st_mode & 0o170000 == 0o100000:
            continue
        count += 1
        stored += stat.st_size
    return count, stored


def new_record(token, stored_count=0, stored_bytes=0):
    """Build a newly minted segment-job record with fixed quotas."""
    return {
        'token': token,
        'sig': secrets.token_urlsafe(32),
        'max_segment_index': MAX_SEGMENT_INDEX,
        'max_segment_count': MAX_SEGMENTS_PER_JOB,
        'max_bytes': MAX_SEGMENT_JOB_SIZE,
        'stored_count': stored_count,
        'stored_bytes': stored_bytes,
    }


def _dirty_path(job):
    """Where write_usage marks that job's totals may not have landed."""
    path = record_path(job)
    return path.with_name(f'.{path.name}.dirty')


def needs_recount(job):
    """Whether a previous write_usage for `job` may not have landed.

    The mark goes down before the write it guards even starts, so it is
    still there after a write that fails outright and after a crash
    partway through one — both leave the record at its old totals, and
    this is what stops the next read from trusting them. write_usage
    clears it itself once the replace it guards has actually landed.
    """
    return _dirty_path(job).exists()


def write_usage(job, count, stored):
    """Persist a job's totals, leaving every other field of its record alone.

    Read-modify-write under the caller's lock. A record that has become
    unreadable is left alone rather than replaced: the mint is the only
    writer allowed to answer for corruption, and overwriting here would
    destroy the owner and capability a resume depends on.
    """
    try:
        record = load_record(job)
    except SegmentRecordError:
        return
    if record is None:
        return
    path = record_path(job)
    dirty = _dirty_path(job)
    # Set before the record write is even attempted: the segment this
    # write is accounting for is already stored by the time we get here,
    # so from this point on the record and the directory can disagree,
    # whether because this write fails, or because the process dies
    # before it finishes. The mark is what a later read checks instead of
    # trusting whatever total the record still carries.
    try:
        dirty.write_text('', encoding='utf-8')
    except OSError:
        pass
    record['stored_count'] = count
    record['stored_bytes'] = stored
    tmp = path.with_name(f'.{path.name}.tmp')
    try:
        tmp.write_text(json.dumps(record), encoding='utf-8')
        atomic_file.replace_atomically(tmp, path)
    except OSError:
        # The segment itself is already stored, so a usage update that
        # cannot be written leaves the record at its previous totals —
        # the mark above is what keeps the next read from trusting that,
        # rather than the write that just failed quietly correcting it.
        try:
            tmp.unlink()
        except OSError:
            pass  # the next write of this record reuses the same temp name
        return
    try:
        dirty.unlink()
    except OSError:
        pass  # a stale mark just costs one extra recount, never a missed one


def log_timing(job, stored, marks):
    """Print one per-phase line for a segment write, when DEBUG_TIMING is on.

    The measured total is printed beside the sum of the named parts. A gap
    between them is an unmeasured phase, and that arithmetic is the only thing
    that makes instrumentation with holes visible.
    """
    if not DEBUG_TIMING:
        return
    # Each mark is named for the phase that ENDS at it, so an interval is
    # reported under what it did. Naming intervals after the mark they start
    # from reads plausibly and is off by one, which is how a first pass here
    # blamed the byte sum for the directory scans' cost.
    parts = [(name, (ts - marks[i][1]) * 1000)
             for i, (name, ts) in enumerate(marks[1:])]
    total_ms = (marks[-1][1] - marks[0][1]) * 1000
    print(f'[SEGMENT-TIMING] {job} stored={stored} '
          + ' '.join(f'{name}={ms:.2f}' for name, ms in parts)
          + f' parts={sum(ms for _n, ms in parts):.2f} total={total_ms:.2f}',
          flush=True)


def timing_marks():
    """Return the first timing mark when segment timing is enabled."""
    return [('enter', time.perf_counter())] if DEBUG_TIMING else None
