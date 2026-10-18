#!/usr/bin/env python3
"""What the bridge does with a request before any route sees it.

Bodies are bounded, `Content-Length` is validated rather than trusted, JSON
nesting is bounded before it is parsed, and a connection past the worker cap
is closed instead of given a thread. Each of these is settled by the request
machinery, so a failure here is a failure on every route at once.
"""
import http.client
import json
import select
import socket
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (BRIDGE_ENV, TOK, raw_request, read_answer,  # noqa: E402
                     stalled_request)


def test_unknown_paths_404(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, body = _util.get_json(base + '/nope')
        assert status == 404 and body['error'] == 'not found', (status, body)
        status, body = _util.post_json(base + '/nope', {'token': TOK})
        assert status == 404, (status, body)
        status, body = _util.request(
            base + '/nope', 'PUT', body={'token': TOK})
        assert status == 404, status
        status, body = _util.request(
            base + '/nope', 'DELETE', body={'token': TOK})
        assert status == 404, status
        # Malformed JSON on POST is a 400, not a crash.
        status, body = _util.request(
            base + '/result', 'POST', body=b'{not json')
        assert status == 400, status
        assert json.loads(body)['error'] == 'invalid JSON body'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        payload = b'x' * 8192
        status, body = _util.request(
            base + '/nope', 'PUT', body=payload,
            headers={'Content-Type': 'application/octet-stream'})
        assert status == 404, (status, body)
        assert json.loads(body)['error'] == 'not found', body

        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_a_malformed_absolute_form_target_is_refused_not_dropped(tmp):
    """`GET http://[ HTTP/1.1` makes urlparse raise ValueError in every verb
    that parses the target; uncaught, the request thread died and the client
    saw the connection close with zero response bytes. A garbled target is a
    client error — every parsing verb answers a deterministic 400 for it.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        for method in ('GET', 'POST', 'PUT'):
            resp = raw_request(
                base,
                (f'{method} http://[ HTTP/1.1\r\nHost: x\r\n'
                 'Content-Type: application/json\r\n'
                 'Content-Length: 0\r\n\r\n').encode())
            assert resp.startswith(b'HTTP/1.0 400'), (
                f'{method} did not answer 400: {resp[:80]!r}')
            assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
                'error': 'invalid request target'
            }, resp
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_request_body_cap_applies_to_every_body_reader(tmp):
    """Oversized POST, DELETE, and PUT bodies are refused before parsing."""
    segment_job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(
            tmp, env={**BRIDGE_ENV, 'DAEDALUS_MAX_BODY_SIZE': '8'}
    ) as (base, docroot):
        cases = (
            ('POST', '/result', {'Content-Type': 'application/json'}),
            ('POST', f'/segment?job={segment_job}&seg=1&total=1',
             {'Content-Type': 'application/octet-stream'}),
            ('DELETE', '/upload', {'Content-Type': 'application/json'}),
            ('PUT', '/command', {'Content-Type': 'application/json'}),
        )
        for method, path, headers in cases:
            status, body = _util.request(
                base + path, method, body=b'x' * 9, headers=headers)
            assert status == 413, (method, path, status, body)
        assert not (Path(docroot) / 'segments' / segment_job).exists(), \
            'oversized segment body was written'


def test_a_negative_content_length_does_not_bypass_the_body_cap(tmp):
    job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(
            tmp, env={**BRIDGE_ENV, 'DAEDALUS_MAX_BODY_SIZE': '4096'}
    ) as (base, docroot):
        _, minted = _util.post_json(
            base + '/segment-job', {'token': TOK, 'job': job})
        over_cap = b'x' * 8192
        resp = raw_request(
            base,
            (f'POST /segment?job={job}&seg=1&total=1'
             f'&sig={minted["sig"]} HTTP/1.0\r\n'
             'Host: x\r\nContent-Type: application/octet-stream\r\n'
             'Content-Length: -1\r\n\r\n').encode() + over_cap)
        assert resp.startswith(b'HTTP/1.0 400'), resp[:120]
        assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
            'error': 'invalid Content-Length'
        }, resp
        written = list((Path(docroot) / 'segments' / job).rglob('*'))
        assert written == [], (
            f'a negative Content-Length still wrote: {written}')
        # The JSON verbs share the one guard: a well-formed body behind a
        # negative length must never reach a handler either.
        resp = raw_request(
            base,
            b'POST /result HTTP/1.0\r\nHost: x\r\n'
            b'Content-Type: application/json\r\n'
            b'Content-Length: -1\r\n\r\n'
            b'{"token": "httptok", "id": "x", "result": 1}')
        assert resp.startswith(b'HTTP/1.0 400'), resp[:120]
        assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
            'error': 'invalid Content-Length'
        }, resp

    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        for method, path in (('POST', '/result'), ('PUT', '/command'),
                             ('DELETE', '/upload')):
            resp = raw_request(
                base,
                (f'{method} {path} HTTP/1.0\r\nHost: x\r\n'
                 'Content-Type: application/json\r\n'
                 'Content-Length: notanumber\r\n\r\n{}').encode())
            assert resp.startswith(b'HTTP/1.0 400'), (
                f'{method} {path} did not answer 400: {resp[:80]!r}')
            assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
                'error': 'invalid Content-Length'
            }, resp

    payload = b'x' * 16384
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        for declared in ('notanumber', '-1'):
            resp, reset = _refused_exchange(
                base,
                (f'POST /result HTTP/1.0\r\nHost: x\r\n'
                 'Content-Type: application/json\r\n'
                 f'Content-Length: {declared}\r\n\r\n').encode() + payload)
            assert resp.startswith(b'HTTP/1.0 400'), (
                declared, reset, resp[:120])
            assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
                'error': 'invalid Content-Length'}, (declared, reset, resp)
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def _refused_exchange(base, request_bytes):
    """One raw exchange that records how the connection ended.

    raw_request cannot tell a clean close from a reset — it treats the reset
    as the end of the answer — so this records the close kind instead of
    swallowing it, for the tests to read as failure context.
    """
    port = int(base.rsplit(':', 1)[1])
    chunks = []
    reset = None
    with socket.create_connection(('127.0.0.1', port), timeout=10) as sock:
        try:
            sock.sendall(request_bytes)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            # A reset during the send is traffic this exchange invites; what
            # arrived is still readable below.
            pass
        sock.settimeout(3)
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except ConnectionError as exc:
                reset = exc
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b''.join(chunks), reset


def test_an_incomplete_body_never_holds_a_request_worker(tmp):
    """A declared body that stops mid-flight is answered, not waited on.

    One client per stalled body used to hold one request thread for as long
    as it kept the socket open, so a peer could grow the bridge's thread
    count by opening connections and sending a single byte. The peer here
    authenticates: an unauthenticated one no longer reaches the body read at
    all, so this deadline is what still bounds the case that does.
    """
    with _util.bridge(
            tmp, env={**BRIDGE_ENV,
                      'DAEDALUS_REQUEST_TIMEOUT': '2'}) as (base, _docroot):
        sock = stalled_request(base)
        try:
            started = time.time()
            answer = read_answer(sock, 30)
            elapsed = time.time() - started
        finally:
            sock.close()
        assert answer.startswith(b'HTTP/1.0 408'), answer[:120]
        assert elapsed < 20, elapsed
        # The bridge kept serving: the stalled request took nothing with it.
        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


_SHORT_ERROR = {'error': 'request body shorter than Content-Length'}


def _segment_post(base, job, declared, sent):
    """Mint `job` and POST /segment declaring `declared`, sending `sent`.

    The write side is half-closed after the send, so the bridge's read
    returns at EOF instead of stalling to the request deadline — the same
    shape the reported sighting drove by raw socket.
    """
    _, minted = _util.post_json(
        base + '/segment-job', {'token': TOK, 'job': job})
    sig = minted['sig']
    resp = raw_request(
        base,
        (f'POST /segment?job={job}&seg=0&total=1&sig={sig} HTTP/1.0\r\n'
         'Host: x\r\nContent-Type: application/octet-stream\r\n'
         f'Content-Length: {declared}\r\n\r\n').encode() + sent)
    return resp, sig


def test_a_short_segment_body_is_answered_a_bad_request(tmp):
    """A body that ends before its declared Content-Length is a 400.

    The refusal is the transport's: a shortfall against the request's own
    declaration is a client error, whatever the route would have done with
    the bytes that did arrive.
    """
    job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        resp, _sig = _segment_post(base, job, 1000, b'0123456789')
        assert resp.startswith(b'HTTP/1.0 400'), resp[:120]
        assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == _SHORT_ERROR, resp


def test_a_short_segment_body_publishes_nothing(tmp):
    """No segment file and no temp file survive a refused short body."""
    job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        _segment_post(base, job, 1000, b'0123456789')
        seg_dir = Path(docroot) / 'segments' / job
        assert sorted(path.name for path in seg_dir.iterdir()) == [], (
            'the short body was published')


def test_a_short_segment_body_is_not_reported_done(tmp):
    """GET /segment-status reads the directory, and the directory is empty."""
    job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        _resp, sig = _segment_post(base, job, 1000, b'0123456789')
        status, payload = _util.get_json(
            base + f'/segment-status?job={job}&sig={sig}')
        assert (status, payload) == (200, {'done': [], 'count': 0}), payload


def test_a_short_segment_body_leaves_the_job_record_at_zero(tmp):
    """The stored totals a refused short body leaves behind are the mint's."""
    job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        _segment_post(base, job, 1000, b'0123456789')
        record = json.loads(
            (Path(docroot) / 'segments' / f'{job}.json').read_text(
                encoding='utf-8'))
        assert (record['stored_count'], record['stored_bytes']) == (0, 0), (
            record)


def test_a_complete_segment_body_still_stores(tmp):
    for body in (b'ab' * 500, b''):
        job = 'tt-' + uuid.uuid4().hex[:12]
        with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
            resp, sig = _segment_post(base, job, len(body), body)
            assert resp.startswith(b'HTTP/1.0 200'), resp[:120]
            assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
                'ok': True}, resp
            stored = Path(docroot) / 'segments' / job / '000000.ts'
            assert stored.read_bytes() == body
            status, payload = _util.get_json(
                base + f'/segment-status?job={job}&sig={sig}')
            assert (status, payload) == (
                200, {'done': [0], 'count': 1}), payload


def test_a_short_json_body_is_refused_before_parsing(tmp):
    for identity, complete in (('short-body', False),
                               ('complete-prefix', True)):
        with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
            body = json.dumps(
                {'token': TOK, 'id': identity, 'result': {'n': 1}}).encode()
            resp = raw_request(
                base,
                b'POST /result HTTP/1.0\r\nHost: x\r\n'
                b'Content-Type: application/json\r\n'
                b'Content-Length: '
                + str(len(body) + int(complete)).encode() + b'\r\n\r\n'
                + (body if complete else body[:8]))
            assert resp.startswith(b'HTTP/1.0 400'), resp[:120]
            assert json.loads(
                resp.split(b'\r\n\r\n', 1)[1]) == _SHORT_ERROR, resp
            status, slot = _util.get_json(base + '/result?token=' + TOK)
            assert (status, slot) == (200, {'pending': True}), (status, slot)


def _settled(socks, timeout, grace=1.0):
    """`socks` split into the closed, the answered and the still-held.

    A connection the bridge refused is readable at once, so `timeout` is what
    the first settlement gets and the rest get `grace` after it: "still being
    held" can only ever be measured as "nothing arrived in a while", and
    measuring it per socket would cost that wait once per connection.
    """
    pending, closed, answered = list(socks), [], []
    deadline = time.time() + timeout
    while pending:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        ready, _, _ = select.select(pending, [], [], remaining)
        if not ready:
            break
        for sock in ready:
            pending.remove(sock)
            try:
                data = sock.recv(65536)
            except ConnectionError:
                data = b''
            (answered if data else closed).append(sock)
        deadline = min(deadline, time.time() + grace)
    return closed, answered, pending


def test_request_workers_are_capped_rather_than_grown_per_connection(tmp):
    """Past the cap a connection is closed instead of given a thread.

    Which of these connections is the one past the cap is not fixed. The
    fixture starts the bridge by asking it for /health, and that request's
    worker releases its slot only after the handler returns — so a slot can
    still be spent when the first of these arrives, and it is the second that
    is refused. The property is that the cap refuses somebody and holds the
    rest; reading it per connection rather than per position is what keeps an
    unrelated worker's lifetime from deciding the verdict.
    """
    with _util.bridge(
            tmp, env={**BRIDGE_ENV, 'DAEDALUS_MAX_REQUEST_WORKERS': '2',
                      'DAEDALUS_REQUEST_TIMEOUT': '30'}) as (
                          base, _docroot):
        opened = [stalled_request(base) for _ in range(3)]
        try:
            closed, answered, held = _settled(opened, 10)
            assert not answered, 'a stalled body was answered, not held'
            assert closed, 'no connection was refused at the worker cap'
            assert held, 'the cap refused every connection'
        finally:
            for sock in opened:
                sock.close()
        # Closing them frees the workers, so the bridge answers again.
        deadline = time.time() + 10
        while True:
            try:
                status, body = _util.get_json(base + '/health')
            except OSError as exc:  # the last worker may not be released yet
                status, body = 0, exc
            if status == 200:
                break
            assert time.time() < deadline, (status, body)
        assert body['ok'] is True, body


def test_recursive_json_is_refused_on_every_body_verb_before_authentication(
        tmp):
    payload = (b'{"token":"wrongtoken","value":'
               + b'[' * 10000 + b'0' + b']' * 10000 + b'}')
    assert len(payload) == 20032
    routes = (('POST', '/result'), ('PUT', '/command'),
              ('DELETE', '/upload'))
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        replies = []
        for method, path in routes:
            try:
                status, raw = _util.request(
                    base + path, method, body=payload,
                    headers={'Content-Type': 'application/json'})
                error = json.loads(raw).get('error')
            except http.client.RemoteDisconnected:
                status, error = 'dropped', None
            replies.append((method, path, status, error))
            health_status, health = _util.get_json(base + '/health')
            assert health_status == 200 and health['ok'] is True, (
                method, path, health_status, health)

        expected = (400, 'JSON body too deeply nested')
        assert all((status, error) == expected
                   for _method, _path, status, error in replies), replies

    with _util.bridge(
            tmp, env={**BRIDGE_ENV, 'DAEDALUS_MAX_JSON_DEPTH': '4'}
    ) as (base, _d):
        # depth 4 counting the object itself: {"token": [[[0]]]}
        at_limit = (b'{"token":"wrongtoken","value":'
                    + b'[' * 3 + b'0' + b']' * 3 + b'}')
        status, raw = _util.request(
            base + '/result', 'POST', body=at_limit,
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (
            401, 'unauthorized'), (
            status, raw)

        past_limit = (b'{"token":"wrongtoken","value":'
                      + b'[' * 4 + b'0' + b']' * 4 + b'}')
        status, raw = _util.request(
            base + '/result', 'POST', body=past_limit,
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (
            400, 'JSON body too deeply nested'), (status, raw)


def test_json_scalars_arrays_and_null_are_refused_on_every_body_verb(tmp):
    """Syntactically valid JSON still has to be an object for JSON routes."""
    routes = (('POST', '/result'), ('PUT', '/command'),
              ('DELETE', '/upload'))
    values = (('scalar', 1), ('array', []), ('null', None))
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        replies = []
        for shape, value in values:
            encoded = json.dumps(value).encode()
            for method, path in routes:
                try:
                    status, raw = _util.request(
                        base + path, method, body=encoded,
                        headers={'Content-Type': 'application/json'})
                    error = json.loads(raw).get('error')
                except http.client.RemoteDisconnected:
                    status, error = 'dropped', None
                replies.append((shape, method, status, error))
                health_status, health = _util.get_json(base + '/health')
                assert health_status == 200 and health['ok'] is True, (
                    shape, method, health_status, health)

        assert all(
            status == 400 and error == 'JSON body must be an object'
            for _shape, _method, status, error in replies), replies


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgerequests_')


if __name__ == '__main__':
    raise SystemExit(main())
