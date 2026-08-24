"""Sending one command and waiting for what it produced.

Both halves of the CLI's work — evaluating source in a page and driving a
typed extension command — are the same shape: enqueue, wait for the result
that matches this invocation, then report it. The two spellings differ in
what they do with a result, so they are two functions rather than one with a
flag.
"""
import sys

from .output import MARK, print_result
from .transport import api, token, wait_for_result


def send_and_wait(cmd_id, code, target_tab, wait, timeout):
    if not cmd_id:
        sys.exit('Command ID is required')
    if not code:
        sys.exit('Code is empty')
    if not isinstance(cmd_id, str):
        sys.exit(f'Command ID must be a string, got {type(cmd_id).__name__}')

    payload = {'token': token(), 'id': cmd_id, 'code': code}
    if target_tab:
        payload['tab'] = target_tab

    resp = api('PUT', '/command', payload)
    target = resp.get('target', '?')
    print(f'{MARK["out"]} {cmd_id} {MARK["out"]} {target}  ({len(code)} bytes)')

    if not wait:
        return

    res = wait_for_result(cmd_id, target_tab, resp.get('did'), timeout)
    if res is None:
        sys.exit(f'Timeout ({timeout}s) — no result received')
    print_result(res)


def ext_cmd(cmd_id, cmd_type, timeout=10, **fields):
    """Send an extension command and wait for result. Returns result dict or exits on error."""
    cmd = {'id': cmd_id, 'type': cmd_type, 'token': token(), 'tab': 'extension', **fields}
    sent = api('PUT', '/command', cmd)
    res = wait_for_result(cmd_id, 'extension', sent.get('did'), timeout)
    if res is None:
        sys.exit(f'Timeout ({timeout}s)')
    if res.get('error'):
        sys.exit(f'Error: {res["error"]}')
    return res.get('result', {})
