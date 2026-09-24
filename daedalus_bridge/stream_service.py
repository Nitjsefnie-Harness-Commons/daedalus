"""SSE stream lifecycle and command-consumption operations."""
import itertools
import json
import os
import threading
import time

from daedalus_bridge import command_queue
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import path_safety


# {stream id: {'key', 'tab', 'killed'}}. The registry is keyed by a
# per-connection id rather than the replacement key, so a stream that named
# no tab is still visible to health instead of serving commands while holding
# a worker invisibly. `key` is what replacement matches on and is None for a
# tabless stream, which has no identity another connection can claim. `killed`
# set() means "die".
_active_streams = {}
_stream_ids = itertools.count(1)
_stream_lock = threading.Lock()
_last_delivery_ts = 0.0

# Refused candidates are retained, so without a record of what was already
# reported the same object would log a line on every drain pass for as long
# as a stream stays connected. Each key pairs the candidate's logical name
# with the refused object's incarnation (device, inode, change-time): the
# TTL sweep vacates a queue name by unlinking it without an alias check, so
# a different object taking that name later must log again, while the same
# object refused again under the same name must not. Bounded like
# result_store's delivery record: past the bound the oldest entry is
# forgotten and its next refusal logs again, which keeps the log at one line
# per candidate per bounded window.
_refused_candidates = {}
_REFUSED_CANDIDATE_LIMIT = 4096
_refused_lock = threading.Lock()


def _refusal_once(key, name, reason, secret=''):
    """Report one refused candidate the first time this process refuses it."""
    with _refused_lock:
        if key in _refused_candidates:
            return
        _refused_candidates[key] = None
        while len(_refused_candidates) > _REFUSED_CANDIDATE_LIMIT:
            del _refused_candidates[next(iter(_refused_candidates))]
    print(f'[STREAM] REFUSED '
          f'{path_safety.redacted(log_safe(name), secret)}: '
          f'{path_safety.redacted(log_safe(reason), secret)}', flush=True)


def _forget_vacated(name):
    """The TTL sweep vacated `name`; forget every refusal recorded there.

    Whatever occupied the name is gone, so its record must not suppress a
    different object that later takes the name under.
    """
    with _refused_lock:
        for key in [k for k in _refused_candidates if k[0] == name]:
            del _refused_candidates[key]


command_queue.on_name_vacated(_forget_vacated)


def register(token, tab):
    """Register one connection and return its opaque id and kill event.

    Named streams replace an existing stream when their `(token, tab)` values
    compare equal. A tabless stream never replaces and is never replaced. The
    caller owns the returned pair: it stops when the event is set and passes
    both values to `unregister` when the connection ends.
    """
    key = (token, tab) if tab else None
    killed_event = threading.Event()
    with _stream_lock:
        # None is not an identity another connection can claim. A tabless
        # stream is still registered under its per-connection id so health can
        # see it, while any number of tabless connections may coexist.
        if key is not None:
            for old_id, old in list(_active_streams.items()):
                if old['key'] == key:
                    old['killed'].set()
                    del _active_streams[old_id]
                    print(f'[STREAM] REPLACED tab={tab[:8]}', flush=True)
        stream_id = next(_stream_ids)
        _active_streams[stream_id] = {
            'key': key, 'tab': tab, 'killed': killed_event}
    return stream_id, killed_event


def unregister(stream_id, killed_event):
    """Remove the stream only while it still owns `killed_event`."""
    with _stream_lock:
        entry = _active_streams.get(stream_id)
        if entry is not None and entry['killed'] is killed_event:
            del _active_streams[stream_id]


def snapshot():
    """Return the live connection count and sorted distinct tab names."""
    with _stream_lock:
        # Count connections, not distinct display names: two tokens streaming
        # the same named tab are two live workers even though stream_tabs has
        # one distinct name for them.
        return (
            len(_active_streams),
            sorted({entry['tab'] for entry in _active_streams.values()}),
        )


def record_delivery():
    """Record a delivery at the current wall-clock time."""
    global _last_delivery_ts
    _last_delivery_ts = time.time()


def last_delivery_at():
    """Return the latest delivery time, or None before the first delivery."""
    return _last_delivery_ts or None


def write_frame(stream, data):
    """Serialize, write, and flush one SSE command frame.

    Socket write and flush errors propagate so the caller can tear down the
    stream.
    """
    stream.write(f'event: command\ndata: {json.dumps(data)}\n\n'.encode())
    stream.flush()


def drain_queue(qdir, chrome_tab, killed_event, *, command_ttl,
                frame_writer, secret=''):
    """Deliver every ready command from a directory queue in FIFO order.

    TTL-expired and non-object entries are removed; unreadable or invalid-JSON
    entries remain for a later scan because a non-atomic publisher may still
    be writing them. Every candidate is read through a descriptor checked
    against the name it was found under, so an aliased entry is never
    delivered. The socket write happens before unlink, so a failed write
    leaves the command queued for redelivery and propagates to tear the stream
    down. `secret` is the credential the queue directory is named from, kept
    to its 8-character prefix in the lines this drain prints. Returns the
    number of commands handed to `frame_writer`.
    """
    if not qdir.is_dir():
        return 0
    count = 0
    try:
        queued_files = sorted(qdir.iterdir())
    except OSError:
        return 0
    for path in queued_files:
        if killed_event and killed_event.is_set():
            break
        name = path.name
        if name.startswith('.') or not name.endswith('.json'):
            continue  # skip .tmp in-flight writes
        # Use logical names: path spellings can differ between realpath and
        # directory enumeration; see result_store.delivery_lock_for.
        with command_queue.claimed(
                f'queue:{qdir.name}/{name}') as owned:
            if not owned:
                continue  # another consumer covering this queue has it
            opened, reason, ident = command_queue.open_command_candidate(path)
            if opened is None:
                if reason is not None:
                    _refusal_once(
                        (f'queue:{qdir.name}/{name}', ident),
                        f'q={qdir.name}/{name}', reason, secret=secret)
                continue  # absent, or refused: never delivered or unlinked
            # Decide with the descriptor open; unlink only once it closes.
            with opened:
                age = time.time() - os.fstat(opened.fileno()).st_mtime
                expired = age > command_ttl
                deliverable, data = False, {}
                if not expired:
                    try:
                        parsed = json.loads(opened.read().decode('utf-8'))
                    except (OSError, json.JSONDecodeError, RecursionError,
                            ValueError):
                        # An older non-atomic writer may still hold it;
                        # leave it in place, the TTL sweep bounds retries.
                        continue
                    # Readable JSON that is not a command object is dropped
                    # with the expired entries, once the descriptor is closed.
                    if isinstance(parsed, dict):
                        deliverable, data = True, parsed
            if not deliverable:
                try:
                    path.unlink()
                except OSError:
                    pass  # expired either way, or the sweep takes it
                if expired:
                    print(
                        f'[STREAM] TTL-DROP '
                        f'{path_safety.redacted(log_safe(qdir.name), secret)}'
                        f'/{log_safe(name)}', flush=True)
                continue
            if chrome_tab is not None:
                data['chromeTab'] = chrome_tab
            frame_writer(data)  # BEFORE unlink
            # The claim excludes other consumers until this write and
            # unlink finish; a file that will not unlink is redelivered
            # on the next tick and deduplicated by its `_did`.
            try:
                path.unlink()
            except OSError:
                pass
            record_delivery()
            count += 1
            print(
                f'[STREAM] DELIVERED '
                f'q={path_safety.redacted(log_safe(qdir.name), secret)} '
                f'id={log_safe(data.get("id", ""))} '
                f'did={log_safe(data.get("_did", ""))}', flush=True)
    return count


def legacy_claim_key(name):
    """The logical claim key one legacy command file is consumed under."""
    return f'legacy:{name}'


def poll_legacy(cmd_dir, token):
    """POST /poll — consume the token's legacy broadcast command file.

    Takes the claim `drain_legacy_file` takes, so a poll arriving while an
    SSE stream is draining the same file is answered empty instead of
    handing the one command to a second consumer.
    """
    try:
        # Both of these raise ValueError on a name that cannot be a safe
        # component or a path that leaves the queue root.
        _, legacy_name = command_queue.command_target_names(token)
        cmd_file = path_safety.under(cmd_dir, legacy_name, secret=token)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    # Keyed off the resolved name, as `drain_legacy_file` is: both
    # consumers of one file must land on one key.
    with command_queue.claimed(legacy_claim_key(cmd_file.name)) as owned:
        if not owned:
            return 200, {}
        data = {}
        with command_queue.command_fs_lock:
            opened, reason, ident = command_queue.open_command_candidate(
                cmd_file)
            if opened is None:
                if reason is not None:
                    _refusal_once(
                        (legacy_claim_key(cmd_file.name), ident),
                        f'legacy={cmd_file.name}', reason, secret=token)
                return 200, data
            try:
                with opened:
                    candidate = json.loads(
                        opened.read().decode('utf-8'))
            except (OSError, json.JSONDecodeError,
                    RecursionError, ValueError):
                # A legacy drop that cannot be read is not a command. The
                # empty answer is the one an absent file gives, and the
                # file is left to the TTL sweep.
                return 200, data
            if isinstance(candidate, dict):
                data = candidate
                try:
                    cmd_file.unlink()
                except OSError:
                    # The command is answered either way, so this is the
                    # at-least-once outcome the drains document: the file
                    # stays and a later poll may answer it again.
                    pass
        return 200, data


def drain_legacy_file(path, chrome_tab, *, command_ttl, frame_writer,
                      secret=''):
    """Deliver one atomically published legacy command file.

    A malformed visible file may still have an open writer from an older,
    non-atomic publisher. Leave it in place and retry on the next scan;
    deleting it would discard the writer's eventual complete command. The
    candidate is read through a descriptor checked against the name it was
    found under, so an aliased name is never delivered. `secret` is the
    credential the file's name is derived from, kept to its 8-character
    prefix in the lines this drain prints.
    """
    # Use the logical filename: path spellings can differ between routes;
    # result_store.delivery_lock_for documents its logical target key.
    with command_queue.claimed(legacy_claim_key(path.name)) as owned:
        if not owned:
            return 0
        opened, reason, ident = command_queue.open_command_candidate(path)
        if opened is None:
            if reason is not None:
                _refusal_once((legacy_claim_key(path.name), ident),
                              f'legacy={path.name}', reason, secret=secret)
            return 0  # absent, or refused: left in place
        with opened:
            try:
                age = time.time() - os.fstat(opened.fileno()).st_mtime
                data = json.loads(opened.read().decode('utf-8'))
            except (OSError, json.JSONDecodeError, RecursionError, ValueError):
                # An older non-atomic publisher may still hold it; leave it
                # in place and retry on the next scan.
                return 0
            if not isinstance(data, dict):
                return 0
            expired = age > command_ttl
        if expired:
            # Removal waits until the descriptor above is closed.
            try:
                path.unlink()
            except OSError:
                pass  # expired either way
            return 0
        if chrome_tab is not None:
            data['chromeTab'] = chrome_tab
        frame_writer(data)  # BEFORE unlink
        # The claim excludes other consumers until this write and unlink
        # finish; a redelivery is deduplicated by the `_did` it carries.
        try:
            path.unlink()
        except OSError:
            pass
        record_delivery()
        print(
            f'[STREAM] DELIVERED '
            f'legacy={path_safety.redacted(log_safe(path.name), secret)} '
            f'id={log_safe(data.get("id", ""))}', flush=True)
        return 1


def drain_legacy_ext(cmd_dir, token, killed_event, *,
                     extension_legacy_name, command_ttl, frame_writer):
    """Deliver legacy `<token>_<tab>.json` files to the extension stream.

    Each delivered command carries its tab in `chromeTab`. The dashboard file
    and `extension_legacy_name` are skipped; the latter is delivered
    separately without a tag. Scanning stops when `killed_event` is set.
    Returns the delivered command count.
    """
    prefix = f'{token}_'
    count = 0
    for path in sorted(cmd_dir.iterdir()):
        if killed_event and killed_event.is_set():
            break
        name = path.name
        if (not path.is_file() or not name.startswith(prefix)
                or not name.endswith('.json')):
            continue
        if name == extension_legacy_name:
            continue  # handled separately (no chromeTab tag)
        sub = name[len(prefix):-5]
        if sub == 'dashboard':
            continue
        count += drain_legacy_file(
            path, sub, command_ttl=command_ttl,
            frame_writer=frame_writer, secret=token)
    return count
