#!/usr/bin/env python3
"""The two `/result` routes as functions returning `(status, payload)`.

`daedalus_bridge/result_routes.py` owns the POST body that stores a result
and the GET body that peeks at or consumes one. Both take the result
directory as a parameter and call `daedalus_bridge.result_store` for the
locks and the slot primitives.

`result_store` imports `daedalus_bridge.config` for `DELIVERY_DIR` and
`MAX_DELIVERY_RESULTS`, so `DAEDALUS_DIR` and `DAEDALUS_PORT` are set here
before the first load and the result directory these tests pass in is the
one that configuration names. Each test uses its own token, because the
delivery directory and the accepted-delivery record are process-wide.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

_BASE = tempfile.mkdtemp(prefix='resultroutes_base_')
atexit.register(shutil.rmtree, _BASE, ignore_errors=True)
os.environ['DAEDALUS_DIR'] = _BASE
os.environ['DAEDALUS_PORT'] = '0'
RES_DIR = Path(_BASE) / 'results'


def _load(name):
    RES_DIR.mkdir(parents=True, exist_ok=True)
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_routes.py', name)


def _slot(token, tab=''):
    return RES_DIR / (f'{token}_{tab}.json' if tab else f'{token}.json')


def _delivery(token, tab, did):
    key = f'{token}_{tab}' if tab else token
    return RES_DIR / 'deliveries' / key / f'{did}.json'


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _events(cmd_dir, token):
    """Every dashboard event the routes published under `cmd_dir`."""
    queue = Path(cmd_dir) / f'{token}_dashboard'
    if not queue.is_dir():
        return []
    return [json.loads(path.read_text(encoding='utf-8'))
            for path in sorted(queue.iterdir())]


def test_accept_writes_both_slots_and_the_delivery_file(tmp):
    routes = _load('fixture_result_routes_accept')
    token, did = 'accepttok', '1700000000000_1'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '7', 'id': 'cmd-1', 'value': 'hi', '_did': did})
    assert (status, payload) == (200, {'ok': True})
    token_slot = _read(_slot(token))
    tab_slot = _read(_slot(token, '7'))
    delivery = _read(_delivery(token, '7', did))
    assert token_slot['value'] == 'hi'
    assert tab_slot == token_slot and delivery == token_slot
    assert token_slot['deliveryId'] == did
    assert token_slot['resultGeneration']
    assert isinstance(token_slot['roundtrip_ms'], int)


def test_accept_answers_duplicate_for_a_repeated_delivery_id(tmp):
    routes = _load('fixture_result_routes_duplicate')
    token, did = 'duptok', 'dup-1'
    routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '3', 'id': 'first', 'value': 'first', '_did': did})
    first = _read(_slot(token, '3'))
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '3', 'id': 'second', 'value': 'second', '_did': did})
    assert (status, payload) == (200, {'ok': True, 'duplicate': True})
    assert _read(_slot(token, '3')) == first
    assert _read(_slot(token)) == first
    assert _read(_delivery(token, '3', did)) == first


def test_accept_publishes_one_dashboard_event_per_stored_result(tmp):
    routes = _load('fixture_result_routes_events')
    token, did = 'eventtok', 'event-1'
    body = {'tabId': '9', 'id': 'cmd-9', 'world': 'cdp', '_did': did}
    routes.accept_result(RES_DIR, tmp, token, dict(body))
    routes.accept_result(RES_DIR, tmp, token, dict(body))
    events = _events(tmp, token)
    assert len(events) == 1, events
    assert events[0]['type'] == 'result'
    assert events[0]['tabId'] == '9'
    assert events[0]['resultId'] == 'cmd-9'
    assert events[0]['world'] == 'cdp'
    assert events[0]['ok'] is True


def test_accept_treats_a_non_string_delivery_id_as_absent(tmp):
    routes = _load('fixture_result_routes_baddid')
    token = 'baddidtok'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '2', 'id': 'x', '_did': 12})
    assert (status, payload) == (200, {'ok': True})
    stored = _read(_slot(token, '2'))
    assert 'deliveryId' not in stored
    assert not (RES_DIR / 'deliveries' / f'{token}_2').exists()


def test_accept_refuses_a_result_it_cannot_serialize(tmp):
    routes = _load('fixture_result_routes_unencodable')
    token = 'badbodytok'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '1', 'value': {1, 2}})
    assert status == 400
    assert payload == {'error': 'result is not encodable'}
    assert not _slot(token).exists()
    assert not _slot(token, '1').exists()


def test_accept_refuses_an_unsafe_tab_id(tmp):
    routes = _load('fixture_result_routes_unsafe_post')
    status, payload = routes.accept_result(
        RES_DIR, tmp, 'unsafeposttok', {'tabId': '..', 'id': 'x'})
    assert status == 400
    assert payload == {'error': 'invalid path component'}


def test_fetch_peeks_without_deleting_the_slot(tmp):
    routes = _load('fixture_result_routes_peek')
    token = 'peektok'
    routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '4', 'value': 'kept'})
    status, payload = routes.fetch_result(RES_DIR, token, {'tab': ['4']})
    assert status == 200
    assert payload['value'] == 'kept'
    assert _slot(token, '4').exists()


def test_fetch_consumes_only_when_the_expected_generation_matches(tmp):
    routes = _load('fixture_result_routes_expected')
    token = 'expectedtok'
    routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '5', 'value': 'once'})
    generation = _read(_slot(token, '5'))['resultGeneration']
    status, payload = routes.fetch_result(
        RES_DIR, token,
        {'tab': ['5'], 'consume': ['1'], 'expected': ['not-the-one']})
    assert (status, payload) == (200, {'consumed': False})
    assert _slot(token, '5').exists()
    status, payload = routes.fetch_result(
        RES_DIR, token,
        {'tab': ['5'], 'consume': ['1'], 'expected': [generation]})
    assert status == 200
    assert payload == {'consumed': True, 'resultGeneration': generation}
    assert not _slot(token, '5').exists()


def test_fetch_consuming_a_delivery_removes_the_slot_copy(tmp):
    routes = _load('fixture_result_routes_delivery')
    token, did = 'deliverytok', 'delivery-1'
    routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '6', 'value': 'paired', '_did': did})
    generation = _read(_slot(token, '6'))['resultGeneration']
    status, payload = routes.fetch_result(
        RES_DIR, token,
        {'delivery': [did], 'consume': ['1'], 'expected': [generation]})
    assert status == 200
    assert payload == {'consumed': True, 'resultGeneration': generation}
    assert not _delivery(token, '6', did).exists()
    assert not _slot(token, '6').exists()
    assert not _slot(token).exists()


def test_fetch_answers_pending_for_an_absent_delivery(_tmp):
    routes = _load('fixture_result_routes_pending')
    status, payload = routes.fetch_result(
        RES_DIR, 'pendingtok', {'delivery': ['never-stored']})
    assert (status, payload) == (200, {'pending': True})


def test_fetch_refuses_an_unsafe_tab(_tmp):
    routes = _load('fixture_result_routes_unsafe_get')
    status, payload = routes.fetch_result(
        RES_DIR, 'unsafegettok', {'tab': ['..']})
    assert status == 400
    assert payload == {'error': 'invalid path component'}


def test_the_module_needs_no_configuration_of_its_own(_tmp):
    """Its only configuration-bound import is `result_store`.

    `result_store` reads `DELIVERY_DIR` from `daedalus_bridge.config`, so
    with no `DAEDALUS_*` variable set importing it fails — that is the
    control proving the environment really is stripped. With `result_store`
    standing in, the route module still imports, which is what says it
    binds no configuration of its own.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    refused = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.result_store'],
        env=env, capture_output=True, text=True, check=False)
    assert refused.returncode != 0, refused.stderr
    assert 'DAEDALUS_DIR' in refused.stderr, refused.stderr
    stubbed = subprocess.run(
        [sys.executable, '-c', _STUBBED_IMPORT],
        env=env, capture_output=True, text=True, check=False)
    assert stubbed.returncode == 0, stubbed.stderr


_STUBBED_IMPORT = """
import sys, types
import daedalus_bridge
stub = types.ModuleType('daedalus_bridge.result_store')
sys.modules['daedalus_bridge.result_store'] = stub
daedalus_bridge.result_store = stub
import daedalus_bridge.result_routes
"""


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='resultroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
