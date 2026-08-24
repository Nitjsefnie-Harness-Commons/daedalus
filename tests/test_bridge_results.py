#!/usr/bin/env python3
"""The shared result slot: what replaces it, and what may consume it.

A result arrives through `POST /result` and lands in two slots at once, and
the generation it is given is what lets a conditional consume tell its own
answer from a newer one. These tests drive that lifecycle, the delivery-id
dedup a retry depends on, and the storage failures each write can meet.
"""
import http.client
import json
import time
import urllib.parse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import TOK  # noqa: E402


def test_result_roundtrip_and_consume(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and body == {'pending': True}, (status, body)

        res = {'token': TOK, 'id': 'r1', 'result': {'v': 1}, 'error': None, 'ts': 1}
        status, body = _util.post_json(base + '/result', res)
        assert status == 200 and body == {'ok': True}, (status, body)
        assert (Path(docroot) / 'results' / f'{TOK}.json').is_file()

        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and body['id'] == 'r1' and body['result'] == {'v': 1}
        # Not consumed: a second read returns the same result.
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body['id'] == 'r1'

        status, body = _util.get_json(base + f'/result?token={TOK}&consume=1')
        assert body['id'] == 'r1'
        assert not (Path(docroot) / 'results' / f'{TOK}.json').exists()
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body == {'pending': True}, body


def test_result_per_tab_and_broadcast_files(tmp):
    with _util.bridge(tmp) as (base, docroot):
        res = {'token': TOK, 'tabId': 'tab1', 'id': 'r2',
               'result': 'x', 'error': None, 'ts': 1}
        status, _ = _util.post_json(base + '/result', res)
        assert status == 200, status
        res_dir = Path(docroot) / 'results'
        # Per-tab result AND the token-only back-compat file are both written.
        assert (res_dir / f'{TOK}_tab1.json').is_file()
        assert (res_dir / f'{TOK}.json').is_file()

        status, body = _util.get_json(base + f'/result?token={TOK}&tab=tab1')
        assert body['id'] == 'r2', body
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body['id'] == 'r2', body

        # Consuming the per-tab result leaves the token-only file readable.
        status, body = _util.get_json(base + f'/result?token={TOK}&tab=tab1&consume=1')
        assert body['id'] == 'r2'
        assert not (res_dir / f'{TOK}_tab1.json').exists()
        status, body = _util.get_json(base + f'/result?token={TOK}&tab=tab1')
        assert body == {'pending': True}, body
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body['id'] == 'r2', body


def test_result_did_becomes_roundtrip_ms(tmp):
    with _util.bridge(tmp) as (base, docroot):
        did = f'{int(time.time() * 1000) - 50}_000001'
        res = {'token': TOK, 'id': 'r3', 'result': 1, 'error': None,
               'ts': 1, '_did': did}
        status, _ = _util.post_json(base + '/result', res)
        assert status == 200, status
        stored = json.loads((Path(docroot) / 'results' / f'{TOK}.json').read_text(encoding='utf-8'))
        assert '_did' not in stored, stored
        assert stored['deliveryId'] == did, stored
        assert isinstance(stored['roundtrip_ms'], int) and stored['roundtrip_ms'] >= 0


def test_conditional_consume_preserves_a_newer_waiters_result(tmp):
    """A waiter may consume only the exact result generation it peeked."""
    with _util.bridge(tmp) as (base, _docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'shared', 'id': 'waiter-a',
            'result': 'first', 'resultGeneration': 'generation-a'})
        assert status == 200, status
        status, peeked = _util.get_json(
            base + f'/result?token={TOK}&tab=shared')
        assert status == 200 and peeked['id'] == 'waiter-a', (status, peeked)
        expected = peeked['resultGeneration']

        # Waiter B's result replaces A after A peeked but before A consumes.
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'shared', 'id': 'waiter-b',
            'result': 'second', 'resultGeneration': 'generation-b'})
        assert status == 200, status
        status, consume = _util.get_json(
            base + f'/result?token={TOK}&tab=shared&consume=1&expected={expected}')
        assert status == 200, (status, consume)

        # A failed conditional consume must leave B's result for waiter B.
        status, owner = _util.get_json(
            base + f'/result?token={TOK}&tab=shared')
        assert status == 200 and owner.get('id') == 'waiter-b', (consume, owner)
        assert consume.get('consumed') is False, consume

        generation = owner['resultGeneration']
        status, consume = _util.get_json(
            base + f'/result?token={TOK}&tab=shared&consume=1&expected={generation}')
        assert status == 200 and consume == {
            'consumed': True, 'resultGeneration': generation}, consume


def test_a_retried_result_never_replaces_a_newer_one(tmp):
    """A lost 200 makes the extension re-POST; that must not undo the next result.

    background.js retries a result POST up to three times on a transient
    failure, and a response lost after the server stored it looks exactly like
    one that never arrived. The retry carries the same delivery id, so the
    bridge can tell a repeat from a fresh result and leave both slots alone.
    """
    with _util.bridge(tmp) as (base, docroot):
        first = {'token': TOK, 'tabId': 'extension', 'id': 'a',
                 'result': 'first', 'error': None, 'ts': 1,
                 '_did': '1700000000000_000001'}
        status, _ = _util.post_json(base + '/result', first)
        assert status == 200, status
        status, peeked = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200 and peeked['id'] == 'a', (status, peeked)
        first_generation = peeked['resultGeneration']

        # The same delivery id twice is one result, whatever else has landed.
        status, body = _util.post_json(base + '/result', first)
        assert status == 200 and body == {'ok': True, 'duplicate': True}, (
            status, body)
        status, peeked = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert peeked['resultGeneration'] == first_generation, peeked

        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': 'b',
            'result': 'second', 'error': None, 'ts': 2,
            '_did': '1700000000001_000002'})
        assert status == 200, status

        status, body = _util.post_json(base + '/result', first)
        assert status == 200 and body == {'ok': True, 'duplicate': True}, (
            status, body)
        # Both slots still hold B: its waiter is still able to read it.
        status, owner = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200 and owner['id'] == 'b', (status, owner)
        status, shared = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and shared['id'] == 'b', (status, shared)
        stored = json.loads((docroot / 'results' / f'{TOK}_extension.json')
                            .read_text(encoding='utf-8'))
        assert stored['deliveryId'] == '1700000000001_000002', stored


def test_a_result_without_a_delivery_id_still_replaces_the_slot(tmp):
    """Dedup keys on the delivery id, so a result that has none is never one."""
    with _util.bridge(tmp) as (base, _docroot):
        for value in ('first', 'second'):
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'no-did',
                'result': value, 'error': None, 'ts': 1})
            assert status == 200 and body == {'ok': True}, (status, body)
        status, latest = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200 and latest['result'] == 'second', latest


def test_result_path_component_byte_boundaries(tmp):
    """Result names honor both the component and derived-filename budgets."""
    token = 'lengthtoken'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token}) as (base, docroot):
        # This 239-byte tab makes a 256-byte derived filename for this token,
        # one byte beyond filesystems with a 255-byte component limit.
        status, body = _util.post_json(
            base + '/result',
            {'token': token, 'tabId': 'x' * 239, 'id': 'long', 'result': 'x'})
        assert status == 400, (status, body)

        # token + underscore + tab + ".json" is exactly 240 UTF-8 bytes.
        boundary_tab = 't' * 223
        status, body = _util.post_json(
            base + '/result',
            {'token': token, 'tabId': boundary_tab, 'id': 'edge',
             'result': 'kept'})
        assert status == 200 and body == {'ok': True}, (status, body)
        stored = docroot / 'results' / f'{token}_{boundary_tab}.json'
        assert json.loads(stored.read_text(encoding='utf-8'))['result'] == 'kept'
        status, body = _util.get_json(
            base + f'/result?token={token}&tab={boundary_tab}')
        assert status == 200 and body['result'] == 'kept', (status, body)

        over_boundary = 't' * 224
        status, body = _util.post_json(
            base + '/result',
            {'token': token, 'tabId': over_boundary, 'id': 'over',
             'result': 'rejected'})
        assert status == 400, (status, body)
        status, body = _util.get(
            base + f'/result?token={token}&tab={over_boundary}')
        assert status == 400, (status, body)


def test_malformed_result_slot_returns_a_storage_error(tmp):
    """Malformed local result data must answer without ending the request."""
    with _util.bridge(tmp) as (base, docroot):
        result_file = Path(docroot) / 'results' / f'{TOK}.json'
        for stored in ('[]', '{not json'):
            result_file.write_text(stored, encoding='utf-8')
            try:
                status, raw = _util.get(base + f'/result?token={TOK}')
            except http.client.RemoteDisconnected as exc:
                raise AssertionError(
                    f'malformed result {stored!r} ended the request') from exc
            assert status == 500, (stored, status, raw)
            assert json.loads(raw) == {'error': 'result storage failure'}, raw
            assert result_file.read_text(encoding='utf-8') == stored

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_unencodable_result_is_refused_without_poisoning_the_existing_slot(tmp):
    """A result that cannot become UTF-8 must not truncate the current slot."""
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'id': 'kept', 'result': 'safe',
        })
        assert status == 200 and body == {'ok': True}, (status, body)
        result_file = Path(docroot) / 'results' / f'{TOK}.json'
        original = result_file.read_bytes()

        raw_result = (
            b'{"token":"httptok","id":"rejected","result":"\\ud800"}')
        try:
            status, raw = _util.request(
                base + '/result', 'POST', body=raw_result,
                headers={'Content-Type': 'application/json'})
            error = json.loads(raw).get('error')
        except http.client.RemoteDisconnected:
            status, error = 'dropped', None

        assert (status, error) == (400, 'result is not encodable'), (status, error)
        assert result_file.read_bytes() == original
        assert sorted(path.name for path in result_file.parent.iterdir()) == [
            result_file.name
        ]
        status, stored = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and stored['id'] == 'kept', (status, stored)
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_result_with_a_surrogate_id_is_answered_and_the_bridge_survives(tmp):
    """A lone surrogate in a result's id must not drop the request unanswered.

    json.loads accepts "\\ud800"; the [RESULT] log line used to raise
    UnicodeEncodeError at the stdout encode, killing the request thread before
    any HTTP answer. The line now logs the value escaped, and the existing
    encoding guard below it answers 400.
    """
    with _util.bridge(tmp) as (base, docroot):
        raw_result = b'{"token":"httptok","id":"\\ud800","result":1}'
        try:
            status, raw = _util.request(
                base + '/result', 'POST', body=raw_result,
                headers={'Content-Type': 'application/json'})
            error = json.loads(raw).get('error')
        except http.client.RemoteDisconnected:
            status, error = 'dropped', None
        assert (status, error) == (400, 'result is not encodable'), (
            status, error)
        assert list((Path(docroot) / 'results').iterdir()) == []
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_a_result_survives_a_data_root_read_under_any_locale(tmp):
    """Stored JSON is UTF-8 on both sides, not whatever the machine prefers.

    Results are written as `json.dumps(..., ensure_ascii=False).encode()` —
    UTF-8 — and were read back with `Path.read_text()`, which decodes with
    the process locale. The two agree only where that locale is UTF-8. Under
    a C locale the read raises and the fetch answers 500; under a Windows
    code page it does not raise at all and quietly returns a DIFFERENT id,
    so a caller waiting for its own result waits until it times out.

    Forcing the child's locale reproduces the platform difference here
    rather than only on the runner that has it.
    """
    ascii_locale = {'LC_ALL': 'C', 'LANG': 'C', 'PYTHONCOERCECLOCALE': '0',
                    'PYTHONUTF8': '0'}
    wanted = 'shot&branch#caf\u00e9 \u4e16\u754c'
    with _util.bridge(tmp, env=ascii_locale) as (base, _docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': wanted,
            'result': wanted, 'error': None, 'ts': 1})
        assert status == 200, status
        status, got = _util.get_json(
            base + '/result?' + urllib.parse.urlencode(
                {'token': TOK, 'tab': 'extension'}))
        assert status == 200, (status, got)
        assert got['id'] == wanted, (repr(got.get('id')), repr(wanted))
        assert got['result'] == wanted, (repr(got.get('result')), repr(wanted))


def test_result_partial_temp_write_preserves_the_existing_slot(tmp):
    """A failed result write may dirty its temp file, never the live slot."""
    fault_dir = Path(tmp) / 'result-write-fault'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_write_bytes = pathlib.Path.write_bytes\n'
        'def _partial_result_write(path, data):\n'
        '    if path.parent.name == "results":\n'
        '        with path.open("wb") as handle:\n'
        '            handle.write(b\'{"partial":\')\n'
        '        raise OSError("injected partial result write")\n'
        '    return _real_write_bytes(path, data)\n'
        'pathlib.Path.write_bytes = _partial_result_write\n',
        encoding='utf-8')

    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, docroot):
        result_dir = Path(docroot) / 'results'
        result_file = result_dir / f'{TOK}.json'
        original = json.dumps({
            'token': TOK,
            'id': 'kept',
            'result': 'safe',
            'resultGeneration': 'kept-generation',
        }).encode()
        result_file.write_bytes(original)

        status, raw = _util.request(
            base + '/result', 'POST',
            body={'token': TOK, 'id': 'replacement', 'result': 'new'})
        assert status == 500, (status, raw)
        assert json.loads(raw) == {'error': 'result storage failure'}, raw
        assert result_file.read_bytes() == original
        assert sorted(path.name for path in result_dir.iterdir()) == [result_file.name]
        status, stored = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and stored['id'] == 'kept', (status, stored)
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def _replace_fault(tmp, name, failures):
    """A bridge whose os.replace refuses result-slot publishes `failures` times.

    Windows refuses a replace while any handle is open on the target, and the
    handle need not belong to the bridge. That cannot be produced on demand on
    a POSIX runner, so the sharing violation it raises is injected instead:
    what is under test is what the bridge does about it, not the platform.
    """
    fault_dir = Path(tmp) / name
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        '_real_replace = os.replace\n'
        '_left = [' + str(failures) + ']\n'
        'def _sharing_violation(src, dst, **kw):\n'
        '    if os.path.basename(str(dst)).startswith("' + TOK + '"):\n'
        '        if _left[0]:\n'
        '            _left[0] -= 1\n'
        '            raise PermissionError(32, "injected sharing violation")\n'
        '    return _real_replace(src, dst, **kw)\n'
        'os.replace = _sharing_violation\n',
        encoding='utf-8')
    return {'PYTHONPATH': str(fault_dir)}


def test_a_result_write_survives_a_transient_replace_failure(tmp):
    """A replace that fails once and then works must not lose the result.

    The slot is published by replacing it, and on Windows that replace fails
    for as long as anything else holds the target open. Answering 500 for a
    write that was about to succeed discards a result the extension already
    computed and reported.
    """
    env = _replace_fault(tmp, 'transient-replace-fault', 1)
    with _util.bridge(tmp, env=env) as (base, docroot):
        status, payload = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'retried', 'id': 'kept', 'result': 'value'})
        assert status == 200 and payload == {'ok': True}, (status, payload)
        slot = Path(docroot) / 'results' / f'{TOK}_retried.json'
        assert slot.is_file(), sorted((Path(docroot) / 'results').iterdir())
        stored = json.loads(slot.read_text(encoding='utf-8'))
        assert stored['result'] == 'value', stored


def test_a_replace_that_never_clears_is_still_answered(tmp):
    """The retry is bounded: a violation that never clears is not waited on.

    A refusal that is permanent - a read-only volume, a full disk - is not
    going to start working, and a bridge that kept retrying would hold the
    result lock instead of answering.
    """
    env = _replace_fault(tmp, 'permanent-replace-fault', 1000)
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, payload = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'refused', 'id': 'lost', 'result': 'value'})
        assert status == 500, (status, payload)
        assert payload == {'error': 'result storage failure'}, payload


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgeresults_')


if __name__ == '__main__':
    raise SystemExit(main())
