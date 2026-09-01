"""Tab registry state and the four routes that operate it.

Authoritative source: /sync-tabs (replaces all). /register only updates
existing. Each route returns `(status, payload)` and takes the command
directory as a parameter rather than importing `config`.
"""
import threading
import time

from daedalus_bridge import command_queue


_registry = {}  # {token: {tabId: {url, title, ts}}}
_lock = threading.Lock()


def normalized_tab_id(value):
    """Normalize string or integer tab-id values; None otherwise."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def list_tabs(token, now=None):
    """GET /tabs — every registered tab with its registration age."""
    now = time.time() if now is None else now
    with _lock:
        tabs = _registry.get(token, {})
        result = [
            {'tabId': tid, 'url': info.get('url', ''),
             'title': info.get('title', ''),
             'age': round(now - info.get('ts', 0))}
            for tid, info in tabs.items()
        ]
    return 200, result


def refresh(cmd_dir, token, body):
    """POST /register — refresh one tab the registry already holds."""
    raw_tab_id = body.get('tabId', '')
    tab_id = normalized_tab_id(raw_tab_id)
    url = body.get('url', '')
    title = body.get('title', '')
    if tab_id == '':
        return 400, {'error': 'missing tabId'}
    if tab_id is None:
        return 400, {'error': 'invalid tabId'}
    updated = False
    with _lock:
        tabs = _registry.get(token, {})
        if tab_id in tabs:
            # Update-only: refresh existing tab, never create new entries
            tabs[tab_id] = {'url': url, 'title': title, 'ts': time.time()}
            updated = True
    if updated:
        command_queue.notify_dashboard(
            cmd_dir, token,
            {'type': 'tab-updated', 'tabId': tab_id,
             'url': url, 'title': title})
    # This route is update-only, so a tab the registry has never seen
    # is a no-op — and answering it {'ok': True} told the caller its
    # state had been refreshed when nothing had. `updated` is what
    # separates the two, so a client whose tab has fallen out of the
    # registry can notice and re-sync instead of reporting stale
    # entries forever.
    return 200, {'ok': True, 'updated': updated}


def replace(cmd_dir, token, body):
    """POST /sync-tabs — replace this token's registry wholesale."""
    tabs_list = body.get('tabs', [])
    if (not isinstance(tabs_list, list)
            or any(not isinstance(tab, dict) for tab in tabs_list)):
        return 400, {'error': 'invalid tabs'}
    normalized_tabs = []
    for tab_info in tabs_list:
        tab_id = normalized_tab_id(tab_info.get('tabId', ''))
        if tab_id is None:
            return 400, {'error': 'invalid tabs'}
        if tab_id:
            normalized_tabs.append((tab_id, tab_info))
    with _lock:
        _registry[token] = {}
        for tab_id, tab_info in normalized_tabs:
            _registry[token][tab_id] = {
                'url': tab_info.get('url', ''),
                'title': tab_info.get('title', ''),
                'ts': time.time(),
            }
    count = len(_registry.get(token, {}))
    command_queue.notify_dashboard(
        cmd_dir, token, {'type': 'tabs-synced', 'count': count})
    return 200, {'ok': True, 'count': count}


def remove(cmd_dir, token, body):
    """POST /unregister — drop one tab from this token's registry."""
    tab_id = body.get('tabId', '')
    if not tab_id:
        return 400, {'error': 'missing tabId'}
    with _lock:
        tabs = _registry.get(token, {})
        removed = tabs.pop(str(tab_id), None)
    command_queue.notify_dashboard(
        cmd_dir, token,
        {'type': 'tab-unregistered', 'tabId': str(tab_id)})
    return 200, {'ok': True, 'removed': removed is not None}


def counts():
    """GET /health — the registered token and tab totals."""
    with _lock:
        return len(_registry), sum(len(v) for v in _registry.values())
