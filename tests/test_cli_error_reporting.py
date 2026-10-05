#!/usr/bin/env python3
"""The CLI's failure paths: one line, on one stream, never a traceback.

Same fixture style as the other tests/test_cli_*.py suites: the CLI is always
run as a subprocess, the way a shell would run it, and the
proxy that fronts the bridge is a stub from tests/_frontend.py.
"""
import contextlib
import http.server
import io
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _cli_helpers import CLI, cli_env, run_cli  # noqa: E402
from _frontend import html_front_end  # noqa: E402
from _queueread import queued_command  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli.output import print_result  # noqa: E402

TOK = 'clitok'


# One malformed argument per handler module, in the shapes issue 673
# reproduced: each died in its handler with a Python traceback.
MALFORMED_PER_MODULE = (
    ['close-tab', 'x'],
    ['inject-css', '--css', 'a{color:red}', '--chrome-tab', 'x'],
    ['screenshot', '--chrome-tab', 'abc'],
)


def test_a_malformed_argument_refuses_before_anything_is_printed(tmp):
    del tmp
    for argv in MALFORMED_PER_MODULE:
        r = run_cli(argv, cli_env(DAEDALUS_TOKEN=TOK))
        assert r.returncode == 2, (argv, r.returncode, r.stdout, r.stderr)
        assert r.stdout == '', (argv, r.stdout)
        assert 'usage:' in r.stderr, (argv, r.stderr)
        assert 'Traceback' not in r.stderr, (argv, r.stderr)


def test_a_200_with_an_html_body_is_a_connection_failure(tmp):
    """A proxy's captive page is not an answer, but it is not a crash either.

    transport.api() JSON-decoded every 200 outside its ConnectionFailed
    handling, so a front end answering HTML where the bridge's JSON should
    be killed the command with a JSONDecodeError traceback instead of the
    Connection-failed line every other broken-transport shape gets.
    """
    del tmp
    with html_front_end() as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        commands = (
            ['tabs'],                               # api() GET
            ['result', '--raw'],                    # api() GET /result
            ['cookies'],                            # api() PUT /command
            ['uploads', '--delete', '--id', 'x'],   # api_delete()
        )
        for command in commands:
            r = run_cli(command, env)
            assert r.returncode != 0, (command, r.returncode, r.stdout)
            assert 'Connection failed' in r.stderr, (command, r.stderr)
            assert 'Traceback' not in r.stderr, (command, r.stderr)
            assert r.stdout == '', (command, r.stdout)


def test_segment_status_reports_an_html_body_as_a_connection_failure(tmp):
    """The segment-job lookup reads its own 200; it fails like a connection."""
    del tmp
    with html_front_end() as base:
        r = run_cli(['segment-status', 'somejob'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'Connection failed' in r.stderr, r.stderr
        assert 'Traceback' not in r.stderr, r.stderr
        assert r.stdout == '', r.stdout


def test_segment_status_survives_a_200_cut_off_mid_body(tmp):
    """A declared length the body does not keep is no answer, not a crash."""
    del tmp
    with html_front_end(body=b'<html><body>', declared=400) as base:
        r = run_cli(['segment-status', 'somejob'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'Connection failed' in r.stderr, r.stderr
        assert 'Traceback' not in r.stderr, r.stderr
        assert r.stdout == '', r.stdout


def test_segment_status_survives_a_200_whose_json_is_not_an_object(tmp):
    """`[1,2]` parses as JSON and then is not the object the sig is read
    from; that is still a front end's answer, not the bridge's."""
    del tmp
    with html_front_end(body=b'[1,2]') as base:
        r = run_cli(['segment-status', 'somejob'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'Connection failed' in r.stderr, r.stderr
        assert 'Traceback' not in r.stderr, r.stderr
        assert r.stdout == '', r.stdout


def test_an_error_result_reports_on_stderr_with_stdout_left_for_data(tmp):
    """stdout carries only result data; the ERROR diagnostic is stderr's."""
    del tmp
    stdout, stderr = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(stdout), \
            contextlib.redirect_stderr(stderr):
        try:
            print_result(
                {'id': 'job9', 'result': None, 'error': 'boom', 'ts': 1})
        except SystemExit as exit_request:
            code = exit_request.code
    assert code == 1, (code, stdout.getvalue(), stderr.getvalue())
    out, err = stdout.getvalue(), stderr.getvalue()
    assert 'ERROR: boom' in err, err
    assert 'job9' in err, err
    assert out == '', out


def test_cdp_sends_a_chrome_tab_zero_instead_of_dropping_it(tmp):
    """Zero is the tab id the parser typed, not an absence.

    The handler's truthy guard dropped it, so the command ran on the
    extension's active tab instead of the tab the operator named. Real
    Chrome tab ids are positive, so this pins the guard's shape rather
    than a reachable browser state.
    """
    bridge_env = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cdp', 'Page.enable', '--chrome-tab', '0'],
            cwd=str(_util.ROOT), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            queued = queued_command(qdir, 'the cdp command')
        finally:
            _drain.kill_and_drain(proc)
    assert queued.get('tabId') == 0, queued


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
    port = _util.free_port()
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL=f'http://127.0.0.1:{port}',
                                  DAEDALUS_TOKEN=TOK))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'Connection failed' in r.stderr, r.stderr


def test_missing_token_is_an_error(tmp):
    # No TOKEN and no DAEDALUS_TOKEN: required() must refuse before any HTTP.
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL='http://127.0.0.1:1'))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'DAEDALUS_TOKEN is not set' in r.stderr, r.stderr


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
