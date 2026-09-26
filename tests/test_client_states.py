#!/usr/bin/env python3
"""The client-state helper's own record, and what a client exit announces."""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _overlap_clients  # noqa: E402
import _util  # noqa: E402

# Sized so it cannot be spent on a real boot, which is what makes it a
# liveness escape rather than a wall-clock assertion: measured Popen -> ready
# peaks on this box are 7.4s (mine, n=30 per site, load 68) and 19.2s (the
# task review's, load 124-134), so 120s is ~16x and ~6x. It fires only on a
# client that is alive and wedged, and then only to name what never arrived.
BOOT_DEADLINE = 120

# The bound the publish wait is declared to carry. A control below reads the
# bound back off the wait and pins it, which is the only way to notice it
# being shortened: the client publishes the moment it boots, so the wait
# never comes near its own deadline and no elapsed time ever reflects it.
PUBLISH_BOUND = 5


class _FixedClock:
    """A monotonic reading that never advances.

    The control that pins the bounds needs a deadline's arithmetic to be
    reproducible, and `(base + N) - base` reproduces N only when N is
    exactly representable. A whole number of seconds is, at any scale; a
    small one is not, at any scale either -- a small base shrinks the size
    of the residue by about five orders of magnitude, it does not remove
    it. So the fix is a base small enough to keep that residue far below
    anything a bound here would have to tell apart, not a base that makes
    the subtraction exact, which no base can do.
    """

    def __init__(self, at=100.0):
        self.at = at

    def __call__(self):
        return self.at


def _wait_for_path(process, booted, path, clock=time.monotonic):
    """Wait for the client to boot, then for the path it publishes.

    Returns the boot bound and the publish bound this call applied.

    The bound above governs the boot so the one below covers the publish and
    not a fresh interpreter's startup. The boot marker is a file rather than
    the child's printed line because every caller reads that line through
    client_states, which reads the stdout pipe itself. The two deadlines are
    named apart because the second is reassigned below: a pair of returns
    reading one name would hand back the publish bound twice.
    """
    boot_opened = clock()
    boot_deadline = boot_opened + BOOT_DEADLINE
    while not booted.exists():
        assert process.poll() is None, (
            f'the client exited with {process.returncode} before booting')
        assert clock() < boot_deadline, (
            f'the client is alive and never published {booted.name}')
        time.sleep(0.01)
    opened = clock()
    deadline = opened + PUBLISH_BOUND
    while not path.exists() and clock() < deadline:
        time.sleep(0.01)
    assert path.exists(), f'{path.name} was not published'
    return boot_deadline - boot_opened, deadline - opened


def test_client_states_kills_and_reports_a_client_past_its_grace(tmp):
    """A client that misses its grace is diagnostic data, not an exception."""
    ready_path = Path(tmp) / 'client.ready'
    booted_path = Path(tmp) / 'client.booted'
    client = (
        'import sys, time\n'
        'from pathlib import Path\n'
        'print("started", flush=True)\n'
        'Path(sys.argv[2]).write_text("booted", encoding="ascii")\n'
        'assert Path(sys.argv[2]).exists(), "published before boot"\n'
        'Path(sys.argv[1]).write_text("ready", encoding="ascii")\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_path(process, booted_path, ready_path)
        states = _overlap_clients.client_states(
            {'slow-owner': process}, grace=0.1)
    finally:
        _drain.kill_and_drain(process)
    state = states['slow-owner']
    assert state['stillRunning'] is True, state
    assert state['returncode'] is None, state
    assert state['stdout'] == 'started', state
    assert state['stderr'] == '', state


def test_the_publish_wait_applies_the_bound_the_suite_declares(tmp):
    """A shortened publish bound has to be caught by its arithmetic.

    The client publishes the moment it boots, so the wait returns on its
    first check and no elapsed time ever comes near either bound it was
    given. Both are therefore read back off the wait rather than measured,
    and the wait is given a clock that never advances, so this says nothing
    about how fast the machine is.

    The two expectations are LITERALS and must stay that way. Compared
    against BOOT_DEADLINE and PUBLISH_BOUND they would compare each wait
    against the very constant it was built from, so shortening either
    bound would move both sides of the assertion and the shortened bound
    would pass -- which is the defect this control exists to catch.
    """
    ready_path = Path(tmp) / 'bound.ready'
    booted_path = Path(tmp) / 'bound.booted'
    client = (
        'import sys, time\n'
        'from pathlib import Path\n'
        'print("started", flush=True)\n'
        'Path(sys.argv[2]).write_text("booted", encoding="ascii")\n'
        'assert Path(sys.argv[2]).exists(), "published before boot"\n'
        'Path(sys.argv[1]).write_text("ready", encoding="ascii")\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        boot_applied, applied = _wait_for_path(
            process, booted_path, ready_path, clock=_FixedClock())
        assert boot_applied == 120, boot_applied
        assert applied == 5, applied
    finally:
        _drain.kill_and_drain(process)


class _KillRecordsOwnStatus:
    """A client the harness killed, whose kill left its own status behind.

    Windows `Popen.kill()` is `TerminateProcess(handle, 1)` and POSIX's is
    SIGKILL, so `proc.returncode` after that kill describes the kill rather
    than the client.
    """

    stdout = None
    stderr = None
    returncode = None

    def __init__(self, kill_status):
        self._kill_status = kill_status
        self._drained = False

    def communicate(self, timeout: float = 0.1):
        if self._drained:
            return '', ''
        self._drained = True
        raise subprocess.TimeoutExpired('stub-client', timeout)

    def kill(self):
        self.returncode = self._kill_status


def test_client_states_records_no_exit_status_for_a_client_it_killed(tmp):
    """A killed client's record cannot carry an exit status at all."""
    del tmp
    states = _overlap_clients.client_states({
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
    booted_path = Path(tmp) / 'slow-pipes.booted'
    client = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        'print("slow-pipe-marker", flush=True)\n'
        'print("slow-pipe-error", file=sys.stderr, flush=True)\n'
        'grandchild = subprocess.Popen('
        '[sys.executable, "-c", "import time; time.sleep(1)"])\n'
        'target = Path(sys.argv[1])\n'
        'Path(sys.argv[2]).write_text("booted", encoding="ascii")\n'
        'assert Path(sys.argv[2]).exists(), "published before boot"\n'
        'pending = target.with_suffix(".tmp")\n'
        'pending.write_text("ready", encoding="ascii")\n'
        'pending.replace(target)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_path(process, booted_path, ready_path)
        states = _overlap_clients.client_states(
            {'slow-pipe-owner': process}, grace=0.1)
    finally:
        _drain.kill_and_drain(process)
    state = states['slow-pipe-owner']
    assert state['stillRunning'] is True, state
    assert state['drainTimedOut'] is False, state
    assert state['stdout'] == 'slow-pipe-marker', state
    assert state['stderr'] == 'slow-pipe-error', state


def test_client_states_records_a_killed_clients_held_pipes(tmp):
    """A grandchild-held pipe forces the drain timeout, and the record holds.

    The killed client has already written to its pipe and its grandchild keeps
    that pipe open, so the second drain expires whatever the reader won in the
    window. The contents are pinned against a stub by the client-state suite,
    not raced here.
    """
    ready_path = Path(tmp) / 'grandchild.ready'
    booted_path = Path(tmp) / 'grandchild.booted'
    client = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        'print("held-pipe-marker", flush=True)\n'
        'grandchild = subprocess.Popen('
        '[sys.executable, "-c", "import time; time.sleep(10)"])\n'
        'target = Path(sys.argv[1])\n'
        'Path(sys.argv[2]).write_text("booted", encoding="ascii")\n'
        'assert Path(sys.argv[2]).exists(), "published before boot"\n'
        'pending = target.with_suffix(".tmp")\n'
        'pending.write_text("ready", encoding="ascii")\n'
        'pending.replace(target)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_path(process, booted_path, ready_path)
        states = _overlap_clients.client_states(
            {'pipe-owner': process}, grace=0.1, killed_pipe_release=0.1)
    finally:
        _drain.kill_and_drain(process)
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

    state = _overlap_clients.client_states(
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
        _overlap_clients.assert_clients_exited(states, posted)
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
        _overlap_clients.assert_clients_exited(states, posted)
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
