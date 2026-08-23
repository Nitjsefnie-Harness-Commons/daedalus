#!/usr/bin/env python3
"""End-to-end suite for the real server.py bridge.

Every test drives the actual HTTP surface through _util.bridge(), except the
direct _log_safe unit test, which imports the module instead. Storage failure
tests inject OSError at filesystem boundaries; all requests still cross the
real HTTP, parsing, path-handling, and on-disk queue layers.
"""
import base64
import concurrent.futures
import http.client
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
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
    with _util.bridge(tmp, env=strict) as (base, docroot):
        conn, response = _stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            commands = os.fsencode(Path(docroot) / 'commands')
            # A legacy raw-write file whose own name carries the raw byte.
            with open(commands + b'/' + os.fsencode(TOK) + b'_\xfftab.json',
                      'wb') as handle:
                handle.write(b'{"id":"legacybad","code":"1"}')
            first = _next_stream_data(response)
            assert first.get('id') == 'legacybad', first
            # A queue directory whose name carries the raw byte.
            bad_dir = commands + b'/' + os.fsencode(TOK) + b'_\xffdir'
            os.mkdir(bad_dir)
            with open(bad_dir + b'/0000000000001_000001.json', 'wb') as handle:
                handle.write(b'{"id":"qbad","code":"1"}')
            second = _next_stream_data(response)
            assert second.get('id') == 'qbad', second
            status, _ = _put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            third = _next_stream_data(response)
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
        assert status == 200 and body == {'ok': True}, (status, body)
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        by_id = {t['tabId']: t for t in body}
        assert by_id['11']['title'] == 'C' and len(body) == 2

        # ...but never creates one (sync-tabs is authoritative).
        status, _ = _util.post_json(
            base + '/register',
            {'token': TOK, 'tabId': '33', 'url': 'https://example.com/d'})
        assert status == 200, status
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


def test_recursive_json_is_refused_on_every_body_verb_before_authentication(tmp):
    """Parser recursion is a 400 even when the caller lacks the bridge token."""
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

        assert all(status == 400 and error == 'JSON body too deeply nested'
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
