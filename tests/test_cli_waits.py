#!/usr/bin/env python3
"""Suite for daedalus_cli — what a command's wait admits and survives.

A sibling of tests/test_cli.py, which is size-frozen. Same fixture style:
the CLI is always run as a subprocess, the way a shell would run it, against
a real bridge() or against a stub front end that answers the way a proxy in
front of the bridge does.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _cmdqueue import clear_command_queue  # noqa: E402
from _frontend import (  # noqa: E402
    TruncatingFrontEndHandler, truncating_front_end)
from _queueread import queued_command  # noqa: E402

# Keep bridge children off the fixed MCP port (see tests/_bridge.py).
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']

# The result header's marker is whichever glyph the console can encode, so
# what is pinned is that a marker immediately precedes the id (see
# tests/test_cli.py).
IN_MARKS = ('←', '<-')
TOK = 'clitok'
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}


def cli_env(**overrides):
    """A clean environment: none of the CLI's config vars leak in from ours."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    for k in ('TOKEN', 'ID', 'PYTHONIOENCODING'):
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


def _answer_ext(base, docroot, argv, env, result):
    """Run one typed subcommand and answer the command it enqueues.

    Returns (returncode, stdout, stderr, the payload the bridge received).
    """
    qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
    survivors = clear_command_queue(qdir)
    proc = subprocess.Popen(
        CLI + argv, cwd=str(_util.ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8')
    try:
        queued = queued_command(
            qdir, f'the command {argv[0]} enqueues', exclude=survivors)
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': queued['id'],
            'result': result, 'error': None, 'ts': 1,
            '_did': queued['_did']})
        assert status == 200, status
        out, err = proc.communicate(timeout=60)
    finally:
        _drain.kill_and_drain(proc)
    return proc.returncode, out, err, queued


def test_store_hotfix_sends_permanent_only_when_it_was_asked_for(tmp):
    """Re-storing a hotfix without --permanent keeps its stored flag.

    The extension preserves an existing fix's flag only when the command
    carries no `permanent` field at all. The CLI sent `args.permanent`,
    which argparse always makes a bool, so every update of a permanent
    hotfix that did not restate --permanent silently demoted it to
    version-gated.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        stored = {'stored': 'fx', 'total': 1, 'permanent': True}
        code, out, err, queued = _answer_ext(
            base, docroot, ['store-hotfix', 'fx', '--code', '1'], env,
            stored)
        assert code == 0, (code, out, err)
        assert queued['type'] == 'store-hotfix', queued
        assert queued['fixId'] == 'fx' and queued['code'] == '1', queued
        assert 'permanent' not in queued, queued
        assert '[PERM]' in out, out
        code, out, err, queued = _answer_ext(
            base, docroot,
            ['store-hotfix', 'fx', '--code', '1', '--permanent'], env,
            stored)
        assert code == 0, (code, out, err)
        assert queued.get('permanent') is True, queued


def _answer_eval(base, docroot, argv, env, tab, result):
    """Run one eval subcommand and play the extension over HTTP.

    Returns (returncode, stdout, stderr). The answer is posted as soon as
    the command is queued, so a CLI that gives up before that is one that
    gave up before the browser could possibly have answered.
    """
    # Nothing drains this queue — there is no extension here — so an
    # earlier case's entry may remain; the entry answered must be this one.
    qdir = Path(docroot) / 'commands' / f'{TOK}_{tab}'
    survivors = clear_command_queue(qdir)
    proc = subprocess.Popen(
        CLI + argv, cwd=str(_util.ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8')
    try:
        queued = queued_command(
            qdir, 'the enqueued command file', exclude=survivors)
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': queued['id'],
            'result': result, 'error': None, 'ts': 1,
            'world': 'page:cdp', '_did': queued['_did']})
        assert status == 200, status
        out, err = proc.communicate(timeout=60)
    finally:
        _drain.kill_and_drain(proc)
    return proc.returncode, out, err


def test_a_zero_timeout_on_exec_and_put_reads_as_the_default(tmp):
    """`-t 0` waits the documented default; it does not time out at once.

    positive_timeout admits 0 on the promise that every call site reads it
    as unset, the way screenshot and cookies do. put and exec handed it
    straight to the waiter, whose deadline was now plus nothing, so the CLI
    reported `Timeout (0s)` for a command the bridge had already queued and
    the browser went on to run — and a retry ran the side effect twice.
    """
    source = Path(tmp) / 'job.js'
    source.write_text('document.title', encoding='utf-8')
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab0')
        cases = (
            ['exec', 'job0', 'document.title', '-t', '0'],
            ['put', 'job1', str(source), '--timeout', '0'],
        )
        for argv in cases:
            code, out, err = _answer_eval(
                base, docroot, argv, env, 'tab0', 'Zero Title')
            assert code == 0, (argv, code, out, err)
            assert 'Timeout' not in err, (argv, err)
            assert any(f'{m} {argv[1]}' in out for m in IN_MARKS), (argv, out)
            assert 'Zero Title' in out, (argv, out)


_RECORD_WAITER_TIMEOUTS = """
import argparse, json, sys
from daedalus_cli import commands_eval
handed = []
commands_eval.send_and_wait = (
    lambda cmd_id, code, target_tab, wait, timeout:
    handed.append([cmd_id, timeout]))
for timeout in (0, 7):
    commands_eval.do_exec(argparse.Namespace(
        id='ex', code='1', broadcast=True, no_result=False,
        timeout=timeout))
    commands_eval.do_put(argparse.Namespace(
        id='pt', file=sys.argv[1], broadcast=True, no_result=False,
        timeout=timeout))
print(json.dumps(handed))
"""


def test_exec_and_put_hand_the_waiter_exactly_fifteen_seconds_for_zero(
        tmp):
    """Zero means 15, the parser's documented default, and only zero does.

    The bridge test above cannot tell 15 from any other positive fallback;
    this one records what the two handlers hand send_and_wait and pins the
    number, and that an explicit positive timeout travels unchanged. A
    child with the repository root as its working directory, so the
    package under test is this tree's and not an installed copy.
    """
    source = Path(tmp) / 'job.js'
    source.write_text('document.title', encoding='utf-8')
    run = _run([sys.executable, '-c', _RECORD_WAITER_TIMEOUTS, str(source)],
               cli_env(DAEDALUS_TOKEN=TOK))
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    handed = json.loads(run.stdout.strip().splitlines()[-1])
    assert handed == [['ex', 15], ['pt', 15], ['ex', 7], ['pt', 7]], handed


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
    with truncating_front_end(truncate=2) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab4')
        r = run_cli(['exec', 'job4', 'document.title', '-t', '10'], env)
        seen = list(TruncatingFrontEndHandler.seen)
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
    with truncating_front_end(truncate=None) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab4')
        r = run_cli(['exec', 'job4', 'document.title', '-t', '1'], env)
        seen = list(TruncatingFrontEndHandler.seen)
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
    with truncating_front_end(truncate=None) as base:
        outcomes = _every_api_entry(base)
    _assert_connection_failed(outcomes, 'IncompleteRead')


def _every_api_entry(base):
    """One subprocess per public request entry point, against `base`."""
    env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
    return {
        'api': run_cli(['tabs'], env),
        'api_delete': run_cli(['uploads', '--delete', '--id', 'x'], env),
        'api_raw': run_python(
            'from daedalus_cli.transport import api_raw\n'
            'api_raw("GET", "/screenshot?path=x")\n', env),
    }


def _assert_connection_failed(outcomes, cause):
    for name, r in outcomes.items():
        assert r.returncode != 0, (name, r.returncode, r.stdout)
        assert 'Traceback' not in r.stderr, (name, r.stderr)
        assert 'Connection failed' in r.stderr, (name, r.stderr)
        assert cause in r.stderr, (name, r.stderr)


def test_the_result_wait_outlives_a_truncated_error_peek(tmp):
    """A 502 cut off mid-body is the same failed peek as a 200 cut off.

    The HTTPError handler read the body to report `HTTP 502: <detail>`,
    and that read is where the truncation surfaces — so IncompleteRead
    was raised inside the handler and escaped the wait as a traceback
    (issue 700), for a command the browser was already running.
    """
    del tmp
    with truncating_front_end(truncate=2, status=502) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab4')
        r = run_cli(['exec', 'job4', 'document.title', '-t', '10'], env)
        seen = list(TruncatingFrontEndHandler.seen)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'Traceback' not in r.stderr, r.stderr
    assert 'Survived' in r.stdout, r.stdout
    puts = [path for verb, path in seen if verb == 'PUT']
    assert puts == ['/command'], seen
    peeks = [path for verb, path in seen
             if verb == 'GET' and 'consume=1' not in path]
    assert peeks == ['/result?tab=tab4&delivery=d1'] * 3, seen


def test_the_result_wait_reports_a_timeout_when_every_error_peek_is_cut(
        tmp):
    del tmp
    with truncating_front_end(truncate=None, status=502) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab4')
        r = run_cli(['exec', 'job4', 'document.title', '-t', '1'], env)
        seen = list(TruncatingFrontEndHandler.seen)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'Traceback' not in r.stderr, r.stderr
    assert 'Timeout (1s)' in r.stderr, r.stderr
    puts = [path for verb, path in seen if verb == 'PUT']
    assert puts == ['/command'], seen
    peeks = [path for verb, path in seen if verb == 'GET']
    assert len(peeks) >= 2, seen


def test_a_truncated_error_answer_is_a_connection_failure(tmp):
    """An error body cut off mid-read is reported like any other cut."""
    del tmp
    with truncating_front_end(truncate=None, status=502) as base:
        outcomes = _every_api_entry(base)
    _assert_connection_failed(outcomes, 'IncompleteRead')


def test_a_complete_error_answer_is_still_reported_by_status(tmp):
    """Only an error body that cannot be read is a connection failure.

    A whole 502 body keeps the `HTTP <code>: <detail>` report, with the
    detail JSON-decoded — tests/test_cli.py pins the same words against
    the bridge's own refusals.
    """
    del tmp
    body = b'{"error": "bad gateway"}'
    with truncating_front_end(truncate=None, status=502, body=body) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['tabs'], env)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'Traceback' not in r.stderr, r.stderr
    assert "HTTP 502: {'error': 'bad gateway'}" in r.stderr, r.stderr
    assert 'Connection failed' not in r.stderr, r.stderr


_RAISING_URLOPEN = """
import builtins, sys, urllib.request
from daedalus_cli import transport
family, where, entry = sys.argv[1:4]
exc = getattr(builtins, family)
attempts = []


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        raise exc(f'{family} from read')


def urlopen(req, timeout=None):
    attempts.append(req.full_url)
    if where == 'open':
        raise exc(f'{family} from open')
    return Response()


urllib.request.urlopen = urlopen
if entry == 'wait':
    out = transport.wait_for_result('c', 't', 'd', 0.2, interval=0)
    print('WAIT', out, len(attempts))
else:
    transport.api('GET', '/tabs')
"""


def _check_transport_family(family):
    """Every entry point survives `family` raised by urlopen and by read.

    A stub urlopen stands in for the socket, so the exception the CLI
    meets is exactly the family named and nothing else. api() reports it
    as a connection failure; the result wait treats it as a failed peek
    and returns None at its deadline, having tried at least once.
    """
    env = cli_env(DAEDALUS_TOKEN=TOK)
    for where in ('open', 'read'):
        r = _run([sys.executable, '-c', _RAISING_URLOPEN, family, where,
                  'api'], env)
        assert r.returncode != 0, (where, r.returncode, r.stdout)
        assert 'Traceback' not in r.stderr, (where, r.stderr)
        assert f'Connection failed: {family} from {where}' in r.stderr, (
            where, r.stderr)
        r = _run([sys.executable, '-c', _RAISING_URLOPEN, family, where,
                  'wait'], env)
        assert r.returncode == 0, (where, r.returncode, r.stderr)
        assert 'Traceback' not in r.stderr, (where, r.stderr)
        verdict, outcome, attempts = r.stdout.strip().split()
        assert (verdict, outcome) == ('WAIT', 'None'), (where, r.stdout)
        assert int(attempts) >= 1, (where, r.stdout)


def test_a_connection_reset_is_a_connection_failure_on_every_entry(tmp):
    """ConnectionResetError is an OSError that is not a URLError.

    The cut-off controls above raise IncompleteRead, an HTTPException, so
    they stay green when OSError leaves _exchange's transport clause
    (issue 699) while a reset by the proxy escapes as a traceback.
    """
    del tmp
    _check_transport_family('ConnectionResetError')


def test_a_timeout_is_a_connection_failure_on_every_entry(tmp):
    """TimeoutError is the other OSError a socket raises past urlopen."""
    del tmp
    _check_transport_family('TimeoutError')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
