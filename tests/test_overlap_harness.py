#!/usr/bin/env python3
"""Diagnostics from the same-id overlap harness and its client processes.

Each stall is driven through a temporary JavaScript worker so the suite checks
the real Node subprocess boundary and the exact evidence returned to Python.
"""
import contextlib
import io
import subprocess
import sys
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _overlap_clients  # noqa: E402
import _util  # noqa: E402
from _overlap import (  # noqa: E402
    _assert_step_trace, _harness_failure)
from _overlap_clients import _slow_result_server  # noqa: E402


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

_SHIPPED_BACKGROUND = _util.ROOT / 'extension' / 'background.js'


def _worker(tmp, source):
    path = Path(tmp) / 'background.js'
    path.write_text(source, encoding='utf-8')
    return path


def test_run_background_overlap_accepts_a_short_inner_bound(tmp):
    """A caller can shorten diagnostic bounds without changing production."""
    actual = _overlap.run_background_overlap(
        _worker(tmp, _SETTLING_WORKER),
        [{'id': '_cookies', 'domain': 'owner-a'}],
        ['owner-a'], inner_wait=1, boot=False)
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
    failure = _harness_failure(_worker(tmp, _STALLED_DISPATCH_WORKER),
                               boot=False)
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


def test_a_drain_with_nothing_unread_still_names_the_backstop(tmp):
    """A timed-out drain hands back None for a pipe with nothing unread.

    Those Nones must reach the diagnostic message, not the trace reader:
    a TypeError raised out of the backstop would replace the one report
    that names what the harness was doing when it stalled.
    """
    with mock.patch.object(_overlap.subprocess, 'Popen') as popen:
        popen.return_value.pid = 4711
        popen.return_value.returncode = None
        popen.return_value.communicate.side_effect = subprocess.TimeoutExpired(
            cmd='node', timeout=1.0, output=None, stderr=None)
        message = ''
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                _overlap.run_background_overlap('background', [], [])
        except TypeError as broken:
            raise AssertionError(
                f'the trace reader was handed None: {broken}') from broken
        except AssertionError as failure:
            message = str(failure)
    assert 'outer backstop' in message, message
    assert 'last step: none recorded' in message, message


def test_a_drained_non_ascii_stream_survives_the_backstop_message(tmp):
    """Bytes a timed-out drain returns reach the message decoded.

    The backstop message is the one report naming what a stalled harness
    was doing, so the stream it captured must come back as decoded text:
    non-ASCII content dropped by the normalization is a diagnosis lost.
    """
    with mock.patch.object(_overlap.subprocess, 'Popen') as popen:
        popen.return_value.pid = 4712
        popen.return_value.returncode = None
        popen.return_value.communicate.side_effect = subprocess.TimeoutExpired(
            cmd='node', timeout=1.0, output=b'caf\xc3\xa9-step', stderr=None)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                _overlap.run_background_overlap('background', [], [])
        except AssertionError as failure:
            message = str(failure)
        else:
            message = ''
    assert 'outer backstop' in message, message
    assert 'café-step' in message, message


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
    failure = _harness_failure(_worker(tmp, _FINISHED_BUT_RUNNING_WORKER),
                               boot=False)
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
            wait_between=True, inner_wait=2, boot=False)
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
    with _slow_result_server(post_delay=2) as base:
        failure = _harness_failure(
            _worker(tmp, _SETTLING_WORKER), commands=commands,
            order=['owner-a', 'owner-b'], result_base=base,
            wait_between=True, inner_wait=1, boot=False,
            # outer_slack restores the 14s backstop at zero wall-clock cost.
            outer_slack=7)
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
            order=['owner-a'], result_base=base, inner_wait=2, boot=False)
    assert ('the result POST for owner-a failed: '
            'id=_cookies _did=null' in failure), failure
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
        ['owner-a'], inner_wait=2, boot=False, results=2)
    assert [item['owner'] for item in posted] == ['owner-a', 'owner-a'], posted


def test_shipped_worker_retries_a_transient_result_post(tmp):
    """A shipped worker retries a 5xx and completes on the later 2xx."""
    commands = [{
        'id': '_cookies', 'type': 'cookies', 'domain': 'owner-a',
        '_did': 'did-retry',
    }]
    with _slow_result_server(post_statuses=[500, 200]) as base:
        actual = _overlap.run_background_overlap(
            _SHIPPED_BACKGROUND, commands, ['owner-a'], result_base=base,
            inner_wait=2, results=2)
    assert actual == [{
        'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-retry',
    }], actual


def test_shipped_worker_terminal_5xx_fails_fast_with_clipped_diagnostics(tmp):
    """A terminal shipped-worker refusal names its clipped failure."""
    long_body = 'x' * 250
    commands = [{
        'id': '_cookies', 'type': 'cookies', 'domain': 'owner-a',
        '_did': 'did-terminal',
    }]
    with _slow_result_server(post_statuses=[500], post_body=long_body) as base:
        # The worker's own retry loop makes three attempts against a terminal
        # 5xx (measured: 500, then two more), so the plan declares all three.
        # This run fails before the gate's whole-list check, so the refusals
        # this count governs are enforced by the plan's count, not asserted
        # here — which is why the count must be the real one.
        failure = _harness_failure(
            _SHIPPED_BACKGROUND, inner_wait=2, commands=commands,
            order=['owner-a'], result_base=base, results=3)
    clipped = 'x' * 200 + '...'
    assert 'outer backstop' not in failure, failure
    assert ('the result POST for owner-a failed: '
            'id=_cookies _did=did-terminal' in failure), failure
    assert 'status 500' in failure, failure
    assert clipped in failure, failure
    assert 'x' * 201 not in failure, failure
    assert ('[post-failure] owner=owner-a id=_cookies '
            '_did=did-terminal status 500 body ' + clipped
            in failure), failure


def test_real_overlap_bridge_defaults_to_the_durable_token_path(tmp):
    """The relocated driver preserves the existing durable token carrier."""
    real_bridge = _overlap_clients._util.bridge
    recorded = None

    @contextlib.contextmanager
    def recording_bridge(bridge_tmp, env=None, output=None):
        nonlocal recorded
        recorded = env
        with real_bridge(bridge_tmp, env=env, output=output) as running:
            yield running

    token = 'overlap-client-token'
    with mock.patch.object(
            _overlap_clients._util, 'bridge', recording_bridge):
        try:
            _overlap_clients.run_same_id_client_overlap(
                tmp, ['missing-owner'], _overlap_clients.cookie_client_argv,
                _overlap_clients.client_env(), token,
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
    real_client_states = _overlap_clients.client_states

    def recording_client_states(processes, grace, **kwargs):
        recorded['grace'] = grace
        return real_client_states(processes, grace, **kwargs)

    with mock.patch.object(
            _overlap_clients, 'client_states', recording_client_states):
        actual = _overlap_clients.run_same_id_client_overlap(
            tmp, ['owner-a', 'owner-b'], _overlap_clients.cookie_client_argv,
            _overlap_clients.client_env(), 'overlap-client-token',
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
        _overlap_clients.run_same_id_client_overlap(
            tmp, ['missing-owner'], _overlap_clients.cookie_client_argv,
            _overlap_clients.client_env(), 'overlap-client-token',
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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
