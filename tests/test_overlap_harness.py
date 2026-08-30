#!/usr/bin/env python3
"""Diagnostics from the same-id overlap harness and its client processes.

Each stall is driven through a temporary JavaScript worker so the suite checks
the real Node subprocess boundary and the exact evidence returned to Python.
"""
import contextlib
import http.server
import re
import subprocess
import sys
import threading
import time
from unittest import mock
from pathlib import Path

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

# VM timers are stubs, and a pending promise alone does not keep Node alive.
# Stall workers use this host-realm interval until the tested bound fires.
_HOST_REALM_KEEPALIVE = """
chrome.runtime.getPlatformInfo.constructor(
  'setInterval(() => {{}}, {interval})')();
"""

_STALLED_CONFIG_WORKER = _HOST_REALM_KEEPALIVE.format(interval=10) + """

function loadConfig() {
  return new Promise(() => {});
}

function dispatchCommand() {}
"""

_STALLED_DISPATCH_WORKER = _HOST_REALM_KEEPALIVE.format(interval=10) + """
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

_SYNCHRONOUS_DISPATCH_STALL_WORKER = """
async function loadConfig() {}

function dispatchCommand() {
  for (;;) {}
}
"""

_FINISHED_BUT_RUNNING_WORKER = _HOST_REALM_KEEPALIVE.format(
    interval=1000) + """
async function loadConfig() {}

async function dispatchCommand(command) {
  const result = await chrome.cookies.getAll({ domain: command.domain });
  await fetch('test-bridge/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
}
"""

# Two POSTs for one owner, the first issued and only the second awaited. With
# a fabricated response both attempts are recorded during the dispatch, so the
# recorded count jumps past one inside a single completion-wait poll window.
_DOUBLE_POST_WORKER = """
async function loadConfig() {}

async function dispatchCommand(command) {
  const result = await chrome.cookies.getAll({ domain: command.domain });
  const first = fetch('test-bridge/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
  await fetch('test-bridge/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
  await first;
}
"""


def _worker(tmp, source):
    path = Path(tmp) / 'background.js'
    path.write_text(source, encoding='utf-8')
    return path


def _wait_for_path(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), f'{path.name} was not published'


@contextlib.contextmanager
def _slow_result_server(post_delay=0, post_status=200):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if post_delay:
                time.sleep(post_delay)
            body = b'{}' if post_status == 200 else b'{"error":"no"}'
            try:
                self.send_response(post_status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # A delayed POST can outlive the child the backstop
                # killed, so writing to its closed socket is expected.
                pass

        def do_GET(self):
            time.sleep(60)
            body = b'{"pending":false}'
            try:
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # The test deliberately ends the peer while this is pending,
                # so its closed socket is expected to reset here.
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


def _assert_step_trace(failure, labels):
    marker = '[step] '
    trace_start = failure.find(marker)
    trace_text = failure[trace_start:] if trace_start >= 0 else ''
    trace_text = trace_text.replace('\\n', '\n')
    actual = re.findall(r'^\[step\] (.+)$', trace_text, re.MULTILINE)
    position = 0
    for expected in labels:
        try:
            position = actual.index(expected, position) + 1
        except ValueError as mismatch:
            reason = 'out of order' if expected in actual else 'missing'
            raise AssertionError(
                f'expected step {expected!r} was {reason}; '
                f'actual step labels: {actual}'
            ) from mismatch


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
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
    ])


def test_posted_results_with_stalled_dispatches_name_the_settle_wait(tmp):
    """Posted results do not hide dispatch promises that never settle."""
    failure = _harness_failure(_worker(tmp, _STALLED_DISPATCH_WORKER))
    assert ('timed out waiting for all dispatchCommand calls to settle'
            in failure), failure
    assert 'outer backstop' not in failure, failure
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
        'the dispatchCommand calls to start',
        'all cookie handlers to start',
        'result POST for owner-a',
        'all dispatchCommand calls to settle',
    ])


def test_a_synchronous_stall_reports_the_outer_backstop_and_last_step(tmp):
    """A blocked Node event loop is killed with its last entered step named."""
    failure = _harness_failure(_worker(tmp, _SYNCHRONOUS_STALL_WORKER))
    assert 'outer backstop' in failure, failure
    assert 'last step: the worker to load its config' in failure, failure
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
    ])


def test_a_synchronous_dispatch_stall_names_the_dispatch_checkpoint(tmp):
    """A blocked dispatch call is not blamed on completed config loading."""
    failure = _harness_failure(
        _worker(tmp, _SYNCHRONOUS_DISPATCH_STALL_WORKER))
    assert 'outer backstop' in failure, failure
    assert 'last step: the dispatchCommand calls to start' in failure, failure
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
        'the dispatchCommand calls to start',
    ])


def test_completed_work_that_does_not_exit_reports_the_finished_step(tmp):
    """Finished work is distinct from a harness that never completed."""
    failure = _harness_failure(_worker(tmp, _FINISHED_BUT_RUNNING_WORKER))
    assert 'last step: the overlap harness finished' in failure, failure
    assert '"owner":"owner-a"' in failure, failure
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
        'the dispatchCommand calls to start',
        'all cookie handlers to start',
        'result POST for owner-a',
        'all dispatchCommand calls to settle',
        'the overlap harness finished',
    ])


def test_a_stalled_async_predicate_cannot_outlive_its_wait(tmp):
    """A result-consume fetch is bounded by the waitFor deadline around it."""
    commands = [
        {'id': '_cookies', 'domain': 'owner-a'},
        {'id': '_cookies', 'domain': 'owner-b'},
    ]
    with _slow_result_server() as base:
        failure = _harness_failure(
            _worker(tmp, _SETTLING_WORKER), commands=commands,
            order=['owner-a', 'owner-b'], result_base=base,
            wait_between=True, inner_wait=5)
    assert ('timed out waiting for the first result to be consumed'
            in failure), failure
    assert 'outer backstop' not in failure, failure
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
        'the dispatchCommand calls to start',
        'all cookie handlers to start',
        'result POST for owner-a',
        'the first result to be consumed',
    ])


def test_a_slow_result_post_cannot_preempt_the_consume_wait(tmp):
    """A delayed result POST cannot spend the consume wait's inner bound."""
    commands = [
        {'id': '_cookies', 'domain': 'owner-a'},
        {'id': '_cookies', 'domain': 'owner-b'},
    ]
    with _slow_result_server(post_delay=4) as base:
        failure = _harness_failure(
            _worker(tmp, _SETTLING_WORKER), commands=commands,
            order=['owner-a', 'owner-b'], result_base=base,
            wait_between=True, inner_wait=2)
    assert ('timed out waiting for the first result to be consumed'
            in failure), failure
    assert 'outer backstop' not in failure, failure
    _assert_step_trace(failure, [
        'the worker script to initialize',
        'the worker to load its config',
        'the dispatchCommand calls to start',
        'all cookie handlers to start',
        'result POST for owner-a',
        'the first result to be consumed',
    ])


def test_a_rejected_result_post_is_reported_not_posted(tmp):
    """A non-2xx result POST is a named failure, never a posted result."""
    with _slow_result_server(post_status=400) as base:
        failure = _harness_failure(
            _worker(tmp, _SETTLING_WORKER),
            commands=[{'id': '_cookies', 'domain': 'owner-a'}],
            order=['owner-a'], result_base=base, inner_wait=2)
    assert 'the result POST for owner-a failed' in failure, failure
    assert 'status 400' in failure, failure
    assert '{"error":"no"}' in failure, failure


def test_two_posts_for_one_owner_cannot_deadlock_the_wait(tmp):
    """Two resolving fetches for one owner are attempts, not a missing post.

    The completion wait keys on the owner's recorded 2xx, so a second POST
    landing beside the first leaves the wait answerable instead of pinned at a
    count that no longer matches the pushes.
    """
    posted = _overlap.run_background_overlap(
        _worker(tmp, _DOUBLE_POST_WORKER),
        [{'id': '_cookies', 'domain': 'owner-a'}],
        ['owner-a'], inner_wait=2)
    assert [item['owner'] for item in posted] == ['owner-a', 'owner-a'], posted


def test_real_overlap_bridge_defaults_to_the_durable_token_path(tmp):
    """The relocated driver preserves the existing durable token carrier."""
    real_bridge = _overlap._util.bridge
    recorded = None

    @contextlib.contextmanager
    def recording_bridge(bridge_tmp, env=None, output=None):
        nonlocal recorded
        recorded = env
        with real_bridge(bridge_tmp, env=env, output=output) as running:
            yield running

    token = 'overlap-client-token'
    with mock.patch.object(_overlap._util, 'bridge', recording_bridge):
        try:
            _overlap.run_same_id_client_overlap(
                tmp, ['missing-owner'], _overlap.cookie_client_argv,
                _overlap.client_env(), token,
                _util.ROOT / 'extension' / 'background.js')
        except AssertionError as failure:
            message = str(failure)
            assert 'missing cookie completion for missing-owner' in message
        else:
            raise AssertionError('the injected harness failure was accepted')
    assert recorded == {'TOKEN': '', 'DAEDALUS_TOKEN': token}, recorded


def test_real_overlap_success_path_waits_for_clients_without_a_bound(tmp):
    """A client whose result is posted is waited for, not killed at a margin.

    The client's own `--timeout` already bounds it, so a second wall-clock
    grace could only kill a client that was about to finish on its own — the
    kill that once left a self-contradictory record behind.
    """
    recorded = {}
    real_client_states = _overlap.client_states

    def recording_client_states(processes, grace, **kwargs):
        recorded['grace'] = grace
        return real_client_states(processes, grace, **kwargs)

    with mock.patch.object(
            _overlap, 'client_states', recording_client_states):
        actual = _overlap.run_same_id_client_overlap(
            tmp, ['owner-a', 'owner-b'], _overlap.cookie_client_argv,
            _overlap.client_env(), 'overlap-client-token',
            _util.ROOT / 'extension' / 'background.js')
    assert actual == {
        owner: {
            'returncode': 0, 'ownResult': True, 'foreignResult': False,
            'stderr': '',
        }
        for owner in ('owner-a', 'owner-b')
    }, actual
    assert recorded == {'grace': None}, recorded


def test_real_overlap_failure_keeps_harness_and_live_client_states(tmp):
    """Client cleanup cannot mask a named failure from the real harness."""
    message = None
    try:
        _overlap.run_same_id_client_overlap(
            tmp, ['missing-owner'], _overlap.cookie_client_argv,
            _overlap.client_env(), 'overlap-client-token',
            _util.ROOT / 'extension' / 'background.js')
    except AssertionError as failure:
        message = str(failure)
    assert message is not None, 'the injected harness failure was accepted'
    assert 'missing cookie completion for missing-owner' in message, message
    assert 'clients:' in message, message
    assert "'owner-a': {'stillRunning': True" in message, message
    assert "'owner-b': {'stillRunning': True" in message, message


def test_killed_client_pipe_release_keeps_an_independent_floor(tmp):
    """Pipe release keeps enough slack to distinguish load from a broken drain.

    An ordinary kill releases pipes in milliseconds, yet a 0.1-second bound
    already failed on a busy runner. Five seconds is the minimum below which
    the bound cannot distinguish the reproduced slow release from a broken
    drain. This floor stands alone so tuning another timeout cannot weaken it.
    """
    del tmp
    minimum_release = 5
    actual = _overlap._KILLED_CLIENT_PIPE_RELEASE_S
    assert actual >= minimum_release, (actual, minimum_release)


def test_client_states_kills_and_reports_a_client_past_its_grace(tmp):
    """A client that misses its grace is diagnostic data, not an exception."""
    ready_path = Path(tmp) / 'client.ready'
    client = (
        'import sys, time\n'
        'from pathlib import Path\n'
        'print("started", flush=True)\n'
        'Path(sys.argv[1]).write_text("ready", encoding="ascii")\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_path(ready_path)
        states = _overlap.client_states({'slow-owner': process}, grace=0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    state = states['slow-owner']
    assert state['stillRunning'] is True, state
    assert state['returncode'] is None, state
    assert state['stdout'] == 'started', state
    assert state['stderr'] == '', state


class _KillRecordsOwnStatus:
    """A client the harness killed, whose kill left its own status behind.

    Windows `Popen.kill()` is `TerminateProcess(handle, 1)` and POSIX's is
    SIGKILL, so `proc.returncode` after that kill describes the kill rather
    than the client. The post-kill drain completes with nothing on either
    stream, which is what the reported Windows record showed.
    """

    stdout = None
    stderr = None
    returncode = None

    def __init__(self, kill_status):
        self._kill_status = kill_status
        self._drained = False

    def communicate(self, timeout=None):
        if self._drained:
            return '', ''
        self._drained = True
        raise subprocess.TimeoutExpired('stub-client', timeout)

    def kill(self):
        self.returncode = self._kill_status


def test_client_states_records_no_exit_status_for_a_client_it_killed(tmp):
    """A killed client's record cannot carry an exit status at all."""
    del tmp
    states = _overlap.client_states({
        owner: _KillRecordsOwnStatus(status)
        for owner, status in (('owner-a', 1), ('owner-b', -9))
    }, grace=0.1)
    assert states == {
        'owner-a': {
            'stillRunning': True, 'returncode': None,
            'stdout': '', 'stderr': '', 'drainTimedOut': False,
        },
        'owner-b': {
            'stillRunning': True, 'returncode': None,
            'stdout': '', 'stderr': '', 'drainTimedOut': False,
        },
    }, states


def test_client_states_waits_out_a_slow_pipe_release_after_a_kill(tmp):
    """A killed client keeps its output while inherited pipes close."""
    ready_path = Path(tmp) / 'slow-pipes.ready'
    client = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        'print("slow-pipe-marker", flush=True)\n'
        'print("slow-pipe-error", file=sys.stderr, flush=True)\n'
        'grandchild = subprocess.Popen('
        '[sys.executable, "-c", "import time; time.sleep(3)"])\n'
        'target = Path(sys.argv[1])\n'
        'pending = target.with_suffix(".tmp")\n'
        'pending.write_text("ready", encoding="ascii")\n'
        'pending.replace(target)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_path(ready_path)
        states = _overlap.client_states(
            {'slow-pipe-owner': process}, grace=0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    state = states['slow-pipe-owner']
    assert state['stillRunning'] is True, state
    assert state['drainTimedOut'] is False, state
    assert state['stdout'] == 'slow-pipe-marker', state
    assert state['stderr'] == 'slow-pipe-error', state


def test_client_states_records_a_killed_clients_held_pipes(tmp):
    """A grandchild-held pipe forces the drain timeout, and the record holds.

    The killed client has already written to its pipe and its grandchild keeps
    that pipe open, so the second drain expires whatever the reader won in the
    window. What the fixture proves is that the recorded state still comes out
    self-consistent through that expiry: still running, no exit status of its
    own, and the timeout recorded rather than raised. Whether the reader won
    the pipe's contents before the deadline is a wall-clock race, so the
    contents themselves are pinned against a stub by the client-state suite.
    """
    ready_path = Path(tmp) / 'grandchild.ready'
    client = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        'print("held-pipe-marker", flush=True)\n'
        'grandchild = subprocess.Popen('
        '[sys.executable, "-c", "import time; time.sleep(10)"])\n'
        'target = Path(sys.argv[1])\n'
        'pending = target.with_suffix(".tmp")\n'
        'pending.write_text("ready", encoding="ascii")\n'
        'pending.replace(target)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_path(ready_path)
        states = _overlap.client_states(
            {'pipe-owner': process}, grace=0.1, killed_pipe_release=0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    state = states['pipe-owner']
    assert state['stillRunning'] is True, state
    assert state['returncode'] is None, state
    assert state['drainTimedOut'] is True, state


def test_client_states_bounds_fallback_wait_after_drain_timeout(tmp):
    """A failed drain cannot turn its last-resort reap into an unbounded hang.

    The fallback wait runs only after the killed client's drain has already
    timed out. If that wait were unbounded, the diagnostic helper would hang
    precisely when the process was already known to be broken.
    """
    del tmp

    class NeverReapedProcess:
        """A killed client whose communicate and reap never complete."""

        stdout = None
        stderr = None
        returncode = None

        def communicate(self, timeout):
            del self
            raise subprocess.TimeoutExpired('fake-client', timeout)

        def kill(self):
            del self

        def wait(self, timeout=None):
            del self
            if timeout is None:
                raise AssertionError('fallback wait was unbounded')
            raise subprocess.TimeoutExpired('fake-client', timeout)

    state = _overlap.client_states(
        {'stuck-owner': NeverReapedProcess()}, grace=0.1,
        killed_pipe_release=0.1)['stuck-owner']
    assert state == {
        'stillRunning': True,
        'returncode': None,
        'stdout': '',
        'stderr': '',
        'drainTimedOut': True,
    }, state


def test_a_silent_nonzero_client_is_named_as_its_own_failure(tmp):
    """A silent non-zero exit is a different failure from outliving grace."""
    del tmp
    posted = [{'id': '_cookies', 'owner': 'owner-a'}]
    states = {
        'owner-a': {
            'stillRunning': False, 'returncode': 1,
            'stdout': '', 'stderr': '', 'drainTimedOut': False,
        },
    }
    message = None
    try:
        _overlap.assert_clients_exited(states, posted)
    except AssertionError as failure:
        message = str(failure)
    else:
        raise AssertionError('a silent non-zero client was accepted')
    assert 'clients exited non-zero with no output' in message, message
    assert "['owner-a']" in message, message
    assert 'still running after grace' not in message, message


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
            'stillRunning': True, 'returncode': None,
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
