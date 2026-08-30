"""Sending a command and reading its delivered result from a real bridge.

The real-browser fixture's delivery helpers, separate from the machinery
that starts the browser and reaches its service worker.
"""
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _matching_delivery(sent_did, result_status, result):
    """Return the polled result when it is this command's own delivery."""
    if result_status != 200 or not isinstance(result, dict):
        return None
    delivery_id = result.get('deliveryId')
    if not sent_did or not delivery_id or delivery_id != sent_did:
        return None
    return result


def real_ext_command(bridge_url, token, cmd_id, payload):
    """Send a typed extension command and return its delivered result."""
    body = {'token': token, 'tab': 'extension', 'id': cmd_id, **payload}
    status, raw = _util.request(bridge_url + '/command', 'PUT', body=body)
    if status != 200:
        raise AssertionError(
            f'extension command {cmd_id!r} was rejected by the bridge: '
            f'status={status}, response={raw!r}')
    sent = json.loads(raw)
    sent_did = sent.get('did')
    deadline = time.time() + 20
    query = urllib.parse.urlencode({'token': token, 'tab': 'extension'})
    while time.time() < deadline:
        result_status, result = _util.get_json(bridge_url + '/result?' + query)
        matched = _matching_delivery(sent_did, result_status, result)
        if matched is not None:
            return matched
        time.sleep(0.05)
    raise AssertionError(f'{cmd_id!r} did not return its delivery result')


def real_eval(bridge_url, token, tab_id, cmd_id, code):
    status, raw = _util.request(
        bridge_url + '/command', 'PUT', body={
            'token': token,
            'tab': tab_id,
            'id': cmd_id,
            'code': code,
        })
    if status != 200:
        raise AssertionError(
            f'eval command {cmd_id!r} was rejected by the bridge: '
            f'status={status}, response={raw!r}')
    sent = json.loads(raw)
    sent_did = sent.get('did')
    deadline = time.time() + 20
    query = urllib.parse.urlencode({'token': token, 'tab': tab_id})
    while time.time() < deadline:
        result_status, body = _util.get_json(
            bridge_url + '/result?' + query)
        matched = _matching_delivery(sent_did, result_status, body)
        if matched is not None:
            generation = matched.get('resultGeneration')
            if generation:
                consume = urllib.parse.urlencode({
                    'token': token,
                    'tab': tab_id,
                    'consume': '1',
                    'expected': generation,
                })
                consumed_status, consumed = _util.get_json(
                    bridge_url + '/result?' + consume)
                if consumed_status != 200:
                    raise AssertionError(
                        f'eval {cmd_id!r} conditional consume failed: '
                        f'status={consumed_status}, response={consumed!r}')
            return matched
        time.sleep(0.05)
    raise AssertionError(f'eval {cmd_id!r} did not return its delivery result')
