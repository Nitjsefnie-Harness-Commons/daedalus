"""SSE stream registry and delivery clock operations."""
import itertools
import threading
import time


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
