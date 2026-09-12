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
import _drain  # noqa: E402
import _util  # noqa: E402
from _cmdqueue import clear_command_queue  # noqa: E402
from _queueread import queued_command  # noqa: E402

# Keep bridge children off the fixed MCP port (see tests/_bridge.py).
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']

# The result header's marker is whichever glyph the console can encode, so
# what is pinned is that a marker immediately precedes the id (see
# tests/test_cli.py).
IN_MARKS = ('←', '<-')
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
    with _util.bridge(tmp) as (base, docroot):
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
    with _util.bridge(tmp) as (base, docroot):
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
