#!/usr/bin/env python3
"""The two `/result` routes as functions returning `(status, payload)`.

`daedalus_bridge/result_routes.py` owns the POST body that stores a result
and the GET body that peeks at or consumes one. Both take the result
directory as a parameter and call `daedalus_bridge.result_store` for the
locks and the slot primitives.

`DAEDALUS_DIR` and `DAEDALUS_PORT` are set here so the result directory
most of these tests pass in is the one configuration names; the controls
for the root parameter deliberately pass a different one. Each test uses
its own token and its own delivery ids, because the accepted-delivery
record is process-wide.
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
# Above any delivery count these tests store, so eviction never fires in
# them; the cap has its own controls.
DELIVERY_CAP = 8


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


def _configured_delivery_dir():
    config = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'config.py',
        'fixture_result_routes_config')
    return Path(config.DELIVERY_DIR)


def test_accept_writes_both_slots_and_the_delivery_file(tmp):
    routes = _load('fixture_result_routes_accept')
    token, did = 'accepttok', '1700000000000_1'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '7', 'id': 'cmd-1', 'value': 'hi', '_did': did},
        DELIVERY_CAP)
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
        {'tabId': '3', 'id': 'first', 'value': 'first', '_did': did},
        DELIVERY_CAP)
    first = _read(_slot(token, '3'))
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '3', 'id': 'second', 'value': 'second', '_did': did},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True, 'duplicate': True})
    assert _read(_slot(token, '3')) == first
    assert _read(_slot(token)) == first
    assert _read(_delivery(token, '3', did)) == first


def test_accept_publishes_one_dashboard_event_per_stored_result(tmp):
    routes = _load('fixture_result_routes_events')
    token, did = 'eventtok', 'event-1'
    body = {'tabId': '9', 'id': 'cmd-9', 'world': 'cdp', '_did': did}
    routes.accept_result(RES_DIR, tmp, token, dict(body), DELIVERY_CAP)
    routes.accept_result(RES_DIR, tmp, token, dict(body), DELIVERY_CAP)
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
        RES_DIR, tmp, token, {'tabId': '2', 'id': 'x', '_did': 12},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True})
    stored = _read(_slot(token, '2'))
    assert 'deliveryId' not in stored
    assert not (RES_DIR / 'deliveries' / f'{token}_2').exists()


def test_accept_refuses_a_result_it_cannot_serialize(tmp):
    routes = _load('fixture_result_routes_unencodable')
    token = 'badbodytok'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '1', 'value': {1, 2}},
        DELIVERY_CAP)
    assert status == 400
    assert payload == {'error': 'result is not encodable'}
    assert not _slot(token).exists()
    assert not _slot(token, '1').exists()


def test_accept_refuses_an_unsafe_tab_id(tmp):
    routes = _load('fixture_result_routes_unsafe_post')
    status, payload = routes.accept_result(
        RES_DIR, tmp, 'unsafeposttok', {'tabId': '..', 'id': 'x'},
        DELIVERY_CAP)
    assert status == 400
    assert payload == {'error': 'invalid path component'}


def test_fetch_peeks_without_deleting_the_slot(tmp):
    routes = _load('fixture_result_routes_peek')
    token = 'peektok'
    routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '4', 'value': 'kept'},
        DELIVERY_CAP)
    status, payload = routes.fetch_result(RES_DIR, token, {'tab': ['4']})
    assert status == 200
    assert payload['value'] == 'kept'
    assert _slot(token, '4').exists()


def test_fetch_consumes_only_when_the_expected_generation_matches(tmp):
    routes = _load('fixture_result_routes_expected')
    token = 'expectedtok'
    routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '5', 'value': 'once'},
        DELIVERY_CAP)
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
        {'tabId': '6', 'value': 'paired', '_did': did},
        DELIVERY_CAP)
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
    """Neither the route module nor anything it imports reads `config`.

    `daedalus_bridge.config` still exits without `DAEDALUS_DIR`, which is
    what proves the environment is stripped. The route module then imports
    for real, so nothing on its graph — `result_store` included — binds
    configuration.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    refused = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.config'],
        env=env, capture_output=True, text=True, check=False)
    assert refused.returncode != 0, refused.stderr
    assert 'DAEDALUS_DIR' in refused.stderr, refused.stderr
    imported = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.result_routes'],
        env=env, capture_output=True, text=True, check=False)
    assert imported.returncode == 0, imported.stderr


def test_the_results_root_governs_where_a_delivery_lands(tmp):
    """A root that is not the configured one takes the delivery file too."""
    routes = _load('fixture_result_routes_root')
    root = Path(tmp) / 'other-results'
    root.mkdir(parents=True)
    token, did = 'roottok', 'root-1'
    status, payload = routes.accept_result(
        root, tmp, token,
        {'tabId': '8', 'id': 'cmd-8', 'value': 'redirected', '_did': did}, 4)
    assert (status, payload) == (200, {'ok': True})
    moved = root / 'deliveries' / f'{token}_8' / f'{did}.json'
    assert _read(moved)['value'] == 'redirected'
    assert _read(root / f'{token}_8.json')['value'] == 'redirected'
    assert not (_configured_delivery_dir() / f'{token}_8').exists()
    assert not _slot(token, '8').exists()


def test_the_results_root_governs_the_delivery_read(tmp):
    routes = _load('fixture_result_routes_readroot')
    root = Path(tmp) / 'read-results'
    empty = Path(tmp) / 'empty-results'
    root.mkdir(parents=True)
    empty.mkdir(parents=True)
    token, did = 'readroottok', 'read-1'
    routes.accept_result(
        root, tmp, token, {'tabId': '2', 'value': 'found', '_did': did}, 4)
    status, payload = routes.fetch_result(root, token, {'delivery': [did]})
    assert status == 200, (status, payload)
    assert payload['value'] == 'found', payload
    status, payload = routes.fetch_result(empty, token, {'delivery': [did]})
    assert (status, payload) == (200, {'pending': True})


def test_the_passed_delivery_cap_is_the_one_enforced(tmp):
    """The cap comes from the argument, not from the configured 1024."""
    routes = _load('fixture_result_routes_cap')
    root = Path(tmp) / 'cap-results'
    root.mkdir(parents=True)
    token = 'captok'
    for did in ('cap-1', 'cap-2'):
        routes.accept_result(
            root, tmp, token, {'tabId': '1', 'value': did, '_did': did}, 1)
    kept = sorted(path.name for path
                  in (root / 'deliveries' / f'{token}_1').iterdir())
    assert kept == ['cap-2.json'], kept


def test_a_zero_delivery_cap_evicts_nothing(tmp):
    """A cap of 0 evicts nothing.

    Not evidence for the guard's falsy clause: `ordered[-0]` is
    `ordered[0]`, so with 0 the boundary is the oldest stamp anyway and
    nothing is below it.
    """
    routes = _load('fixture_result_routes_nocap')
    root = Path(tmp) / 'nocap-results'
    root.mkdir(parents=True)
    token = 'nocaptok'
    for did in ('nocap-1', 'nocap-2'):
        routes.accept_result(
            root, tmp, token, {'tabId': '1', 'value': did, '_did': did}, 0)
    kept = sorted(path.name for path
                  in (root / 'deliveries' / f'{token}_1').iterdir())
    assert kept == ['nocap-1.json', 'nocap-2.json'], kept


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='resultroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
