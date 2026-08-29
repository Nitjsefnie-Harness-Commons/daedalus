#!/usr/bin/env python3
"""Pins on the overlap client diagnostics the harness suite does not hold.

`tests/test_overlap_harness.py` sits at its module-size ceiling, so the pins
added by the issue 280 review round live here: what `client_states` does with
`grace=None`, which diagnosis kind answers first, what the silent diagnosis
refuses to name, and the real caller that routes its states through them.
"""
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
    stalled = {
        'stillRunning': True, 'returncode': None,
        'stdout': '', 'stderr': '', 'drainTimedOut': False,
    }

    def failing_states(processes, grace, **kwargs):
        return {owner: dict(stalled) for owner in processes}

    message = None
    with mock.patch.object(_overlap, 'client_states', failing_states):
        try:
            _overlap.run_same_id_client_overlap(
                tmp, ['owner-a', 'owner-b'],
                _overlap.cookie_client_argv, _overlap.client_env(),
                'overlap-client-token',
                _util.ROOT / 'extension' / 'background.js')
        except AssertionError as failure:
            message = str(failure)
        else:
            raise AssertionError('the caller never diagnosed its clients')
    assert 'clients still running after grace' in message, message


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
