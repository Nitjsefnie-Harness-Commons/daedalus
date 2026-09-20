"""A stub proxy that cuts a body off mid-read (issues 647 and 700)."""
import contextlib
import http.server
import json
import threading


class TruncatingFrontEndHandler(http.server.BaseHTTPRequestHandler):
    """Answers `truncate` GETs (None: all) with the fault, then properly.

    `body` None sends 5 of 40 declared bytes; bytes are sent whole, so a
    complete error body is one fault too. Every request is recorded.
    """

    truncate = 0
    status = 200
    body = None
    seen = []
    result = {'id': 'job4', 'deliveryId': 'd1', 'resultGeneration': 'g1',
              'result': 'Survived', 'error': None, 'ts': 1, 'world': 'cdp'}

    def _answer(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _fault(self):
        raw = b'{"pen' if self.body is None else self.body
        declared = 40 if self.body is None else len(raw)
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(declared))
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()

    def do_PUT(self):  # noqa: N802  (http.server's spelling)
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.seen.append(('PUT', self.path))
        self._answer({'ok': True, 'did': 'd1', 'target': 'tab=tab4'})

    def do_DELETE(self):  # noqa: N802
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.seen.append(('DELETE', self.path))
        self._fault()

    def do_GET(self):  # noqa: N802
        self.seen.append(('GET', self.path))
        cut = sum(1 for verb, _ in self.seen if verb == 'GET') - 1
        if self.truncate is None or cut < self.truncate:
            self._fault()
        elif 'consume=1' in self.path:
            self._answer({'consumed': True, 'resultGeneration': 'g1'})
        else:
            self._answer(self.result)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def truncating_front_end(truncate, status=200, body=None):
    TruncatingFrontEndHandler.truncate = truncate
    TruncatingFrontEndHandler.status = status
    TruncatingFrontEndHandler.body = body
    TruncatingFrontEndHandler.seen = []
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), TruncatingFrontEndHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
