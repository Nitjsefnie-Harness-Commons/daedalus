"""Commands that change what a page loads: CSS, blocking, capture, hotfixes."""
import argparse
import json
import os
import sys
import time

from .invoke import ext_cmd
from .transport import api, token, wait_for_result


def do_inject_css(args):
    """Inject CSS into a tab via extension."""
    if args.file:
        with open(args.file, encoding='utf-8') as f:
            css = f.read()
    else:
        css = args.css
    fields: dict = {'css': css}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    if args.all_frames:
        fields['allFrames'] = True
    result = ext_cmd('_inject_css', 'inject-css', **fields)
    print(f'Injected {result.get("injected", "?")} chars CSS into tab {result.get("tabId", "?")}')


def do_remove_css(args):
    """Remove injected CSS from a tab via extension."""
    if args.file:
        with open(args.file, encoding='utf-8') as f:
            css = f.read()
    else:
        css = args.css
    fields: dict = {'css': css}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    if args.all_frames:
        fields['allFrames'] = True
    result = ext_cmd('_remove_css', 'remove-css', **fields)
    print(f'Removed {result.get("removed", "?")} chars CSS from tab {result.get("tabId", "?")}')


def do_block_requests(args):
    """Block page requests matching a URL pattern via extension."""
    cmd = {'id': '_block', 'type': 'block-requests', 'token': token(), 'tab': 'extension',
           'pattern': args.pattern}
    if args.chrome_tab:
        cmd['tabId'] = int(args.chrome_tab)
    sent = api('PUT', '/command', cmd)
    res = wait_for_result('_block', 'extension', sent.get('did'), 10)
    if res is None:
        sys.exit('Timeout (10s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    result = res.get('result', {})
    print(f'Blocked: {result.get("pattern", "")}  ruleId={result.get("ruleId", "")}  tabs={result.get("tabIds", [])}')


def _positive_rule_id(value):
    """A block rule's id, which is always a positive integer.

    Zero reached the extension as a present-but-invalid id, where it read as
    absent and widened into removing every rule.
    """
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f'{value!r} is not an integer') from None
    if number <= 0:
        raise argparse.ArgumentTypeError(
            f'rule ids are positive; got {number}')
    return number


def do_unblock_requests(args):
    """Remove block rules via extension."""
    cmd: dict = {'id': '_unblock', 'type': 'unblock-requests', 'token': token(), 'tab': 'extension'}
    if args.rule_id is not None:
        cmd['ruleId'] = args.rule_id
    sent = api('PUT', '/command', cmd)
    res = wait_for_result('_unblock', 'extension', sent.get('did'), 10)
    if res is None:
        sys.exit('Timeout (10s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    removed = res.get('result', {}).get('removed', [])
    print(f'Removed {len(removed)} rule(s): {removed}')


def do_list_block_rules(args):
    """List active block rules via extension."""
    cmd = {'id': '_list_rules', 'type': 'list-block-rules', 'token': token(), 'tab': 'extension'}
    sent = api('PUT', '/command', cmd)
    res = wait_for_result(
        '_list_rules', 'extension', sent.get('did'), 10)
    if res is None:
        sys.exit('Timeout (10s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    rules = res.get('result', [])
    if not rules:
        print('No active block rules')
        return
    for r in rules:
        cond = r.get('condition', {})
        print(f'  id={r["id"]}  pattern={cond.get("urlFilter", "")}  tabs={cond.get("tabIds", "all")}')
    print(f'{len(rules)} rule(s)')


def do_net_capture(args):
    """Start network capture on a Chrome tab via CDP."""
    fields = {}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    if args.max:
        fields['maxRequests'] = args.max
    result = ext_cmd('_net_cap', 'net-capture', timeout=15, **fields)
    if result.get('already'):
        print(f'Already capturing on tab {result.get("tabId")} ({result.get("buffered", 0)} requests buffered)')
    else:
        print(f'Capturing network on tab {result.get("tabId")}')


def do_net_capture_stop(args):
    """Stop network capture and dump results."""
    fields = {}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    if args.bodies:
        fields['bodies'] = True
    result = ext_cmd('_net_stop', 'net-capture-stop', timeout=30, **fields)
    if not result.get('stopped'):
        print(f'Not capturing: {result.get("reason", "?")}')
        return
    requests = result.get('requests', [])
    if args.raw:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f'Captured {len(requests)} requests from tab {result.get("tabId")}')
    for r in requests:
        status = r.get('status', '???')
        method = r.get('method', '?')
        url = r.get('url', '')[:120]
        rtype = r.get('type', '')
        print(f'  {status} {method} {rtype:<12} {url}')


def do_net_capture_get(args):
    """Get current network capture buffer."""
    fields = {}
    if args.chrome_tab:
        fields['tabId'] = int(args.chrome_tab)
    if args.filter:
        fields['filter'] = args.filter
    if args.bodies:
        fields['bodies'] = True
    result = ext_cmd('_net_get', 'net-capture-get', timeout=30, **fields)
    requests = result.get('requests', [])
    if args.raw:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f'{len(requests)} requests on tab {result.get("tabId")}')
    for r in requests:
        status = r.get('status', '???')
        method = r.get('method', '?')
        url = r.get('url', '')[:120]
        rtype = r.get('type', '')
        print(f'  {status} {method} {rtype:<12} {url}')


def do_store_hotfix(args):
    """Store a hotfix in the extension."""
    if args.file is not None:
        if not os.path.isfile(args.file):
            sys.exit(f'File not found: {args.file}')
        with open(args.file, encoding='utf-8') as f:
            code = f.read()
    else:
        code = args.code
    result = ext_cmd('_store_hf', 'store-hotfix', fixId=args.fix_id, code=code, permanent=args.permanent)
    perm = ' [PERM]' if result.get('permanent') else ''
    print(f'Stored hotfix "{result.get("stored", "?")}"{perm} ({result.get("total", "?")} total)')


def do_clear_hotfix(args):
    """Clear a specific hotfix from the extension."""
    result = ext_cmd('_clear_hf', 'clear-hotfix', fixId=args.fix_id)
    found = result.get('found', False)
    print(f'Cleared hotfix "{result.get("cleared", "?")}" (found={found}, remaining={result.get("remaining", 0)})')


def do_clear_hotfixes(args):
    """Clear all hotfixes from the extension."""
    result = ext_cmd('_clear_all_hf', 'clear-all-hotfixes', includePermanent=args.include_permanent)
    if args.include_permanent:
        print('All hotfixes cleared (incl. permanent)')
    else:
        kept = result.get('kept', 0)
        removed = result.get('removed', 0)
        print(f'Cleared {removed} hotfix(es); {kept} permanent kept')


def do_list_hotfixes(args):
    """List all stored hotfixes."""
    result = ext_cmd('_list_hf', 'list-hotfixes')
    fixes = result.get('fixes', [])
    if not fixes:
        print('No hotfixes stored')
        return
    print(f'Version: {result.get("version", "?")}')
    for hf in fixes:
        ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(hf.get('ts', 0) / 1000))
        code_preview = hf['code'][:80].replace('\n', '\\n')
        marker = '[PERM]' if hf.get('permanent') else '      '
        print(f'  {marker} {hf["id"]}  {ts}  {code_preview}')
    print(f'{len(fixes)} hotfix(es)')


_TRUE_SPELLINGS = ('true', '1', 'yes', 'y', 'on')


_FALSE_SPELLINGS = ('false', '0', 'no', 'n', 'off')


def _boolean_argument(value):
    """One of the documented spellings, or an argument error.

    Anything outside the true list used to read as false, so a misspelling
    sent the opposite of what was asked and the command reported success --
    `set-permanent <id> ture` quietly made a permanent hotfix version-gated.
    Refusing here means argparse rejects it before any mutation is sent.
    """
    lowered = value.lower()
    if lowered in _TRUE_SPELLINGS:
        return True
    if lowered in _FALSE_SPELLINGS:
        return False
    accepted = ', '.join(_TRUE_SPELLINGS + _FALSE_SPELLINGS)
    raise argparse.ArgumentTypeError(
        f'{value!r} is not one of {accepted}')


def do_set_permanent(args):
    """Toggle the permanent flag on an existing hotfix."""
    val = args.permanent
    result = ext_cmd('_set_perm', 'set-permanent', fixId=args.fix_id, permanent=val)
    found = result.get('found', False)
    if not found:
        sys.exit(f'No hotfix with id "{args.fix_id}"')
    print(f'Set permanent={val} on "{args.fix_id}"')
