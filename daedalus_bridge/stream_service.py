"""SSE stream lifecycle and command-consumption operations."""
import itertools
import json
import threading
import time

from daedalus_bridge import command_queue
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import path_safety


# {stream id: {'key', 'tab', 'killed'}}. The registry is keyed by a
# per-connection id rather than the replacement key: a stream that named no
# tab once got no key at all, so it served commands and held a worker while
# being invisible to health and replacement. `key` is what replacement
# matches on and is None for a tabless stream, which has no identity another
# connection can claim. `killed` set() means "die".
_active_streams = {}
_stream_ids = itertools.count(1)
_stream_lock = threading.Lock()
_last_delivery_ts = 0.0


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
                frame_writer):
    """Deliver every ready command from a directory queue in FIFO order.

    TTL-expired and non-object entries are removed; unreadable or invalid-JSON
    entries remain for a later scan because a non-atomic publisher may still
    be writing them. The socket write happens before unlink, so a failed write
    leaves the command queued for redelivery and propagates to tear the stream
    down. Returns the number of commands handed to `frame_writer`.
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
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                continue  # vanished or became unavailable during the scan
            if age > command_ttl:
                # Already gone, or gone by the next sweep: an expired command
                # is not delivered either way.
                try:
                    path.unlink()
                except OSError:
                    pass  # expired either way
                print(
                    f'[STREAM] TTL-DROP {log_safe(qdir.name)}/'
                    f'{log_safe(name)}', flush=True)
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError, ValueError):
                # A visible final name may still have an older non-atomic
                # writer. Leave it in place so that writer does not finish a
                # command into an unlinked inode; the TTL sweep bounds retries
                # for an entry that never becomes valid.
                continue
            if not isinstance(data, dict):
                # Same: readable JSON that is not a command object.
                try:
                    path.unlink()
                except OSError:
                    pass  # the TTL sweep takes it
                continue
            if chrome_tab is not None:
                data['chromeTab'] = chrome_tab
            frame_writer(data)  # BEFORE unlink
            # The claim excludes other consumers until this write and
            # unlink finish. A file that will not unlink is redelivered
            # on the next tick and deduplicated by its `_did`.
            try:
                path.unlink()
            except OSError:
                pass  # a redelivery is deduplicated by _did
            record_delivery()
            count += 1
            print(
                f'[STREAM] DELIVERED q={log_safe(qdir.name)} '
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
        cmd_file = path_safety.under(cmd_dir, legacy_name)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    with command_queue.claimed(legacy_claim_key(legacy_name)) as owned:
        if not owned:
            return 200, {}
        data = {}
        with command_queue.command_fs_lock:
            if cmd_file.exists():
                try:
                    candidate = json.loads(
                        cmd_file.read_text(encoding='utf-8'))
                    if isinstance(candidate, dict):
                        data = candidate
                        cmd_file.unlink()
                except (OSError, json.JSONDecodeError,
                        RecursionError, ValueError):
                    # A legacy drop that cannot be read is not a command. The
                    # empty answer is the one an absent file gives, and the
                    # file is left to the TTL sweep.
                    pass
        return 200, data


def drain_legacy_file(path, chrome_tab, *, command_ttl, frame_writer):
    """Deliver one atomically published legacy command file.

    A malformed visible file may still have an open writer from an older,
    non-atomic publisher. Leave it in place and retry on the next scan;
    deleting it would discard the writer's eventual complete command.
    """
    # Use the logical filename: path spellings can differ between routes;
    # result_store.delivery_lock_for documents its logical target key.
    with command_queue.claimed(legacy_claim_key(path.name)) as owned:
        if not owned:
            return 0
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, RecursionError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return 0
        if age > command_ttl:
            # Already gone, or gone by the next sweep.
            try:
                path.unlink()
            except OSError:
                pass  # expired either way
            return 0
        if chrome_tab is not None:
            data['chromeTab'] = chrome_tab
        frame_writer(data)  # BEFORE unlink
        # The claim excludes other consumers until this write and unlink
        # finish. A redelivery is deduplicated by the `_did` it carries.
        try:
            path.unlink()
        except OSError:
            pass  # a redelivery is deduplicated by _did
        record_delivery()
        print(
            f'[STREAM] DELIVERED legacy={log_safe(path.name)} '
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
            frame_writer=frame_writer)
    return count
