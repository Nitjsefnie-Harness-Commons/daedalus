"""Serve and observe the loopback pages used by the browser fixture."""
import contextlib
import http.server
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _evalpages import (HOSTILE_EVAL_SCRIPT,  # noqa: E402
                        PERFORMANCE_POISON_EVAL_SCRIPT, PLAIN_EVAL_SCRIPT,
                        STRICT_CSP_EVAL_SCRIPT)


class _EvalPageServer(http.server.ThreadingHTTPServer):
    def __init__(self, address, handler):
        super().__init__(address, handler)
        self._request_lock = threading.Lock()
        self._request_paths = []

    def record_request(self, path):
        with self._request_lock:
            self._request_paths.append(path)

    def request_marker(self):
        with self._request_lock:
            return len(self._request_paths)

    def received_request_since(self, page_url, marker):
        path = urllib.parse.urlsplit(page_url).path
        with self._request_lock:
            return path in self._request_paths[marker:]


_FIXTURE_SERVERS = {}
_FIXTURE_SERVERS_LOCK = threading.Lock()


class _EvalPageHandler(http.server.BaseHTTPRequestHandler):
    """Serve the real-page evaluator fixtures over one loopback origin."""

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.server.record_request(path)
        pages = {
            '/hostile.html': (
                b'<title>loading</title><body>'
                b'<script src="/hostile.js"></script></body>',
                'text/html', None),
            '/hostile.js': (
                HOSTILE_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/strict.html': (
                b'<title>loading</title><body>'
                b'<script src="/strict.js"></script></body>',
                'text/html', "default-src 'self'; script-src 'self'"),
            '/strict.js': (
                STRICT_CSP_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/performance-poison.html': (
                b'<title>loading</title><body>'
                b'<script src="/performance-poison.js"></script></body>',
                'text/html', None),
            '/performance-poison.js': (
                PERFORMANCE_POISON_EVAL_SCRIPT.encode(),
                'text/javascript', None),
            '/plain.html': (
                b'<title>loading</title><body>'
                b'<script src="/plain.js"></script></body>',
                'text/html', None),
            '/plain.js': (
                PLAIN_EVAL_SCRIPT.encode(), 'text/javascript', None),
        }
        fixture = pages.get(path)
        if fixture is None:
            self.send_error(404)
            return
        body, content_type, csp = fixture
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if csp:
            self.send_header('Content-Security-Policy', csp)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def eval_page_server():
    server = _EvalPageServer(('127.0.0.1', 0), _EvalPageHandler)
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    with _FIXTURE_SERVERS_LOCK:
        _FIXTURE_SERVERS[origin] = server
    thread = threading.Thread(target=server.serve_forever)
    try:
        thread.start()
        yield origin
    finally:
        started = thread.ident is not None
        try:
            if started:
                server.shutdown()
        finally:
            try:
                server.server_close()
                if started:
                    thread.join(timeout=10)
            finally:
                with _FIXTURE_SERVERS_LOCK:
                    _FIXTURE_SERVERS.pop(origin, None)


def _fixture_request_marker(page_url):
    parts = urllib.parse.urlsplit(page_url)
    origin = f'{parts.scheme}://{parts.netloc}'
    with _FIXTURE_SERVERS_LOCK:
        server = _FIXTURE_SERVERS.get(origin)
    return None if server is None else (server, server.request_marker())


def _fixture_request_arrived(page_url, marker):
    if marker is None:
        return False
    server, request_index = marker
    parts = urllib.parse.urlsplit(page_url)
    origin = f'{parts.scheme}://{parts.netloc}'
    with _FIXTURE_SERVERS_LOCK:
        if _FIXTURE_SERVERS.get(origin) is not server:
            return False
    return server.received_request_since(page_url, request_index)
