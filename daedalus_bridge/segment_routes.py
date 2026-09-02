"""The `/segment` and `/segment-status` routes, plus the job lookup.

Each function returns `(status, payload)`, except `admit_segment`, which
returns an `Admission` for a request that may proceed. The request handler
tells the two apart with `isinstance`, because an `Admission` is a tuple
too and would otherwise be written as a response.

`seg_dir_root` is the segments directory these routes join a job's own
directory under, and it governs the record beside that directory too:
every `segment_store` call here is given the same root, so one root names
one tree and a caller can redirect a whole request's storage by passing a
different one.
"""
import pathlib
import time
from typing import NamedTuple

from daedalus_bridge import atomic_file
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import path_safety
from daedalus_bridge import segment_store

SEGMENT_DECIMAL_MAX_DIGITS = 20


class Admission(NamedTuple):
    """A `POST /segment` settled far enough to be worth reading a body for."""

    job: str
    segment_index: int
    quota: tuple
    seg_dir: pathlib.Path
    seg_dir_root: pathlib.Path


def admit_segment(seg_dir_root, params, sig):
    """Settle POST /segment?job=X&seg=N&total=T before its body.

    The documented poster is page JavaScript running in a hostile page's
    MAIN world, so it must never hold the bridge token. It carries the
    job-scoped capability minted by POST /segment-job instead, resolved by
    the caller and handed in as `sig`. A stolen sig authorizes status reads
    and segment writes only for that job. The finalized .ts set stays
    inside the record's index, count, and byte quotas; stale temp writes
    are removed before the next admission.

    Returns an Admission for a request that may proceed, or the refusal to
    answer instead. The quota travels with the admission rather than being
    read again under the write lock: a record's recorded limits are fixed
    at mint and never rewritten, so re-reading them would cost a second
    file read per segment and settle nothing the first read did not.
    """
    job = params.get('job', [''])[0]
    seg = params.get('seg', [''])[0]
    total = params.get('total', [''])[0]
    if not job or not seg:
        return 400, {'error': 'missing job or seg'}
    if (seg.isascii() and seg.isdecimal()
            and len(seg) > SEGMENT_DECIMAL_MAX_DIGITS):
        return 400, {'error': 'seg must be a bounded ASCII decimal'}
    for val in (job, seg, total):
        if path_safety.unsafe_component(val):
            return 400, {'error': 'invalid param'}
    if not seg.isascii() or not seg.isdecimal():
        return 400, {'error': 'seg must be a bounded ASCII decimal'}
    try:
        segment_index = int(seg)
    except (ValueError, OverflowError):
        return 400, {'error': 'seg must be a bounded ASCII decimal'}

    # `total` is untrusted progress metadata supplied by the page on every
    # request. Only the server-minted record controls storage.
    try:
        seg_dir = path_safety.under(seg_dir_root, job)
        with segment_store.seg_lock:
            record = segment_store.record_for_sig(
                seg_dir_root, job, sig)
            quota = (segment_store.quota(record)
                     if record is not None else None)
    except ValueError:
        return 400, {'error': 'invalid param'}
    if quota is None:
        return 403, {'error': 'bad sig'}
    if segment_index > quota[0]:
        return 400, {'error': 'seg out of range'}
    # The directory travels with the admission so the namespace is decided
    # once, here, where the refusal is a 400 about the request rather than
    # a storage error raised under the write lock.
    return Admission(job, segment_index, quota, seg_dir, seg_dir_root)


def store_segment(raw, admission):
    """Store one admitted segment body under the job's remaining budget.

    The capability, the parameter shapes and the quota were settled by
    admit_segment. What is left has to be atomic: the file listing, the
    byte sum and the write happen under one hold of
    segment_store.seg_lock, so two segments arriving together cannot both
    spend the same remaining bytes.
    """
    job, segment_index, quota, seg_dir, seg_dir_root = admission
    _, max_count, max_bytes = quota
    marks = segment_store.timing_marks()
    with segment_store.seg_lock:
        if marks is not None:
            marks.append(('acquire', time.perf_counter()))
        filename = f'{segment_index:06d}.ts'
        tmp = seg_dir / f'.{filename}.tmp'
        final = seg_dir / filename
        try:
            seg_dir.mkdir(parents=True, exist_ok=True)
            # The totals are read here rather than carried from admission,
            # and this is the difference between them and the quota: a
            # quota is fixed at mint, while these change with every write,
            # so a value read outside this lock could be spent twice.
            try:
                record = segment_store.load_record(seg_dir_root, job)
            except segment_store.SegmentRecordError:
                record = None
            usage = (segment_store.usage(record)
                     if record is not None
                     and not segment_store.needs_recount(
                         seg_dir_root, job) else None)
            if usage is None:
                # A job minted before totals were kept, or one whose
                # last write_usage never confirmed landing. Either way
                # the record can't be trusted, and this is the only
                # scan on this path, so nothing after it pays for it
                # again.
                usage = segment_store.recount(seg_dir)
                if usage is None:
                    return 500, {'error': 'segment storage failure'}
                # Persisted now, whether or not this request's own
                # write goes on to be accepted: a rejected write never
                # reaches the write_usage call below, so without this
                # every later rejection on this job would pay for the
                # same full scan again and never clear the mark.
                segment_store.write_usage(seg_dir_root, job, *usage)
            stored_count, stored_bytes = usage
            if marks is not None:
                marks.append(('usage', time.perf_counter()))
            # One stat, for the one file this request may be replacing.
            try:
                replaced_bytes = final.stat().st_size
                replacing = True
            except FileNotFoundError:
                replaced_bytes = 0
                replacing = False
            if marks is not None:
                marks.append(('replaced', time.perf_counter()))
            if not replacing and stored_count >= max_count:
                return 413, {'error': 'segment count limit exceeded'}
            if stored_bytes - replaced_bytes + len(raw) > max_bytes:
                return 413, {'error': 'job byte limit exceeded'}
            # Marked before the segment is published, not after: once
            # the .ts file lands, this job's true storage can already
            # disagree with its record, and a crash between here and
            # the write_usage call below must not be the one window
            # where that disagreement leaves no trace at all. Refusing
            # the write outright when even this cannot be established
            # is the alternative #203 asks for to a mark that fails
            # silently and lets the write through unaccounted.
            if not segment_store.mark_dirty(seg_dir_root, job):
                return 500, {'error': 'segment storage failure'}
            try:
                atomic_file.write_bytes_retrying(tmp, raw)
                atomic_file.replace_atomically(tmp, final)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    # os.replace consumed it, which is the success path.
                    pass
            if marks is not None:
                marks.append(('write', time.perf_counter()))
            segment_store.write_usage(
                seg_dir_root, job,
                stored_count + (0 if replacing else 1),
                stored_bytes - replaced_bytes + len(raw))
            if marks is not None:
                marks.append(('record', time.perf_counter()))
                segment_store.log_timing(
                    log_safe(job), stored_count, marks)
        except OSError:
            return 500, {'error': 'segment storage failure'}
    print(f'[SEGMENT] {job}/{filename} ({len(raw)} bytes)', flush=True)
    return 200, {'ok': True}


def segment_status(seg_dir_root, params, sig):
    """GET /segment-status?job=X&sig=S — list received segments."""
    job = params.get('job', [''])[0]
    if not job or path_safety.unsafe_component(job):
        return 400, {'error': 'bad job'}
    # Both path uses inside one guard: the directory and the record the
    # sig is checked against are separate joins, and either can be the one
    # that leaves the namespace.
    try:
        seg_dir = path_safety.under(seg_dir_root, job)
        authorized = segment_store.sig_ok(seg_dir_root, job, sig)
    except ValueError:
        return 400, {'error': 'bad job'}
    if not authorized:
        # Unknown job and wrong sig get the same answer: no existence oracle.
        return 403, {'error': 'bad sig'}
    try:
        done = sorted(int(f.stem) for f in seg_dir.iterdir()
                      if f.suffix == '.ts' and f.stem.isascii()
                      and f.stem.isdecimal()) if seg_dir.is_dir() else []
    except OSError:
        return 500, {'error': 'segment storage failure'}
    return 200, {'done': done, 'count': len(done)}


def lookup_job(seg_dir_root, token, params):
    """GET /segment-job?token=X&job=Y — the capability, without minting.

    POST mints a job that does not exist yet, which is what a producer
    wants and the opposite of what a status query wants: asking about a
    name that was never used created it, so a typo left a permanent
    record behind and answered as though the job were real.

    Unlike GET /segment-status this route takes the bridge token, so an
    absent job can be reported as absent — the capability route has to
    conflate "no such job" with "wrong sig" to avoid being an existence
    oracle, and a caller holding the bridge token is owed neither.
    """
    job = params.get('job', [''])[0]
    if not job or path_safety.unsafe_component(job):
        return 400, {'error': 'bad job'}
    with segment_store.seg_lock:
        try:
            record = segment_store.load_record(seg_dir_root, job)
        except ValueError:
            return 400, {'error': 'bad job'}
        except segment_store.SegmentRecordError:
            return 500, {'error': 'segment storage failure'}
        if record is None:
            return 404, {'error': 'no such job'}
        if record.get('token') != token:
            return 409, {'error': 'job owned by a different token'}
        sig = record.get('sig', '')
        if not isinstance(sig, str) or not sig or not sig.isascii():
            return 409, {'error': 'job record cannot resume'}
    return 200, {'ok': True, 'sig': sig}
