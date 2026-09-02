"""`POST /segment-job` — minting and resuming an HLS relay job.

`mint_job` returns `(status, payload)` and takes the quotas as a
`JobQuotas`, so the route reads no configuration itself: they are fixed
into a fresh record and are what a legacy record is measured against.

`seg_dir_root` is the segments root the job's directory and its record
both live under; `segment_routes`' module docstring carries the one
statement of what that root governs.
"""
import json
from typing import NamedTuple

from daedalus_bridge import atomic_file
from daedalus_bridge import path_safety
from daedalus_bridge import segment_store


class JobQuotas(NamedTuple):
    """The limits a mint fixes into a record, new or converted."""

    max_index: int
    max_count: int
    max_bytes: int


def mint_job(seg_dir_root, token, body, quotas):
    """POST /segment-job — mint (or re-fetch) the capability for an HLS job.

    Idempotent for the owning token: the relay is documented as resumable,
    so re-minting returns the same sig and a resume keeps working. A job
    already owned by a different token answers 409. The record lives beside
    the job's directory so both survive together.
    """
    max_index, max_count, max_bytes = quotas
    job = body.get('job', '')
    if not job or path_safety.unsafe_component(job):
        return 400, {'error': 'bad job'}
    with segment_store.seg_lock:
        try:
            record = segment_store.load_record(seg_dir_root, job)
            job_dir = path_safety.under(seg_dir_root, job)
            tmp = path_safety.under(seg_dir_root, f'.{job}.json.tmp')
            record_path = segment_store.record_path(seg_dir_root, job)
        except ValueError:
            return 400, {'error': 'bad job'}
        except segment_store.SegmentRecordError:
            return 500, {'error': 'segment storage failure'}
        if record is not None:
            if record.get('token') != token:
                return 409, {'error': 'job owned by a different token'}
            sig = record.get('sig', '')
            if not isinstance(sig, str) or not sig or not sig.isascii():
                return 409, {'error': 'job record cannot resume'}
            quota = segment_store.quota(record)
            if quota is not None:
                # A resume is the right moment to reconcile: this counts
                # the directory, refreshes the totals, and sweeps temps a
                # crashed write left behind. It is O(files), which is why
                # it lives here and not on the per-segment path -- a job
                # is minted once per resume, not once per segment.
                #
                # It also heals the one drift the write path can leave: a
                # crash between publishing a segment and recording it.
                reconciled = segment_store.recount(job_dir)
                if reconciled is not None and (
                        record.get('stored_count'),
                        record.get('stored_bytes')) != reconciled:
                    segment_store.mark_dirty(seg_dir_root, job)
                    segment_store.write_usage(seg_dir_root, job, *reconciled)
                return 200, {'ok': True, 'sig': sig}

            quota_fields = (
                'max_segment_index', 'max_segment_count', 'max_bytes')
            if any(field in record for field in quota_fields):
                return 409, {'error': 'job record cannot resume'}
            if any(value < 0 for value in (
                    max_index, max_count, max_bytes)):
                return 409, {'error': 'job record cannot resume'}

            try:
                if not job_dir.is_dir():
                    return 409, {'error': 'job record cannot resume'}
                segment_files = [
                    path for path in job_dir.iterdir()
                    if path.is_file() and path.suffix == '.ts'
                ]
                stored_bytes = sum(
                    path.stat().st_size for path in segment_files)
            except OSError:
                return 500, {'error': 'segment storage failure'}
            stored_indices = [
                int(path.stem) for path in segment_files
                if path.stem.isascii() and path.stem.isdecimal()
            ]
            if (len(segment_files) > max_count
                    or stored_bytes > max_bytes
                    or any(index > max_index
                           for index in stored_indices)):
                return 409, {'error': 'legacy job exceeds current quotas'}

            record = {
                **record,
                'max_segment_index': max_index,
                'max_segment_count': max_count,
                'max_bytes': max_bytes,
                # This branch has already counted and measured the job to
                # decide whether it fits current quotas, so seeding the
                # totals here costs nothing and spares the first segment
                # write a recount.
                'stored_count': len(segment_files),
                'stored_bytes': stored_bytes,
            }
            try:
                atomic_file.write_text_retrying(tmp, json.dumps(record))
                atomic_file.replace_atomically(tmp, record_path)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    # The record write already failed and the 500 below is
                    # the answer; a leftover temp is overwritten by the
                    # next write to this job's name.
                    pass
                return 500, {'error': 'segment storage failure'}
            return 200, {'ok': True, 'sig': sig}
        # Counted, not assumed empty: a record can be deleted while its
        # directory survives, and seeding zero there would hand the job a
        # budget it has already spent. This is also where a temp left by a
        # crashed write is swept, which is off the per-segment path.
        seeded = segment_store.recount(job_dir)
        seeded_count, seeded_bytes = seeded if seeded is not None else (0, 0)
        record = segment_store.new_record(
            token, quotas, seeded_count, seeded_bytes)
        sig = record['sig']
        made_dir = not job_dir.exists()
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            atomic_file.write_text_retrying(tmp, json.dumps(record))
            atomic_file.replace_atomically(tmp, record_path)  # publish
        except OSError:
            # Job names may contain dots, so the flat namespace collides
            # in EITHER minting order: with job 'a' taken, this mkdir for
            # job 'a.json' hits the existing 'a.json' record file; with
            # job 'a.json' taken, this os.replace for job 'a' targets the
            # existing 'a.json' directory. Both raise OSError and both
            # mean the name is unavailable. A refused mint must write
            # nothing, so the half-publish is rolled back: the tmp record,
            # and the job directory when this call created it.
            try:
                tmp.unlink()
            except OSError:
                # Best effort: the 409 below is the answer either way, and
                # the next mint on this name overwrites the temp.
                pass
            if made_dir:
                try:
                    job_dir.rmdir()
                except OSError:
                    # Only this call's own directory is removed, and only
                    # while empty. One that is not empty belongs to
                    # whatever filled it.
                    pass
            return 409, {'error': 'job name unavailable'}
    return 200, {'ok': True, 'sig': sig}
