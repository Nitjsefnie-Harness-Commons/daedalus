"""Commands that evaluate something in a page and report what came back."""
import json
import os
import sys
import time

from .invoke import send_and_wait
from .output import _format_eval_world, print_result
from .transport import _query_path, api, tab, token, wait_for_result


def do_tabs(args):
    tabs = api('GET', '/tabs')
    if getattr(args, 'json', False):
        print(json.dumps(tabs or [], indent=2))
        return
    if not tabs:
        print('No active tabs')
        return
    for t in sorted(tabs, key=lambda x: x.get('age', 0)):
        tid = t['tabId']
        age = t.get('age', '?')
        url = t.get('url', '')
        title = t.get('title', '')[:50]
        print(f'  {tid}  {age:>4}s  {title:<50}  {url}')


def do_put(args):
    if args.file == '-':
        code = sys.stdin.read()
    else:
        if not os.path.isfile(args.file):
            sys.exit(f'File not found: {args.file}')
        with open(args.file, encoding='utf-8') as f:
            code = f.read()
    target_tab = '' if args.broadcast else tab()
    send_and_wait(args.id, code.strip(), target_tab,
                  wait=not args.no_result, timeout=args.timeout)


def do_exec(args):
    target_tab = '' if args.broadcast else tab()
    send_and_wait(args.id, args.code.strip(), target_tab,
                  wait=not args.no_result, timeout=args.timeout)


def do_result(args):
    params = {}
    t = tab()
    if t:
        params['tab'] = t
    if args.consume:
        params['consume'] = '1'
    res = api('GET', _query_path('/result', params))
    if res.get('pending'):
        print('No result pending')
        return
    print_result(res, raw=args.raw)


def do_ping(args):
    target_tab = tab()
    t0 = time.time()
    payload = {'token': token(), 'id': '_ping', 'code': 'document.title'}
    if target_tab:
        payload['tab'] = target_tab
    sent = api('PUT', '/command', payload)

    res = wait_for_result(
        '_ping', target_tab, sent.get('did'), 10, interval=0.3)
    if res is None:
        sys.exit('Ping timeout (10s)')
    err = res.get('error')
    if err:
        sys.exit(f'Ping error: {err}')
    ms = (time.time() - t0) * 1000
    title = res.get('result', '')
    world = res.get('world', '')
    loc = f'@{_format_eval_world(world)}' if world else ''
    print(f'Pong {loc}: "{title}"  ({ms:.0f}ms)')


def do_navigate(args):
    target_tab = tab()
    code = f'location.href = {json.dumps(args.url)}'
    send_and_wait('_nav', code, target_tab, wait=False, timeout=0)


def do_reload(args):
    target_tab = '' if args.broadcast else tab()
    send_and_wait('_reload', 'location.reload()', target_tab,
                  wait=False, timeout=0)


def do_title(args):
    target_tab = tab()
    send_and_wait('_title', 'document.title', target_tab,
                  wait=True, timeout=10)


def do_url(args):
    target_tab = tab()
    send_and_wait('_url', 'location.href', target_tab,
                  wait=True, timeout=10)
