#!/usr/bin/env python3
"""Suite for daedalus_cli — how transport failures reach the operator.

A proxy that never reached the bridge, a refused connect, and a poll
that stalls: each renders as the CLI's own sentence on stderr, and the
wait's deadline bounds the whole wait rather than each poll. The CLI is
always run as a subprocess, the way a shell would run it.
"""
import contextlib
import http.server
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import TOK, cli_env, run_cli  # noqa: E402


def run_python(code, env, timeout=60):
    return subprocess.run([sys.executable, '-c', code], cwd=str(_util.ROOT),
                          env=env, capture_output=True, text=True,
                          encoding='utf-8', timeout=timeout)


class _RefusingFrontEndHandler(http.server.BaseHTTPRequestHandler):
    """Answer every request the way a proxy that never reached the bridge does.

    The body is HTML rather than JSON on purpose: that is the shape a front
    end in front of the bridge produces, and the CLI has to carry its text
    through rather than reporting a status with nothing after it.
    """

    BODY = b'<html><body>502 upstream is not answering</body></html>'

    def _refuse(self):
        declared = self.headers.get('Content-Length')
        if declared:
            self.rfile.read(int(declared))
        self.send_response(502)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(self.BODY)))
        self.end_headers()
        self.wfile.write(self.BODY)

    do_GET = _refuse
    do_PUT = _refuse
    do_POST = _refuse
    do_DELETE = _refuse

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def _refusing_front_end():
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), _RefusingFrontEndHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_a_non_json_error_body_reaches_the_operator(tmp):
    """An error the bridge did not write must still arrive with its text.

    Every one of these paths read the body once to try JSON and then read it
    again for the fallback; the second read of a consumed response is empty,
    so the operator saw "HTTP 502:" and nothing else.
    """
    del tmp
    with _refusing_front_end() as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        commands = (
            ['tabs'],                               # api
            ['uploads', '--delete', '--id', 'x'],   # api_delete
            ['segment-status', 'somejob'],          # the segment-job mint
        )
        for command in commands:
            r = run_cli(command, env)
            assert r.returncode != 0, (command, r.returncode, r.stdout)
            assert 'HTTP 502' in r.stderr, (command, r.stderr)
            assert 'upstream is not answering' in r.stderr, (command, r.stderr)


def test_connection_failure_is_a_clean_error(tmp):
    port = _util.free_port()  # nothing listens here
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL=f'http://127.0.0.1:{port}',
                                  DAEDALUS_TOKEN=TOK))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'Connection failed' in r.stderr, r.stderr


def test_a_stalled_poll_cannot_outlast_the_requested_timeout(tmp):
    """The timeout bounds the whole wait, not just the top of each lap.

    The loop checked the clock before each iteration and then handed every
    HTTP call its own fixed 30s, so one stalled poll ran far past what the
    caller asked for — a 50ms wait returned after 320ms against a single
    300ms stall.
    """
    del tmp
    code = (
        'import time\n'
        'from daedalus_cli import transport\n'
        'seen = []\n'
        'def fake_api(method, path, body=None, timeout=None, headers=None):\n'
        '    seen.append(timeout)\n'
        '    time.sleep(0.3)\n'       # the stall
        '    return {"pending": True}\n'
        'transport._request = fake_api\n'
        'start = time.monotonic()\n'
        'res = transport.wait_for_result("c1", "extension", "d1", 0.5)\n'
        'print("ELAPSED", round(time.monotonic() - start, 3))\n'
        'print("RESULT", res)\n'
        'print("FIRST_TIMEOUT", seen[0] if seen else None)\n')
    r = run_python(code, cli_env(DAEDALUS_TOKEN=TOK))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    fields = dict(
        line.split(' ', 1) for line in r.stdout.splitlines() if ' ' in line)
    assert fields['RESULT'] == 'None', r.stdout
    # Two correct outcomes, and which one appears depends on how fast the
    # interpreter got here: either no poll was started at all, because the
    # deadline went before the first sleep returned — the loop refusing a
    # poll it has no budget for — or exactly one was started and handed the
    # REMAINING budget rather than 30 seconds. What must not happen is a poll
    # carrying a timeout larger than the wait it belongs to.
    if fields['FIRST_TIMEOUT'] != 'None':
        first = float(fields['FIRST_TIMEOUT'])
        assert 0 < first <= 0.5, r.stdout
    # One stall of 0.3s can be absorbed; a second would mean the loop kept
    # polling past its deadline. A generous ceiling, because this asserts the
    # absence of a 30-second poll, not the scheduler's precision.
    assert float(fields['ELAPSED']) < 3.0, r.stdout


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
