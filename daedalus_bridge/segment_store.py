"""Segment job records and HLS segment usage accounting."""
import os, json, hmac, secrets, threading, time, unicodedata

from daedalus_bridge import atomic_file
from daedalus_bridge import delivery_stripes
from daedalus_bridge.env_config import debug_timing
from daedalus_bridge import path_safety


# Neither a root nor a limit, so it stays a module-level switch read once
# at import rather than travelling as a parameter.
DEBUG_TIMING = debug_timing()


# One flat job namespace under the data root: segments/<job>/ holds the .ts
# files, and segments/<job>.json beside the directory records the owning token,
# minted capability, and fixed index/count/byte quotas. The page-JavaScript
# relay presents the capability (sig) rather than the bridge token, because
# anything that script carries the visited page can read.
#
# A job's own writes are held together, but two jobs are not held against
# each other: a job name is caller-chosen and unbounded in number, so the
# lock a job takes is a stripe of a fixed table rather than an entry keyed by
# the name. Stripe membership is observable as one job's write waiting out
# another job's on a shared stripe, and that is accepted for the reason
# `result_store` accepts the same trade-off for delivery results: the keyed
# mapping leaves whoever chooses job names nothing to compute offline, and a
# per-job table would be unbounded over names an authenticated caller
# controls.
#
# What the key must cover is every pair of names that could be ONE filesystem
# entry, because two names that are one entry and take two stripes lose mutual
# exclusion. Two things make names one entry here, and both are folded before
# hashing: the record affix, so `a` and `a.json` meet, and case plus
# normalisation, so `Foo`/`foo` and a composed/decomposed pair meet the way a
# case-insensitive or normalising filesystem would put them together. The
# bookkeeping names a job spends are refused at mint, so no fourth kind of
# name can reach a path — see `reserved_bookkeeping_name`. That the four
# names ARE the whole set is argued from the operations rather than
# enumerated by a control: nothing here writes or reads a fifth, and a
# fifth would be a change to a function below. The one
# filesystem equivalence the fold does not cover is an exclusion named in
# `_job_chain_root`, not a promise.
SEGMENT_LOCK_STRIPES = 64
seg_locks = tuple(threading.Lock() for _ in range(SEGMENT_LOCK_STRIPES))

_RECORD_AFFIX = '.json'
_MARK_AFFIX = '.dirty'
_TEMP_AFFIX = '.tmp'

# The two names a job spends beside its record, as suffixes on the job name.
# Every derivation of those names goes through here — `record_temp_path` and
# `_dirty_path` for the paths themselves, and this tuple for the refusal — so
# a change to the record affix moves the layout and the reservation together
# instead of leaving the mint writing a temp the refusal no longer reserves.
_BOOKKEEPING_SUFFIXES = (f'{_RECORD_AFFIX}{_MARK_AFFIX}',
                         f'{_RECORD_AFFIX}{_TEMP_AFFIX}')


def record_temp_path(seg_dir_root, job):
    """The temp a record is written from, beside the record it replaces.

    The mint publishes through this one, so the name it writes and the name
    `write_usage` replaces from and the name the mint refuses are one name.
    """
    return path_safety.under(
        seg_dir_root, f'.{job}{_RECORD_AFFIX}{_TEMP_AFFIX}')


def _folded(name):
    """The name as the stripe key compares it: decomposed and casefolded.

    ONE fold, and both halves of the namespace derive from it — the chain
    root the lock key is built from, and the shape the mint refuses on. A
    second normalisation beside the first rebuilds #1167, which is what a
    case-sensitive reservation beside a folded key is: the refused
    `.a.json.dirty` and the accepted `.a.json.DIRTY` are one directory on a
    case-insensitive filesystem, so the accepted spelling parks a directory
    exactly where `mark_dirty` has to write and every later write for the
    owner answers 500.
    """
    return unicodedata.normalize('NFKD', name).casefold()


def reserved_bookkeeping_name(job):
    """Whether `job` is a name the segment layout already spends on a job.

    A job named K owns four names under the segments root: its directory
    `K`, its record `K.json`, the marker `.{K}.json.dirty` and the temp
    `.{K}.json.tmp`. Only the last two can be another job's own name, and
    minting one parks a directory exactly where its owner has to write a
    file: `mark_dirty` cannot, so every segment write for the owner
    answers 500 from then on.

    The test is the shape, not what is on disk, because a lookup answers
    differently on the two sides of the minting order. With the reserved
    name minted first there is no owner to collide with yet, so a
    conditional refusal admits it — and the owner's own mint then succeeds
    into a namespace the squatter already holds, which is the same 500 one
    step later. The shape asks the same question whichever job is minted
    first, needs no stat, and cannot be raced by a concurrent mint.

    `.json.dirty` and `.json.tmp` are not that shape: they carry no job
    name between the leading dot and the affix, so no job reserves them.
    """
    # The same folded name the lock key compares, so the two halves cannot
    # answer differently about one name. The length test runs on the folded
    # form too: it is what keeps `.json.dirty` — and every case variant of
    # it — mintable, because there is no owner between the dot and the affix.
    folded = _folded(job)
    if not folded.startswith('.'):
        return False
    return any(folded.endswith(suffix) and len(folded) > len(suffix) + 1
               for suffix in _BOOKKEEPING_SUFFIXES)


def _job_chain_root(job):
    """The name whose chain of `<job>.json` descendants `job` belongs to.

    The namespace is flat, so the job named `a` keeps its record at
    `a.json` — the very directory the job named `a.json` keeps its segments
    in, and the write path joins that pair as well as the mint does,
    because it writes that record too. Folding the affix off names the
    chain, and every member of a chain then takes one stripe: the two calls
    that touch one path cannot interleave.

    The case and normalisation folds are what make the chain the whole
    story rather than nearly all of it. `os.path.normcase` cannot see what
    a case-insensitive filesystem does to `Foo` and `foo`, and those two
    names are one directory and one record there; a key that split them
    would let two writes to one directory interleave the usage read, the
    quota check and the record write that `store_segment` holds as one.
    Normalising and casefolding before the strip also puts `Foo.JSON` and
    `foo.json` on one chain, which is where a case-insensitive parent would
    have put them.

    The second normalise afterwards is REDUNDANT, and the fold is ordered this
    way anyway: proved inert, 0 non-fixpoints over all 1,114,112 code points
    against four affix templates. The first pass leaves a string already
    decomposed and lowercased and the strip only removes a trailing ASCII
    affix from it, so no adjacency is left for a second pass to collapse. It
    stays because a redundant fold in the superset direction costs nothing:
    0 differences across 4,456,448 affix-template names, so it is free to
    keep and free to lose.
    `test_the_chain_root_is_a_fixpoint` pins the property that makes it
    redundant, so a strip that became prefix-removing, or a normalisation
    that composed, would fail a control rather than leave this untrue.

    Every fold here is a superset of the equivalence a case-folding or
    normalising filesystem applies, and of NTFS's upcased comparison. It
    is a superset in the direction that is safe: two names that are
    genuinely distinct and land on one stripe cost the wait that sharing a
    stripe already costs, while two names that are one entry and land on
    different stripes lose mutual exclusion entirely. So the key folds more
    whenever in doubt.

    One exclusion, stated rather than papered over: NTFS short-name (8.3)
    aliases. A generated alias is always `XXXXXX~N`, so a job deliberately
    named `Abcdef~1` and the job whose long name generates that alias are
    one directory, and the two keys differ. That needs an authenticated
    caller to craft the name on purpose, and this says so instead of
    claiming a universal the fold does not deliver.

    With that exclusion named, and with the bookkeeping names refused at
    mint, the chain is then the set of job names that can own one path.
    """
    root = _folded(job)
    while root.endswith(_RECORD_AFFIX):
        root = root[:-len(_RECORD_AFFIX)]
    return _folded(root)


def seg_lock_for(job):
    """Return the lock that serializes one segment job's storage.

    ONE authority: this is the only function that turns a job name into a
    lock, and every caller passes the job name it was given, unchanged.
    Four sites used to share one module-level lock and so agreed by
    construction; keyed, they agree only as long as each one calls this
    with the same name, and an edit that passes a decorated name is a
    concurrency change wearing a rename's clothes. `admit_segment` and
    `store_segment` no longer even compute it independently — the admission
    carries the lock, so the load-bearing pair cannot drift apart.

    Keyed on the job's chain root, so the `a` / `a.json` pair takes one
    lock and two unrelated jobs generally do not. Every caller takes this
    one lock and nothing else, so there is no order to acquire in and no
    way to hold two of them.

    The mapping is per-PROCESS — `delivery_stripes` seeds it from a secret
    at import — so mutual exclusion holds only within one bridge. Two
    bridges over one data root would map the same job to different locks
    and silently lose it. `server.py`'s `data_root_lock` is what makes that
    impossible: one bridge process per data root, refused at startup. This
    module depends on that and does not enforce it.

    The four acquisition sites agreeing on one lock is likewise a property
    ARGUED, not one a control checks: it follows from each site passing the
    `job` it was handed, and the control that would catch a site decorating
    that name catches it through the record of what was asked for rather than
    through anything that enumerates the sites.
    """
    index = delivery_stripes.stripe_index(
        _job_chain_root(job).encode('utf-8', 'surrogatepass'),
        SEGMENT_LOCK_STRIPES)
    return seg_locks[index]


def record_path(seg_dir_root, job):
    """The record beside a job's directory, refused if it lands outside.

    One root names both a job's directory and the record accounting for it.

    Raises ValueError like `under`. Every route reaching here has already
    answered for a bad job name, so a containment failure joins that answer
    rather than becoming a storage error.
    """
    return path_safety.under(seg_dir_root, f'{job}{_RECORD_AFFIX}')


class SegmentRecordError(Exception):
    """A job record exists but could not be read as one."""


def load_record(seg_dir_root, job):
    """Return `job`'s JSON object, or None when there is no record at all.

    A record that exists and cannot be read raises instead of arriving as
    None: the mint reads None as "this job does not exist yet" and writes a
    fresh owner and capability over whatever is there, so collapsing the two
    turned local corruption into a destroyed resume identity reported as a
    successful mint.
    """
    path = record_path(seg_dir_root, job)
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


def record_for_sig(seg_dir_root, job, sig):
    """Return `job` metadata when `sig` matches its minted capability.

    compare_digest raises TypeError on non-ASCII str input, and the sig arrives
    as a query string, so both sides are gated before the comparison.
    """
    try:
        record = load_record(seg_dir_root, job)
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


def sig_ok(seg_dir_root, job, sig):
    """Constant-time check of `sig` against the capability minted for `job`."""
    return record_for_sig(seg_dir_root, job, sig) is not None


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


def new_record(token, quotas, stored_count=0, stored_bytes=0):
    """Build a newly minted job record fixing `quotas` into it.

    `quotas` is taken positionally so this module does not import the
    route module that names its fields.
    """
    max_index, max_count, max_bytes = quotas
    return {
        'token': token,
        'sig': secrets.token_urlsafe(32),
        'max_segment_index': max_index,
        'max_segment_count': max_count,
        'max_bytes': max_bytes,
        'stored_count': stored_count,
        'stored_bytes': stored_bytes,
    }


def _dirty_path(seg_dir_root, job):
    """Where write_usage marks that job's totals may not have landed."""
    return record_path(seg_dir_root, job).with_name(
        f'.{job}{_RECORD_AFFIX}{_MARK_AFFIX}')


def needs_recount(seg_dir_root, job):
    """Whether a previous write_usage for `job` may not have landed.

    The mark goes down before the write it guards even starts, so it is
    still there after a write that fails outright and after a crash
    partway through one — both leave the record at its old totals, and
    this is what stops the next read from trusting them. write_usage
    clears it itself once the replace it guards has actually landed.
    """
    return _dirty_path(seg_dir_root, job).exists()


def mark_dirty(seg_dir_root, job):
    """Establish the durable "this job's totals may go stale" marker.

    Returns whether it actually landed. A caller about to make this job's
    stored bytes disagree with its record — publishing a new segment, or
    about to overwrite the record itself — has to know that before it
    goes ahead: proceeding after a swallowed failure leaves neither a
    marker nor a correct record once the write also fails.
    """
    try:
        atomic_file.write_text_retrying(_dirty_path(seg_dir_root, job), '')
    except OSError:
        return False
    return True


def write_usage(seg_dir_root, job, count, stored):
    """Persist a job's totals, leaving every other field of its record alone.

    Read-modify-write under the caller's lock. A record that has become
    unreadable is left alone rather than replaced: the mint is the only
    writer allowed to answer for corruption, and overwriting here would
    destroy the owner and capability a resume depends on.

    Callers are responsible for having called mark_dirty themselves
    before whatever made this update necessary took effect — a segment
    publish, or a recount — since only they know when that was. This only
    clears the mark, and only once the record write it guards has
    actually landed.
    """
    try:
        record = load_record(seg_dir_root, job)
    except SegmentRecordError:
        return
    if record is None:
        return
    path = record_path(seg_dir_root, job)
    dirty = _dirty_path(seg_dir_root, job)
    record['stored_count'] = count
    record['stored_bytes'] = stored
    tmp = record_temp_path(seg_dir_root, job)
    try:
        atomic_file.write_text_retrying(tmp, json.dumps(record))
        atomic_file.replace_atomically(tmp, path)
    except OSError:
        # The segment itself is already stored, so a usage update that
        # cannot be written leaves the record at its previous totals —
        # the caller's mark is what keeps the next read from trusting
        # that, rather than the write that just failed quietly correcting
        # it.
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
    # reported under what it did; naming intervals after the mark they start
    # from would report them off by one.
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
