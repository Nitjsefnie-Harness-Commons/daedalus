"""The non-destructive fan-out drain behind the dashboard event stream.

The dashboard is a subscription, not an addressed delivery: every window on
one token receives every event published while it is connected. That makes
this drain's removal rule a join over the live subscribers' cursors rather
than `drain_queue`'s unconditional unlink, so the per-connection cursor and
its registry queries live in `stream_service` and are called from here.
"""
import json
import os
import time

from daedalus_bridge import command_queue
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import path_safety
from daedalus_bridge import stream_service


def _unlink(path):
    """Remove one queue entry, tolerating one that is already gone."""
    try:
        path.unlink()
    except OSError:
        pass  # the peer or the sweep took it


def drain_dashboard(qdir, token, killed_event, *, command_ttl,
                    frame_writer, secret=''):
    """Fan one dashboard event stream out to this connection.

    Unlike `drain_queue`, an entry is not unlinked just because this
    connection has read it: it is unlinked only once every live dashboard
    subscription for the token holds a cursor at or past it, or the TTL
    takes it. A slow or wedged window therefore costs retained files for at
    most the command TTL and never costs another window an event. Returns
    the number of events handed to `frame_writer`.
    """
    if not qdir.is_dir():
        return 0
    try:
        queued_files = sorted(qdir.iterdir())
    except OSError:
        return 0
    # None means the connection is not a live dashboard subscription.
    start = stream_service.subscription_cursor(killed_event)
    if start is None:
        return 0
    count = 0
    for path in queued_files:
        if killed_event and killed_event.is_set():
            break
        name = path.name
        if name.startswith('.') or not name.endswith('.json'):
            continue  # skip .tmp in-flight writes
        if name <= start:
            # Already consumed by this connection on an earlier pass;
            # reconsider removal, never redeliver.
            if stream_service.every_subscription_past(token, name):
                _unlink(path)
            continue
        key = command_queue.queue_key(qdir.name, name)
        with command_queue.claimed(key) as owned:
            if not owned:
                continue  # another consumer covering this queue has it
            opened, reason, ident = command_queue.open_command_candidate(path)
            if opened is None:
                if reason is not None:
                    stream_service.refusal_once(
                        (key, ident),
                        f'q={qdir.name}/{name}', reason, secret=secret)
                continue  # absent, or refused: never delivered or unlinked
            # Decide with the descriptor open; unlink only once it closes.
            with opened:
                age = time.time() - os.fstat(opened.fileno()).st_mtime
                expired = age > command_ttl
                data = None
                if not expired:
                    try:
                        parsed = json.loads(opened.read().decode('utf-8'))
                    except (OSError, json.JSONDecodeError, RecursionError,
                            ValueError):
                        # An older non-atomic writer may still hold it;
                        # leave it in place, the TTL sweep bounds retries.
                        continue
                    if isinstance(parsed, dict):
                        data = parsed
            if data is not None:
                frame_writer(data)  # BEFORE any removal
                stream_service.record_delivery()
                count += 1
                print(
                    f'[STREAM] DELIVERED '
                    f'q={path_safety.redacted(log_safe(qdir.name), secret)} '
                    f'id={log_safe(data.get("id", ""))} '
                    f'did={log_safe(data.get("_did", ""))}', flush=True)
            stream_service.advance_subscription(killed_event, name)
            # A refused object and an expired entry leave now; a real event
            # waits until every live peer has reached it.
            if (data is None
                    or stream_service.every_subscription_past(token, name)):
                _unlink(path)
                if expired:
                    print(
                        f'[STREAM] TTL-DROP '
                        f'{path_safety.redacted(log_safe(qdir.name), secret)}'
                        f'/{log_safe(name)}', flush=True)
    return count
