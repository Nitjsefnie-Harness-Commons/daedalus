"""Shared lifecycle helpers for files waiting in the command queue."""
import contextlib
import itertools
import json
import threading
import time
import uuid

import atomic_file
from log_safe import log_safe
import path_safety


_lock = threading.Lock()
_claimed = set()
command_fs_lock = threading.Lock()
_seq_counter = itertools.count(1)
_cmd_events = {}  # {token: threading.Event}
_cmd_events_lock = threading.Lock()


def command_target_names(token, tab=''):
    """Return the checked queue directory and bounded legacy filename."""
    queue_name = path_safety.derived_component(
        f'{token}_{tab}' if tab else token)
    return queue_name, f'{queue_name}.json'


def claim(key):
    """Claim one logical queue key without holding a lock during delivery.

    The key is the logical target name; consumers using the same spelling
    cannot both take a claim. Hard links and symlinks leave distinct names as
    two keys; see issue #186.
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


def remove_expired(path, now, ttl, legacy=False):
    """Remove one expired command artifact without following symlinks.

    Expiry is opportunistic, so a producer can leave a malformed legacy file
    or a file can disappear while the sweep is looking at it.
    """
    if not path.name.endswith(('.json', '.tmp')):
        return
    try:
        if now - path.lstat().st_mtime <= ttl:
            return
        if legacy:
            if path.name.startswith('.') or not path.name.endswith('.json'):
                return
            # A parse failure means a writer may still hold the file mid-write;
            # any value that parses completely — dict or not — is not that
            # case, so it is expired like any other aged command artifact.
            json.loads(path.read_text(encoding='utf-8'))
        path.unlink()
    except (OSError, json.JSONDecodeError, RecursionError, ValueError):
        # A file that cannot be read or removed is reconsidered on the next
        # pass; nothing downstream depends on this call having acted.
        pass


# ─── Dashboard event queue ───
# Directory-per-token queue: commands/{token}_dashboard/<ts>_<uuid>.json
# Directory form (not single file) because concurrent writes to one file
# truncate each other.
def notify_dashboard(cmd_dir, token, payload):
    """Enqueue a dashboard SSE event. No-op if its queue cannot be named."""
    if path_safety.bad_token(token):
        return
    try:
        queue_name, _ = command_target_names(token, 'dashboard')
        dash_dir = path_safety.under(cmd_dir, queue_name)
    except ValueError:
        return
    try:
        with command_fs_lock:
            dash_dir.mkdir(parents=True, exist_ok=True)
            event_id = f'{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}'
            dashboard_event = {'id': event_id, 'kind': 'event', **payload}
            (dash_dir / f'{event_id}.json').write_text(
                json.dumps(dashboard_event, ensure_ascii=False))
        event(token).set()  # wake the dashboard stream immediately
    except Exception as e:
        print(f'[DASH-NOTIFY-FAIL] {log_safe(e)}', flush=True)


# ─── Command queue (directory-per-target, FIFO) ───
# PUT /command enqueues into commands/{token}_{tab}/<seq>.json (per-tab) or
# commands/{token}/<seq>.json (broadcast). Directory form so back-to-back
# commands to the same target queue instead of overwriting a single file.
# Legacy single-file drops (commands/{token}[_{tab}].json) are still delivered
# for the documented raw-write escape hatch.
def next_seq():
    """Monotonic, lexically-sortable queue filename stem: <ms>_<counter>."""
    return f'{int(time.time() * 1000):013d}_{next(_seq_counter):06d}'


# ─── Per-token wake events: writers signal, SSE streams wait
# (near-zero latency) ───
def event(token):
    with _cmd_events_lock:
        ev = _cmd_events.get(token)
        if ev is None:
            ev = threading.Event()
            _cmd_events[token] = ev
        return ev


def enqueue(cmd_dir, token, tab, cmd):
    """Append a command to the target's directory queue.

    Returns the delivery id.

    Refuses an unsafe `tab` itself rather than trusting the caller: this is the
    single place the value becomes a directory name, and the handler that used
    to be its only caller did not check it.
    """
    if tab and path_safety.unsafe_component(tab):
        raise ValueError(f'unsafe tab component: {tab!r}')
    queue_name, _ = command_target_names(token, tab)
    qdir = path_safety.under(cmd_dir, queue_name)
    with command_fs_lock:
        qdir.mkdir(parents=True, exist_ok=True)
        seq = next_seq()
        cmd = {**cmd, '_did': seq}
        tmp, destination = qdir / f'.{seq}.tmp', qdir / f'{seq}.json'
        try:
            tmp.write_text(
                json.dumps(cmd, ensure_ascii=False), encoding='utf-8')
            atomic_file.replace_atomically(str(tmp), str(destination))
        except (OSError, UnicodeEncodeError):
            # A refused enqueue must not leave its hidden temp behind: the
            # zero-byte artifact would sit in the queue until the background
            # collector's TTL sweep; rollback as result_store's atomic write,
            # plus the encode failure write_text raises after creating it.
            try:
                tmp.unlink()
            except OSError:
                # Best effort, and the enqueue failure re-raised below is the
                # error the caller needs. A temp left behind is collected by
                # the TTL sweep.
                pass
            raise
    event(token).set()
    return seq


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
                # Not empty, or a producer wrote into it between the scan and
                # this call — either way the namespace is not expired after
                # all, and the next sweep will look again.
                pass


def gc_loop(cmd_dir, ttl):
    """Run command expiry independently of producers and SSE consumers."""
    interval = max(0.05, min(30.0, ttl))
    while True:
        time.sleep(interval)
        collect_expired(cmd_dir, ttl)
