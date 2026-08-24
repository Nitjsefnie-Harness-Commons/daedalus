#!/usr/bin/env python3
"""Who the bridge lets in, on every route and in every carrier.

The token may travel in a query, in a JSON body or in an `Authorization`
header, and the rules are the same everywhere: a wrong one is refused before
anything is stored, a repeated one is refused before either value is
selected, and two that disagree are refused rather than resolved. These tests
walk the whole route table rather than a sample of it.
"""
import ast
import http.client
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (TOK, framer, put_command, queue_files,  # noqa: E402
                     stream_response)


# Routes the bridge answers without the configured token, by design: the
# liveness probe, the dashboard assets, and the two page-facing segment
# routes, which carry a job-scoped capability instead. Everything else the
# handler routes is a control route and belongs in the matrix below.
_CAPABILITY_OR_PUBLIC_ROUTES = frozenset({
    '/health', '/dashboard', '/segment', '/segment-status',
})


def test_token_validation_across_endpoints(tmp):
    with _util.bridge(tmp) as (base, docroot):
        for bad in ('a/b', 'a.b', '..', ''):
            status, _ = _util.get_json(base + f'/result?token={bad}')
            assert status == 400, ('result', bad, status)
            status, _ = _util.get_json(base + f'/tabs?token={bad}')
            assert status == 400, ('tabs', bad, status)
            status, _ = _util.get_json(base + f'/upload?token={bad}')
            assert status == 400, ('upload', bad, status)
            status, _ = _util.get_json(base + f'/screenshot?token={bad}')
            assert status == 400, ('screenshot', bad, status)
            status, _ = _util.post_json(base + '/register',
                                        {'token': bad, 'tabId': '1'})
            assert status == 400, ('register', bad, status)
            status, _ = _util.request(base + '/upload', 'DELETE',
                                      body={'token': bad})
            assert status == 400, ('delete', bad, status)
        # No stray directories from any of those.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        for d in ('commands', 'results', 'uploads'):
            entries = list((Path(docroot) / d).iterdir())
            assert entries == [], (d, entries)


def test_bridge_storage_fails_closed_without_a_configured_token(tmp):
    """A path-safe caller token is not authorization when no secret exists."""
    env = {'TOKEN': '', 'DAEDALUS_TOKEN': ''}
    with _util.bridge(tmp, env=env) as (base, docroot):
        calls = (
            ('PUT', '/command',
             {'token': 'attackerchosen', 'id': 'probe', 'code': '1'}),
            ('POST', '/upload',
             {'token': 'attackerchosen', 'id': 'probe',
              'filename': 'probe.bin', 'data': 'QQ=='}),
            ('POST', '/segment-job',
             {'token': 'attackerchosen', 'job': 'probe-job'}),
        )
        replies = []
        for method, path, body in calls:
            status, raw = _util.request(base + path, method, body=body)
            replies.append((status, json.loads(raw)))
        assert replies == [(401, {'error': 'unauthorized'})] * 3, replies
        for directory in ('commands', 'uploads', 'segments'):
            assert list((Path(docroot) / directory).iterdir()) == [], directory
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def _handler_routes():
    """Every request path server.py compares a target against.

    Derived from the source rather than listed here, so a control route
    added later is covered by the matrix or fails the test that claims to
    cover every one of them. The handler dispatches through `if
    parsed.path == '...'` chains rather than a table, so the chain is what
    is read; a route introduced in some other shape would be missed, and
    that shape does not exist in this file today.
    """
    tree = ast.parse((_util.ROOT / 'server.py').read_text(encoding='utf-8'))
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        target = node.left
        if not (isinstance(target, ast.Attribute) and target.attr == 'path'):
            continue
        for candidate in node.comparators:
            if (isinstance(candidate, ast.Constant)
                    and isinstance(candidate.value, str)
                    and candidate.value.startswith('/')):
                routes.add(candidate.value)
    assert routes, 'no request paths found in server.py'
    return routes


def test_wrong_token_is_refused_on_every_bridge_control_route(tmp):
    """A page relay cannot turn its own well-shaped token into authority."""
    wrong = 'attackerchosen'
    page_headers = {
        'Origin': 'null',
        'Sec-Fetch-Site': 'cross-site',
    }
    with _util.bridge(tmp) as (base, _docroot):
        conn, response = stream_response(base, wrong, 'extension')
        try:
            replies = [('GET /stream', response.status)]
            if response.status != 200:
                assert json.loads(response.read()) == {'error': 'unauthorized'}
        finally:
            response.close()
            conn.close()

        calls = (
            ('GET', f'/tabs?token={wrong}', None),
            ('GET', f'/result?token={wrong}', None),
            ('GET', f'/upload?token={wrong}', None),
            ('GET', f'/screenshot?token={wrong}', None),
            ('POST', '/register', {'token': wrong, 'tabId': '1'}),
            ('POST', '/sync-tabs', {'token': wrong, 'tabs': []}),
            ('POST', '/unregister', {'token': wrong, 'tabId': '1'}),
            ('POST', '/poll', {'token': wrong}),
            ('POST', '/result', {'token': wrong, 'id': 'r', 'result': 1}),
            ('POST', '/upload',
             {'token': wrong, 'id': 'u', 'filename': 'x', 'data': 'QQ=='}),
            ('POST', '/segment-job', {'token': wrong, 'job': 'wrong-job'}),
            ('PUT', '/command',
             {'token': wrong, 'id': 'c', 'code': '1'}),
            ('DELETE', '/upload', {'token': wrong}),
        )
        for method, path, body in calls:
            status, raw = _util.request(
                base + path, method, body=body, headers=page_headers)
            replies.append((f'{method} {path.split("?", 1)[0]}', status))
            assert json.loads(raw) == {'error': 'unauthorized'}, (
                method, path, status, raw)

        assert all(status == 401 for _route, status in replies), replies

        # The list above is checked against the handler, not trusted: a
        # control route added later either enters the matrix or fails here.
        exercised = {'/stream'} | {path.split('?', 1)[0]
                                   for _method, path, _body in calls}
        missing = _handler_routes() - _CAPABILITY_OR_PUBLIC_ROUTES - exercised
        assert not missing, sorted(missing)

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_repeated_wrong_tokens_create_no_storage_namespaces(tmp):
    """Repeated attacker-chosen names cannot allocate queue, upload, or job state."""
    with _util.bridge(tmp) as (base, docroot):
        replies = []
        for index in range(8):
            wrong = f'attacker{index}'
            calls = (
                ('PUT', '/command',
                 {'token': wrong, 'id': 'c', 'code': '1'}),
                ('POST', '/upload',
                 {'token': wrong, 'id': 'u', 'data': 'QQ=='}),
                ('POST', '/segment-job',
                 {'token': wrong, 'job': f'job{index}'}),
            )
            for method, path, body in calls:
                status, _raw = _util.request(base + path, method, body=body)
                replies.append(status)
        assert replies == [401] * 24, replies
        for directory in ('commands', 'uploads', 'segments'):
            assert list((Path(docroot) / directory).iterdir()) == [], directory


def test_duplicate_query_credentials_are_rejected_without_parser_order(tmp):
    """No query route may select one token from duplicate credentials."""
    routes = (
        ('/tabs', ''),
        ('/result', ''),
        ('/upload', ''),
        ('/screenshot', ''),
        # Keep the old accepted-token stream finite by making its tab invalid.
        ('/stream', '&tab=..'),
    )
    orders = ((TOK, 'wrongtoken'), ('wrongtoken', TOK))
    with _util.bridge(tmp) as (base, _docroot):
        replies = []
        for first, second in orders:
            for path, suffix in routes:
                status, raw = _util.get(
                    f'{base}{path}?token={first}&token={second}{suffix}')
                parsed = json.loads(raw)
                error = parsed.get('error') if isinstance(parsed, dict) else None
                replies.append((path, first, status, error))

        assert all(status == 400 and error == 'duplicate token'
                   for _path, _first, status, error in replies), replies
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_duplicate_body_credentials_are_rejected_on_every_json_route(tmp):
    """No JSON body route may select one token from duplicate credentials."""
    routes = (
        ('POST', '/register', b'"tabId":"duplicate-tab"'),
        ('POST', '/sync-tabs', b'"tabs":[]'),
        ('POST', '/unregister', b'"tabId":"duplicate-tab"'),
        ('POST', '/poll', b'"probe":true'),
        ('POST', '/upload', b'"id":"duplicate-upload","data":"QQ=="'),
        ('POST', '/segment-job', b'"job":"duplicate-job"'),
        ('POST', '/result', b'"id":"duplicate-result","result":1'),
        ('PUT', '/command', b'"id":"duplicate-command","code":"1"'),
        ('DELETE', '/upload', b'"id":"duplicate-delete"'),
    )
    orders = ((TOK, 'wrongtoken'), ('wrongtoken', TOK))
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'duplicate-delete', 'data': 'QQ=='})
        assert status == 200 and body['ok'] is True, (status, body)

        replies = []
        for first, second in orders:
            for method, path, fields in routes:
                raw_body = (f'{{"token":"{first}","token":"{second}",'
                            .encode() + fields + b'}')
                status, raw = _util.request(
                    base + path, method, body=raw_body,
                    headers={'Content-Type': 'application/json'})
                parsed = json.loads(raw)
                error = parsed.get('error') if isinstance(parsed, dict) else None
                replies.append((method, path, first, status, error))

        assert all(status == 400 and error == 'duplicate token'
                   for _method, _path, _first, status, error in replies), replies
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_query_token_duplicates_reject_blank_and_equal_values(tmp):
    """Blank or equal repeated tokens are ambiguous on every query route."""
    routes = (
        ('/tabs', ''),
        ('/result', ''),
        ('/upload', ''),
        ('/screenshot', ''),
        ('/stream', '&tab=duplicate-token'),
    )
    duplicates = ((TOK, TOK), ('', TOK), (TOK, ''))
    with _util.bridge(tmp) as (base, _docroot):
        replies = []
        port = int(base.rsplit(':', 1)[1])
        for first, second in duplicates:
            for path, suffix in routes:
                request_path = (
                    f'{path}?token={first}&token={second}{suffix}')
                if path == '/stream':
                    connection = http.client.HTTPConnection(
                        '127.0.0.1', port, timeout=10)
                    connection.request('GET', request_path)
                    response = connection.getresponse()
                    status = response.status
                    raw = response.read() if status != 200 else b'{}'
                    response.close()
                    connection.close()
                else:
                    status, raw = _util.get(base + request_path)
                parsed = json.loads(raw)
                error = parsed.get('error') if isinstance(parsed, dict) else None
                replies.append((path, first, second, status, error))

        assert all(status == 400 and error == 'duplicate token'
                   for _path, _first, _second, status, error in replies), replies
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['active_streams'] == 0, (
            status, health)


def test_body_token_duplicates_reject_blank_and_equal_values(tmp):
    """All JSON routes reject repeated tokens without inspecting their values."""
    routes = (
        ('POST', '/register', b'"tabId":"duplicate-tab"'),
        ('POST', '/sync-tabs', b'"tabs":[]'),
        ('POST', '/unregister', b'"tabId":"duplicate-tab"'),
        ('POST', '/poll', b'"probe":true'),
        ('POST', '/upload', b'"id":"duplicate-upload","data":"QQ=="'),
        ('POST', '/segment-job', b'"job":"duplicate-job"'),
        ('POST', '/result', b'"id":"duplicate-result","result":1'),
        ('PUT', '/command', b'"id":"duplicate-command","code":"1"'),
        ('DELETE', '/upload', b'"id":"duplicate-delete"'),
    )
    duplicates = ((TOK, TOK), ('', TOK), (TOK, ''))
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'duplicate-delete', 'data': 'QQ=='})
        assert status == 200 and body['ok'] is True, (status, body)

        replies = []
        for first, second in duplicates:
            for method, path, fields in routes:
                raw_body = (b'{"token":' + json.dumps(first).encode()
                            + b',"token":' + json.dumps(second).encode()
                            + b',' + fields + b'}')
                status, raw = _util.request(
                    base + path, method, body=raw_body,
                    headers={'Content-Type': 'application/json'})
                parsed = json.loads(raw)
                error = parsed.get('error') if isinstance(parsed, dict) else None
                replies.append((method, path, first, second, status, error))

        assert all(status == 400 and error == 'duplicate token'
                   for _method, _path, _first, _second, status, error in replies), replies
        assert not queue_files(docroot, TOK)
        assert not list((Path(docroot) / 'results').iterdir())
        assert not list((Path(docroot) / 'segments').iterdir())
        kept = Path(docroot) / 'uploads' / TOK / 'duplicate-delete'
        assert kept.is_dir() and len(list(kept.iterdir())) == 1


def _declared_post(base, path, declared, sent, headers):
    """Declare a large body, send only `sent`, and read whatever comes back.

    The proof that a refusal happened before the body did: the answer arrives
    while most of the declared body has not been sent. A bridge that read
    first would still be waiting on bytes this deliberately withholds.
    """
    port = int(base.rsplit(':', 1)[1])
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    try:
        conn.putrequest('POST', path)
        conn.putheader('Content-Type', 'application/json')
        conn.putheader('Content-Length', str(declared))
        for name, value in headers:
            conn.putheader(name, value)
        conn.endheaders()
        conn.send(sent)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_an_unauthenticated_body_is_refused_before_it_arrives(tmp):
    """A body token cannot be checked without reading the body.

    Every JSON route parsed the whole declared body and only then compared
    the token inside it, so an unauthenticated 24 MiB request moved the
    process high-water mark by 72,904 KiB on its way to a 401 — and every
    concurrent worker could be made to do the same. The Bearer header is
    what makes the decision reachable first.
    """
    env = {'DAEDALUS_REQUEST_TIMEOUT': '5'}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, payload = _declared_post(
            base, '/result', 8 * 1024 * 1024, b'{' + b' ' * 65535, ())
    assert status == 401, (status, payload)
    assert json.loads(payload) == {'error': 'unauthorized'}, payload


def test_a_bearer_header_admits_a_body_past_that_window(tmp):
    """The header is the carrier that lets a large body be authenticated."""
    env = {'DAEDALUS_REQUEST_TIMEOUT': '5'}
    with _util.bridge(tmp, env=env) as (base, docroot):
        big = 'x' * (256 * 1024)
        status, payload = _util.post_json(
            base + '/result', {'id': 'big', 'result': big},
            headers={'Authorization': f'Bearer {TOK}'})
        assert status == 200, (status, payload)
        slot = Path(docroot) / 'results' / f'{TOK}.json'
        assert slot.is_file(), sorted((Path(docroot) / 'results').iterdir())
        stored = json.loads(slot.read_text(encoding='utf-8'))
        assert stored['result'] == big, stored['id']
        # And the same body without the header is refused unread.
        status, payload = _declared_post(
            base, '/result', 8 * 1024 * 1024, b'{' + b' ' * 65535, ())
        assert status == 401, (status, payload)


def test_a_small_body_still_authenticates_from_its_own_token(tmp):
    """The older form keeps working where its size was never the problem."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.post_json(
            base + '/result', {'token': TOK, 'id': 'small', 'result': 'ok'})
        assert status == 200, (status, payload)
        status, payload = _util.post_json(
            base + '/result', {'token': 'wrong', 'id': 'no', 'result': 'no'})
        assert status == 401, (status, payload)


def test_a_header_and_a_body_token_must_agree(tmp):
    """Two different tokens in one request is an ambiguous carrier."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.post_json(
            base + '/result', {'token': 'other', 'id': 'x', 'result': 'y'},
            headers={'Authorization': f'Bearer {TOK}'})
        assert status == 400, (status, payload)
        assert payload == {'error': 'conflicting token'}, payload
        # Repeating the same one is not a disagreement.
        status, payload = _util.post_json(
            base + '/result', {'token': TOK, 'id': 'x', 'result': 'y'},
            headers={'Authorization': f'Bearer {TOK}'})
        assert status == 200, (status, payload)


def test_an_ambiguous_authorization_header_is_refused(tmp):
    """Two Authorization headers are refused before either is selected."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _declared_post(
            base, '/result', 2, b'{}',
            (('Authorization', f'Bearer {TOK}'),
             ('Authorization', f'Bearer {TOK}')))
        assert status == 400, (status, payload)
        assert json.loads(payload) == {
            'error': 'duplicate Authorization header'}, payload


def test_an_authorization_that_is_not_bearer_is_refused(tmp):
    """A header that carries something else is not a fallback to the body."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.post_json(
            base + '/result', {'token': TOK, 'id': 'x', 'result': 'y'},
            headers={'Authorization': f'Basic {TOK}'})
        assert status == 401, (status, payload)
        assert payload == {'error': 'missing Bearer token'}, payload


def test_every_body_verb_settles_credentials_before_the_body(tmp):
    """PUT and DELETE take the same route as POST, not a private one."""
    env = {'DAEDALUS_REQUEST_TIMEOUT': '5'}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        port = int(base.rsplit(':', 1)[1])
        for method, path in (('PUT', '/command'), ('DELETE', '/upload')):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
            try:
                conn.putrequest(method, path)
                conn.putheader('Content-Type', 'application/json')
                conn.putheader('Content-Length', str(8 * 1024 * 1024))
                conn.endheaders()
                conn.send(b'{' + b' ' * 65535)
                response = conn.getresponse()
                status, payload = response.status, response.read()
            finally:
                conn.close()
            assert status == 401, (method, status, payload)
            assert json.loads(payload) == {'error': 'unauthorized'}, (
                method, payload)


def test_authenticated_get_routes_accept_a_bearer_header(tmp):
    """A reusable credential need not be written into the request target.

    A request target is retained by reverse-proxy access logs, browser
    tooling and anything that copies a URL, so the token every authenticated
    GET carried was reusable and durably recorded. The header is the carrier
    that keeps it out of all three.
    """
    auth = {'Authorization': f'Bearer {TOK}'}
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.get_json(base + '/tabs', headers=auth)
        assert status == 200 and payload == [], (status, payload)
        status, payload = _util.get_json(base + '/result', headers=auth)
        assert status == 200 and payload == {'pending': True}, (status, payload)
        status, payload = _util.get_json(base + '/upload', headers=auth)
        assert status == 200 and payload == [], (status, payload)
        # Reached its handler rather than the credential check: an
        # unauthorized caller never learns that the job does not exist.
        status, payload = _util.get_json(
            base + '/segment-job?job=nosuchjob', headers=auth)
        assert status == 404 and payload == {'error': 'no such job'}, (
            status, payload)
        status, payload = _util.get_json(base + '/screenshot', headers=auth)
        assert status == 404 and payload == {'error': 'no uploads'}, (
            status, payload)


def test_the_stream_accepts_a_bearer_header(tmp):
    """The extension's own stream carries no credential in its target."""
    served = []
    with _util.bridge(tmp, output=served) as (base, _docroot):
        conn, response = _util.header_stream(
            base, '/stream?tab=extension',
            (('Authorization', f'Bearer {TOK}'),))
        frame = framer(response, served)
        try:
            assert response.status == 200, response.status
            status, _ = put_command(
                base, {'token': TOK, 'id': 'headerauth', 'code': '1'})
            assert status == 200, status
            delivered = frame('the command sent to a header-authorized stream')
            assert delivered.get('id') == 'headerauth', delivered
        finally:
            response.close()
            conn.close()


def test_an_unauthorized_bearer_header_is_refused_on_a_get(tmp):
    """The header is a credential, not a hint: a wrong one is not ignored."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.get_json(
            base + '/tabs', headers={'Authorization': 'Bearer wrongtoken'})
        assert status == 401 and payload == {'error': 'unauthorized'}, (
            status, payload)
        status, payload = _util.get_json(
            base + '/tabs', headers={'Authorization': f'Basic {TOK}'})
        assert status == 401 and payload == {
            'error': 'missing Bearer token'}, (status, payload)


def test_a_get_header_and_query_token_must_agree(tmp):
    """Two different tokens in one request is an ambiguous carrier."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.get_json(
            base + '/tabs?token=other',
            headers={'Authorization': f'Bearer {TOK}'})
        assert status == 400 and payload == {
            'error': 'conflicting token'}, (status, payload)
        # Repeating the same one is not a disagreement.
        status, payload = _util.get_json(
            base + f'/tabs?token={TOK}',
            headers={'Authorization': f'Bearer {TOK}'})
        assert status == 200 and payload == [], (status, payload)


def test_a_duplicate_authorization_header_is_refused_on_a_get(tmp):
    """Two Authorization headers are refused before either is selected."""
    with _util.bridge(tmp) as (base, _docroot):
        conn, response = _util.header_stream(
            base, '/tabs',
            (('Authorization', f'Bearer {TOK}'),
             ('Authorization', f'Bearer {TOK}')))
        try:
            status, payload = response.status, json.loads(response.read())
        finally:
            response.close()
            conn.close()
        assert status == 400 and payload == {
            'error': 'duplicate Authorization header'}, (status, payload)


def test_a_query_token_still_authorizes_a_get(tmp):
    """The older carrier keeps working; this removes a leak, not a route."""
    with _util.bridge(tmp) as (base, _docroot):
        status, payload = _util.get_json(base + f'/tabs?token={TOK}')
        assert status == 200 and payload == [], (status, payload)
        status, payload = _util.get_json(base + '/tabs?token=wrong')
        assert status == 401 and payload == {'error': 'unauthorized'}, (
            status, payload)
        status, payload = _util.get_json(base + '/tabs')
        assert status == 400 and payload == {'error': 'bad token'}, (
            status, payload)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgecredentials_')


if __name__ == '__main__':
    raise SystemExit(main())
