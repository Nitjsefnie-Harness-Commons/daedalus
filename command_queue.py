"""Shared lifecycle helpers for files waiting in the command queue."""
import contextlib
import json
import threading


_lock = threading.Lock()
_claimed = set()


def claim(key):
    """Claim one logical queue key without holding a lock during delivery.

    The key is a logical name, so callers must never hand it an aliased entry;
    drain enumeration refuses aliases rather than normalising their spellings.
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
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return
        path.unlink()
    except (OSError, json.JSONDecodeError, RecursionError, ValueError):
        # A file that cannot be read or removed is reconsidered on the next
        # pass; nothing downstream depends on this call having acted.
        pass
