"""Atomic result slots and retained delivery-result files."""
import os, json, threading, time, uuid

from daedalus_bridge import atomic_file
from daedalus_bridge.delivery_stripes import stripe_index
from daedalus_bridge import path_safety


# Every caller derives the delivery namespace from the results root it was
# handed, so a root and a delivery directory cannot name two trees.
DELIVERY_SUBDIR = 'deliveries'

# Result files are single-value delivery slots. POST replacement and
# conditional GET consumption share this lock so a newer result cannot land
# between the consumer's generation check and unlink.
result_lock = threading.Lock()
DELIVERY_LOCK_STRIPES = 64
delivery_locks = tuple(
    threading.Lock() for _ in range(DELIVERY_LOCK_STRIPES))

# Stripe membership is observable in delivery-write timing: same-target
# writes serialize, so a write to one target waits out a write to another
# target on the same stripe. Discovering a shared stripe that way is
# accepted, because the keyed mapping (`delivery_stripes`) leaves a caller
# nothing to compute or choose offline and a per-target lock table would be
# unbounded over attacker-controlled names.


def delivery_root(res_dir):
    """The delivery namespace belonging to one results root."""
    return res_dir / DELIVERY_SUBDIR


def delivery_stripe_key(target_dir):
    """The byte key one delivery directory is striped on, or None.

    The entry itself, as the filesystem reports it: the device and inode of
    the directory, which every path that reaches that one entry shares. A
    name could not serve here. `realpath` answers with the caller's
    spelling on every POSIX platform, so `Foo` and `foo` reach one directory
    on a case-insensitive parent while carrying two different names, and
    two names take two locks: the serialization the stripe exists to
    provide, silently absent. An inode is not a spelling and is not
    something a caller can compute or steer, which is a little more than
    the per-process secret alone was asked for.

    None means there is no entry to stripe on. That is not the same as "key
    it on the name": a name-keyed stripe for a directory that is not there
    is a stripe the writer of that directory will never take, because a
    writer creates the entry first and then keys on the entry. Two callers
    on such stripes are not serialized against each other at all, and a
    consume arriving on one while a publish holds the other destroys a
    delivery the publish reported as stored. So the question is refused
    rather than answered, and the caller that holds None does nothing to
    the delivery files.

    An entry that exists and reports no inode number is a different case:
    the entry is there, so it is keyed on its name -- the pre-round
    behaviour, and over-serialized rather than not at all, which is the
    direction a mutual-exclusion device may err in.
    """
    if not isinstance(target_dir, os.PathLike):
        # The mistake that shipped as the original bug was a caller handing
        # this a bare name, and a name here is silently resolvable against
        # the process working directory -- it would take a stripe for a
        # directory nobody in the request ever named. Refused instead.
        raise TypeError('delivery stripe takes a directory, not a name')
    try:
        info = os.stat(target_dir)
    except OSError:
        return None
    if not info.st_ino:
        return os.fsencode(os.path.basename(os.fspath(target_dir)))
    return f'{info.st_dev}:{info.st_ino}'.encode()


def delivery_lock_for(target_dir):
    """Return the lock that serializes one target's delivery files.

    Keyed on the target directory itself, not on anything a caller spelled:
    see `delivery_stripe_key` for why a name cannot answer, and for what
    None means. Every caller holds the resolved directory before the lock
    is taken, so no caller has to resolve a name to reach the right stripe.
    """
    key = delivery_stripe_key(target_dir)
    if key is None:
        return None
    index = stripe_index(key, DELIVERY_LOCK_STRIPES)
    return delivery_locks[index]


def atomic_result_write(path, data):
    """Replace one result slot only after its temp file is fully written."""
    tmp = path.parent / f'.result-{uuid.uuid4().hex}.tmp'
    try:
        atomic_file.write_bytes_retrying(tmp, data)
        atomic_file.replace_atomically(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            # The write already failed and is re-raised below. A temp that
            # will not unlink is part of the same partial state the caller is
            # about to be told about, and raising it here would replace the
            # error that explains what happened.
            pass
        raise


def result_key(token, tab=''):
    """The checked target component used by result slots and deliveries."""
    return f'{token}_{tab}' if tab else token


def delivery_result_paths(res_dir, token, tab, did):
    """Return the per-target directory and one checked result file."""
    if (not isinstance(did, str) or not did
            or path_safety.unsafe_component(did)):
        raise ValueError('invalid delivery id')
    root = delivery_root(res_dir)
    key = path_safety.derived_component(result_key(token, tab))
    delivery_dir = path_safety.under(root, key, secret=token)
    # The stripe is keyed on the entry's own name, so the resolved directory
    # must be the one-to-one namespace entry that key names -- under the
    # spelling this filesystem gives it, which is not always the spelling the
    # caller used -- and not an alias standing in for another entry.
    alias_attempts = []
    parent_matches = path_safety.same_path(
        delivery_dir.parent, root, alias_attempts)
    if (not parent_matches
            or not path_safety.same_entry(
                delivery_dir.parent, key, delivery_dir.name,
                deny_symlink=True)):
        path_safety.log_path_refusal(
            'alias', root, (key,), alias_attempts, secret=token)
        raise ValueError('delivery target is an alias')
    delivery_file = path_safety.under(
        delivery_dir, path_safety.derived_component(f'{did}.json'),
        secret=token)
    return delivery_dir, delivery_file


def _present(path):
    """Whether the path is there, answering a failed stat as "not there".

    `Path.exists()` re-raises a stat that failed for a reason other than
    absence -- a permission error, a stale handle on a network filesystem --
    and a read that lets that out leaves the caller with no response at all
    rather than an answer. For a read the safe reading of "could not tell" is
    the one it already gives for a file that is not there: nothing to read,
    and the stripe the caller asks for next is refused on the same ground.
    """
    try:
        return os.path.exists(path)
    except OSError:
        return False


def find_delivery_result(res_dir, token, tab, did):
    """Find a delivery file and the tab component that owns its slot."""
    delivery_dir, delivery_file = delivery_result_paths(
        res_dir, token, tab, did)
    if tab or _present(delivery_file):
        return delivery_dir, delivery_file, tab
    root = delivery_root(res_dir)
    prefix = f'{token}_'
    try:
        with os.scandir(root) as entries:
            names = sorted(
                entry.name for entry in entries
                if entry.name.startswith(prefix)
                and entry.is_dir(follow_symlinks=False))
    except OSError:
        names = []
    for name in names:
        try:
            candidate_dir = path_safety.under(
                root, path_safety.derived_component(name), secret=token)
            candidate_file = path_safety.under(
                candidate_dir, path_safety.derived_component(f'{did}.json'),
                secret=token)
        except ValueError:
            continue
        if _present(candidate_file):
            return candidate_dir, candidate_file, name[len(prefix):]
    return delivery_dir, delivery_file, ''


_delivery_order = {'stamp': 0}
_delivery_order_lock = threading.Lock()


def scan_delivery_results(delivery_dir):
    """Read the acceptance stamps for one target's delivery files."""
    entries = []
    try:
        with os.scandir(delivery_dir) as directory:
            for entry in directory:
                if not entry.name.endswith('.json'):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stamp = entry.stat(follow_symlinks=False).st_mtime_ns
                except OSError:
                    continue
                entries.append((stamp, entry.name))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        return None
    return entries


def mark_delivery_result(path, entries):
    """Stamp a delivery after incorporating persisted acceptance order."""
    highest = (max((stamp for stamp, _name in entries), default=0)
               if entries is not None else 0)
    with _delivery_order_lock:
        stamp = max(time.time_ns(), _delivery_order['stamp'] + 1,
                    highest + 1)
        try:
            os.utime(path, ns=(stamp, stamp))
        except OSError:
            return None
        _delivery_order['stamp'] = stamp
    return stamp


def evict_delivery_results(delivery_dir, entries, max_results):
    """Drop files older than the caller's stamp boundary."""
    if not max_results or len(entries) <= max_results:
        return
    ordered = sorted(entries, key=lambda item: item[0])
    boundary = ordered[-max_results][0]
    names = [name for stamp, name in entries if stamp < boundary]
    for name in names:
        try:
            (delivery_dir / name).unlink()
        except FileNotFoundError:
            # A consumer took this delivery between the scan and here.
            # Eviction wanted it gone and it is gone, so there is nothing to
            # report and nothing to retry.
            pass


def read_result_file(path, consume, expected):
    """Read one slot with the shared peek/conditional-consume semantics."""
    if not path.exists():
        return {'pending': True}, ''
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('result slot is not a JSON object')
    generation = data.get('resultGeneration', '')
    if consume and expected and generation != expected:
        return {'consumed': False}, ''
    if consume:
        path.unlink()
        response = ({'consumed': True, 'resultGeneration': generation}
                    if expected else data)
        return response, data.get('deliveryId', '')
    return data, ''


def remove_matching_result_file(path, generation):
    """Remove a result file only when it still names this generation."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        return
    if isinstance(data, dict) and data.get('resultGeneration') == generation:
        try:
            path.unlink()
        except OSError:
            # Removing the mirrored copy is best effort on both paths: the
            # response is already determined by the copy that was consumed, so
            # a failure here must not turn that success into a reported error.
            # FileNotFoundError is the ordinary case — the other path's
            # consume or an eviction already reached the state this call
            # wanted.
            pass


# Delivery ids of results already accepted, newest last. background.js retries
# a result POST whose response it never saw, and that retry carries the same
# delivery id as the accepted original — so without this the retry would
# republish a finished result over whatever landed after it. Bounded because
# the bridge runs for weeks: past the bound the oldest delivery is forgotten,
# which is safe as its retries are long over.
_accepted_deliveries = {}
_ACCEPTED_DELIVERY_LIMIT = 4096


def delivery_recorded(did):
    """Return whether `did` is already recorded as an accepted delivery."""
    return did in _accepted_deliveries


def record_delivery(did):
    """Remember `did` as stored, dropping the oldest once past the bound.

    Recorded only after both slots are written, and under result_lock with
    the membership test that guards them — so a delivery is never treated as
    stored on the strength of a write that failed, and two retries racing
    each other cannot both find it absent.
    """
    _accepted_deliveries[did] = None
    while len(_accepted_deliveries) > _ACCEPTED_DELIVERY_LIMIT:
        del _accepted_deliveries[next(iter(_accepted_deliveries))]
