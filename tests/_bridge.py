"""Shared fixtures for the bridge HTTP suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

Importing this configures the bridge credential for the importing process:
`_util.bridge()` hands its own environment to the child it starts, so the
token these suites authenticate with has to be in `os.environ` before the
first bridge exists, not passed per call.
"""
import http.client
import json
import os
import socket
import sys
import time
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
# one-off override so an ambient shell value cannot shadow these suites' token.
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK


def put_command(base, payload):
    return _util.request(base + '/command', 'PUT', body=payload)


def raw_request(base, request_bytes):
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


def stalled_request(base, body_bytes=b'{'):
    """Open one connection whose declared body never finishes arriving.

    Returns the connected socket, which the caller closes. Nothing is read
    here: what these tests measure is whether the bridge answers a body that
    stops mid-flight, so the read belongs in the test, with its own bound.

    The request authenticates in its header, because the property under test
    is what a stalled body does to a worker — and credentials are now settled
    before the body is read, so an unauthenticated request this size is
    refused without ever reaching the read that stalls. That refusal is its
    own test; this helper has to get past it to measure anything.
    """
    port = int(base.rsplit(':', 1)[1])
    sock = socket.create_connection(('127.0.0.1', port), timeout=30)
    try:
        sock.sendall(b'POST /result HTTP/1.0\r\n'
                     b'Content-Type: application/json\r\n'
                     b'Authorization: Bearer ' + TOK.encode('ascii') + b'\r\n'
                     b'Content-Length: 1000000\r\n\r\n' + body_bytes)
    except OSError:
        # A connection the bridge refuses is closed with these bytes still
        # unread, which is a reset rather than an EOF — and Windows delivers
        # it during the send rather than during the read that follows. The
        # refusal is what the caller measures, so hand the socket back and
        # let the read report it, the same way raw_request does.
        pass
    return sock


def read_answer(sock, timeout):
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


def queue_files(docroot, name):
    qdir = Path(docroot) / 'commands' / name
    if not qdir.is_dir():
        return []
    return sorted(p for p in qdir.iterdir() if p.suffix == '.json')


def stream_response(base, token, tab=None):
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


def read_stream_data(base, token, tab=None, timeout=30):
    """Read the first data payload from a real SSE stream.

    Bounded by the same monotonic deadline as next_stream_data, and for the
    same reason: the stream's keepalives arrive more often than the
    connection's socket timeout and reset it, so a read bounded only by the
    socket waits out a lost command for as long as the bridge stays healthy.
    The ceiling is generous — it bounds waiting for a frame the bridge may
    legitimately need time to deliver under load, never what is asserted
    about the frame.
    """
    conn, response = stream_response(base, token, tab)
    try:
        assert response.status == 200, response.status
        try:
            return next_stream_data(response, timeout=timeout)
        except AssertionError as failure:
            raise AssertionError(
                f'{failure}: waiting on the stream for '
                f'token={token!r} tab={tab!r}') from failure
    finally:
        response.close()
        conn.close()


def framer(response, served):
    """Read stream frames, attaching the bridge's own log to a timeout.

    #23 is an intermittent "no data frame arrived" across this family of
    tests, and the bridge's DELIVERED lines are the only direct evidence of
    whether its drain ever saw the file the reader is waiting for. One test
    captured them and one sighting was consequently diagnosable; the siblings
    failed with nothing but the timeout, which is how the fifth sighting
    arrived carrying no evidence at all. `served` is the list handed to
    `_util.bridge(output=...)`.
    """
    def frame(what, **kwargs):
        try:
            return next_stream_data(response, **kwargs)
        except AssertionError as failure:
            raise AssertionError(
                f'{what}: {failure}; the bridge said: '
                f'{"".join(served)!r}') from failure

    return frame


def next_stream_data(response, timeout=10):
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
        try:
            stream_socket.settimeout(original_timeout)
        except OSError:
            # http.client closed the socket once readline reached EOF, so
            # there is no timeout left to restore; this cleanup failure must
            # not displace the diagnosis raised above it.
            pass


def assert_oversize_stream_matches_enqueue(base):
    """Both sides of an impossible target reject it without killing the bridge."""
    token = '123e4567-e89b-12d3-a456-426614174000'
    tab = 't' * 240
    conn, response = stream_response(base, token, tab)
    try:
        stream_status = response.status
        stream_body = response.read() if stream_status != 200 else b''
    finally:
        response.close()
        conn.close()

    enqueue_status, enqueue_body = put_command(
        base, {'token': token, 'tab': tab, 'id': 'overflow', 'code': '1'})
    health_status, health_body = _util.get_json(base + '/health')
    assert (stream_status, enqueue_status, health_status) == (400, 400, 200), (
        stream_status, enqueue_status, health_status)
    assert json.loads(stream_body)['error'] == 'invalid path component', stream_body
    assert json.loads(enqueue_body)['error'] == 'invalid path component', enqueue_body
    assert health_body['ok'] is True, health_body
