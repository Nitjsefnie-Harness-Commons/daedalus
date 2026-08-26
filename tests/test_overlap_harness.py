#!/usr/bin/env python3
"""Diagnostics from the same-id overlap harness and its client processes.

Each stall is driven through a temporary JavaScript worker so the suite checks
the real Node subprocess boundary and the exact evidence returned to Python.
"""
import contextlib
import http.server
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402


_SETTLING_WORKER = """
async function loadConfig() {}

async function dispatchCommand(command) {
  const result = await chrome.cookies.getAll({ domain: command.domain });
  const stored = await chrome.storage.local.get();
  await fetch(stored['daedalus-server'] + '/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
}
"""

_STALLED_CONFIG_WORKER = """
chrome.runtime.getPlatformInfo.constructor(`
  const hold = setInterval(() => {
    if (process.exitCode) clearInterval(hold);
  }, 10);
`)();

function loadConfig() {
  return new Promise(() => {});
}

function dispatchCommand() {}
"""

_STALLED_DISPATCH_WORKER = """
chrome.runtime.getPlatformInfo.constructor(`
  const hold = setInterval(() => {
    if (process.exitCode) clearInterval(hold);
  }, 10);
`)();

async function loadConfig() {}

async function dispatchCommand(command) {
  const result = await chrome.cookies.getAll({ domain: command.domain });
  await fetch('test-bridge/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
  return new Promise(() => {});
}
"""

_SYNCHRONOUS_STALL_WORKER = """
function loadConfig() {
  for (;;) {}
}

function dispatchCommand() {}
"""

_FINISHED_BUT_RUNNING_WORKER = """
chrome.runtime.getPlatformInfo.constructor(
  'setInterval(() => {}, 1000)')();

async function loadConfig() {}

async function dispatchCommand(command) {
  const result = await chrome.cookies.getAll({ domain: command.domain });
  await fetch('test-bridge/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
}
"""


def _worker(tmp, source):
    path = Path(tmp) / 'background.js'
    path.write_text(source, encoding='utf-8')
    return path


@contextlib.contextmanager
def _slow_result_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')

        def do_GET(self):
            time.sleep(30)
            body = b'{"pending":false}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        # pylint: disable-next=redefined-builtin
        def log_message(self, format, *args):
            del format, args

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _harness_failure(background, inner_wait=1, commands=None, order=None,
                     result_base='', wait_between=False):
    commands = commands or [{'id': '_cookies', 'domain': 'owner-a'}]
    order = order or ['owner-a']
    try:
        _overlap.run_background_overlap(
            background, commands, order, result_base=result_base,
            wait_between=wait_between, inner_wait=inner_wait)
    except AssertionError as failure:
        return str(failure)
    except subprocess.TimeoutExpired as failure:
        raise AssertionError(
            f'bare TimeoutExpired after {failure.timeout}s') from failure
    raise AssertionError('the stalled overlap harness unexpectedly succeeded')


def test_run_background_overlap_accepts_a_short_inner_bound(tmp):
    """A caller can shorten diagnostic bounds without changing production."""
    actual = _overlap.run_background_overlap(
        _worker(tmp, _SETTLING_WORKER),
        [{'id': '_cookies', 'domain': 'owner-a'}],
        ['owner-a'], inner_wait=1)
    assert actual == [{
        'id': '_cookies',
        'owner': 'owner-a',
        'deliveryId': None,
    }], actual


def test_a_stalled_config_load_names_the_wait(tmp):
    """A never-settling loadConfig promise identifies the config-load step."""
    failure = _harness_failure(_worker(tmp, _STALLED_CONFIG_WORKER))
    assert ('timed out waiting for the worker to load its config'
            in failure), failure
    assert 'outer backstop' not in failure, failure


def test_posted_results_with_stalled_dispatches_name_the_settle_wait(tmp):
    """Posted results do not hide dispatch promises that never settle."""
    failure = _harness_failure(_worker(tmp, _STALLED_DISPATCH_WORKER))
    assert ('timed out waiting for both dispatchCommand calls to settle'
            in failure), failure
    assert 'outer backstop' not in failure, failure


def test_a_synchronous_stall_reports_the_outer_backstop_and_last_step(tmp):
    """A blocked Node event loop is killed with its last entered step named."""
    failure = _harness_failure(_worker(tmp, _SYNCHRONOUS_STALL_WORKER))
    assert 'outer backstop' in failure, failure
    assert 'last step: the worker to load its config' in failure, failure


def test_completed_work_that_does_not_exit_reports_the_finished_step(tmp):
    """Finished work is distinct from a harness that never completed."""
    failure = _harness_failure(_worker(tmp, _FINISHED_BUT_RUNNING_WORKER))
    assert 'last step: the overlap harness finished' in failure, failure
    assert '"owner":"owner-a"' in failure, failure


def test_a_stalled_async_predicate_cannot_outlive_its_wait(tmp):
    """A result-consume fetch is bounded by the waitFor deadline around it."""
    commands = [
        {'id': '_cookies', 'domain': 'owner-a'},
        {'id': '_cookies', 'domain': 'owner-b'},
    ]
    source = _overlap._BACKGROUND_OVERLAP_HARNESS.replace(
        'process.exitCode = 1;', 'process.exit(1);')
    assert source != _overlap._BACKGROUND_OVERLAP_HARNESS
    with mock.patch.object(_overlap, '_BACKGROUND_OVERLAP_HARNESS', source):
        with _slow_result_server() as base:
            failure = _harness_failure(
                _worker(tmp, _SETTLING_WORKER), commands=commands,
                order=['owner-a', 'owner-b'], result_base=base,
                wait_between=True)
    assert ('timed out waiting for the first result to be consumed'
            in failure), failure
    assert 'outer backstop' not in failure, failure


def test_client_states_kills_and_reports_a_client_past_its_grace(tmp):
    """A client that misses its grace is diagnostic data, not an exception."""
    del tmp
    process = subprocess.Popen(
        [sys.executable, '-c',
         'import time; print("started", flush=True); time.sleep(60)'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        states = _overlap.client_states({'slow-owner': process}, grace=0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    state = states['slow-owner']
    assert state['stillRunning'] is True, state
    assert state['returncode'] is not None, state
    assert state['stdout'] == 'started', state
    assert state['stderr'] == '', state


def test_client_states_records_a_killed_clients_held_pipes(tmp):
    """A grandchild-held pipe makes the second timeout diagnostic data."""
    pid_path = Path(tmp) / 'grandchild.pid'
    client = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        'grandchild = subprocess.Popen('
        '[sys.executable, "-c", "import time; time.sleep(60)"])\n'
        'target = Path(sys.argv[1])\n'
        'pending = target.with_suffix(".tmp")\n'
        'pending.write_text(str(grandchild.pid), encoding="ascii")\n'
        'pending.replace(target)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(pid_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    grandchild_pid = None
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.exists(), 'client did not record its grandchild pid'
        grandchild_pid = int(pid_path.read_text(encoding='ascii'))
        states = _overlap.client_states({'pipe-owner': process}, grace=0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        if grandchild_pid is not None:
            try:
                os.kill(grandchild_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    state = states['pipe-owner']
    assert state['stillRunning'] is True, state
    assert state['returncode'] is not None, state
    assert state['stdout'] == '', state
    assert state['stderr'] == '', state
    assert state['drainTimedOut'] is True, state


def test_running_clients_report_the_owner_posted_results_and_states(tmp):
    """Success-path client stalls preserve all diagnostics in one assertion."""
    del tmp
    posted = [{'id': '_cookies', 'owner': 'owner-a'}]
    states = {
        'owner-a': {
            'stillRunning': False, 'returncode': 0,
            'stdout': 'owner-a', 'stderr': '', 'drainTimedOut': False,
        },
        'owner-b': {
            'stillRunning': True, 'returncode': -9,
            'stdout': 'partial', 'stderr': 'waiting',
            'drainTimedOut': False,
        },
    }
    message = None
    try:
        _overlap.assert_clients_exited(states, posted)
    except AssertionError as failure:
        message = str(failure)
    else:
        raise AssertionError('a still-running client was accepted')
    assert message is not None
    assert 'owner-b' in message, message
    assert f'harness posted: {posted}' in message, message
    assert f'client states: {states}' in message, message


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
