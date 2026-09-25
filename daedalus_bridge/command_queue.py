"""Shared lifecycle helpers for files waiting in the command queue."""
import contextlib
import json
import os
import stat
import threading
import time

from daedalus_bridge import atomic_file
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge.queue_order import next_seq  # re-exported
from daedalus_bridge import path_safety


_lock = threading.Lock()
_claimed = set()
command_fs_lock = threading.Lock()
_cmd_events = {}  # {token: threading.Event}
_cmd_events_lock = threading.Lock()

# The one name of the dashboard target, so "is this the dashboard" is a
# single criterion rather than a spelling repeated at each site.
DASHBOARD_TAB = 'dashboard'

# Set by the stream service, which owns the refusal registry, so the TTL
# sweep can retire a name whose occupant it unlinked. None when nothing
# registered, so this module stands alone.
_name_vacated = None


def command_target_names(token, tab=''):
    """Return the checked queue directory and bounded legacy filename."""
    queue_name = path_safety.derived_component(
        f'{token}_{tab}' if tab else token)
    return queue_name, f'{queue_name}.json'


def claim(key):
    """Claim one logical queue key without holding a lock during delivery.

    The key is the logical target name; consumers using the same spelling
    cannot both claim. Aliasing is refused in the read, not here: a linked
    or multiply-named object is refused by the identity check in
    `open_command_candidate`.
    """
    if not isinstance(key, str) or not key:
        raise TypeError('claim key must be a non-empty string')
    with _lock:
        if key in _claimed:
            return False
        _claimed.add(key)
        return True


def release(key):
    """Release a key so a later consumer can retry its queued command."""
    with _lock:
        _claimed.discard(key)


@contextlib.contextmanager
def claimed(key):
    """Yield ownership and release it even when delivery raises."""
    owner = claim(key)
    try:
        yield owner
    finally:
        if owner:
            release(key)


def _candidate_refusal(opened, named):
    """Why one opened candidate must not be delivered, or None to accept it."""
    if not stat.S_ISREG(opened.st_mode):
        return 'candidate is not a regular file'
    if opened.st_nlink != 1:
        return f'candidate carries {opened.st_nlink} names'
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        return 'the name stopped naming the opened object'
    return None


def _identity(stat_result):
    """The object incarnation a stat result names.

    The inode alone does not identify an object: a filesystem may hand a
    freed inode straight back to the next object at the name, which is what
    the TTL sweep makes room for. The change time is a best-effort second
    discriminator, not a generation: a coarse clock gives two objects in one
    tick one value, and an outside `utime`/`chmod` re-logs the same object
    once more. Where ctime cannot separate two objects the sweep's
    `on_name_vacated` retire decides the last case; this is kept for when no
    retire runs.
    """
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_ctime_ns)


def _name_identity(path):
    """The identity of whatever object occupies `path`, or None.

    A refusal that never opened a descriptor still needs an identity, so a
    later object at the same name is not mistaken for this one. None only
    when the name cannot be stat'd; two such objects at one name then share
    the `(name, None)` key, so the second is suppressed — a residual gap in
    the same class this key fixes.
    """
    try:
        return _identity(os.lstat(path))
    except OSError:
        return None


def open_command_candidate(path):
    """Open one command candidate through a descriptor checked against its
    name.

    Returns ``(stream, None, ident)``, or ``(None, reason, ident)`` when the
    candidate must not be delivered. `ident` is the object incarnation
    ``(device, inode, change-time)``, so a caller recording one refusal per
    object can tell this object from a later one at the same name; None only
    when neither the descriptor nor the name can be stat'd. Refused: a
    symlinked name, a non-regular or multiply-named object, and a name that
    stopped naming the opened object. A symlink is refused where the platform
    offers ``O_NOFOLLOW`` (a broken one included) and elsewhere by the
    identity check, so a linked name delivers nothing either way; a platform
    without ``O_NOFOLLOW`` reports a broken link as absence. ``reason`` is
    None only when the name named nothing — absence, not refusal.

    No exception escapes: a failing open or stat is itself a refusal, and a
    refused candidate is never removed here. Callers read and decide with the
    descriptor open, then act with it closed, because Windows cannot unlink a
    file it still holds open; a parse failure must leave the name in place.
    """
    # O_NONBLOCK keeps a FIFO named like a command file from hanging the
    # drain on an open with no writer; a no-op for regular files, absent on
    # Windows.
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0)
             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None, None, None
    except OSError as error:
        # The open was refused (a symlink where the platform offers
        # O_NOFOLLOW, or an object that would not open); there is no
        # descriptor, so the name's own lstat carries the identity.
        return None, f'cannot open: {log_safe(error)}', _name_identity(path)
    try:
        opened = os.fstat(fd)
        named = os.lstat(path)
    except OSError as error:
        os.close(fd)
        return None, f'cannot check: {log_safe(error)}', _name_identity(path)
    reason = _candidate_refusal(opened, named)
    if reason is not None:
        os.close(fd)
        return None, reason, _identity(opened)
    try:
        return os.fdopen(fd, 'rb'), None, _identity(opened)
    except OSError as error:
        # io.open closes a descriptor it could not wrap, so there is nothing
        # left to close here.
        return None, f'cannot read: {log_safe(error)}', _identity(opened)


def queue_key(dir_name, name):
    """The logical key one queue entry is recorded and retired under."""
    return f'queue:{dir_name}/{name}'


def legacy_key(name):
    """The logical key one legacy command file is recorded and retired
    under."""
    return f'legacy:{name}'


def on_name_vacated(callback):
    """Register `callback`, called with a name's logical key when the TTL
    sweep vacates that name by unlinking its child (queue entry or legacy
    file).

    The sweep is the one moment that knows a name is free, and a replacement
    object can be indistinguishable from the recorded one (see `_identity`),
    so the registry retires the name here rather than guess. The callback
    runs INSIDE the sweep holding the non-reentrant `command_fs_lock`; it
    must not re-enter that lock and must not raise.
    """
    global _name_vacated
    _name_vacated = callback


def remove_expired(path, now, ttl, legacy=False):
    """Remove one expired command artifact without following symlinks.

    Expiry is opportunistic, so a file can disappear mid-sweep. The two
    namespaces differ: a queue entry is expired by name, while a legacy
    candidate the read refuses is retained, because unlinking it would take
    the name from a consumer about to refuse it for the same reason.
    """
    if not path.name.endswith(('.json', '.tmp')):
        return
    try:
        if now - path.lstat().st_mtime <= ttl:
            return
        if legacy:
            if (path.name.startswith('.')
                    or not path.name.endswith(('.json', '.json.tmp'))):
                return
            opened, _, _ = open_command_candidate(path)
            if opened is None:
                return
            # A parse failure means a writer may still hold the file mid-write;
            # any value that parses completely — dict or not — is not that
            # case, so it is expired like any other aged command artifact.
            with opened:
                json.loads(opened.read().decode('utf-8'))
        path.unlink()
        if _name_vacated is not None:
            # A successful unlink frees the name, so retire it rather than
            # leave a record for a gone object; the callback runs under
            # command_fs_lock, so guard it — a raising one must not kill it.
            key = (legacy_key(path.name) if legacy
                   else queue_key(path.parent.name, path.name))
            try:
                _name_vacated(key)
            except Exception:  # pylint: disable=broad-except
                pass
    except (OSError, json.JSONDecodeError, RecursionError, ValueError):
        # A file that cannot be read or removed is reconsidered on the next
        # pass; nothing downstream depends on this call having acted.
        pass


# ─── Queue file publication ───
def _publish(qdir, stem, document):
    """Write `document` as `<stem>.json` in `qdir` through a hidden temp.

    The drain decodes UTF-8 and skips dot-prefixed names, so the encoding is
    fixed here rather than left to the locale -- a code-page file is
    undecodable on Windows and stays queued until the TTL sweep -- and the
    final name only appears complete. Under `command_fs_lock`, the same lock
    that guards the `next_seq` mint, so the temp-then-rename and the mint are
    mutually exclusive: a stem cannot be handed out between the temp and the
    rename that publishes it.
    """
    tmp, destination = qdir / f'.{stem}.tmp', qdir / f'{stem}.json'
    try:
        atomic_file.write_text_retrying(
            tmp, json.dumps(document, ensure_ascii=False), encoding='utf-8')
        atomic_file.replace_atomically(str(tmp), str(destination))
    except (OSError, UnicodeEncodeError):
        # A refused publish must not leave its hidden temp behind: the
        # zero-byte artifact would sit in the queue until the background
        # collector's TTL sweep; rollback as result_store's atomic write,
        # plus the encode failure write_text raises after creating it.
        try:
            tmp.unlink()
        except OSError:
            # Best effort, and the publish failure re-raised below is the
            # error the caller needs. A temp left behind is collected by
            # the TTL sweep.
            pass
        raise


# ─── Dashboard event queue ───
# Directory-per-token queue: commands/{token}_dashboard/<ms>_<counter>.json
# Directory form (not a single file) because concurrent writes to one file
# truncate each other.
def notify_dashboard(cmd_dir, token, payload):
    """Enqueue a dashboard SSE event. No-op if its queue cannot be named."""
    if path_safety.bad_token(token):
        return
    try:
        queue_name, _ = command_target_names(token, DASHBOARD_TAB)
        dash_dir = path_safety.under(cmd_dir, queue_name, secret=token)
    except ValueError:
        return
    try:
        with command_fs_lock:
            dash_dir.mkdir(parents=True, exist_ok=True)
            # The fan-out drain's cursor orders events by name, so the stem
            # must be publish-ordered. Both callers take `next_seq` under
            # this same lock the publish is taken under, so a stem cannot be
            # handed out in an order the writes do not follow.
            event_id = next_seq(cmd_dir)
            # The bridge's own id and kind go AFTER the payload: the client
            # dedups on id, so a publisher must not forge one or the kind.
            _publish(dash_dir, event_id,
                     {**payload, 'id': event_id, 'kind': 'event'})
        event(token).set()  # wake the dashboard stream immediately
    except Exception as e:
        # The stream connect line's by-design residual; alert 124 is a
        # false positive, dismissed on the Security tab: this prefix
        # prints, not the credential (issue 890).
        print(f'[DASH-NOTIFY-FAIL] '
              f'{path_safety.redacted(log_safe(e), token)}', flush=True)


# ─── Command queue (directory-per-target, FIFO) ───
# PUT /command enqueues into commands/{token}_{tab}/<seq>.json (per-tab) or
# commands/{token}/<seq>.json (broadcast). Directory form so back-to-back
# commands to the same target queue instead of overwriting a single file.
# Legacy single-file drops (commands/{token}[_{tab}].json) are still delivered
# for the documented raw-write escape hatch.


# ─── Per-token wake events: writers signal, SSE streams wait ───
def event(token):
    with _cmd_events_lock:
        ev = _cmd_events.get(token)
        if ev is None:
            ev = threading.Event()
            _cmd_events[token] = ev
        return ev


def _live_duplicate(qdir, cmd, command_ttl):
    """Return the delivery id of a live queued copy of `cmd`, or None.

    A live candidate is a complete, non-hidden `.json` entry young enough to
    be delivered — `remove_expired`'s boundary — read through the drain's own
    descriptor check. A refused or unreadable entry matches nothing: a
    delivery the drain will not make is not a live delivery.
    """
    now = time.time()
    try:
        entries = sorted(qdir.iterdir())
    except OSError:
        return None
    for path in entries:
        name = path.name
        if name.startswith('.') or not name.endswith('.json'):
            continue  # skip .tmp in-flight writes
        opened, _, _ = open_command_candidate(path)
        if opened is None:
            continue
        with opened:
            if now - os.fstat(opened.fileno()).st_mtime > command_ttl:
                continue
            try:
                parsed = json.loads(opened.read().decode('utf-8'))
            except (OSError, json.JSONDecodeError, RecursionError,
                    ValueError):
                continue
        if isinstance(parsed, dict) and {
                k: v for k, v in parsed.items() if k != '_did'} == cmd:
            return name[:-len('.json')]
    return None


def enqueue(cmd_dir, token, tab, cmd, *, command_ttl):
    """Append a command to the target's directory queue.

    Returns ``(delivery_id, duplicate)``. While an identical copy of `cmd`
    is still queued, a retry admits nothing and returns the live delivery's
    id: a caller whose wait timed out cannot retract what it queued, so its
    retry must wait on the first execution. Refuses an unsafe `tab`: this is
    the single place the value becomes a directory name.
    """
    if tab and path_safety.unsafe_component(tab):
        raise ValueError(f'unsafe tab component: {tab!r}')
    queue_name, _ = command_target_names(token, tab)
    qdir = path_safety.under(cmd_dir, queue_name, secret=token)
    with command_fs_lock:
        qdir.mkdir(parents=True, exist_ok=True)
        live = _live_duplicate(qdir, cmd, command_ttl)
        if live is not None:
            seq, duplicate = live, True
        else:
            seq = next_seq(cmd_dir)
            _publish(qdir, seq, {**cmd, '_did': seq})
            duplicate = False
    event(token).set()
    return seq, duplicate


def collect_expired(cmd_dir, ttl):
    """Expire command files and empty queue directories without an SSE
    reader.
    """
    now = time.time()
    with command_fs_lock:
        try:
            entries = list(cmd_dir.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                remove_expired(entry, now, ttl, legacy=True)
                continue
            try:
                children = list(entry.iterdir())
            except OSError:
                continue
            for child in children:
                remove_expired(child, now, ttl)
            try:
                entry.rmdir()
            except OSError:
                # Not empty, or a producer wrote in between — the next sweep
                # looks again.
                pass


def gc_loop(cmd_dir, ttl):
    """Run command expiry independently of producers and SSE consumers."""
    interval = max(0.05, min(30.0, ttl))
    while True:
        time.sleep(interval)
        collect_expired(cmd_dir, ttl)
