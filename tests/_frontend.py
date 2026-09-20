"""A stub front end that answers the way a proxy in front of the bridge does.

The bridge answers /result at once, so a reset or a truncated body on that
read is the proxy's doing. The shape here is the one issue 647 reports: the
headers arrive whole and the body stops short of its declared length, which
the client sees as IncompleteRead while it is reading the response — after
urlopen has already returned. Issue 700 is the same cut with an error
status, where the read happens inside the HTTPError handler instead.
"""
import contextlib
import http.server
import json
import threading


class TruncatingFrontEndHandler(http.server.BaseHTTPRequestHandler):
    """Answers `truncate` GETs with the fault, then answers properly.

    None faults every one. `status` is the fault's status line. `body`
    None sends 5 of 40 declared bytes; bytes are sent whole, so the same
    front end can answer a complete error body. Every request is recorded
    so a test can say the PUT was not retried.
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
        # HTTP/1.0, the handler's default, closes the connection when the
        # handler returns; a cut-off client is left with 5 of the 40 bytes
        # it was promised.
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
