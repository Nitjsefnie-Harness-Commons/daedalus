#!/usr/bin/env python3
"""Suite for daedalus_cli — what a command's wait admits and survives.

A sibling of tests/test_cli.py, which is size-frozen. Same fixture style:
the CLI is always run as a subprocess, the way a shell would run it, against
a real bridge() or against a stub front end that answers the way a proxy in
front of the bridge does.
"""
import contextlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# Keep bridge children off the fixed MCP port (see tests/_bridge.py).
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']

TOK = 'clitok'
# The bridge child inherits its token from here, the way test_cli.py's does.
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK


def cli_env(**overrides):
    """A clean environment: none of the CLI's config vars leak in from ours."""
    env = dict(os.environ)
    for k in ('DAEDALUS_URL', 'DAEDALUS_TOKEN', 'TOKEN', 'ID',
              'PYTHONIOENCODING'):
        env.pop(k, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(overrides)
    return env


def _run(argv, env):
    return subprocess.run(argv, cwd=str(_util.ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=60)


def run_cli(args, env):
    return _run(CLI + args, env)


def run_python(code, env):
    return _run([sys.executable, '-c', code], env)


class _TruncatingFrontEndHandler(http.server.BaseHTTPRequestHandler):
    """A proxy that cuts the body off mid-read, then answers properly.

    The bridge answers /result at once, so a reset or a truncated body on
    that read is the proxy's doing. The shape here is the one issue 647
    reports: the headers arrive whole and the body stops short of its
    declared length, which the client sees as IncompleteRead while it is
    reading the response — after urlopen has already returned.

    `truncate` is how many GETs are cut off before they are answered;
    None cuts every one. Every request is recorded so a test can say the
    PUT was not retried.
    """

    truncate = 0
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

    def _cut_off(self):
        # HTTP/1.0, the handler's default, closes the connection when the
        # handler returns; the client is left with 5 of the 40 bytes it
        # was promised.
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', '40')
        self.end_headers()
        self.wfile.write(b'{"pen')
        self.wfile.flush()

    def do_PUT(self):  # noqa: N802  (http.server's spelling)
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.seen.append(('PUT', self.path))
        self._answer({'ok': True, 'did': 'd1', 'target': 'tab=tab4'})

    def do_DELETE(self):  # noqa: N802
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.seen.append(('DELETE', self.path))
        self._cut_off()

    def do_GET(self):  # noqa: N802
        self.seen.append(('GET', self.path))
        cut = sum(1 for verb, _ in self.seen if verb == 'GET') - 1
        if self.truncate is None or cut < self.truncate:
            self._cut_off()
        elif 'consume=1' in self.path:
            self._answer({'consumed': True, 'resultGeneration': 'g1'})
        else:
            self._answer(self.result)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def _truncating_front_end(truncate):
    _TruncatingFrontEndHandler.truncate = truncate
    _TruncatingFrontEndHandler.seen = []
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), _TruncatingFrontEndHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_the_result_wait_outlives_a_truncated_peek(tmp):
    """A peek the proxy cuts off is retried until the deadline, once.

    api() caught only HTTPError and URLError, so a response cut off while
    its body was being read escaped as an IncompleteRead traceback — and
    the command had already been queued, so the browser ran it while the
    operator was reading a stack trace (issue 647). The wait now treats a
    connection failure on the peek like a pending slot: keep polling while
    the deadline has time left. The PUT is not idempotent and is never
    retried.
    """
    del tmp
    with _truncating_front_end(truncate=2) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab4')
        r = run_cli(['exec', 'job4', 'document.title', '-t', '10'], env)
        seen = list(_TruncatingFrontEndHandler.seen)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'Traceback' not in r.stderr, r.stderr
    assert 'Survived' in r.stdout, r.stdout
    puts = [path for verb, path in seen if verb == 'PUT']
    assert puts == ['/command'], seen
    peeks = [path for verb, path in seen
             if verb == 'GET' and 'consume=1' not in path]
    # Two cut-off peeks, then the one that was answered.
    assert peeks == ['/result?tab=tab4&delivery=d1'] * 3, seen


def test_the_result_wait_reports_a_timeout_when_every_peek_is_cut_off(tmp):
    """Retrying is bounded by the deadline, and ends the usual way."""
    del tmp
    with _truncating_front_end(truncate=None) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab4')
        r = run_cli(['exec', 'job4', 'document.title', '-t', '1'], env)
        seen = list(_TruncatingFrontEndHandler.seen)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'Traceback' not in r.stderr, r.stderr
    assert 'Timeout (1s)' in r.stderr, r.stderr
    puts = [path for verb, path in seen if verb == 'PUT']
    assert puts == ['/command'], seen
    peeks = [path for verb, path in seen if verb == 'GET']
    assert len(peeks) >= 2, seen


def test_a_truncated_answer_is_a_connection_failure_not_a_traceback(tmp):
    """api(), api_delete() and api_raw() all report it the one way.

    Each of them mapped a refused connection to `Connection failed:` and
    let a reset or a cut-off body while reading the answer escape as a
    stack trace. The same words for the same class of failure, whichever
    request met it.
    """
    del tmp
    with _truncating_front_end(truncate=None) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        outcomes = {
            'api': run_cli(['tabs'], env),
            'api_delete': run_cli(['uploads', '--delete', '--id', 'x'], env),
            'api_raw': run_python(
                'from daedalus_cli.transport import api_raw\n'
                'api_raw("GET", "/screenshot?path=x")\n', env),
        }
    for name, r in outcomes.items():
        assert r.returncode != 0, (name, r.returncode, r.stdout)
        assert 'Traceback' not in r.stderr, (name, r.stderr)
        assert 'Connection failed' in r.stderr, (name, r.stderr)
        assert 'IncompleteRead' in r.stderr, (name, r.stderr)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
