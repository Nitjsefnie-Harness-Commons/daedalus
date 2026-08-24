#!/usr/bin/env python3
"""What the bridge does with a request before any route sees it.

Bodies are bounded, `Content-Length` is validated rather than trusted, JSON
nesting is bounded before it is parsed, and a connection past the worker cap
is closed instead of given a thread. Each of these is settled by the request
machinery, so a failure here is a failure on every route at once.
"""
import http.client
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import TOK, raw_request, read_answer, stalled_request  # noqa: E402


def test_unknown_paths_404(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.get_json(base + '/nope')
        assert status == 404 and body['error'] == 'not found', (status, body)
        status, body = _util.post_json(base + '/nope', {'token': TOK})
        assert status == 404, (status, body)
        status, body = _util.request(base + '/nope', 'PUT', body={'token': TOK})
        assert status == 404, status
        status, body = _util.request(base + '/nope', 'DELETE', body={'token': TOK})
        assert status == 404, status
        # Malformed JSON on POST is a 400, not a crash.
        status, body = _util.request(base + '/result', 'POST', body=b'{not json')
        assert status == 400, status
        assert json.loads(body)['error'] == 'invalid JSON body'


def test_a_malformed_absolute_form_target_is_refused_not_dropped(tmp):
    """`GET http://[ HTTP/1.1` makes urlparse raise ValueError in every verb
    that parses the target; uncaught, the request thread died and the client
    saw the connection close with zero response bytes. A garbled target is a
    client error — every parsing verb answers a deterministic 400 for it.
    """
    with _util.bridge(tmp) as (base, _docroot):
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
    with _util.bridge(tmp, env={'DAEDALUS_MAX_BODY_SIZE': '8'}) as (base, docroot):
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
    """rfile.read(-1) reads to EOF, so a negative Content-Length is not a small
    body — it is an unbounded one. One character turned the cap off on every
    body-reading path: the guard must reject the sign, not test `clen > MAX`.
    """
    job = 'tt-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env={'DAEDALUS_MAX_BODY_SIZE': '4096'}) as (base, docroot):
        _, minted = _util.post_json(
            base + '/segment-job', {'token': TOK, 'job': job})
        over_cap = b'x' * 8192
        resp = raw_request(
            base,
            (f'POST /segment?job={job}&seg=1&total=1&sig={minted["sig"]} HTTP/1.0\r\n'
             'Host: x\r\nContent-Type: application/octet-stream\r\n'
             'Content-Length: -1\r\n\r\n').encode() + over_cap)
        assert resp.startswith(b'HTTP/1.0 400'), resp[:120]
        assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
            'error': 'invalid Content-Length'
        }, resp
        written = list((Path(docroot) / 'segments' / job).rglob('*'))
        assert written == [], f'a negative Content-Length still wrote: {written}'
        # The JSON verbs share the one guard: a well-formed body behind a
        # negative length must never reach a handler either.
        resp = raw_request(
            base,
            b'POST /result HTTP/1.0\r\nHost: x\r\nContent-Type: application/json\r\n'
            b'Content-Length: -1\r\n\r\n'
            b'{"token": "httptok", "id": "x", "result": 1}')
        assert resp.startswith(b'HTTP/1.0 400'), resp[:120]
        assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
            'error': 'invalid Content-Length'
        }, resp


def test_a_malformed_content_length_is_refused_not_dropped(tmp):
    """A Content-Length int() cannot parse raised uncaught in every
    body-reading verb: the request thread died and the client saw the
    connection close with no answer at all. A garbled header is a client
    error — every verb answers 400 for it.
    """
    with _util.bridge(tmp) as (base, _docroot):
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


def test_a_refused_put_absorbs_its_body_so_the_answer_survives(tmp):
    """A PUT the bridge refuses must still deliver the refusal.

    An unread body makes the close an RST, and an RST discards the answer
    the client has not read yet — a reset this suite has already seen twice
    on a refused body. A refused PUT never reads its body at all.

    The payload stays inside the drain bound on purpose. Past that bound the
    early close is the documented trade, made so that a body far larger than
    the server will take is never read into the process; a test asserting a
    404 there would be asserting against the design rather than for it, and
    on macOS it duly reported the reset.
    """
    with _util.bridge(tmp) as (base, _docroot):
        payload = b'x' * 8192
        status, body = _util.request(
            base + '/nope', 'PUT', body=payload,
            headers={'Content-Type': 'application/octet-stream'})
        assert status == 404, (status, body)
        assert json.loads(body)['error'] == 'not found', body

        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_an_incomplete_body_never_holds_a_request_worker(tmp):
    """A declared body that stops mid-flight is answered, not waited on.

    One client per stalled body used to hold one request thread for as long
    as it kept the socket open, so a peer could grow the bridge's thread
    count by opening connections and sending a single byte. The peer here
    authenticates: an unauthenticated one no longer reaches the body read at
    all, so this deadline is what still bounds the case that does.
    """
    with _util.bridge(
            tmp, env={'DAEDALUS_REQUEST_TIMEOUT': '2'}) as (base, _docroot):
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


def test_request_workers_are_capped_rather_than_grown_per_connection(tmp):
    """Past the cap a connection is closed instead of given a thread."""
    with _util.bridge(tmp, env={'DAEDALUS_MAX_REQUEST_WORKERS': '2',
                                'DAEDALUS_REQUEST_TIMEOUT': '30'}) as (
                                    base, _docroot):
        held = [stalled_request(base), stalled_request(base)]
        refused = stalled_request(base)
        try:
            # The cap is spent on the two held bodies, so this one is closed
            # without an answer rather than given a worker of its own.
            assert read_answer(refused, 10) == b'', 'over-cap connection was served'
            # ... and the two under the cap are still being waited on.
            for sock in held:
                assert read_answer(sock, 1) is None, 'a held body was answered early'
        finally:
            for sock in held + [refused]:
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


def test_the_json_depth_bound_is_the_bridges_and_is_configurable(tmp):
    """The limit is a setting, and a body at it is still accepted.

    A bound nothing can be measured against is not a bound: this drives one
    body to exactly the configured depth and one past it, so the refusal is
    shown to be the number's doing rather than the interpreter's.
    """
    with _util.bridge(tmp, env={'DAEDALUS_MAX_JSON_DEPTH': '4'}) as (base, _d):
        # depth 4 counting the object itself: {"token": [[[0]]]}
        at_limit = b'{"token":"wrongtoken","value":' + b'[' * 3 + b'0' + b']' * 3 + b'}'
        status, raw = _util.request(
            base + '/result', 'POST', body=at_limit,
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (401, 'unauthorized'), (
            status, raw)

        past_limit = b'{"token":"wrongtoken","value":' + b'[' * 4 + b'0' + b']' * 4 + b'}'
        status, raw = _util.request(
            base + '/result', 'POST', body=past_limit,
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (
            400, 'JSON body too deeply nested'), (status, raw)


def test_a_brace_inside_a_json_string_opens_nothing(tmp):
    """Structure is counted, not characters.

    A scan that treated every `[` as a container would refuse a body whose
    only nesting is inside a string literal — and an escaped quote inside one
    would end the string early, so the rest of a hostile value would be read
    as structure.
    """
    with _util.bridge(tmp, env={'DAEDALUS_MAX_JSON_DEPTH': '3'}) as (base, _d):
        literal = json.dumps({'token': 'wrongtoken',
                              'value': '[' * 50 + '\\"' + '{' * 50}).encode()
        status, raw = _util.request(
            base + '/result', 'POST', body=literal,
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (401, 'unauthorized'), (
            status, raw)


def test_recursive_json_is_refused_on_every_body_verb_before_authentication(tmp):
    """A deeply nested body is refused, before the token is looked at.

    The answer used to depend on the interpreter, because the depth bound was
    the interpreter's rather than the bridge's: through 3.13 this nesting
    exceeded the C recursion limit and json.loads raised, which
    _load_json_object turned into the 400; 3.14 parsed the same payload and
    answered the ordinary unauthorized instead. Both were safe and neither
    was a crash, but one body had two answers.

    The bound is now the bridge's, so the answer is the same everywhere.
    """
    payload = (b'{"token":"wrongtoken","value":'
               + b'[' * 10000 + b'0' + b']' * 10000 + b'}')
    assert len(payload) == 20032
    routes = (('POST', '/result'), ('PUT', '/command'),
              ('DELETE', '/upload'))
    with _util.bridge(tmp) as (base, _docroot):
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


def test_json_scalars_arrays_and_null_are_refused_on_every_body_verb(tmp):
    """Syntactically valid JSON still has to be an object for JSON routes."""
    routes = (('POST', '/result'), ('PUT', '/command'),
              ('DELETE', '/upload'))
    values = (('scalar', 1), ('array', []), ('null', None))
    with _util.bridge(tmp) as (base, _docroot):
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
