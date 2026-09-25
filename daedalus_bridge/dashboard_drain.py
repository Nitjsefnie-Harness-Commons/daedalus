"""The non-destructive fan-out drain behind the dashboard event stream.

The dashboard is a subscription, not an addressed delivery: every window on
one token receives every event published while it is connected. Removal is
therefore a join over the live subscribers' cursors rather than
`drain_queue`'s unconditional unlink, so the cursor and its registry queries
live in `stream_service` and are called from here.
"""
import json
import os
import time

from daedalus_bridge import command_queue
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import path_safety
from daedalus_bridge import stream_service


def _unlink(path):
    try:
        path.unlink()
    except OSError:
        pass  # the peer or the sweep took it


def drain_dashboard(qdir, token, killed_event, *, command_ttl,
                    frame_writer, secret=''):
    """Fan one dashboard event stream out to this connection.

    Unlike `drain_queue`, an entry is unlinked only once every live dashboard
    subscription for the token holds a cursor at or past it, or the TTL takes
    it — a slow window costs retained files, never another window an event.

    The drain delivers a *prefix* of the queue and never advances its cursor
    past an entry it did not consume. The first entry it cannot resolve
    (claim held, refused, or unparseable) stops this tick's scan; advancing
    past it would strand it below the cursor, where the next pass treats it
    as already-consumed and the join unlinks it — a real event lost, a refused
    object removed outside the registry's retire path, an unparseable file
    dropped before its TTL.

    The liveness cost is deliberate and bounded, and the bound depends on the
    head's shape. A *contended* claim clears as soon as the holder releases
    it. An *unparseable* head is cleared by the drain's own age check within
    the command TTL. A *refused* head (e.g. a symlink) is never opened, so the
    drain's age check never sees it: only `command_queue.remove_expired`, run
    by the background `gc_loop`, can vacate it, and that is the command TTL
    plus up to one sweep interval — about 120 s at the 90 s / 30 s defaults.
    Returns the number of events handed to `frame_writer`.
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
            # Consumed on an earlier pass: reconsider removal, never redeliver.
            if stream_service.every_subscription_past(token, name):
                _unlink(path)
            continue
        key = command_queue.queue_key(qdir.name, name)
        with command_queue.claimed(key) as owned:
            if not owned:
                # Held by another consumer: not ours to skip, and advancing
                # past it would strand and then unlink it.
                break
            opened, reason, ident = command_queue.open_command_candidate(path)
            if opened is None:
                if reason is None:
                    continue  # absent: the name named nothing, resolved
                stream_service.refusal_once(
                    (key, ident),
                    f'q={qdir.name}/{name}', reason, secret=secret)
                # Refused: an undeliverable object is there. Leave it in
                # place (never unlinked) and stop before stranding it.
                break
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
                        # Unparseable: a non-atomic writer may still hold it.
                        # Leave it in place (the age check clears it) and
                        # stop before stranding it below the cursor.
                        break
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
            # An expired entry leaves now; a real event waits until every
            # live peer has reached it.
            if (data is None
                    or stream_service.every_subscription_past(token, name)):
                _unlink(path)
                if expired:
                    print(
                        f'[STREAM] TTL-DROP '
                        f'{path_safety.redacted(log_safe(qdir.name), secret)}'
                        f'/{log_safe(name)}', flush=True)
    return count
