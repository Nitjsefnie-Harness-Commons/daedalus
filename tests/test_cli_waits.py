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
    """A 502 cut off mid-body is the same failed peek as a 200 cut off:
    the HTTPError handler reads the body for its report (issue 700)."""
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
    del tmp
    with truncating_front_end(truncate=None, status=502) as base:
        outcomes = _every_api_entry(base)
    _assert_connection_failed(outcomes, 'IncompleteRead')


def test_a_complete_error_answer_is_still_reported_by_status(tmp):
    """A whole 502 body keeps the decoded `HTTP <code>: <detail>` report."""
    del tmp
    body = b'{"error": "bad gateway"}'
    with truncating_front_end(truncate=None, status=502, body=body) as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['tabs'], env)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'Traceback' not in r.stderr, r.stderr
    assert "HTTP 502: {'error': 'bad gateway'}" in r.stderr, r.stderr
    assert 'Connection failed' not in r.stderr, r.stderr


def test_a_refused_connect_is_reported_by_its_reason_alone(tmp):
    """The URLError clause is met before the OSError one.

    URLError is an OSError, so swapping the two clauses still exits
    cleanly but reports the whole `<urlopen error ...>` wrapper instead
    of its `.reason`; this pins the order by the wrapper's absence.
    """
    del tmp
    port = _util.free_port()  # nothing listens here
    env = cli_env(DAEDALUS_URL=f'http://127.0.0.1:{port}',
                  DAEDALUS_TOKEN=TOK)
    r = run_cli(['tabs'], env)
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'Traceback' not in r.stderr, r.stderr
    assert 'Connection failed: ' in r.stderr, r.stderr
    assert 'urlopen error' not in r.stderr, r.stderr


WAIT_FAILURES = 3
WAIT_TIMEOUT = 30
WAIT_RESULT = {'id': 'c', 'deliveryId': 'd', 'resultGeneration': 7,
               'result': 'x'}

_RAISING_URLOPEN = """
import builtins, json, sys, urllib.error, urllib.request
from daedalus_cli import transport
family, where, entry = sys.argv[1:4]
exc = getattr(builtins, family)
attempts = []
peeks = 0
consumes = 0
FAILURES = %(failures)d
TIMEOUT = %(timeout)d
RESULT = %(result)s
CONSUMED = {'consumed': True,
            'resultGeneration': RESULT['resultGeneration']}
REFUSED = {'consumed': False,
           'resultGeneration': RESULT['resultGeneration']}


class Response:
    def __init__(self, body=None):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        if self.body is None:
            raise exc(f'{family} from {where}')
        return self.body

    def close(self):
        pass


def urlopen(req, timeout=None):
    global peeks, consumes
    attempts.append(req.full_url)
    if 'consume=1' in req.full_url:
        expected = f"expected={RESULT['resultGeneration']}"
        if expected not in req.full_url:
            raise AssertionError(f'unexpected consume: {req.full_url}')
        consumes += 1
        if consumes == 1:
            return Response(json.dumps(REFUSED).encode())
        return Response(json.dumps(CONSUMED).encode())
    peeks += 1
    if peeks > FAILURES:
        return Response(json.dumps(RESULT).encode())
    if where == 'open':
        raise exc(f'{family} from open')
    if where == 'error':
        raise urllib.error.HTTPError(
            req.full_url, 502, 'Bad Gateway', None, Response())
    return Response()


urllib.request.urlopen = urlopen
if entry == 'wait':
    out = transport.wait_for_result('c', 't', 'd', TIMEOUT, interval=0)
    print('WAIT', json.dumps({'out': out, 'attempts': len(attempts)}))
else:
    transport.api('GET', '/tabs')
"""

# A literal % in the template breaks this interpolation at import time.
_RAISING_URLOPEN %= {
    'failures': WAIT_FAILURES,
    'timeout': WAIT_TIMEOUT,
    'result': repr(WAIT_RESULT),
}


def _check_transport_family(family):
    """`family` from urlopen, the body read and an HTTPError's body read.

    The wait pin is causal, not a wall clock: WAIT_TIMEOUT leaves the
    sub-second scheduler stalls that flaked the 0.2s budget no way to
    expire the wait, WAIT_FAILURES failed peeks must be survived, and
    the peek that lands must survive a refused consume and be accepted
    on the second — attempts is failures + 4, exactly (issues 893,
    900). The stub answers a consume only when its URL names the
    peeked generation, so a transport that drops `expected` from the
    consume fails here rather than passing.
    """
    env = cli_env(DAEDALUS_TOKEN=TOK)
    for where in ('open', 'read', 'error'):
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
        verdict, payload = r.stdout.strip().split(' ', 1)
        assert verdict == 'WAIT', (where, r.stdout)
        report = json.loads(payload)
        assert report['out'] == WAIT_RESULT, (where, r.stdout)
        assert report['attempts'] == WAIT_FAILURES + 4, (where, r.stdout)


def test_a_connection_reset_is_a_connection_failure_on_every_entry(tmp):
    """The cut-off controls raise IncompleteRead, an HTTPException; this
    is the OSError half of _exchange's transport clauses (issue 699)."""
    del tmp
    _check_transport_family('ConnectionResetError')


def test_a_timeout_is_a_connection_failure_on_every_entry(tmp):
    del tmp
    _check_transport_family('TimeoutError')


def test_a_bare_oserror_is_a_connection_failure_on_every_entry(tmp):
    """The clause admits the family, not the members met so far."""
    del tmp
    _check_transport_family('OSError')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
