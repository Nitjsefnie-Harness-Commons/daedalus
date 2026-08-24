#!/usr/bin/env python3
"""End-to-end suite for the real server.py bridge.

Every test drives the actual HTTP surface through _util.bridge(), except the
direct _log_safe unit test, which imports the module instead. Storage failure
tests inject OSError at filesystem boundaries; all requests still cross the
real HTTP, parsing, path-handling, and on-disk queue layers.
"""
import ast
import base64
import concurrent.futures
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# Keep the bridge child's MCP side-thread off the fixed port 8086: several
# bridges run per suite, and the second one to bind 8086 would only log a
# crash, but port 0 removes the collision entirely.
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

TOK = 'httptok'
PNG = b'\x89PNG\r\n\x1a\n' + b'not-really-a-png-but-the-bridge-does-not-care'

# A bridge under test has one configured control credential. Clear the generic
# one-off override so an ambient shell value cannot shadow this suite's token.
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK

# Segment storage lives under the bridge's own data root (<docroot>/segments/)
# since the capability fix; the pre-auth server wrote to a world-shared
# /tmp/hls-segments instead. One test below pins that nothing lands there any
# more; that path means something else on Windows, so the /tmp assertion
# skips there.
TMP_SEG_ROOT = Path('/tmp/hls-segments')


def _put_command(base, payload):
    return _util.request(base + '/command', 'PUT', body=payload)


def _raw_request(base, request_bytes):
    """One raw HTTP exchange, returning the raw response bytes.

    urllib cannot express the requests the hostile-header tests need — a
    Content-Length that is negative or not a number — so they go straight to
    a socket. Half-close the write side before reading: a handler that reads
    the body to EOF receives EOF immediately, and the response path does not
    depend on a read timeout before the client declares its request complete.
    """
    port = int(base.rsplit(':', 1)[1])
    chunks = []
    with socket.create_connection(('127.0.0.1', port), timeout=10) as sock:
        try:
            sock.sendall(request_bytes)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            # The server may refuse and close before the body finishes
            # uploading — that refusal is exactly what these tests measure.
            # A reset during send or half-close is expected traffic then
            # (ENOTCONN under load), and the answer, if it arrived, is still
            # readable below; an empty read fails the caller's own assertion.
            pass
        sock.settimeout(3)
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except ConnectionResetError:
                break  # answered, then reset: the refusal already arrived
            if not chunk:
                break
            chunks.append(chunk)
    return b''.join(chunks)


def _stalled_request(base, body_bytes=b'{'):
    """Open one connection whose declared body never finishes arriving.

    Returns the connected socket, which the caller closes. Nothing is read
    here: what these tests measure is whether the bridge answers a body that
    stops mid-flight, so the read belongs in the test, with its own bound.
    """
    port = int(base.rsplit(':', 1)[1])
    sock = socket.create_connection(('127.0.0.1', port), timeout=30)
    try:
        sock.sendall(b'POST /result HTTP/1.0\r\n'
                     b'Content-Type: application/json\r\n'
                     b'Content-Length: 1000000\r\n\r\n' + body_bytes)
    except OSError:
        # A connection the bridge refuses is closed with these bytes still
        # unread, which is a reset rather than an EOF — and Windows delivers
        # it during the send rather than during the read that follows. The
        # refusal is what the caller measures, so hand the socket back and
        # let the read report it, the same way _raw_request does.
        pass
    return sock


def _read_answer(sock, timeout):
    """One answer; b'' where the peer closed without sending one; None where
    nothing arrived inside `timeout`, i.e. the request is still being held.

    A close after unread request bytes arrives as a reset rather than an EOF,
    and Windows spells that abort differently again, so every "closed on me"
    reports the same b''.
    """
    sock.settimeout(timeout)
    chunks = []
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            return b''.join(chunks) if chunks else None
        except ConnectionError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b''.join(chunks)


def _queue_files(docroot, name):
    qdir = Path(docroot) / 'commands' / name
    if not qdir.is_dir():
        return []
    return sorted(p for p in qdir.iterdir() if p.suffix == '.json')


def _stream_response(base, token, tab=None):
    """Open one stream and return its connection and response headers.

    The read ceiling is generous: it bounds how long a test waits for a frame
    the bridge legitimately needs time to deliver under load (two full suites
    running side by side), never what it asserts about the frame.
    """
    port = int(base.rsplit(':', 1)[1])
    path = f'/stream?token={token}'
    if tab is not None:
        path += f'&tab={tab}'
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    conn.request('GET', path)
    return conn, conn.getresponse()


def _read_stream_data(base, token, tab=None, timeout=30):
    """Read the first data payload from a real SSE stream.

    Bounded by the same monotonic deadline as _next_stream_data, and for the
    same reason: the stream's keepalives arrive more often than the
    connection's socket timeout and reset it, so a read bounded only by the
    socket waits out a lost command for as long as the bridge stays healthy.
    The ceiling is generous — it bounds waiting for a frame the bridge may
    legitimately need time to deliver under load, never what is asserted
    about the frame.
    """
    conn, response = _stream_response(base, token, tab)
    try:
        assert response.status == 200, response.status
        try:
            return _next_stream_data(response, timeout=timeout)
        except AssertionError as failure:
            raise AssertionError(
                f'{failure}: waiting on the stream for '
                f'token={token!r} tab={tab!r}') from failure
    finally:
        response.close()
        conn.close()


def _next_stream_data(response, timeout=10):
    """Read an open SSE response until the next data frame arrives.

    A closed or reset stream is reported as an assertion with a diagnosis:
    the surrogate tests below pin that a malformed command cannot tear the
    stream down. The monotonic deadline remains effective even when keepalive
    lines keep arriving, so a lost command fails instead of hanging forever.
    """
    deadline = time.monotonic() + timeout
    stream_socket = response.fp.raw._sock
    original_timeout = stream_socket.gettimeout()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f'no data frame arrived within {timeout} seconds')
            stream_socket.settimeout(remaining)
            line = response.readline()
            if not line:
                raise AssertionError('the stream closed before the next frame')
            if line.startswith(b'data: '):
                return json.loads(line[len(b'data: '):])
    except socket.timeout as exc:
        raise AssertionError(
            f'no data frame arrived within {timeout} seconds') from exc
    except (ConnectionError, OSError) as exc:
        raise AssertionError(
            f'the stream died before the next frame: {exc}') from exc
    finally:
        stream_socket.settimeout(original_timeout)


def _assert_oversize_stream_matches_enqueue(base):
    """Both sides of an impossible target reject it without killing the bridge."""
    token = '123e4567-e89b-12d3-a456-426614174000'
    tab = 't' * 240
    conn, response = _stream_response(base, token, tab)
    try:
        stream_status = response.status
        stream_body = response.read() if stream_status != 200 else b''
    finally:
        response.close()
        conn.close()

    enqueue_status, enqueue_body = _put_command(
        base, {'token': token, 'tab': tab, 'id': 'overflow', 'code': '1'})
    health_status, health_body = _util.get_json(base + '/health')
    assert (stream_status, enqueue_status, health_status) == (400, 400, 200), (
        stream_status, enqueue_status, health_status)
    assert json.loads(stream_body)['error'] == 'invalid path component', stream_body
    assert json.loads(enqueue_body)['error'] == 'invalid path component', enqueue_body
    assert health_body['ok'] is True, health_body


def test_health_counts_a_stream_that_named_no_tab(tmp):
    """A stream with no tab selector is still a stream.

    It got no entry at all, so it served commands and held a request worker
    while /health reported zero — and the count it reported was the number of
    distinct tab NAMES, so two streams sharing a name counted once.
    """
    with _util.bridge(tmp) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['active_streams'] == 0, health

        tabless_conn, tabless = _stream_response(base, TOK)
        try:
            assert tabless.status == 200, tabless.status
            _util.request(base + '/command', 'PUT',
                          body={'token': TOK, 'id': 'wake', 'code': '1'})
            assert _next_stream_data(tabless).get('id') == 'wake'
            status, health = _util.get_json(base + '/health')
            assert status == 200 and health['active_streams'] == 1, health
            assert health['stream_tabs'] == [''], health

            named_conn, named = _stream_response(base, TOK, tab='extension')
            try:
                assert named.status == 200, named.status
                deadline = time.time() + 10
                while True:
                    status, health = _util.get_json(base + '/health')
                    if health['active_streams'] == 2:
                        break
                    assert time.time() < deadline, health
                assert health['stream_tabs'] == ['', 'extension'], health
            finally:
                named.close()
                named_conn.close()
        finally:
            tabless.close()
            tabless_conn.close()


def test_health_payload(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.get_json(base + '/health')
        assert status == 200, status
        assert body['ok'] is True
        assert isinstance(body['uptime_s'], (int, float)) and body['uptime_s'] >= 0
        assert body['active_streams'] == 0
        assert body['stream_tabs'] == []
        assert body['registry'] == {'tokens': 0, 'tabs': 0}
        assert body['last_delivery_s_ago'] is None  # nothing delivered yet
        assert body['cmd_ttl_s'] == 90
        assert body['stream_max_age_s'] == 3600


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


def test_put_command_broadcast_writes_queue_file(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _put_command(base, {'token': TOK, 'id': 'c1', 'code': '1+1'})
        assert status == 200, (status, body)
        body = json.loads(body)
        assert body['ok'] is True
        assert body['target'] == 'broadcast', body
        files = _queue_files(docroot, TOK)
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'c1' and data['code'] == '1+1'
        assert data['_did'] == body['did'], (data, body)
        assert 'token' not in data  # routing fields stay out of the payload


def test_put_command_per_tab_goes_to_tab_queue(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _put_command(
            base, {'token': TOK, 'id': 'c2', 'code': '2+2', 'tab': 'tab1'})
        assert status == 200, (status, body)
        body = json.loads(body)
        assert body['target'] == 'tab=tab1', body
        files = _queue_files(docroot, f'{TOK}_tab1')
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'c2'
        assert 'tab' not in data and 'token' not in data  # routing-only fields
        # Nothing landed in the broadcast queue.
        assert _queue_files(docroot, TOK) == []


def test_put_command_derived_queue_name_byte_boundary(tmp):
    """Derived command queue names honor the component byte ceiling."""
    token = '123e4567-e89b-12d3-a456-426614174000'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token}) as (base, docroot):
        boundary_tab = 't' * 203
        status, body = _put_command(
            base, {'token': token, 'tab': boundary_tab,
                   'id': 'boundary', 'code': '1'})
        assert status == 200, (status, body)
        assert len(_queue_files(docroot, f'{token}_{boundary_tab}')) == 1

        status, body = _put_command(
            base, {'token': token, 'tab': 't' * 240,
                   'id': 'overflow', 'code': '2'})
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'invalid path component', body

        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_stream_derived_queue_name_matches_command_enqueue(tmp):
    token = '123e4567-e89b-12d3-a456-426614174000'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token}) as (base, _docroot):
        boundary_tab = 't' * 203
        status, body = _put_command(
            base, {'token': token, 'tab': boundary_tab,
                   'id': 'boundary-stream', 'code': '1'})
        assert status == 200, (status, body)
        streamed = _read_stream_data(base, token, boundary_tab)
        assert streamed['id'] == 'boundary-stream', streamed

        _assert_oversize_stream_matches_enqueue(base)


def test_a_lost_command_ends_the_read_instead_of_riding_keepalives(tmp):
    """An undelivered command must fail its reader, not outlive the suite.

    The stream's keepalives reset the connection's socket timeout, and they
    arrive more often than that timeout, so a reader bounded only by the
    socket waits for exactly as long as the bridge stays healthy: a lost
    command hangs the run with no diagnosis instead of failing with one.
    """
    outcome = []
    with _util.bridge(
            tmp, env={'DAEDALUS_STREAM_KEEPALIVE': '1'}) as (base, _docroot):
        def read():
            try:
                _read_stream_data(base, TOK, 'nothing-is-sent-here', timeout=3)
                outcome.append('a data frame arrived on an idle stream')
            except AssertionError as failure:
                outcome.append(str(failure))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout=20)
        assert not reader.is_alive(), 'the read is still riding keepalives'
    assert outcome and 'within 3 seconds' in outcome[0], outcome


def test_stream_modes_deliver_end_to_end(tmp):
    tab_token = 'stream-tab'
    with _util.bridge(
            Path(tmp) / 'tab',
            env={'TOKEN': '', 'DAEDALUS_TOKEN': tab_token}) as (base, _docroot):
        status, body = _put_command(
            base, {'token': tab_token, 'tab': 'tab1', 'id': 'tab', 'code': '1'})
        assert status == 200, (status, body)
        assert _read_stream_data(base, tab_token, 'tab1')['id'] == 'tab'

    dashboard_token = 'stream-dashboard'
    with _util.bridge(
            Path(tmp) / 'dashboard',
            env={'TOKEN': '',
                 'DAEDALUS_TOKEN': dashboard_token}) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': dashboard_token, 'tabId': '7', 'id': 'dashboard-result',
            'result': 'FORGED', 'error': None, 'world': 'page:cdp',
        })
        assert status == 200, (status, body)
        dashboard = _read_stream_data(base, dashboard_token, 'dashboard')
        assert dashboard['kind'] == 'event' and dashboard['type'] == 'result', \
            dashboard
        assert dashboard['world'] == 'page:cdp', dashboard

    extension_token = 'stream-extension'
    with _util.bridge(
            Path(tmp) / 'extension',
            env={'TOKEN': '',
                 'DAEDALUS_TOKEN': extension_token}) as (base, _docroot):
        status, body = _put_command(
            base, {'token': extension_token, 'tab': 'extension',
                   'id': 'extension', 'type': 'screenshot'})
        assert status == 200, (status, body)
        assert _read_stream_data(
            base, extension_token, 'extension')['id'] == 'extension'

    broadcast_token = 'stream-broadcast'
    with _util.bridge(
            Path(tmp) / 'broadcast',
            env={'TOKEN': '',
                 'DAEDALUS_TOKEN': broadcast_token}) as (base, _docroot):
        status, body = _put_command(
            base, {'token': broadcast_token, 'id': 'broadcast', 'code': '2'})
        assert status == 200, (status, body)
        assert _read_stream_data(base, broadcast_token)['id'] == 'broadcast'


def test_put_command_fifo_order(tmp):
    with _util.bridge(tmp) as (base, docroot):
        for i in range(3):
            status, _ = _put_command(
                base, {'token': TOK, 'id': f'c{i}', 'code': str(i)})
            assert status == 200, status
        files = _queue_files(docroot, TOK)
        assert len(files) == 3, files
        # Lexical filename order is enqueue order (ms + counter stem), and the
        # delivery ids sort the same way.
        ids = [json.loads(f.read_text(encoding='utf-8'))['id'] for f in files]
        assert ids == ['c0', 'c1', 'c2'], ids
        dids = [json.loads(f.read_text(encoding='utf-8'))['_did'] for f in files]
        assert dids == sorted(dids), dids


def test_stream_drops_a_non_object_queue_entry(tmp):
    """A JSON value without command fields cannot terminate queue draining."""
    with _util.bridge(tmp) as (base, docroot):
        qdir = Path(docroot) / 'commands' / TOK
        qdir.mkdir()
        malformed = qdir / '0000000000000_000000.json'
        malformed.write_text('[]', encoding='utf-8')
        status, body = _put_command(
            base, {'token': TOK, 'id': 'after-malformed', 'code': '1'})
        assert status == 200, (status, body)

        delivered = _read_stream_data(base, TOK)
        assert delivered['id'] == 'after-malformed', delivered
        assert not malformed.exists(), 'the non-object queue entry was not dropped'


def test_expired_command_namespaces_are_collected_without_a_consumer(tmp):
    """The command TTL applies even when no SSE stream ever drains a queue."""
    env = {'DAEDALUS_CMD_TTL': '1'}
    with _util.bridge(tmp, env=env) as (base, docroot):
        for index in range(4):
            status, body = _put_command(
                base, {'token': TOK, 'tab': f'abandoned{index}',
                       'id': f'c{index}', 'code': '1'})
            assert status == 200, (status, body)

        command_root = Path(docroot) / 'commands'
        assert len(list(command_root.iterdir())) == 4
        expired = time.time() - 2
        for queue_dir in command_root.iterdir():
            for command_file in queue_dir.iterdir():
                os.utime(command_file, (expired, expired))
        deadline = time.time() + 3
        while time.time() < deadline and list(command_root.iterdir()):
            time.sleep(0.05)
        assert list(command_root.iterdir()) == [], list(command_root.iterdir())
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_put_command_validation(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _put_command(base, {'token': TOK, 'code': '1'})  # no id
        assert status == 400, status
        status, _ = _put_command(base, {'token': TOK, 'id': 'x'})  # no code/type
        assert status == 400, status
        # A type alone is a valid (extension) command.
        status, _ = _put_command(base, {'token': TOK, 'id': 'x', 'type': 'screenshot'})
        assert status == 200, status
        for bad in ('a/b', 'a.b', '..', ''):
            status, _ = _put_command(base, {'token': bad, 'id': 'x', 'code': '1'})
            assert status == 400, (bad, status)
        # The rejected tokens created no queue directories.
        names = [p.name for p in (Path(docroot) / 'commands').iterdir()]
        assert names == [TOK], names


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


def test_stream_survives_a_surrogate_id_in_a_queued_command(tmp):
    """A queued command whose id holds a lone surrogate must not kill the stream.

    The SSE frame itself escapes the surrogate (json.dumps defaults); it was
    the DELIVERED log line that raised UnicodeEncodeError and tore the stream
    down after the frame had already gone out.
    """
    with _util.bridge(tmp) as (base, docroot):
        conn, response = _stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            qdir = Path(docroot) / 'commands' / TOK
            qdir.mkdir(parents=True)
            (qdir / '0000000000001_000001.json').write_bytes(
                b'{"id":"\\ud800","code":"1"}')
            first = _next_stream_data(response)
            assert first.get('code') == '1', first
            status, _ = _put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            second = _next_stream_data(response)
            assert second.get('id') == 'after', second
        finally:
            response.close()
            conn.close()
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_stream_survives_a_surrogate_id_in_a_legacy_command_file(tmp):
    """The same lone surrogate in a legacy raw-write file must not kill the stream."""
    with _util.bridge(tmp) as (base, docroot):
        conn, response = _stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            legacy = Path(docroot) / 'commands' / f'{TOK}.json'
            legacy.write_bytes(b'{"id":"\\ud800","code":"1"}')
            first = _next_stream_data(response)
            assert first.get('code') == '1', first
            status, _ = _put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            second = _next_stream_data(response)
            assert second.get('id') == 'after', second
        finally:
            response.close()
            conn.close()
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_legacy_publication_never_deletes_an_in_progress_write(tmp):
    """Visible partial files survive, while sibling temp names wait for rename."""
    with _util.bridge(tmp) as (base, docroot):
        commands = Path(docroot) / 'commands'
        legacy = commands / f'{TOK}.json'
        writer = open(legacy, 'w', encoding='utf-8')
        conn = response = None
        try:
            writer.write('{"id":"held-open"')
            writer.flush()
            os.fsync(writer.fileno())
            conn, response = _stream_response(base, TOK, tab='extension')
            assert response.status == 200, response.status
            time.sleep(1.25)
            assert legacy.exists(), (
                'the reader unlinked a visible file while its writer was open')
            writer.write(',"code":"first"}')
            writer.flush()
            os.fsync(writer.fileno())
            writer.close()
            frame = _next_stream_data(response, timeout=5)
            assert frame.get('id') == 'held-open', frame

            in_progress = commands / f'.{TOK}.json.tmp'
            in_progress.write_text(
                '{"id":"atomic","code":"second"}', encoding='utf-8')
            time.sleep(1.25)
            assert in_progress.exists(), 'the reader deleted a sibling temp file'
            os.replace(in_progress, legacy)
            frame = _next_stream_data(response, timeout=5)
            assert frame.get('id') == 'atomic', frame
        finally:
            if not writer.closed:
                writer.close()
            if response is not None:
                response.close()
            if conn is not None:
                conn.close()


def test_stream_survives_an_undecodable_byte_in_a_dropped_name(tmp):
    """A raw-dropped NAME with an undecodable byte must not kill the stream.

    iterdir() decodes filesystem bytes with surrogateescape, so a dropped
    file or queue directory named with a raw byte arrives as '\\udcff…';
    where sys.stdout.errors is strict (PYTHONIOENCODING=utf-8:strict forces
    it here, because this box's C.UTF-8 stdio would mask it), the DELIVERED
    log line used to raise UnicodeEncodeError and tear the stream down.
    """
    _util.require_undecodable_names(tmp)
    strict = {'PYTHONIOENCODING': 'utf-8:strict'}
    # The bridge's own log goes into every failure here. This test has failed
    # intermittently with nothing but "no data frame arrived", and its
    # DELIVERED lines are the only direct evidence of whether the drain ever
    # saw the file it was waiting for.
    served = []
    with _util.bridge(tmp, env=strict, output=served) as (base, docroot):
        conn, response = _stream_response(base, TOK, tab='extension')

        def frame(what):
            try:
                return _next_stream_data(response)
            except AssertionError as failure:
                raise AssertionError(
                    f'{what}: {failure}; the bridge said: '
                    f'{"".join(served)!r}') from failure

        try:
            assert response.status == 200, response.status
            commands = os.fsencode(Path(docroot) / 'commands')
            # A legacy raw-write file whose own name carries the raw byte.
            with open(commands + b'/' + os.fsencode(TOK) + b'_\xfftab.json',
                      'wb') as handle:
                handle.write(b'{"id":"legacybad","code":"1"}')
            first = frame('the legacy dropped file')
            assert first.get('id') == 'legacybad', first
            # A queue directory whose name carries the raw byte.
            bad_dir = commands + b'/' + os.fsencode(TOK) + b'_\xffdir'
            os.mkdir(bad_dir)
            with open(bad_dir + b'/0000000000001_000001.json', 'wb') as handle:
                handle.write(b'{"id":"qbad","code":"1"}')
            second = frame('the queued command under a dropped name')
            assert second.get('id') == 'qbad', second
            status, _ = _put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            third = frame('the command enqueued afterwards')
            assert third.get('id') == 'after', third
        finally:
            response.close()
            conn.close()
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_stream_survives_an_undecodable_byte_in_an_expired_queue_entry(tmp):
    """The TTL-DROP log line takes the same raw name and must not kill the stream."""
    _util.require_undecodable_names(tmp)
    strict = {'PYTHONIOENCODING': 'utf-8:strict'}
    with _util.bridge(tmp, env=strict) as (base, docroot):
        qdir = Path(docroot) / 'commands' / TOK
        qdir.mkdir(parents=True)
        stale = os.fsencode(qdir) + b'/\xffexpired.json'
        with open(stale, 'wb') as handle:
            handle.write(b'{"id":"stale"}')
        os.utime(stale, (0, 0))  # far past CMD_TTL; the GC's first pass is
        # 30s after bridge start, so the stream's first drain sees this file.
        conn, response = _stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            status, _ = _put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            frame = _next_stream_data(response)
            assert frame.get('id') == 'after', frame
        finally:
            response.close()
            conn.close()
        deadline = time.time() + 5
        while os.path.exists(stale) and time.time() < deadline:
            time.sleep(0.01)
        assert not os.path.exists(stale), 'the expired entry was not dropped'
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_startup_survives_an_undecodable_byte_in_the_data_root(tmp):
    """The startup log line prints the configured data root.

    A root whose name carries an undecodable byte arrives through
    surrogateescape; where sys.stdout.errors is strict
    (PYTHONIOENCODING=utf-8:strict forces it here, because this box's
    C.UTF-8 stdio would mask it), printing it raised UnicodeEncodeError and
    the server exited 1 before ever serving a request.
    """
    _util.require_undecodable_names(tmp)
    bad_root = os.fsencode(tmp) + b'/\xffdocroot'
    os.mkdir(bad_root)
    env = {'PYTHONIOENCODING': 'utf-8:strict',
           'DAEDALUS_DIR': os.fsdecode(bad_root)}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_log_safe_never_raises_and_stays_useful(tmp):
    """The log-line safeguard must not itself be able to raise.

    str(value) was evaluated outside any fallback: a conversion-limited huge
    int raises ValueError under the default int-to-string limit, an exception
    object whose __str__ fails propagates its own error, and a str subclass
    can hand .encode() to code that raises or .decode() to one returning a
    non-string — the same failure class the helper exists to close, one
    layer inside it.
    """
    env = {'DAEDALUS_DIR': str(tmp), 'DAEDALUS_PORT': '8081'}
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    root = str(_util.ROOT)
    added_root = root not in sys.path
    if added_root:
        sys.path.insert(0, root)
    try:
        mod = _util.load(_util.ROOT / 'server.py', 'server_log_safe_unit')
    finally:
        if added_root:
            sys.path.remove(root)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    for value, expected in _util.log_safe_cases():
        assert mod._log_safe(value) == expected, (
            f'_log_safe({type(value).__name__}) disagrees')
    # Ordinary values pass through in full, ASCII and non-ASCII alike.
    assert mod._log_safe('plain ascii') == 'plain ascii'
    assert mod._log_safe('héllo — 世界') == 'héllo — 世界'


def test_a_lost_draw_no_longer_kills_the_bridge_fixture(tmp):
    """A drawn port taken by another process used to fail the bridge child.

    The fixture handed the child a number it had already released; a squatter
    binding it first produced EADDRINUSE under an innocent test's name — the
    intermittent that surfaced under arbitrary tests in this suite and never
    reproduced. The fixture now asks the kernel for port 0, so there is no
    number to lose.
    """
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    real_free_port = _util.free_port
    _util.free_port = lambda: taken
    try:
        with _util.bridge(tmp) as (base, _docroot):
            status, health = _util.get_json(base + '/health')
            assert status == 200 and health['ok'] is True, (status, health)
    finally:
        _util.free_port = real_free_port
        squatter.close()


def test_an_explicit_port_collision_surfaces_verbatim(tmp):
    """A caller that forces an occupied port still gets the original error.

    Port 0 removed the window for the fixture's own draws, but an explicit
    DAEDALUS_PORT is still honored, and its bind failure must arrive as
    itself — not retried into a fresh port or flattened into a timeout.
    """
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    try:
        with _util.bridge(tmp, env={'DAEDALUS_PORT': str(taken)}):
            pass
    except RuntimeError as failure:
        assert _util.is_bind_error(str(failure)), failure
    else:
        raise AssertionError('a squatted explicit port started a bridge')
    finally:
        squatter.close()


def test_unencodable_command_body_names_the_body_not_the_path(tmp):
    """A surrogate in a queued command's body is an encoding failure, not a
    path one — and the refused enqueue leaves no artifact at all, including
    the hidden temp the failed write opened."""
    with _util.bridge(tmp) as (base, docroot):
        status, raw = _put_command(
            base, {'token': TOK, 'id': 'enc', 'code': '\ud800'})
        assert status == 400, (status, raw)
        assert json.loads(raw)['error'] == 'command is not encodable', raw
        qdir = Path(docroot) / 'commands' / TOK
        entries = list(qdir.iterdir()) if qdir.is_dir() else []
        assert entries == [], entries
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


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


def test_a_filename_without_an_id_deletes_nothing(tmp):
    """The narrowest delete must not fall through to the widest one.

    `{token, filename}` matched neither the file branch nor the id branch and
    landed in the one that removes the token's entire upload namespace, so
    naming a single file deleted every upload the token had — and answered
    that as a success.
    """
    with _util.bridge(tmp) as (base, docroot):
        for upload_id, name in (('alpha', 'one.txt'), ('beta', 'two.txt')):
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': upload_id, 'filename': name,
                'data': base64.b64encode(b'keep me').decode()})
            assert status == 200, (status, body)

        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'filename': 'one.txt'})
        assert status == 400, (status, body)

        root = Path(docroot) / 'uploads' / TOK
        assert (root / 'alpha' / 'one.txt').is_file(), sorted(root.rglob('*'))
        assert (root / 'beta' / 'two.txt').is_file(), sorted(root.rglob('*'))


def test_an_upload_path_that_escapes_through_a_symlink_is_refused(tmp):
    """Component validation cannot answer where a path ended up.

    `_unsafe_component` is a shape check on one string: `escape` passes it,
    because there is nothing wrong with the name. If `escape` is a symlink out
    of the token's directory, every component is harmless and the path still
    leaves the namespace — which is the one thing the check exists to prevent.

    The containment check asks the other question, about the result rather
    than the parts, so the delete is refused and the file outside survives.
    """
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'real', 'filename': 'keep.txt',
            'data': base64.b64encode(b'inside').decode()})
        assert status == 200, status

        outside = Path(docroot) / 'outside'
        outside.mkdir()
        secret = outside / 'secret.txt'
        secret.write_text('do not delete me', encoding='utf-8')
        token_dir = Path(docroot) / 'uploads' / TOK
        try:
            (token_dir / 'escape').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as why:
            _util.skip(f'this filesystem will not hold a symlink: {why}')

        status, raw = _util.request(
            base + '/upload', 'DELETE',
            body=json.dumps({'token': TOK, 'id': 'escape',
                             'filename': 'secret.txt'}).encode(),
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (
            400, 'invalid path component'), (status, raw)
        assert secret.is_file(), 'the file outside the namespace was removed'
        assert secret.read_text(encoding='utf-8') == 'do not delete me'

        # The ordinary path still works, so the guard is not simply refusing.
        status, raw = _util.request(
            base + '/upload', 'DELETE',
            body=json.dumps({'token': TOK, 'id': 'real',
                             'filename': 'keep.txt'}).encode(),
            headers={'Content-Type': 'application/json'})
        assert status == 200, (status, raw)


def test_upload_pagination_bounds_the_work_not_only_the_answer(tmp):
    """A page of one must not cost a stat of every file in the namespace.

    Pagination bounded the response and nothing else: every upload directory
    was enumerated, every file statted twice to build a record, the whole list
    materialized, and only then sliced. The count still has to visit every
    entry — `total` says how many pages there are — but counting an entry is
    what the kernel already told us, and describing one is a syscall.

    Measured rather than asserted about the code: sitecustomize counts every
    os.stat under the uploads root, so the number is what the handler actually
    did.
    """
    fault_dir = Path(tmp) / 'stat-counter'
    fault_dir.mkdir()
    counts = Path(tmp) / 'stat-calls'
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        '_real = os.stat\n'
        '_log = open(os.environ["STAT_LOG"], "ab", buffering=0)\n'
        'def _counted(path, *args, **kwargs):\n'
        '    try:\n'
        '        if "uploads" in os.fspath(path):\n'
        '            _log.write(b".")\n'
        '    except TypeError:\n'
        '        pass\n'
        '    return _real(path, *args, **kwargs)\n'
        'os.stat = _counted\n',
        encoding='utf-8')
    env = {'PYTHONPATH': str(fault_dir), 'STAT_LOG': str(counts)}
    ids, per_id = 4, 15
    with _util.bridge(tmp, env=env) as (base, _docroot):
        for id_index in range(ids):
            for file_index in range(per_id):
                status, _ = _util.post_json(base + '/upload', {
                    'token': TOK, 'id': f'batch{id_index}',
                    'filename': f'file{file_index}.txt',
                    'data': base64.b64encode(b'x').decode()})
                assert status == 200, status

        before = counts.stat().st_size
        query = urllib.parse.urlencode({'token': TOK, 'limit': 1, 'offset': 0})
        status, body = _util.get_json(f'{base}/upload?{query}')
        after = counts.stat().st_size
    assert status == 200, (status, body)
    assert body['total'] == ids * per_id, body
    assert len(body['items']) == 1, body

    stats = after - before
    # One per id directory to order them, one for the file being described.
    # The old shape was two per file across the whole namespace.
    assert stats <= ids + 4, (
        f'listing one upload cost {stats} stats over {ids * per_id} files')


def test_upload_pagination_is_validated_before_the_directory_is_looked_at(tmp):
    """The same query must get the same answer whether or not files exist.

    The missing-directory shortcut returned before limit and offset were
    parsed, so a malformed `limit` answered 200 on an empty data root and 400
    once any upload had created the directory — the validity of a request
    depended on unrelated filesystem state. The empty page also reported
    limit 0 and offset 0 rather than what was asked for.
    """
    with _util.bridge(tmp) as (base, _docroot):
        malformed = base + '/upload?' + urllib.parse.urlencode(
            {'token': TOK, 'limit': 'not-an-int'})
        status, body = _util.get_json(malformed)
        assert status == 400, (status, body)

        status, body = _util.get_json(base + '/upload?' + urllib.parse.urlencode(
            {'token': TOK, 'limit': 17, 'offset': 9, 'id': 'absent'}))
        assert status == 200, (status, body)
        assert body == {'items': [], 'total': 0, 'limit': 17, 'offset': 9}, body

        # The same two answers once the directory exists.
        status, _ = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'up1',
            'data': base64.b64encode(PNG).decode()})
        assert status == 200, status
        status, body = _util.get_json(malformed)
        assert status == 400, (status, body)


def test_a_screenshot_path_serves_the_file_that_result_named(tmp):
    """Fetching by id returns the newest file; a capture wants its own.

    Screenshot ids are reused — `_ss` is the default — so a second capture
    under the same id lands beside the first. A client that correlated its
    own result and then fetched by id downloaded whichever file was newest
    at that moment, which is the next invocation's whenever one overlapped.
    """
    with _util.bridge(tmp) as (base, docroot):
        for name, payload in (('capture-a.png', PNG + b'-A'),
                              ('capture-b.png', PNG + b'-B')):
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': '_ss', 'filename': name,
                'data': base64.b64encode(payload).decode()})
            assert status == 200, (status, body)
            assert body['path'] == f'{TOK}/_ss/{name}', body
        # Order the two by mtime explicitly: which one an id fetch picks is
        # the whole point, and a same-millisecond tie would decide it by
        # directory order instead.
        shot_dir = docroot / 'uploads' / TOK / '_ss'
        os.utime(shot_dir / 'capture-a.png', (1_700_000_000, 1_700_000_000))
        os.utime(shot_dir / 'capture-b.png', (1_700_000_100, 1_700_000_100))

        status, newest = _util.get(base + f'/screenshot?token={TOK}&id=_ss')
        assert status == 200 and newest == PNG + b'-B', (status, newest[:32])
        for name, payload in (('capture-a.png', PNG + b'-A'),
                              ('capture-b.png', PNG + b'-B')):
            named = urllib.parse.urlencode(
                {'token': TOK, 'path': f'{TOK}/_ss/{name}'})
            status, served = _util.get(f'{base}/screenshot?{named}')
            assert status == 200 and served == payload, (
                name, status, served[:32])


def test_a_screenshot_path_cannot_leave_its_own_token(tmp):
    """The named path is a component list, checked the way every other is."""
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'mine', 'filename': 'shot.png',
            'data': base64.b64encode(PNG).decode()})
        assert status == 200, (status, body)
        (docroot / 'uploads' / 'othertok' / 'theirs').mkdir(parents=True)
        (docroot / 'uploads' / 'othertok' / 'theirs' / 'shot.png').write_bytes(
            PNG + b'-THEIRS')
        (docroot / 'uploads' / TOK / 'mine' / 'notes.txt').write_bytes(b'text')

        for path, expected in (
                ('../othertok/theirs/shot.png', 400),
                (f'{TOK}/../othertok/theirs/shot.png', 400),
                ('othertok/theirs/shot.png', 404),
                (f'{TOK}/mine/notes.txt', 404),
                (f'{TOK}/mine/absent.png', 404)):
            query = urllib.parse.urlencode({'token': TOK, 'path': path})
            status, body = _util.get(f'{base}/screenshot?{query}')
            assert status == expected, (path, status, body[:120])


def test_every_accepted_screenshot_format_can_be_served_back(tmp):
    """A format the upload route accepts must be one /screenshot can return.

    `webp` was accepted, stored and answered 200, and then /screenshot said
    `no screenshot` because discovery listed three suffixes and the accepted
    set had four. The two lists are now one list, so they cannot drift again;
    this walks every accepted format rather than naming the one that was
    missing.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for index, fmt in enumerate(('png', 'jpeg', 'jpg', 'webp')):
            upload_id = f'shot{index}'
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': upload_id,
                'data': base64.b64encode(PNG).decode(), 'format': fmt})
            assert status == 200, (fmt, status, body)
            query = urllib.parse.urlencode({'token': TOK, 'id': upload_id})
            request = urllib.request.Request(base + '/screenshot?' + query)
            with urllib.request.urlopen(request, timeout=10) as reply:
                served = reply.read()
                content_type = reply.headers.get('Content-Type')
            assert served == PNG, (fmt, len(served))
            assert content_type and content_type.startswith('image/'), (
                fmt, content_type)


def test_upload_list_screenshot_delete(tmp):
    with _util.bridge(tmp) as (base, docroot):
        # Screenshot form: no filename, stored as <ts>.png
        payload = {'token': TOK, 'id': 'up1',
                   'data': base64.b64encode(PNG).decode()}
        status, body = _util.post_json(base + '/upload', payload)
        assert status == 200, (status, body)
        assert body['ok'] is True and body['size'] == len(PNG)
        assert body['path'].startswith(f'{TOK}/up1/') and body['path'].endswith('.png')
        stored = Path(docroot) / 'uploads' / body['path']
        assert stored.is_file() and stored.read_bytes() == PNG

        # Named-file form.
        text = b'hello upload'
        payload = {'token': TOK, 'id': 'up1', 'filename': 'note.txt',
                   'data': base64.b64encode(text).decode()}
        status, body = _util.post_json(base + '/upload', payload)
        assert status == 200, (status, body)
        assert (Path(docroot) / 'uploads' / TOK / 'up1' / 'note.txt').read_bytes() == text

        # Listing, bare-array back-compat form.
        status, body = _util.get_json(base + f'/upload?token={TOK}')
        assert status == 200 and isinstance(body, list) and len(body) == 2, body
        names = {e['filename'] for e in body}
        assert names == {'note.txt', stored.name}, names
        entry = next(e for e in body if e['filename'] == 'note.txt')
        assert entry['id'] == 'up1' and entry['size'] == len(text)
        assert entry['path'] == f'{TOK}/up1/note.txt'
        assert isinstance(entry['mtime'], int)

        # Listing filtered by id.
        status, body = _util.get_json(base + f'/upload?token={TOK}&id=up1')
        assert status == 200 and len(body) == 2, body
        status, body = _util.get_json(base + f'/upload?token={TOK}&id=missing')
        assert status == 200 and body == [], body

        # Paginated form returns the envelope.
        status, body = _util.get_json(base + f'/upload?token={TOK}&limit=1')
        assert status == 200, (status, body)
        assert body['total'] == 2 and body['limit'] == 1 and body['offset'] == 0
        assert len(body['items']) == 1
        status, body = _util.get_json(base + f'/upload?token={TOK}&limit=x')
        assert status == 400, status

        # Screenshot serving, per id and latest-across-ids.
        status, raw = _util.get(base + f'/screenshot?token={TOK}&id=up1')
        assert status == 200 and raw == PNG, (status, raw[:40])
        status, raw = _util.get(base + f'/screenshot?token={TOK}')
        assert status == 200 and raw == PNG, (status, raw[:40])

        # DELETE one file, then the id dir, then the token dir.
        status, body = _util.request(base + '/upload', 'DELETE',
                                     body={'token': TOK, 'id': 'up1',
                                           'filename': 'note.txt'})
        assert status == 200, (status, body)
        assert not (Path(docroot) / 'uploads' / TOK / 'up1' / 'note.txt').exists()
        status, body = _util.request(base + '/upload', 'DELETE',
                                     body={'token': TOK, 'id': 'up1'})
        assert status == 200, (status, body)
        assert not (Path(docroot) / 'uploads' / TOK / 'up1').exists()
        status, body = _util.get_json(base + f'/screenshot?token={TOK}')
        assert status == 404, (status, body)
        status, body = _util.request(base + '/upload', 'DELETE', body={'token': TOK})
        assert status == 200, (status, body)
        assert not (Path(docroot) / 'uploads' / TOK).exists()
        status, body = _util.request(base + '/upload', 'DELETE', body={'token': TOK})
        assert status == 404, (status, body)


def test_an_unhashable_upload_format_is_refused_not_dropped(tmp):
    """A format that is not a string must never reach a membership test.

    `[] in SCREENSHOT_TYPES` raises TypeError instead of answering False, and
    the exception killed the request thread — so an authenticated caller got a
    dropped connection where the same line already knew how to write a 400.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for value in ([], {}, ['png'], 5, None, True):
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': 'fmt', 'filename': 'shot.png',
                'data': base64.b64encode(PNG).decode(), 'format': value})
            assert status == 400, (value, status, body)
            assert body == {'error': 'unsupported format'}, (value, body)
        # The bridge is still answering: no request thread was lost.
        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_register_says_whether_it_actually_updated_a_tab(tmp):
    """Update-only means a tab the registry never had is a no-op, and says so.

    The route answered {'ok': True} either way, so a client whose tab had
    fallen out of the registry was told its entry had been refreshed. Nothing
    in the answer let it notice it should re-sync.
    """
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/register', {
            'token': TOK, 'tabId': '404', 'url': 'http://example.com/a',
            'title': 'a'})
        assert status == 200, (status, body)
        assert body == {'ok': True, 'updated': False}, body

        status, _ = _util.post_json(base + '/sync-tabs', {
            'token': TOK, 'tabs': [{'tabId': '404',
                                    'url': 'http://example.com/a',
                                    'title': 'a'}]})
        assert status == 200, status
        status, body = _util.post_json(base + '/register', {
            'token': TOK, 'tabId': '404', 'url': 'http://example.com/b',
            'title': 'b'})
        assert status == 200, (status, body)
        assert body == {'ok': True, 'updated': True}, body
        status, tabs = _util.get_json(base + f'/tabs?token={TOK}')
        assert [t['url'] for t in tabs] == ['http://example.com/b'], tabs


def test_upload_validation_and_traversal(tmp):
    with _util.bridge(tmp) as (base, docroot):
        docroot = Path(docroot)
        good = base64.b64encode(b'x').decode()
        # Missing parameters.
        status, body = _util.post_json(base + '/upload', {'token': TOK, 'data': good})
        assert status == 400 and body['error'] == 'missing id', (status, body)
        status, body = _util.post_json(base + '/upload', {'token': TOK, 'id': 'u'})
        assert status == 400 and body['error'] == 'missing data', (status, body)
        status, body = _util.post_json(base + '/upload',
                                       {'token': TOK, 'id': 'u', 'data': 'a'})
        assert status == 400 and body['error'] == 'invalid base64', (status, body)

        # Path components containing .., / or \ are refused before any write.
        escapes = [
            {'id': '../x', 'data': good},
            {'id': 'a\\b', 'data': good},
            {'id': 'a/b', 'data': good},
            {'id': 'u', 'data': good, 'filename': '../evil.txt'},
            {'id': 'u', 'data': good, 'filename': '..\\evil.txt'},
            {'id': 'u', 'data': good, 'filename': 'sub/f.txt'},
        ]
        for fields in escapes:
            status, body = _util.post_json(base + '/upload', {'token': TOK, **fields})
            assert status == 400, (fields, status, body)
            assert body['error'] == 'invalid path component', body
        # Token traversal is caught earlier, at dispatch.
        status, body = _util.post_json(base + '/upload',
                                       {'token': 'a/b', 'id': 'u', 'data': good})
        assert status == 400 and body['error'] == 'bad token', (status, body)

        # The point of the exercise: nothing was written, inside or outside the
        # docroot. tmp held only docroot/ before this test and must still.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        uploads = docroot / 'uploads'
        created = [str(p.relative_to(uploads)) for p in uploads.rglob('*')] \
            if uploads.is_dir() else []
        assert created == [], created


def test_upload_path_component_byte_boundaries(tmp):
    """Upload ids and filenames are capped by encoded bytes, not characters."""
    data = base64.b64encode(b'edge').decode()
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': 'i' * 256,
                               'filename': 'edge.bin', 'data': data})
        assert status == 400, (status, body)

        boundary_id = 'i' * 240
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': boundary_id,
                               'filename': 'edge.bin', 'data': data})
        assert status == 200, (status, body)
        assert (docroot / 'uploads' / TOK / boundary_id / 'edge.bin').is_file()

        boundary_filename = 'é' * 120
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': 'encoded-boundary',
                               'filename': boundary_filename, 'data': data})
        assert status == 200, (status, body)
        assert (docroot / 'uploads' / TOK / 'encoded-boundary'
                / boundary_filename).is_file()

        for fields in (
                {'id': 'i' * 241, 'filename': 'edge.bin'},
                {'id': 'encoded-over', 'filename': 'é' * 121}):
            status, body = _util.post_json(
                base + '/upload', {'token': TOK, **fields, 'data': data})
            assert status == 400, (fields, status, body)


def test_delete_upload_path_component_byte_boundaries(tmp):
    """Delete accepts the encoded ceiling and refuses longer components."""
    data = base64.b64encode(b'edge').decode()
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'id': 'i' * 256})
        assert status == 400, (status, body)

        boundary_id = 'i' * 240
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': boundary_id,
                               'filename': 'edge.bin', 'data': data})
        assert status == 200, (status, body)
        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'id': boundary_id})
        assert status == 200, (status, body)

        boundary_filename = 'f' * 240
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': 'delete-file',
                               'filename': boundary_filename, 'data': data})
        assert status == 200, (status, body)
        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'id': 'delete-file',
                  'filename': boundary_filename})
        assert status == 200, (status, body)

        for fields in (
                {'id': 'i' * 241},
                {'id': 'delete-file', 'filename': 'f' * 241}):
            status, body = _util.request(
                base + '/upload', 'DELETE', body={'token': TOK, **fields})
            assert status == 400, (fields, status, body)


def test_result_upload_delete_filesystem_errors_are_answered(tmp):
    """Residual result, upload, and delete OSError paths return HTTP status."""
    fault_dir = Path(tmp) / 'path-fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        'import shutil\n'
        '_real_write_bytes = pathlib.Path.write_bytes\n'
        'def _fail_storage_write(path, data):\n'
        '    if path.parent.name == "results":\n'
        '        raise OSError("injected result write failure")\n'
        '    if path.name == "fault.bin":\n'
        '        raise OSError("injected upload write failure")\n'
        '    return _real_write_bytes(path, data)\n'
        'pathlib.Path.write_bytes = _fail_storage_write\n'
        '_real_rmtree = shutil.rmtree\n'
        'def _fail_upload_delete(path, *args, **kwargs):\n'
        '    if pathlib.Path(path).name == "delete-fault":\n'
        '        raise OSError("injected upload delete failure")\n'
        '    return _real_rmtree(path, *args, **kwargs)\n'
        'shutil.rmtree = _fail_upload_delete\n',
        encoding='utf-8')
    data = base64.b64encode(b'edge').decode()
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, docroot):
        delete_dir = docroot / 'uploads' / TOK / 'delete-fault'
        delete_dir.mkdir(parents=True)
        (delete_dir / 'kept.bin').write_bytes(b'kept')

        calls = (
            lambda: _util.post_json(
                base + '/result',
                {'token': TOK, 'tabId': 'ordinary-tab', 'id': 'fault',
                 'result': 'x'}),
            lambda: _util.post_json(
                base + '/upload',
                {'token': TOK, 'id': 'ordinary-id', 'filename': 'fault.bin',
                 'data': data}),
            lambda: _util.request(
                base + '/upload', 'DELETE',
                body={'token': TOK, 'id': 'delete-fault'}),
        )
        statuses = []
        for call in calls:
            try:
                statuses.append(call()[0])
            except http.client.RemoteDisconnected:
                statuses.append('dropped')

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
        assert statuses == [500, 500, 500], statuses
        assert delete_dir.is_dir() and (delete_dir / 'kept.bin').is_file()


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


# Routes the bridge answers without the configured token, by design: the
# liveness probe, the dashboard assets, and the two page-facing segment
# routes, which carry a job-scoped capability instead. Everything else the
# handler routes is a control route and belongs in the matrix below.
_CAPABILITY_OR_PUBLIC_ROUTES = frozenset({
    '/health', '/dashboard', '/segment', '/segment-status',
})


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
        conn, response = _stream_response(base, wrong, 'extension')
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


def test_command_enqueue_and_dashboard_read_errors_are_answered(tmp):
    """Pre-response command and dashboard failures return storage errors."""
    fault_dir = Path(tmp) / 'boundary-read-write-faults'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_mkdir = pathlib.Path.mkdir\n'
        '_real_read_bytes = pathlib.Path.read_bytes\n'
        'def _fail_command_mkdir(path, *args, **kwargs):\n'
        '    if path.parent.name == "commands":\n'
        '        raise OSError("injected command enqueue failure")\n'
        '    return _real_mkdir(path, *args, **kwargs)\n'
        'def _fail_dashboard_read(path):\n'
        '    if path.parent.name == "dashboard":\n'
        '        raise OSError("injected dashboard read failure")\n'
        '    return _real_read_bytes(path)\n'
        'pathlib.Path.mkdir = _fail_command_mkdir\n'
        'pathlib.Path.read_bytes = _fail_dashboard_read\n',
        encoding='utf-8')
    with _util.bridge(
            tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, _docroot):
        try:
            command_status, command_raw = _put_command(
                base, {'token': TOK, 'id': 'fault', 'code': '1'})
        except http.client.RemoteDisconnected as exc:
            raise AssertionError('a command storage error ended PUT') from exc
        assert command_status == 500, (command_status, command_raw)
        assert json.loads(command_raw) == {'error': 'command storage failure'}

        try:
            dashboard_status, dashboard_raw = _util.get(base + '/dashboard')
        except http.client.RemoteDisconnected as exc:
            raise AssertionError('a dashboard read error ended GET') from exc
        assert dashboard_status == 500, (dashboard_status, dashboard_raw)
        assert json.loads(dashboard_raw) == {'error': 'dashboard storage failure'}

        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)
        status, body = _util.post_json(
            base + '/sync-tabs', {'token': TOK, 'tabs': []})
        assert status == 200 and body == {'ok': True, 'count': 0}, (
            status, body)


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
        assert not _queue_files(docroot, TOK)
        assert not list((Path(docroot) / 'results').iterdir())
        assert not list((Path(docroot) / 'segments').iterdir())
        kept = Path(docroot) / 'uploads' / TOK / 'duplicate-delete'
        assert kept.is_dir() and len(list(kept.iterdir())) == 1


def test_segment_authority_carriers_reject_every_duplicate_shape(tmp):
    """Segment authority never selects a job or sig from repeated carriers."""
    with _util.bridge(tmp) as (base, docroot):
        job = 'duplicate-scope'
        other_job = 'duplicate-other-scope'
        status, minted = _mint_job(base, TOK, job)
        assert status == 200, (status, minted)
        status, _other = _mint_job(base, TOK, other_job)
        assert status == 200, status
        sig = minted['sig']
        duplicate_values = (
            ('sig', sig, 'wrong'),
            ('sig', 'wrong', sig),
            ('sig', sig, sig),
            ('sig', '', sig),
            ('sig', sig, ''),
            ('job', job, other_job),
            ('job', other_job, job),
            ('job', job, job),
            ('job', '', job),
            ('job', job, ''),
        )
        replies = []
        for index, (carrier, first, second) in enumerate(duplicate_values):
            if carrier == 'sig':
                query = (f'job={job}&seg={index}&total=20&'
                         f'sig={first}&sig={second}')
            else:
                query = (f'job={first}&job={second}&seg={index}&total=20&'
                         f'sig={sig}')
            status, raw = _util.request(
                base + '/segment?' + query, 'POST', body=b'G',
                headers={'Content-Type': 'application/octet-stream'})
            body = json.loads(raw)
            replies.append(('POST /segment', carrier, first, second,
                            status, body.get('error')))

            if carrier == 'sig':
                query = f'job={job}&sig={first}&sig={second}'
            else:
                query = f'job={first}&job={second}&sig={sig}'
            status, body = _util.get_json(base + '/segment-status?' + query)
            replies.append(('GET /segment-status', carrier, first, second,
                            status, body.get('error')))

        assert all(status == 400 and error == f'duplicate {carrier}'
                   for _route, carrier, _first, _second, status, error
                   in replies), replies
        segment_files = list(
            (Path(docroot) / 'segments' / job).glob('*.ts'))
        segment_files += list(
            (Path(docroot) / 'segments' / other_job).glob('*.ts'))
        assert segment_files == [], segment_files


def test_segment_job_rejects_duplicate_job_before_minting(tmp):
    """Repeated body job keys never select an ownership or capability scope."""
    duplicates = (
        ('body-job-a', 'body-job-b'),
        ('body-job-c', 'body-job-c'),
        ('', 'body-job-d'),
        ('body-job-e', ''),
    )
    with _util.bridge(tmp) as (base, docroot):
        replies = []
        for first, second in duplicates:
            raw_body = (b'{"token":' + json.dumps(TOK).encode()
                        + b',"job":' + json.dumps(first).encode()
                        + b',"job":' + json.dumps(second).encode() + b'}')
            status, raw = _util.request(
                base + '/segment-job', 'POST', body=raw_body,
                headers={'Content-Type': 'application/json'})
            body = json.loads(raw)
            replies.append((first, second, status, body))

        assert all(status == 400 and body == {'error': 'duplicate job'}
                   for _first, _second, status, body in replies), replies
        assert list((Path(docroot) / 'segments').iterdir()) == []


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


def test_tabs_registry(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        tabs = [{'tabId': '11', 'url': 'https://example.com/a', 'title': 'A'},
                {'tabId': '22', 'url': 'https://example.com/b', 'title': 'B'}]
        status, body = _util.post_json(base + '/sync-tabs',
                                       {'token': TOK, 'tabs': tabs})
        assert status == 200 and body == {'ok': True, 'count': 2}, (status, body)

        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert status == 200 and len(body) == 2, body
        by_id = {t['tabId']: t for t in body}
        assert by_id['11']['url'] == 'https://example.com/a'
        assert by_id['22']['title'] == 'B'
        assert all(isinstance(t['age'], (int, float)) for t in body)

        # /register updates an existing tab...
        status, body = _util.post_json(
            base + '/register',
            {'token': TOK, 'tabId': '11', 'url': 'https://example.com/c',
             'title': 'C'})
        assert status == 200 and body == {'ok': True, 'updated': True}, (
            status, body)
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        by_id = {t['tabId']: t for t in body}
        assert by_id['11']['title'] == 'C' and len(body) == 2

        # ...but never creates one (sync-tabs is authoritative), and says so
        # rather than reporting the no-op as a refresh.
        status, body = _util.post_json(
            base + '/register',
            {'token': TOK, 'tabId': '33', 'url': 'https://example.com/d'})
        assert status == 200 and body == {'ok': True, 'updated': False}, (
            status, body)
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert len(body) == 2, body

        status, body = _util.post_json(base + '/register', {'token': TOK})
        assert status == 400 and body['error'] == 'missing tabId', (status, body)

        status, body = _util.post_json(base + '/unregister',
                                       {'token': TOK, 'tabId': '11'})
        assert status == 200 and body == {'ok': True, 'removed': True}, body
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert [t['tabId'] for t in body] == ['22'], body

        # sync-tabs replaces, it does not merge.
        status, _ = _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': []})
        assert status == 200, status
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert body == [], body


def test_poll_legacy_escape_hatch(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert status == 200 and body == {}, (status, body)

        # The documented raw-write escape hatch: a single legacy file.
        legacy = Path(docroot) / 'commands' / f'{TOK}.json'
        legacy.write_text(json.dumps({'id': 'legacy1', 'code': '1'}))
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert status == 200 and body['id'] == 'legacy1', (status, body)
        assert not legacy.exists()  # consumed

        legacy.write_text('{not json', encoding='utf-8')
        try:
            status, body = _util.post_json(base + '/poll', {'token': TOK})
        except http.client.RemoteDisconnected as exc:
            raise AssertionError('a malformed legacy command ended /poll') from exc
        assert status == 200 and body == {}, (status, body)
        assert legacy.exists(), 'the malformed legacy command was deleted'
        legacy.write_text(
            json.dumps({'id': 'legacy-after-partial', 'code': '2'}),
            encoding='utf-8')
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert status == 200 and body['id'] == 'legacy-after-partial', (
            status, body)
        assert not legacy.exists(), 'the complete legacy command was not consumed'

        # A dir-queue command (PUT /command) is NOT visible to legacy /poll.
        status, _ = _put_command(base, {'token': TOK, 'id': 'q1', 'code': '2'})
        assert status == 200, status
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert body == {}, body


def _seg_job():
    return 'tt-' + uuid.uuid4().hex[:12]


def _mint_job(base, token, job):
    """POST /segment-job and return (status, body)."""
    return _util.post_json(base + '/segment-job', {'token': token, 'job': job})


def _post_segment(base, job, sig, segment, payload=b'bytes', total='1'):
    return _util.request(
        base + f'/segment?job={job}&seg={segment}&total={total}&sig={sig}',
        'POST', body=payload,
        headers={'Content-Type': 'application/octet-stream'})


def test_looking_up_a_segment_job_creates_nothing(tmp):
    """Asking about a job must not be what brings it into existence.

    The capability /segment-status needs was only handed out by POST
    /segment-job, which mints on a name it has not seen — so a status query
    for a mistyped job created that job, left a permanent record beside the
    segments directory, and answered zero segments as though the name had
    been right.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        record = docroot / 'segments' / f'{job}.json'
        query = urllib.parse.urlencode({'token': TOK, 'job': job})
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 404 and body == {'error': 'no such job'}, (status, body)
        assert not record.exists(), 'the lookup created the job'

        status, minted = _mint_job(base, TOK, job)
        assert status == 200 and record.is_file(), (status, minted)
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 200 and body == {
            'ok': True, 'sig': minted['sig']}, (status, body)

        # A job someone else owns is a 409, not a silent re-mint.
        record.write_text(json.dumps({
            'token': 'someoneelse', 'sig': 'their-capability',
            'max_segment_index': 10, 'max_segment_count': 10,
            'max_bytes': 100}), encoding='utf-8')
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 409, (status, body)

    # The lookup is token-gated, so it answers a wrong token before it
    # answers anything about the job.
    with _util.bridge(tmp) as (base, _docroot):
        query = urllib.parse.urlencode({'token': 'wrong', 'job': 'anything'})
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 401 and body == {'error': 'unauthorized'}, (status, body)


def test_segment_job_mint_idempotent_and_owned(tmp):
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        status, body = _mint_job(base, TOK, job)
        assert status == 200 and body['ok'] is True and body['sig'], (status, body)
        sig = body['sig']
        # Idempotent for the owner: the same sig comes back, so resume works.
        status, again = _mint_job(base, TOK, job)
        assert status == 200 and again['sig'] == sig, (status, again)
        # A different request token cannot reach ownership checks.
        status, body = _mint_job(base, 'othertok', job)
        assert status == 401 and 'sig' not in body, (status, body)
        # A record owned by an earlier configured token remains protected after
        # token rotation, when the current configured token reaches the handler.
        foreign_job = _seg_job()
        segment_root = Path(docroot) / 'segments'
        (segment_root / foreign_job).mkdir()
        (segment_root / f'{foreign_job}.json').write_text(json.dumps({
            'token': 'earlierconfigured',
            'sig': 'persistedforeigncapability',
            'max_segment_index': 10,
            'max_segment_count': 10,
            'max_bytes': 100,
        }))
        status, body = _mint_job(base, TOK, foreign_job)
        assert status == 409 and 'sig' not in body, (status, body)
        # Validation: the job name, and the token check of the shared JSON
        # POST path (_bad_token runs before the handler).
        status, _ = _mint_job(base, TOK, 'a/b')
        assert status == 400, status
        status, _ = _mint_job(base, 'a/b', _seg_job())
        assert status == 400, status
        # The record sits beside the job's directory, both under the docroot.
        seg_dir = Path(docroot) / 'segments' / job
        assert seg_dir.is_dir(), os.listdir(Path(docroot) / 'segments')
        record = json.loads((Path(docroot) / 'segments' / f'{job}.json').read_text(encoding='utf-8'))
        assert record == {
            'token': TOK,
            'sig': sig,
            'max_segment_index': 99_999,
            'max_segment_count': 10_000,
            'max_bytes': 4 * 1024 * 1024 * 1024,
        }, record


def test_legacy_segment_job_migrates_with_existing_usage(tmp):
    """An owner re-mint upgrades a legacy record and counts stored segments."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '3',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        sig = 'legacy-capability'
        seg_root = Path(docroot) / 'segments'
        seg_dir = seg_root / job
        seg_dir.mkdir()
        (seg_dir / '000000.ts').write_bytes(b'abc')
        record_path = seg_root / f'{job}.json'
        legacy = {'token': TOK, 'sig': sig}
        record_path.write_text(json.dumps(legacy))

        status, body = _mint_job(base, 'othertok', job)
        assert status == 401 and json.loads(record_path.read_text(encoding='utf-8')) == legacy, (
            status, body)

        status, body = _mint_job(base, TOK, job)
        assert status == 200 and body['sig'] == sig, (status, body)
        assert json.loads(record_path.read_text(encoding='utf-8')) == {
            'token': TOK,
            'sig': sig,
            'max_segment_index': 10,
            'max_segment_count': 3,
            'max_bytes': 5,
        }

        status, body = _post_segment(base, job, sig, '1', payload=b'de')
        assert status == 200, (status, body)
        status, body = _post_segment(base, job, sig, '2', payload=b'x')
        assert status == 413, (status, body)
        assert sorted(path.read_bytes() for path in seg_dir.glob('*.ts')) == [
            b'abc', b'de']


def test_segment_index_is_bound_by_minted_job_quota(tmp):
    """A page cannot turn a small trusted job quota into a sparse huge index."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '1',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '16',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        status, minted = _mint_job(base, TOK, job)
        assert status == 200, (status, minted)
        sig = minted['sig']

        # Exact reviewer reproduction: the request claims total=1 but selects
        # segment 999999. Only the server-minted record is authoritative.
        status, body = _post_segment(base, job, sig, '999999', total='1')
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'seg out of range', body
        assert not list((Path(docroot) / 'segments' / job).glob('*.ts'))

        record = json.loads(
            (Path(docroot) / 'segments' / f'{job}.json').read_text(encoding='utf-8'))
        assert record['max_segment_index'] == 10, record
        assert record['max_segment_count'] == 1, record
        assert record['max_bytes'] == 16, record


def test_segment_count_is_bound_by_minted_job_quota(tmp):
    """Distinct files stop at the record's count even if request totals vary."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '100',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        for segment in ('0', '10'):
            status, body = _post_segment(
                base, job, sig, segment, payload=b'x', total='999999')
            assert status == 200, (segment, status, body)

        status, body = _post_segment(
            base, job, sig, '1', payload=b'x', total='999999')
        assert status == 413, (status, body)
        assert json.loads(body)['error'] == 'segment count limit exceeded', body
        stored = list((Path(docroot) / 'segments' / job).glob('*.ts'))
        assert len(stored) == 2, stored


def test_segment_bytes_are_bound_by_minted_job_quota(tmp):
    """Individually small bodies cannot cross the aggregate per-job byte cap."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '10',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = _post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 200, (status, body)

        status, body = _post_segment(base, job, sig, '1', payload=b'def')
        assert status == 413, (status, body)
        assert json.loads(body)['error'] == 'job byte limit exceeded', body
        seg_dir = Path(docroot) / 'segments' / job
        assert (seg_dir / '000000.ts').read_bytes() == b'abc'
        assert not (seg_dir / '000001.ts').exists()


def test_segment_replacement_reuses_count_and_byte_quota(tmp):
    """Replacing one index subtracts its old bytes and adds no file count."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '1',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = _post_segment(base, job, sig, '0', payload=b'abcde')
        assert status == 200, (status, body)
        status, body = _post_segment(base, job, sig, '0', payload=b'xy')
        assert status == 200, (status, body)
        seg_dir = Path(docroot) / 'segments' / job
        assert list(seg_dir.glob('*.ts')) == [seg_dir / '000000.ts']
        assert (seg_dir / '000000.ts').read_bytes() == b'xy'


def test_concurrent_segment_writes_share_one_quota_snapshot(tmp):
    """Two barrier-released requests cannot both spend the same byte budget."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        barrier = threading.Barrier(3)

        def post(segment):
            barrier.wait(timeout=5)
            return _post_segment(base, job, sig, segment, payload=b'abc')

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(post, segment) for segment in ('0', '1')]
            barrier.wait(timeout=5)
            replies = [future.result(timeout=10) for future in futures]

        assert sorted(status for status, _body in replies) == [200, 413], replies
        stored = list((Path(docroot) / 'segments' / job).glob('*.ts'))
        assert len(stored) == 1 and stored[0].read_bytes() == b'abc', stored


def test_segment_write_failure_removes_temp_and_answers(tmp):
    """A partial pathlib write gets a response and leaves no temp artifacts."""
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_write_bytes = pathlib.Path.write_bytes\n'
        'def _partial_segment_write(path, data):\n'
        '    if path.name.endswith(".ts.tmp"):\n'
        '        with path.open("wb") as stream:\n'
        '            stream.write(data[:2])\n'
        '        raise OSError("injected segment write failure")\n'
        '    return _real_write_bytes(path, data)\n'
        'pathlib.Path.write_bytes = _partial_segment_write\n',
        encoding='utf-8')
    env = {
        'PYTHONPATH': str(fault_dir),
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        statuses = []
        for segment in ('0', '1', '2'):
            try:
                status, _body = _post_segment(
                    base, job, sig, segment, payload=b'abc')
                statuses.append(status)
            except http.client.RemoteDisconnected:
                statuses.append('dropped')

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
        seg_dir = Path(docroot) / 'segments' / job
        residue = sorted(
            (path.name, path.read_bytes()) for path in seg_dir.glob('*.tmp'))
        assert statuses == [500, 500, 500] and not residue, (statuses, residue)


def test_segment_admission_cleans_stale_temp_artifacts(tmp):
    """A stale temp file is removed before another segment is admitted."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '1',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '3',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        seg_dir = Path(docroot) / 'segments' / job
        stale = seg_dir / '.000001.ts.tmp'
        stale.write_bytes(b'stale bytes outside finalized accounting')

        status, body = _post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 200, (status, body)
        assert not stale.exists()
        assert list(seg_dir.glob('*.tmp')) == []
        assert (seg_dir / '000000.ts').read_bytes() == b'abc'


def test_segment_post_and_status_require_capability(tmp):
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        seg_dir = Path(docroot) / 'segments' / job

        def post_seg(query):
            return _util.request(base + '/segment?' + query, 'POST', body=b'\x47',
                                 headers={'Content-Type': 'application/octet-stream'})

        # No sig and wrong sig: 403, and no file written.
        status, _ = post_seg(f'job={job}&seg=1&total=2')
        assert status == 403, status
        status, _ = post_seg(f'job={job}&seg=1&total=2&sig=wrong')
        assert status == 403, status
        # A sig minted for another job opens nothing here.
        _, other = _mint_job(base, TOK, _seg_job())
        status, _ = post_seg(f'job={job}&seg=1&total=2&sig={other["sig"]}')
        assert status == 403, status
        # The bridge token is not a capability either.
        status, _ = post_seg(f'job={job}&seg=1&total=2&sig={TOK}')
        assert status == 403, status
        assert list(seg_dir.rglob('*')) == [], list(seg_dir.iterdir())

        # Status answers the same way.
        status, _ = _util.get_json(base + f'/segment-status?job={job}')
        assert status == 403, status
        status, _ = _util.get_json(base + f'/segment-status?job={job}&sig=wrong')
        assert status == 403, status
        # An unknown job is indistinguishable from a wrong sig (no oracle).
        status, _ = _util.get_json(base + f'/segment-status?job={_seg_job()}&sig={sig}')
        assert status == 403, status


def test_a_bad_segment_capability_is_refused_before_the_body_arrives(tmp):
    """The sig is in the query string, so the body never has to be read.

    A 24 MiB post with a bad sig was buffered in full and then answered 403,
    moving the process high-water mark by the size of a body the bridge was
    always going to reject. Nothing about the answer depends on those bytes.

    The proof is the answer arriving while most of the declared body has not
    been sent: only the drain bound is written, and the refusal comes back
    without the remaining megabytes. Before the fix this request produced no
    answer at all until the socket deadline expired.
    """
    env = {'DAEDALUS_REQUEST_TIMEOUT': '5'}
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        declared = 8 * 1024 * 1024
        port = int(base.rsplit(':', 1)[1])
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        try:
            conn.putrequest(
                'POST', f'/segment?job={job}&seg=0&total=1&sig=notthesig')
            conn.putheader('Content-Type', 'application/octet-stream')
            conn.putheader('Content-Length', str(declared))
            conn.endheaders()
            # Exactly the bound the refusal drains, so the server reaches its
            # close without waiting on bytes this test deliberately withholds.
            conn.send(b'\x47' * 65536)
            response = conn.getresponse()
            status, payload = response.status, response.read()
        finally:
            conn.close()
        assert status == 403, (status, payload)
        assert json.loads(payload) == {'error': 'bad sig'}, payload
        seg_dir = Path(docroot) / 'segments' / job
        stored = sorted(seg_dir.glob('*.ts')) if seg_dir.is_dir() else []
        assert not stored, stored
        assert minted['sig'] != 'notthesig'


def test_a_segment_body_without_a_declared_length_is_refused(tmp):
    """An undeclared body is not an empty one.

    A missing Content-Length was read as zero, and this is the one route
    whose body is opaque bytes rather than JSON that has to parse: the
    sender's segment was discarded, an empty .ts was written in its place,
    and the answer was success.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        port = int(base.rsplit(':', 1)[1])
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        try:
            conn.putrequest(
                'POST', f'/segment?job={job}&seg=0&total=1&sig={sig}')
            conn.putheader('Content-Type', 'application/octet-stream')
            conn.endheaders()
            conn.send(b'\x47' * 188)
            response = conn.getresponse()
            status, payload = response.status, response.read()
        finally:
            conn.close()
        assert status == 411, (status, payload)
        seg_dir = Path(docroot) / 'segments' / job
        stored = sorted(seg_dir.glob('*.ts')) if seg_dir.is_dir() else []
        assert not stored, stored


def test_the_record_loader_answers_a_collision_and_a_corruption_apart(tmp):
    """Absent, name collision and corrupt are three answers, on every platform.

    The first version of this separation asked which exception the read
    raised, and that answer is per-platform: reading a directory raises
    IsADirectoryError on Linux and PermissionError on Windows, so the
    dotted-name collision — whose record path is another job's directory —
    stayed a clean 409 here and became a storage failure there. The question
    is about the path, so it is asked of the path.
    """
    probe = (
        'import os, server\n'
        'os.makedirs(server.SEG_DIR / "collide.json", exist_ok=True)\n'
        'print("collision:", server._load_segment_record("collide"))\n'
        '(server.SEG_DIR / "broken.json").write_text("{", encoding="utf-8")\n'
        'try:\n'
        '    server._load_segment_record("broken")\n'
        'except server._SegmentRecordError:\n'
        '    print("corrupt: raised")\n'
        'print("absent:", server._load_segment_record("nothing"))\n')
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(Path(tmp) / 'docroot'),
        'DAEDALUS_PORT': '0',
        'DAEDALUS_TOKEN': TOK,
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', probe], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    output = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == 0, output
    assert 'collision: None' in output, output
    assert 'corrupt: raised' in output, output
    assert 'absent: None' in output, output


def test_an_incomplete_body_never_holds_a_request_worker(tmp):
    """A declared body that stops mid-flight is answered, not waited on.

    One client per stalled body used to hold one request thread for as long
    as it kept the socket open, so an unauthenticated peer could grow the
    bridge's thread count by opening connections and sending a single byte.
    """
    with _util.bridge(
            tmp, env={'DAEDALUS_REQUEST_TIMEOUT': '2'}) as (base, _docroot):
        sock = _stalled_request(base)
        try:
            started = time.time()
            answer = _read_answer(sock, 30)
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
        held = [_stalled_request(base), _stalled_request(base)]
        refused = _stalled_request(base)
        try:
            # The cap is spent on the two held bodies, so this one is closed
            # without an answer rather than given a worker of its own.
            assert _read_answer(refused, 10) == b'', 'over-cap connection was served'
            # ... and the two under the cap are still being waited on.
            for sock in held:
                assert _read_answer(sock, 1) is None, 'a held body was answered early'
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


def test_a_corrupt_job_record_is_not_replaced_by_a_fresh_mint(tmp):
    """A record that cannot be read is not a job that does not exist.

    Both arrived as None, and the mint reads None as "not minted yet", so a
    truncated record was overwritten with a fresh owner and capability — the
    job's resume identity destroyed, and the caller told the mint succeeded.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        status, minted = _mint_job(base, TOK, job)
        assert status == 200, (status, minted)
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        corrupt = '{"token": "' + TOK + '", "sig": "'
        record_path.write_text(corrupt, encoding='utf-8')

        status, body = _mint_job(base, TOK, job)
        assert status == 500, (status, body)
        assert record_path.read_text(encoding='utf-8') == corrupt

        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_segment_resume_contract(tmp):
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        seg_dir = Path(docroot) / 'segments' / job

        def post_seg(n, payload):
            return _util.request(
                base + f'/segment?job={job}&seg={n}&total=4&sig={sig}', 'POST',
                body=payload,
                headers={'Content-Type': 'application/octet-stream'})

        for n in (0, 1, 3):
            status, body = post_seg(n, f'segment-{n}-bytes'.encode())
            assert status == 200, (n, status, body)
            assert json.loads(body) == {'ok': True}
        assert (seg_dir / '000000.ts').read_bytes() == b'segment-0-bytes'
        assert (seg_dir / '000003.ts').is_file()

        status, body = _util.get_json(base + f'/segment-status?job={job}&sig={sig}')
        assert status == 200, (status, body)
        assert body == {'done': [0, 1, 3], 'count': 3}, body

        # A re-post (resume retry) is idempotent: same status, new bytes.
        status, _ = post_seg(1, b'segment-1-RETRY')
        assert status == 200, status
        status, body = _util.get_json(base + f'/segment-status?job={job}&sig={sig}')
        assert body == {'done': [0, 1, 3], 'count': 3}, body
        assert (seg_dir / '000001.ts').read_bytes() == b'segment-1-RETRY'


def test_segment_rejection_writes_nothing(tmp):
    with _util.bridge(tmp) as (base, docroot):
        docroot = Path(docroot)

        def post(query):
            return _util.request(
                base + '/segment?' + query, 'POST', body=b'\x00',
                headers={'Content-Type': 'application/octet-stream'})

        # Missing parameters.
        status, body = post('seg=1&total=2')
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'missing job or seg'
        status, body = post(f'job={_seg_job()}')
        assert status == 400, status

        # Traversal in any of job / seg / total.
        for query in ('job=../x&seg=1&total=2',
                      'job=a/b&seg=1&total=2',
                      'job=a\\b&seg=1&total=2',
                      f'job={_seg_job()}&seg=..&total=2',
                      f'job={_seg_job()}&seg=1/2&total=2',
                      f'job={_seg_job()}&seg=1\\2&total=2',
                      f'job={_seg_job()}&seg=1&total=..'):
            status, body = post(query)
            assert status == 400, (query, status, body)
            assert json.loads(body)['error'] == 'invalid param', (query, body)

        # The mint endpoint applies the same component rules to the job name.
        status, _ = _mint_job(base, TOK, '../x')
        assert status == 400, status

        # The point of the exercise: nothing was written, inside or outside the
        # docroot. tmp held only docroot/ before this test and must still.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        segments = docroot / 'segments'
        created = [str(p.relative_to(segments)) for p in segments.rglob('*')] \
            if segments.is_dir() else []
        assert created == [], created


def test_segment_status_validation(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.get_json(base + '/segment-status?job=..')
        assert status == 400 and body['error'] == 'bad job', (status, body)
        status, _ = _util.get_json(base + '/segment-status?job=a/b')
        assert status == 400, status
        status, _ = _util.get_json(base + '/segment-status')
        assert status == 400, status
        # An unknown job has no capability to check against: same 403 as a
        # wrong sig, not a 404 that would reveal which jobs exist.
        status, _ = _util.get_json(base + '/segment-status?job=never-seen')
        assert status == 403, status
        # Minted job + its capability: 200 with an empty list.
        _, minted = _mint_job(base, TOK, 'never-seen')
        status, body = _util.get_json(
            base + f'/segment-status?job=never-seen&sig={minted["sig"]}')
        assert status == 200 and body == {'done': [], 'count': 0}, (status, body)


def test_segment_status_ignores_non_ascii_digit_filenames(tmp):
    """Only ASCII decimal segment stems are converted to result indices."""
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        seg_dir = Path(docroot) / 'segments' / job
        (seg_dir / '000001.ts').write_bytes(b'valid')
        (seg_dir / '\u00b2.ts').write_bytes(b'local artifact')

        try:
            status, body = _util.get_json(
                base + f'/segment-status?job={job}&sig={minted["sig"]}')
        except http.client.RemoteDisconnected as exc:
            raise AssertionError(
                'a non-ASCII digit filename ended /segment-status') from exc
        assert status == 200 and body == {'done': [1], 'count': 1}, (status, body)


def test_segment_status_enumeration_error_is_answered(tmp):
    """A job-directory enumeration failure returns a segment storage error."""
    fault_dir = Path(tmp) / 'segment-status-fault'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_iterdir = pathlib.Path.iterdir\n'
        'def _fail_segment_status_iterdir(path):\n'
        '    if path.parent.name == "segments" and path.name == "status-fault":\n'
        '        raise OSError("injected segment status failure")\n'
        '    return _real_iterdir(path)\n'
        'pathlib.Path.iterdir = _fail_segment_status_iterdir\n',
        encoding='utf-8')
    with _util.bridge(
            tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, _docroot):
        job = 'status-fault'
        status, minted = _mint_job(base, TOK, job)
        assert status == 200, (status, minted)

        try:
            status, body = _util.get_json(
                base + f'/segment-status?job={job}&sig={minted["sig"]}')
        except http.client.RemoteDisconnected as exc:
            raise AssertionError(
                'a segment status enumeration error ended GET') from exc
        assert status == 500, (status, body)
        assert body == {'error': 'segment storage failure'}, body

        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_segment_storage_never_touches_the_old_tmp_root(tmp):
    if os.name == 'nt':
        _util.skip('/tmp means something else on Windows')
    before = set(TMP_SEG_ROOT.iterdir()) if TMP_SEG_ROOT.is_dir() else set()
    with _util.bridge(tmp) as (base, docroot):
        job = _seg_job()
        _, minted = _mint_job(base, TOK, job)
        sig = minted['sig']
        status, _ = _util.request(
            base + f'/segment?job={job}&seg=1&total=1&sig={sig}', 'POST',
            body=b'bytes', headers={'Content-Type': 'application/octet-stream'})
        assert status == 200, status
        status, body = _util.get_json(base + f'/segment-status?job={job}&sig={sig}')
        assert status == 200 and body['count'] == 1, (status, body)
        # Everything landed under the bridge's own data root.
        assert (Path(docroot) / 'segments' / job / '000001.ts').read_bytes() == b'bytes'
    after = set(TMP_SEG_ROOT.iterdir()) if TMP_SEG_ROOT.is_dir() else set()
    assert after == before, f'the old world-shared root changed: {after - before}'


def test_request_body_cap_applies_to_every_body_reader(tmp):
    """Oversized POST, DELETE, and PUT bodies are refused before parsing."""
    segment_job = _seg_job()
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
    job = _seg_job()
    with _util.bridge(tmp, env={'DAEDALUS_MAX_BODY_SIZE': '4096'}) as (base, docroot):
        _, minted = _mint_job(base, TOK, job)
        over_cap = b'x' * 8192
        resp = _raw_request(
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
        resp = _raw_request(
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
            resp = _raw_request(
                base,
                (f'{method} {path} HTTP/1.0\r\nHost: x\r\n'
                 'Content-Type: application/json\r\n'
                 'Content-Length: notanumber\r\n\r\n{}').encode())
            assert resp.startswith(b'HTTP/1.0 400'), (
                f'{method} {path} did not answer 400: {resp[:80]!r}')
            assert json.loads(resp.split(b'\r\n\r\n', 1)[1]) == {
                'error': 'invalid Content-Length'
            }, resp


def test_a_malformed_absolute_form_target_is_refused_not_dropped(tmp):
    """`GET http://[ HTTP/1.1` makes urlparse raise ValueError in every verb
    that parses the target; uncaught, the request thread died and the client
    saw the connection close with zero response bytes. A garbled target is a
    client error — every parsing verb answers a deterministic 400 for it.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for method in ('GET', 'POST', 'PUT'):
            resp = _raw_request(
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


def test_sync_tabs_validates_the_list_and_every_member_before_mutation(tmp):
    """Wrong nested shapes receive 400 without clearing the existing registry."""
    wrong_tabs = (None, {}, 1, 'tabs', [1], [None], [[]], ['tab'])
    with _util.bridge(tmp) as (base, _docroot):
        replies = []
        for tabs in wrong_tabs:
            seed = {
                'token': TOK,
                'tabs': [{'tabId': 'kept', 'url': 'about:blank'}],
            }
            status, body = _util.post_json(base + '/sync-tabs', seed)
            assert status == 200 and body['count'] == 1, (status, body)
            try:
                status, body = _util.post_json(
                    base + '/sync-tabs', {'token': TOK, 'tabs': tabs})
                error = body.get('error')
            except http.client.RemoteDisconnected:
                status, error = 'dropped', None
            health_status, health = _util.get_json(base + '/health')
            tabs_status, stored = _util.get_json(base + f'/tabs?token={TOK}')
            replies.append((tabs, status, error, stored))
            assert health_status == 200 and health['ok'] is True, (
                tabs, health_status, health)
            assert tabs_status == 200, (tabs, tabs_status, stored)

        assert all(status == 400 and error == 'invalid tabs'
                   and [tab['tabId'] for tab in stored] == ['kept']
                   for _tabs, status, error, stored in replies), replies


def test_register_refuses_unhashable_tab_ids_and_stays_healthy(tmp):
    """JSON arrays and objects cannot reach the registry's dictionary lookup."""
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/sync-tabs', {
            'token': TOK,
            'tabs': [{'tabId': 'kept', 'url': 'about:blank'}],
        })
        assert status == 200 and body['count'] == 1, (status, body)

        replies = []
        for tab_id in ([1], {'x': 1}):
            try:
                status, body = _util.post_json(
                    base + '/register', {'token': TOK, 'tabId': tab_id})
                error = body.get('error')
            except http.client.RemoteDisconnected:
                status, error = 'dropped', None
            health_status, health = _util.get_json(base + '/health')
            tabs_status, stored = _util.get_json(base + f'/tabs?token={TOK}')
            replies.append((tab_id, status, error, stored))
            assert health_status == 200 and health['ok'] is True, (
                tab_id, health_status, health)
            assert tabs_status == 200, (tab_id, tabs_status, stored)

        assert all(status == 400 and error == 'invalid tabId'
                   and [tab['tabId'] for tab in stored] == ['kept']
                   for _tab_id, status, error, stored in replies), replies


def test_segment_job_dotted_name_collision_is_a_clean_409(tmp):
    """Job names may contain dots, so '<name>' and '<name>.json' collide on
    disk whichever is minted first: one side's record file is the other
    side's directory. The second mint must be a clean 409 that writes
    nothing — not an uncaught IsADirectoryError that drops the connection,
    orphans the tmp record and half-creates the job directory.
    """
    with _util.bridge(tmp) as (base, docroot):
        seg_root = Path(docroot) / 'segments'

        # Plain name first, then the dotted one.
        plain = 'zz-' + uuid.uuid4().hex[:8]
        status, _ = _mint_job(base, TOK, plain)
        assert status == 200, status
        status, body = _mint_job(base, TOK, plain + '.json')
        assert status == 409 and body['error'] == 'job name unavailable', (status, body)

        # Dotted name first, then the plain one — the ordering whose
        # os.replace targets a directory.
        dotted = 'zz-' + uuid.uuid4().hex[:8] + '.json'
        owner = dotted[:-len('.json')]
        status, _ = _mint_job(base, TOK, dotted)
        assert status == 200, status
        try:
            status, body = _mint_job(base, TOK, owner)
        except Exception as e:
            raise AssertionError(
                f'minting {owner!r} after {dotted!r} dropped the connection: '
                f'{type(e).__name__}: {e}') from e
        assert status == 409 and body['error'] == 'job name unavailable', (status, body)

        # Neither refused mint wrote anything: only the two successful jobs'
        # directories and records exist — no orphaned tmp file, no
        # half-created job directory.
        assert sorted(p.name for p in seg_root.iterdir()) == sorted(
            [plain, f'{plain}.json', dotted, f'{dotted}.json']), \
            sorted(p.name for p in seg_root.iterdir())


def test_delete_upload_validation(tmp):
    with _util.bridge(tmp) as (base, docroot):
        for fields in ({'id': '../x'}, {'id': 'u', 'filename': 'a\\b'},
                       {'id': 'u', 'filename': 'a/b'}):
            status, body = _util.request(base + '/upload', 'DELETE',
                                         body={'token': TOK, **fields})
            assert status == 400, (fields, status, body)
            assert json.loads(body)['error'] == 'invalid path component'
        status, body = _util.request(base + '/upload', 'DELETE',
                                     body={'token': TOK, 'id': 'ghost'})
        assert status == 404, (status, body)
        assert json.loads(body)['error'] == 'id not found'
        # Nothing was created by any of that.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        uploads = Path(docroot) / 'uploads'
        assert list(uploads.iterdir()) == [], list(uploads.iterdir())


def test_dashboard_static_serving(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        dash = _util.ROOT / 'dashboard'
        status, raw = _util.get(base + '/dashboard')
        assert status == 200, status
        assert raw == (dash / 'index.html').read_bytes()
        status, raw = _util.get(base + '/dashboard/style.css')
        assert status == 200, status
        assert raw == (dash / 'style.css').read_bytes()
        status, raw = _util.get(base + '/dashboard/app.js')
        assert status == 200, status
        assert raw == (dash / 'app.js').read_bytes()
        status, _ = _util.get(base + '/dashboard/no-such-asset.js')
        assert status == 404, status


def test_dashboard_responses_refuse_cross_origin_framing(tmp):
    """The token-bearing control surface must not be embeddable.

    /dashboard drives the browser and carries the bridge token, so a page
    that can frame it can overlay its controls. Neither frame-ancestors nor
    X-Frame-Options was sent on any dashboard response.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for path in ('/dashboard', '/dashboard/app.js'):
            request = urllib.request.Request(base + path)
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200, (path, response.status)
                headers = response.headers
            assert headers.get('X-Frame-Options') == 'DENY', (path, dict(headers))
            assert headers.get('Content-Security-Policy') == (
                "frame-ancestors 'none'"), (path, dict(headers))


def test_dashboard_refuses_traversal(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        server_py = (_util.ROOT / 'server.py').read_bytes()
        for path in ('/dashboard/../server.py',
                     '/dashboard/../../server.py',
                     '/dashboard/sub/../../server.py'):
            status, raw = _util.get(base + path)
            assert status == 400, (path, status)
            assert raw != server_py and b'BaseHTTPRequestHandler' not in raw
        # Percent-encoded dots never decode to a traversal either: refused or
        # simply absent, but never served.
        status, raw = _util.get(base + '/dashboard/%2e%2e/server.py')
        assert status in (400, 404), status
        assert b'BaseHTTPRequestHandler' not in raw


def test_a_browser_target_survives_routing_but_the_routing_fields_do_not(tmp):
    """`tabId` reaches the client; `token` and `tab` never do.

    Screenshot and CDP used to send the browser target as `tab`, the field the
    server strips for routing — so the target was deleted in transit and the
    extension fell back to the active tab. One sender was worse: it wrote the
    target over the routing value, so the command went to a queue nothing
    drains. This pins the separation both ways.
    """
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.request(
            base + '/command', 'PUT',
            body={'token': TOK, 'tab': 'extension', 'id': 'shot',
                  'type': 'screenshot', 'tabId': 42})
        assert status == 200, body
        queued = list((docroot / 'commands' / f'{TOK}_extension').glob('*.json'))
        assert len(queued) == 1, f'expected one queued command, got {queued}'
        cmd = json.loads(queued[0].read_text(encoding='utf-8'))
        assert cmd.get('tabId') == 42, f'the browser target was lost: {cmd}'
        assert 'tab' not in cmd, f'the routing tab leaked into the command: {cmd}'
        assert 'token' not in cmd, f'the token leaked into the command: {cmd}'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
