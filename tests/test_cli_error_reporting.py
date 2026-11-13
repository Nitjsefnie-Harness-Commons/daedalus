#!/usr/bin/env python3
"""The CLI's failure paths: one line, on one stream, never a traceback."""
import contextlib
import io
import subprocess
import sys
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


def test_segment_status_reports_a_200_that_is_no_answer_as_a_failure(tmp):
    """A 200 that is an HTML page, cut off or not an object is no answer."""
    del tmp
    for body, declared in ((b'<html><body>sign in</body></html>', None),
                           (b'<html><body>', 400), (b'[1,2]', None)):
        with html_front_end(body=body, declared=declared) as base:
            r = run_cli(['segment-status', 'somejob'],
                        cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (body, r.returncode, r.stdout)
        assert 'Connection failed' in r.stderr, (body, r.stderr)
        assert 'Traceback' not in r.stderr, (body, r.stderr)
        assert r.stdout == '', (body, r.stdout)


TABS = b'[{"tabId": 5, "age": 3, "title": "Interim Tab", "url": "u"}]'
ONE_OPENER = [sys.executable, '-c', (
    'import http.client as c\n'
    'from daedalus_cli.transport import api\n'
    'made = []\n'
    'real = c._create_https_context\n'
    'c._create_https_context = lambda *a: made.append(1) or real(*a)\n'
    'api("GET", "/x"); api("GET", "/x")\n'
    'print(len(made) <= 1)')]


def test_interim_responses_before_the_final_one_are_skipped(tmp):
    """A `103 Early Hints` before the final answer is skipped, not taken.

    Two interim statuses (103 alone reds); last row: one opener, two requests.
    """
    del tmp
    rows = (
        (CLI + ['tabs'], TABS, 'Interim Tab'),
        (CLI + ['segment-status', 'somejob'],
         b'{"sig": "s", "count": 0, "done": []}', 'Segments: 0\n'),
        (ONE_OPENER, b'[]', 'True\n'),
    )
    for argv, body, shown in rows:
        with html_front_end(body=body, interim=(103, 102)) as base:
            r = subprocess.run(
                argv, cwd=str(_util.ROOT), capture_output=True, text=True,
                env=cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK),
                encoding='utf-8', timeout=60)
        assert (r.returncode, r.stderr) == (0, ''), (argv, r)
        assert shown in r.stdout, (argv, r.stdout)


def test_interim_skipping_is_bounded(tmp):
    """A hundred interim responses are skipped, a hundred and one are not.

    The stand-in then answers, so an unbounded skip would succeed. `101`
    stays final, and a final error after interim ones is still a status.
    """
    del tmp
    bad_gateway = b'{"error": "bad gateway"}'
    rows = (
        ((102,) * 100, 200, TABS, 0, ''),
        ((102,) * 101, 200, TABS, 1,
         'Connection failed: got more than 100 interim responses\n'),
        ((101,), 200, TABS, 1, 'HTTP 101: \n'),
        ((103,), 502, bad_gateway, 1, "HTTP 502: {'error': 'bad gateway'}\n"),
    )
    for interim, status, body, code, stderr in rows:
        with html_front_end(status, body, interim=interim) as base:
            r = run_cli(['tabs'], cli_env(DAEDALUS_URL=base,
                                          DAEDALUS_TOKEN=TOK))
        assert (r.returncode, r.stderr) == (code, stderr), (len(interim), r)


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
    bridge_env = {'DAEDALUS_TOKEN': TOK, 'DAEDALUS_TOKEN_OVERRIDE': ''}
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


BAD_GATEWAY_PAGE = b'<html><body>502 upstream is not answering</body></html>'


def test_a_non_json_error_body_reaches_the_operator(tmp):
    """An error the bridge did not write must still arrive with its text.

    Every one of these paths read the body once to try JSON and then read it
    again for the fallback; the second read of a consumed response is empty,
    so the operator saw "HTTP 502:" and nothing else.
    """
    del tmp
    with html_front_end(502, BAD_GATEWAY_PAGE) as base:
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
    # No DAEDALUS_TOKEN_OVERRIDE and no DAEDALUS_TOKEN: required() must refuse
    # before any HTTP.
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL='http://127.0.0.1:1'))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'DAEDALUS_TOKEN is not set' in r.stderr, r.stderr


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
