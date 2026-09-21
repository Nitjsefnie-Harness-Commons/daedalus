#!/usr/bin/env python3
"""The CLI's failure paths: one line, on one stream, never a traceback.

A sibling of tests/test_cli.py, which is size-frozen. Same fixture style:
the CLI is always run as a subprocess, the way a shell would run it, and the
proxy that fronts the bridge is a stub from tests/_frontend.py.
"""
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _frontend import html_front_end  # noqa: E402
from _queueread import queued_command  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli.output import print_result  # noqa: E402

# Keep bridge children off the fixed MCP port (see tests/_bridge.py).
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']
TOK = 'clitok'


def cli_env(**overrides):
    """A clean environment: none of the CLI's config vars leak in from ours."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    for k in ('TOKEN', 'ID', 'PYTHONIOENCODING'):
        env.pop(k, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(overrides)
    return env


def run_cli(args, env):
    return subprocess.run(CLI + args, cwd=str(_util.ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=60)


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
