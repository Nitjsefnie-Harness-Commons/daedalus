#!/usr/bin/env python3
"""Issue 665: a failed header delivery must release the stream registration.

GET /stream registered the stream before its response headers were written,
and the try/finally that unregisters began only after end_headers(). A peer
that was already gone raised out of the flush, outside that try, and the
registration leaked: a tabless stream's until process exit, /health
overcounting by one per such failure.
"""
import io
import os
import sys
from contextlib import contextmanager
from http.client import parse_headers
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TOKEN = 'registration-test'


class _DeadPeer:
    """A wfile standing for a peer that left before the headers landed."""

    def write(self, _data):
        raise BrokenPipeError('the peer is gone')

    def flush(self):
        raise BrokenPipeError('the peer is gone')


@contextmanager
def _server(tmp, name):
    """The real Handler under a private configuration window.

    The window must cover the requests too, not just the import: the
    bridge token is resolved from the environment at request time, and an
    inherited value would be answered 401 before anything registers.
    """
    root = Path(tmp) / name
    root.mkdir(parents=True)
    saved = {key: os.environ[key] for key in os.environ
             if key.startswith('DAEDALUS_') or key == 'TOKEN'}
    for key in saved:
        del os.environ[key]
    os.environ.update({
        'DAEDALUS_DIR': str(root),
        'DAEDALUS_PORT': '0',
        'DAEDALUS_TOKEN': TOKEN,
    })
    try:
        yield _util.load(_util.ROOT / 'server.py', name=name)
    finally:
        os.environ.update(saved)
        for key in ('DAEDALUS_DIR', 'DAEDALUS_PORT', 'DAEDALUS_TOKEN'):
            if key not in saved:
                del os.environ[key]


def _request_stream(server, tab):
    """The BrokenPipeError the flush raises propagates out of do_GET whether
    or not the registration is released; what the handler leaves in the
    registry afterwards is the contract, read through the service's own
    public snapshot.
    """
    handler_path = f'/stream?token={TOKEN}' + (f'&tab={tab}' if tab else '')
    handler = server.Handler.__new__(server.Handler)
    handler.requestline = f'GET {handler_path} HTTP/1.1'
    handler.request_version = 'HTTP/1.1'
    handler.path = handler_path
    handler.headers = parse_headers(io.BytesIO(b'\r\n'))
    handler.client_address = ('127.0.0.1', 0)
    handler.wfile = _DeadPeer()

    try:
        handler.do_GET()
    except BrokenPipeError:
        pass  # the injected dead peer, not the assertion under test


def test_a_failed_header_delivery_releases_a_named_stream(tmp):
    with _server(tmp, 'named') as server:
        _request_stream(server, 'chrome1')

        assert server.stream_service.snapshot() == (0, []), (
            'the registration outlived the failed header delivery')


def test_a_failed_header_delivery_releases_a_tabless_stream(tmp):
    with _server(tmp, 'tabless') as server:
        _request_stream(server, '')

        assert server.stream_service.snapshot() == (0, []), (
            'the tabless registration outlived the failed delivery')


if __name__ == '__main__':
    raise SystemExit(_util.runner(
        _util.collect(globals()), tmp_prefix='streamregistration_'))
