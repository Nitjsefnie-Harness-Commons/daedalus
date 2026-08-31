#!/usr/bin/env python3
"""Pins on the overlap client diagnostics the harness suite does not hold.

The client-process pins live here beside the harness controls: what
`client_states` does with `grace=None`, which diagnosis kind answers first,
what a nonzero diagnosis must name, and the real caller that routes its states
through them.
"""
import json
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


def test_a_nonzero_client_with_timeout_output_is_named_as_a_failure(tmp):
    """The filed timeout state is still a failed client outcome."""
    del tmp
    states = {
        'owner-a': {
            'stillRunning': False, 'returncode': 1,
            'stdout': '', 'stderr': 'Timeout (120s)', 'drainTimedOut': False,
        },
    }
    message = None
    try:
        _overlap.assert_clients_exited(states, [{'owner': 'owner-a'}])
    except AssertionError as failure:
        message = str(failure)
    else:
        raise AssertionError('the filed nonzero client was accepted')
    assert 'clients exited non-zero' in message, message
    assert "['owner-a']" in message, message
    assert 'Timeout (120s)' in message, message


def test_run_same_id_client_overlap_diagnoses_its_client_states(tmp):
    """The caller hands its client states to the diagnosis before returning."""
    message = _overlap_client_failure_message(tmp)
    assert 'clients still running after grace' in message, message


def test_a_consumed_delivery_does_not_break_the_listing(tmp):
    """A delivery deleted between the listing and its read is skipped.

    A live client's consume deletes its result, so the diagnostic can race
    it; a vanished file is normal operation and the rest are still named.
    """
    root = Path(tmp) / 'results' / 'deliveries' / 'tok_extension'
    root.mkdir(parents=True)
    survivor = root / '1700000000000_000001.json'
    survivor.write_text(
        json.dumps({'deliveryId': '1700000000000_1'}), encoding='utf-8')
    consumed = root / '1700000000001_000002.json'
    consumed.write_text(
        json.dumps({'deliveryId': '1700000000001_2'}), encoding='utf-8')

    original = Path.read_text

    def vanishes_before_read(candidate, *args, **kwargs):
        if candidate == consumed:
            consumed.unlink()
        return original(candidate, *args, **kwargs)

    with mock.patch.object(Path, 'read_text', vanishes_before_read):
        report = _overlap._client_failure_diagnostics(['log line'], tmp)
    assert 'delivery state' in report, report
    assert ('tok_extension/1700000000000_000001.json: deliveryId '
            '1700000000000_1') in report, report
    assert '1700000000001_2' not in report, report


def _overlap_client_failure_message(tmp, states=None):
    """A real overlap run whose clients are reported in supplied states.

    The clients and the bridge are real, so the failure the caller raises
    carries evidence a stubbed bridge could not produce: the bridge's own log
    and the deliveries its results created.
    """
    stalled = {
        'stillRunning': True, 'returncode': None,
        'stdout': '', 'stderr': '', 'drainTimedOut': False,
    }
    expected = states or {owner: stalled for owner in ('owner-a', 'owner-b')}

    def failing_states(processes, grace, **kwargs):
        assert grace is None, grace
        alive = [owner for owner, process in processes.items()
                 if process.poll() is None]
        assert not alive, f'clients reached the mock still live: {alive}'
        del kwargs
        return {owner: dict(expected[owner]) for owner in processes}

    with mock.patch.object(_overlap, 'client_states', failing_states):
        try:
            _overlap.run_same_id_client_overlap(
                tmp, ['owner-a', 'owner-b'],
                _overlap.cookie_client_argv, _overlap.client_env(),
                'overlap-client-token',
                _util.ROOT / 'extension' / 'background.js',
                stop_clients_after_enqueue=True)
        except AssertionError as failure:
            return str(failure)
    raise AssertionError('the caller never diagnosed its clients')


def test_the_diagnosis_keeps_the_announcement_a_noisy_log_would_bury(tmp):
    """The bridge the clients were talking to is named however loud it got.

    The log tail is a fixed window, and a client that dies mid-request makes
    the bridge print a traceback per connection - on Windows a flood of them,
    which is what pushed the announcement out of the window and reddened both
    Windows legs. Which bridge the diagnosis is about cannot depend on how
    much the bridge had to say afterwards, so it is selected rather than
    hoped for.
    """
    announcement = '[Daedalus] Listening on 127.0.0.1:41234 - base=/tmp/x\n'
    noise = [f'ConnectionResetError: [WinError 10054] {n}\n'
             for n in range(80)]
    message = _overlap._client_failure_diagnostics(
        [announcement] + noise, tmp)
    assert '[Daedalus] Listening on 127.0.0.1:41234' in message, message
    assert noise[-1].strip() in message, message


def test_a_client_failure_names_the_bridge_log_and_delivery_state(tmp):
    """A client failure carries the bridge log tail and the delivery record.

    The question a same-id timeout leaves open is whether the POST reached the
    bridge, and the only surfaces that answer it are the bridge's own log and
    what it retained per delivery.
    """
    message = _overlap_client_failure_message(tmp)
    assert 'bridge announcement' in message, message
    assert '[Daedalus] Listening on 127.0.0.1:' in message, message
    assert 'bridge log tail' in message, message
    assert 'delivery state' in message, message
    assert re.search(
        r'_extension/\d+_\d+\.json: deliveryId \d+_\d+', message), message


def test_a_timeout_client_failure_keeps_both_diagnostics(tmp):
    """The filed timeout state fails at the caller with both evidence."""
    states = {
        'owner-a': {
            'stillRunning': False, 'returncode': 1,
            'stdout': '', 'stderr': 'Timeout (120s)', 'drainTimedOut': False,
        },
        'owner-b': {
            'stillRunning': False, 'returncode': 0,
            'stdout': 'owner-b', 'stderr': '', 'drainTimedOut': False,
        },
    }
    message = _overlap_client_failure_message(tmp, states)
    assert 'clients exited non-zero' in message, message
    assert ("'owner-a': {'stillRunning': False, 'returncode': 1, "
            "'stdout': '', "
            "'stderr': 'Timeout (120s)'" in message), message
    assert "'owner-a'" in message, message
    assert 'Timeout (120s)' in message, message
    assert 'bridge log tail' in message, message
    assert 'delivery state' in message, message


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
