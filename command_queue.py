"""Shared lifecycle helpers for files waiting in the command queue."""
import json


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
