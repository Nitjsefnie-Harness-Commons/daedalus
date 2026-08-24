"""Commands that drive the browser itself — tabs, cookies, CDP, timings."""
import json
import sys

from .invoke import ext_cmd
from .output import MARK, print_result
from .transport import api, token, wait_for_result


def do_cookies(args):
    """Get cookies for a domain via extension."""
    cmd = {'id': '_cookies', 'type': 'cookies', 'token': token(), 'tab': 'extension'}
    if args.domain:
        cmd['domain'] = args.domain
    if args.url:
        cmd['url'] = args.url
    sent = api('PUT', '/command', cmd)
    timeout = args.timeout or 10
    res = wait_for_result('_cookies', 'extension', sent.get('did'), timeout)
    if res is None:
        sys.exit(f'Timeout ({timeout}s)')
    if res.get('error'):
        sys.exit(f'Cookie error: {res["error"]}')
    cookies = res.get('result', [])
    if args.raw:
        print(json.dumps(cookies, indent=2, ensure_ascii=False))
    else:
        for c in cookies:
            print(f'  {c.get("domain", "")}  {c.get("name", "")}={c.get("value", "")}')
    print(f'{len(cookies)} cookies')


def do_set_cookie(args):
    """Set a cookie via extension."""
    cmd = {'id': '_set_cookie', 'type': 'set-cookie', 'token': token(), 'tab': 'extension',
           'url': args.url, 'name': args.name, 'value': args.value}
    if args.domain: cmd['domain'] = args.domain
    if args.path: cmd['path'] = args.path
    if args.http_only: cmd['httpOnly'] = True
    if args.secure: cmd['secure'] = True
    if args.same_site: cmd['sameSite'] = args.same_site
    if args.expires: cmd['expirationDate'] = float(args.expires)
    sent = api('PUT', '/command', cmd)
    res = wait_for_result(
        '_set_cookie', 'extension', sent.get('did'), 10)
    if res is None:
        sys.exit('Timeout (10s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    print(f'Set: {args.name}={args.value[:60]} on {args.url}')


def do_remove_cookie(args):
    """Remove a specific cookie via extension."""
    cmd = {'id': '_rm_cookie', 'type': 'remove-cookie', 'token': token(), 'tab': 'extension',
           'url': args.url, 'name': args.name}
    sent = api('PUT', '/command', cmd)
    res = wait_for_result(
        '_rm_cookie', 'extension', sent.get('did'), 10)
    if res is None:
        sys.exit('Timeout (10s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    print(f'Removed: {args.name} from {args.url}')


def do_clear_cookies(args):
    """Clear all cookies for a domain via extension."""
    cmd = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': token(), 'tab': 'extension'}
    if args.domain: cmd['domain'] = args.domain
    if args.url: cmd['url'] = args.url
    sent = api('PUT', '/command', cmd)
    timeout = args.timeout or 10
    res = wait_for_result(
        '_clear_cookies', 'extension', sent.get('did'), timeout)
    if res is None:
        sys.exit(f'Timeout ({timeout}s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    result = res.get('result', {})
    print(f'Cleared {result.get("removed", 0)} cookies')
    failed = result.get('failed') or []
    if failed:
        # Reported rather than folded into the count: a cookie the browser
        # refused to remove is still there.
        print(f'Could not remove {len(failed)}: {", ".join(failed)}')


def do_cdp(args):
    """Send raw CDP command via extension."""
    params = json.loads(args.params) if args.params else {}
    cmd = {'id': '_cdp', 'type': 'cdp', 'method': args.method, 'params': params,
           'token': token(), 'tab': 'extension'}
    if args.chrome_tab:
        cmd['tabId'] = args.chrome_tab
    if args.keep_session:
        cmd['keep_session'] = True
    sent = api('PUT', '/command', cmd)
    res = wait_for_result('_cdp', 'extension', sent.get('did'), 30)
    if res is None:
        sys.exit('Timeout (30s)')
    print_result(res, raw=args.raw)


def do_close_tab(args):
    """Close one or more Chrome tabs via extension."""
    ids = [int(x) for x in args.chrome_tabs]
    cmd: dict = {'id': '_close_tab', 'type': 'close-tab', 'token': token(), 'tab': 'extension'}
    if len(ids) == 1:
        cmd['tabId'] = ids[0]
    else:
        cmd['tabIds'] = ids
    sent = api('PUT', '/command', cmd)
    res = wait_for_result(
        '_close_tab', 'extension', sent.get('did'), 10)
    if res is None:
        sys.exit('Timeout (10s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    result = res.get('result', {})
    closed = result.get('closed', [])
    errs = result.get('errors', [])
    if closed:
        print(f'Closed {len(closed)} tab(s): {closed}')
    for e in errs:
        print(f'Failed tab {e.get("id", "?")}: {e.get("error", "")}')
    if not closed and not errs:
        print('No tabs affected')


def do_fetch_timings(args):
    """Fetch the background fetch-relay timing ring buffer."""
    fields = {}
    if args.reset:
        fields['reset'] = True
    result = ext_cmd('_fetch_timings', 'fetch-timings', **fields)
    timings = result.get('timings', [])
    count = result.get('count', 0)
    has_native = result.get('hasNativeToBase64', False)
    print(f'Fetch timings: {count} entries  nativeToBase64={has_native}')
    if args.raw:
        print(json.dumps(timings, indent=2, ensure_ascii=False))
        return
    if not timings:
        print('(empty)')
        return
    # Print tail of N entries with columns
    tail = timings[-args.n:]
    print(f'{"method":6} {"status":6} {"size":>10} {"decode":>8} {"fetch":>8} {"encode":>8} {"total":>8}  url')
    for t in tail:
        if 'error' in t:
            print(f'{t.get("method", ""):6} {"ERR":6} {"":>10} {"":>8} {"":>8} {"":>8} {t.get("ms_total", ""):>8}  {t.get("url", "")[:80]} ({t["error"]})')
        else:
            size_mb = t.get('bodySize', 0) / 1024 / 1024
            print(f'{t.get("method", ""):6} {t.get("status", ""):6} {size_mb:>9.2f}M {t.get("ms_bodyDecode", ""):>8} {t.get("ms_fetch", ""):>8} {t.get("ms_encode", ""):>8} {t.get("ms_total", ""):>8}  {t.get("url", "")[:80]}')
    # Summary stats
    completed = [t for t in timings if 'error' not in t]
    if completed:
        import statistics
        totals = [t['ms_total'] for t in completed]
        fetches = [t['ms_fetch'] for t in completed]
        encodes = [t['ms_encode'] for t in completed]
        print(f'\n{len(completed)} successful: total median={statistics.median(totals):.0f}ms mean={statistics.mean(totals):.0f}ms '
              f'fetch median={statistics.median(fetches):.0f}ms encode median={statistics.median(encodes):.0f}ms')


def do_ext_self_reload(args):
    """Reload the extension from disk via chrome.runtime.reload()."""
    result = ext_cmd('_ext_reload', 'ext-reload')
    print(f'Extension reloading from v{result.get("version", "?")} — will reconnect SSE automatically')


def do_open_tab(args):
    """Open a new tab via extension."""
    fields = {'url': args.url}
    if args.background:
        fields['active'] = False
    if args.pinned:
        fields['pinned'] = True
    result = ext_cmd('_open_tab', 'open-tab', **fields)
    print(f'Opened tab {result.get("tabId", "?")} {MARK["out"]} {args.url}')


def do_open_tabs(args):
    """Open multiple tabs in one extension round-trip."""
    fields: dict = {'urls': list(args.urls)}
    if args.background:
        fields['active'] = False
    if args.pinned:
        fields['pinned'] = True
    result = ext_cmd('_open_tabs', 'open-tabs', timeout=30, **fields)
    opened = result.get('opened', [])
    errors = result.get('errors', [])
    for o in opened:
        print(f'Opened tab {o.get("tabId", "?")} {MARK["out"]} {o.get("url", "")}')
    for e in errors:
        print(f'FAILED {e.get("url", "?")}: {e.get("error", "")}')
    print(f'{len(opened)} opened, {len(errors)} failed')


def do_focus_tab(args):
    """Focus a Chrome tab via extension."""
    result = ext_cmd('_focus', 'focus-tab', tabId=int(args.chrome_tab))
    print(f'Focused tab {result.get("tabId", "?")} window={result.get("windowId", "?")}')


def do_ext_navigate(args):
    """Navigate a tab to URL via extension (works on any page including chrome://)."""
    fields = {'url': args.url}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    result = ext_cmd('_nav', 'navigate', **fields)
    print(f'Navigated tab {result.get("tabId", "?")} {MARK["out"]} {args.url}')


def do_ext_reload(args):
    """Reload a tab via extension."""
    fields = {}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    if args.bypass_cache:
        fields['bypassCache'] = True
    result = ext_cmd('_reload', 'reload', **fields)
    print(f'Reloaded tab {result.get("tabId", "?")}' + (' (bypass cache)' if args.bypass_cache else ''))
