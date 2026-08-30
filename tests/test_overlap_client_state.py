#!/usr/bin/env python3
"""Pins on the overlap client diagnostics the harness suite does not hold.

`tests/test_overlap_harness.py` sits at its module-size ceiling, so the pins
added by the issue 280 review round live here: what `client_states` does with
`grace=None`, which diagnosis kind answers first, what the silent diagnosis
refuses to name, and the real caller that routes its states through them.
"""
import re
import subprocess
import sys
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402


class _RecordsDrainTimeout:
    """A client that records every timeout its drain was handed."""

    returncode = 0

    def __init__(self):
        self.timeouts = []

    def communicate(self, timeout=None):
        self.timeouts.append(timeout)
        return 'client output', ''


def test_client_states_hands_grace_none_to_the_client_unbounded(tmp):
    """`grace=None` reaches the client's drain unbounded, never a margin.

    The client's own `--timeout` is the only bound a successful run allows,
    so the helper must hand `None` on rather than invent a grace of its own.
    """
    del tmp
    client = _RecordsDrainTimeout()
    state = _overlap.client_states({'owner-a': client}, grace=None)['owner-a']
    assert client.timeouts == [None], client.timeouts
    assert state == {
        'stillRunning': False, 'returncode': 0,
        'stdout': 'client output', 'stderr': '', 'drainTimedOut': False,
    }, state


class _DrainExpiresCarryingOutput:
    """A killed client whose second drain expires holding what was read.

    A real deadline raises `TimeoutExpired` carrying the bytes read before it
    even under `text=True`, so the stub raises the same shape on its second
    `communicate`.
    """

    stdout = None
    stderr = None
    returncode = None

    def __init__(self):
        self.timeouts = []

    def communicate(self, timeout):
        self.timeouts.append(timeout)
        if len(self.timeouts) == 1:
            raise subprocess.TimeoutExpired('held-client', timeout)
        raise subprocess.TimeoutExpired(
            'held-client', timeout,
            output=b'held-pipe-marker\n', stderr=b'held-pipe-warning\n')

    def kill(self):
        """A kill the stub survives, as a real killed client's pipes do."""

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired('held-client', timeout)


def test_client_states_records_the_output_a_timed_out_drain_held(tmp):
    """A drain that expires records the partial output it had already read.

    The deadline exception attaches whatever the reader won before it, so the
    state recorded for a killed client must carry that output beside
    `drainTimedOut` rather than replace it with nothing.
    """
    del tmp
    state = _overlap.client_states(
        {'held-owner': _DrainExpiresCarryingOutput()}, grace=0.1,
        killed_pipe_release=0.1)['held-owner']
    assert state == {
        'stillRunning': True, 'returncode': None,
        'stdout': 'held-pipe-marker', 'stderr': 'held-pipe-warning',
        'drainTimedOut': True,
    }, state


def test_a_still_running_client_is_diagnosed_before_a_silent_one(tmp):
    """Both diagnosis kinds present raises the still-running kind alone."""
    del tmp
    states = {
        'owner-a': {
            'stillRunning': True, 'returncode': None,
            'stdout': '', 'stderr': '', 'drainTimedOut': False,
        },
        'owner-b': {
            'stillRunning': False, 'returncode': 1,
            'stdout': '', 'stderr': '', 'drainTimedOut': False,
        },
    }
    message = None
    try:
        _overlap.assert_clients_exited(states, [{'owner': 'owner-a'}])
    except AssertionError as failure:
        message = str(failure)
    else:
        raise AssertionError('the mixed outcomes were accepted')
    assert 'clients still running after grace' in message, message
    assert 'exited non-zero with no output' not in message, message


def test_a_nonzero_client_with_output_is_not_named_silent(tmp):
    """Output on either stream keeps a non-zero client off the silent list."""
    del tmp
    states = {
        'owner-a': {
            'stillRunning': False, 'returncode': 1,
            'stdout': 'a traceback', 'stderr': '', 'drainTimedOut': False,
        },
        'owner-b': {
            'stillRunning': False, 'returncode': 1,
            'stdout': '', 'stderr': 'a warning', 'drainTimedOut': False,
        },
    }
    try:
        _overlap.assert_clients_exited(states, [{'owner': 'owner-a'}])
    except AssertionError as failure:
        raise AssertionError(
            f'a client with output was named silent: {failure}') from failure


def test_run_same_id_client_overlap_diagnoses_its_client_states(tmp):
    """The caller hands its client states to the diagnosis before returning."""
    message = _overlap_client_failure_message(tmp)
    assert 'clients still running after grace' in message, message


def _overlap_client_failure_message(tmp):
    """A real overlap run whose clients are reported as still running.

    The clients and the bridge are real, so the failure the caller raises
    carries evidence a stubbed bridge could not produce: the bridge's own log
    and the deliveries its results created.
    """
    stalled = {
        'stillRunning': True, 'returncode': None,
        'stdout': '', 'stderr': '', 'drainTimedOut': False,
    }

    def failing_states(processes, grace, **kwargs):
        return {owner: dict(stalled) for owner in processes}

    with mock.patch.object(_overlap, 'client_states', failing_states):
        try:
            _overlap.run_same_id_client_overlap(
                tmp, ['owner-a', 'owner-b'],
                _overlap.cookie_client_argv, _overlap.client_env(),
                'overlap-client-token',
                _util.ROOT / 'extension' / 'background.js')
        except AssertionError as failure:
            return str(failure)
    raise AssertionError('the caller never diagnosed its clients')


def test_a_client_failure_names_the_bridge_log_and_delivery_state(tmp):
    """A client failure carries the bridge log tail and the delivery record.

    The question a same-id timeout leaves open is whether the POST reached the
    bridge, and the only surfaces that answer it are the bridge's own log and
    what it retained per delivery.
    """
    message = _overlap_client_failure_message(tmp)
    assert 'bridge log tail' in message, message
    assert '[Daedalus] Listening on 127.0.0.1:' in message, message
    assert 'delivery state' in message, message
    assert re.search(
        r'_extension/\d+_\d+\.json: deliveryId \d+_\d+', message), message


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
