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
    most the command TTL and never costs another window an event.

    The drain delivers a *prefix* of the queue and never advances its cursor
    past an entry this connection did not consume. The first entry it cannot
    resolve — another consumer holds its claim, the object is refused, or it
    will not parse — stops the scan for this tick, so the cursor stays at the
    last entry actually delivered and the unresolved entry is retried on the
    next tick. Advancing past it instead would strand it below the cursor,
    where the next pass would treat it as already-consumed and the removal
    join would unlink it: a real event lost, a refused object removed against
    the contract (and outside the refusal registry's retire path), and an
    unparseable file dropped before its TTL.

    The liveness cost is deliberate and bounded: an entry at the head that
    stays unresolvable blocks later events to this connection until
    `command_queue.remove_expired` vacates it on age — at most the command
    TTL — which also retires its refusal record via `on_name_vacated`. A
    contended claim is the common case and clears as soon as the holder
    releases it. Returns the number of events handed to `frame_writer`.
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
                # Another consumer holds this entry right now. Stop here:
                # the entry is not ours to skip over, and advancing the
                # cursor past it would strand and then unlink it.
                break
            opened, reason, ident = command_queue.open_command_candidate(path)
            if opened is None:
                if reason is None:
                    continue  # absent: the name named nothing, resolved
                stream_service.refusal_once(
                    (key, ident),
                    f'q={qdir.name}/{name}', reason, secret=secret)
                # Refused: an object is there we cannot deliver. Leave it
                # in place (never delivered or unlinked) and stop, so it is
                # not stranded below the cursor.
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
                        # Unparseable: an older non-atomic writer may still
                        # hold it. Leave it in place — the TTL sweep bounds
                        # retries — and stop, so it is not stranded below
                        # the cursor and removed before its TTL.
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
