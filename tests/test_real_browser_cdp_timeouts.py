#!/usr/bin/env python3
"""Browser-free controls for which CDP failure becomes a named type.

A reader at 3am on `windows-latest` needs to know which of a call site's
failures the site classifies and which it must leave alone: the child's own
deadline and the outer one that bounds it are the same failure reached two
ways, a failure that is not a timeout is not one however it ended, and the
classification has to be a type of its own rather than a bare
`TimeoutExpired` naming the whole command. That is the property these
controls pin, and they are here rather than in
`tests/test_real_browser_harness.py` because that file sits at its own size
ceiling and this cluster was a quarter of it.
"""
import base64
import contextlib
import hashlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402
import _realbrowser_workers as _WORKERS  # noqa: E402


def test_cdp_response_timeout_has_distinct_assertion_subtype(tmp):
    del tmp
    timeout_exit_code = getattr(
        _realbrowser, 'CDP_TIMEOUT_EXIT_CODE', None)
    assert timeout_exit_code is not None, 'timeout exit code is unnamed'

    def timed_out(args, *, cwd, capture_output, text, timeout):
        assert len(args) == 7, args
        assert args[-1] == '10000', args
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        assert timeout == 15, timeout
        return types.SimpleNamespace(
            returncode=timeout_exit_code, stdout='',
            stderr='CDP response timed out\n')

    failure = None
    with mock.patch.object(_WORKERS.subprocess, 'run', timed_out):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except AssertionError as why:
            failure = why
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert timeout_type is not None, 'cdp_call has no distinct timeout type'
    assert issubclass(timeout_type, AssertionError), timeout_type
    assert failure.__class__ is timeout_type, failure.__class__
    assert 'CDP response timed out' in str(failure), failure


# Above the harness child's outer bound (deadline/1000 + 5) and level with
# the production probe budget; the join waits two quanta plus slack.
PEER_PATIENCE = 10


@contextlib.contextmanager
def _silent_websocket_peer():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(PEER_PATIENCE)
    port = listener.getsockname()[1]
    record = {'served': False, 'errors': []}

    def serve():
        try:
            connection, _address = listener.accept()
            with connection:
                connection.settimeout(PEER_PATIENCE)
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        raise AssertionError('WebSocket handshake ended early')
                    request += chunk
                key = next(
                    line.split(':', 1)[1].strip()
                    for line in request.decode('ascii').split('\r\n')
                    if line.lower().startswith('sec-websocket-key:'))
                accept = base64.b64encode(hashlib.sha1(
                    (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
                    .encode('ascii')).digest()).decode('ascii')
                connection.sendall((
                    'HTTP/1.1 101 Switching Protocols\r\n'
                    'Upgrade: websocket\r\n'
                    'Connection: Upgrade\r\n'
                    f'Sec-WebSocket-Accept: {accept}\r\n\r\n'
                ).encode('ascii'))
                record['served'] = True
                time.sleep(0.2)
        except Exception as why:  # noqa: BLE001
            record['errors'].append(why)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield f'ws://127.0.0.1:{port}', record
    finally:
        listener.close()
        thread.join(timeout=2 * PEER_PATIENCE + 1)
    assert not thread.is_alive(), 'silent WebSocket peer did not stop'


def test_real_cdp_harness_timeout_is_classified_by_exit_code(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the CDP harness control'
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert timeout_type is not None, 'cdp_call has no timeout type'

    records = []
    for _attempt in range(2):
        failure = None
        deadline = mock.patch.object(
            _WORKERS, 'CDP_RESPONSE_DEADLINE_MS', 50)
        with deadline, _silent_websocket_peer() as (target, record):
            records.append(record)
            try:
                _realbrowser.cdp_call(node, target, 'Runtime.evaluate', {})
            except AssertionError as why:
                failure = why
        if record['served']:
            assert not record['errors'], record
            break
    else:
        raise AssertionError(
            f'the silent peer never served the harness child: {records}')
    assert failure.__class__ is timeout_type, failure


def test_outer_subprocess_deadline_is_cdp_timeout(tmp):
    del tmp

    def outer_timeout(args, *, cwd, capture_output, text, timeout):
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        raise subprocess.TimeoutExpired(
            args, timeout, output='', stderr='outer deadline')

    failure = None
    with mock.patch.object(_WORKERS.subprocess, 'run', outer_timeout):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except Exception as why:  # noqa: BLE001
            failure = why
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert failure.__class__ is timeout_type, failure


def test_cdp_non_timeout_failure_stays_plain_assertion(tmp):
    del tmp

    def websocket_failed(args, *, cwd, capture_output, text, timeout):
        assert len(args) == 7, args
        assert args[-1] == '10000', args
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        assert timeout == 15, timeout
        return types.SimpleNamespace(
            returncode=1, stdout='', stderr='CDP websocket failed\n')

    failure = None
    with mock.patch.object(_WORKERS.subprocess, 'run', websocket_failed):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except AssertionError as why:
            failure = why
    assert failure.__class__ is AssertionError, failure.__class__
    assert 'CDP websocket failed' in str(failure), failure


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowsercdptimeouts_')


if __name__ == '__main__':
    raise SystemExit(main())
