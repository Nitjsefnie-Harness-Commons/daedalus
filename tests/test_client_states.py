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
# liveness escape rather than a wall-clock assertion. A boot on a loaded
# machine outlasts the publish bound below by more than its own margin,
# while on an idle one it is quicker, so the bound has to cover the slow
# case rather than be compared against the fast one. It fires only on a
# client that is alive and wedged, and then only to name what never arrived.
BOOT_DEADLINE = 120

# A control below reads this bound back off the wait and pins it, which is
# the only way to notice it being shortened: the client publishes the moment
# it boots, so the wait never comes near its own deadline and no elapsed time
# ever reflects it.
PUBLISH_BOUND = 5


class _SteppingClock:
    """A monotonic reading that advances a fixed step on every call.

    Two things need a clock they can drive. A control that proves a bound
    fires cannot wait BOOT_DEADLINE out for real, so a step large enough to
    cross a short bound reaches that expiry by arithmetic. A control that
    proves where a window OPENED cannot use a reading that stands still,
    since a clock that never moves makes the gap between two windows zero
    whichever order they were taken in. `reads` is here so a control can
    tell that the clock it installed is the one the wait actually used.

    Stepping is also what makes the interval exact. A control here
    compares the interval a wait returns for equality, and
    `(base + N) - base` reproduces N only when N is exactly
    representable. A real clock is not the reason to step: at the scales
    either a boot-relative or an epoch reading reaches, both bounds come
    back exactly, and only a base near 1e16 loses a five-second bound to
    its own ulp. What stepping buys is that the wait costs no wall time,
    which is the whole point of a control whose subject is the bound.
    So a control depending on exactness says so here rather than
    inheriting it.
    """

    def __init__(self, step):
        self.now = 0.0
        self.step = step
        self.reads = 0

    def __call__(self):
        self.now += self.step
        self.reads += 1
        return self.now


def _wait_for_path(process, booted, path, clock=time.monotonic,
                   boot_bound=BOOT_DEADLINE):
    """Wait for the client to boot, then for the path it publishes.

    The bound above governs the boot so the one below covers the publish and
    not a fresh interpreter's startup. The boot marker is a file rather than
    the child's printed line because every caller reads that line through
    client_states, which reads the stdout pipe itself. The two deadlines get
    separate names because sharing one is a hazard no reader can see: a pair
    of returns reading a single name would hand back the publish bound twice
    and still look right.
    """
    boot_opened = clock()
    boot_deadline = boot_opened + boot_bound
    while not booted.exists():
        assert process.poll() is None, (
            f'the client exited with {process.returncode} before booting: '
            + (process.stderr.read() if process.stderr else ''))
        assert clock() < boot_deadline, (
            f'the client is alive and never published {booted.name}')
        time.sleep(0.01)
    opened = clock()
    deadline = opened + PUBLISH_BOUND
    while not path.exists() and clock() < deadline:
        # The boot loop above is not the only wait here that can hang. This
        # one has a deadline, but a client that dies does not reach it: the
        # condition is re-read and re-true for as long as the clock is, so
        # the check the boot loop has is what turns a dead client into a
        # named exit rather than a wait out the rest of the bound.
        assert process.poll() is None, (
            f'the client exited with {process.returncode} before publishing: '
            + (process.stderr.read() if process.stderr else ''))
        time.sleep(0.01)
    assert path.exists(), f'{path.name} was not published'
    return (boot_deadline - boot_opened, deadline - opened,
            opened - boot_opened)


def test_client_states_kills_and_reports_a_client_past_its_grace(tmp):
    """A client that misses its grace is diagnostic data, not an exception."""
    ready_path = Path(tmp) / 'client.ready'
    booted_path = Path(tmp) / 'client.booted'
    client = (
        'import sys, time\n'
        'from pathlib import Path\n'
        'print("started", flush=True)\n'
        'Path(sys.argv[2]).write_text("booted", encoding="ascii")\n'
        # The boot marker must exist before the publish, or the publish
        # bound opens on a fresh interpreter and this branch's defect is
        # back. Written in the child because the parent cannot see the
        # order, only that the wait returned; the child exits non-zero if
        # the order is wrong and the parent's escape names that.
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

    The third is the gap between the two windows, and it is what pins
    where the publish window OPENS rather than how long it is. The clock
    advances a step per reading, so the gap is a count of steps and not a
    duration: the boot loop reads the clock at least once before the
    marker can exist, so the publish window's base is always a later
    reading. Sampled at the same reading instead, the gap is zero and the
    publish bound opens before the child has booted -- the pre-change
    defect this whole branch is about.
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
        clock = _SteppingClock(0.01)
        boot_applied, applied, gap = _wait_for_path(
            process, booted_path, ready_path, clock=clock)
        assert boot_applied == 120, boot_applied
        assert applied == 5, applied
        # The publish window opened at a later reading than the boot one.
        # Stepped, so this is the order the two were taken in and not a
        # duration: sampled at the same reading the gap is zero, and the
        # publish bound would open before the child had booted.
        assert gap >= clock.step, gap
        # And the clock that took those readings is the one this control
        # installed, not time.monotonic, which would put this control's
        # verdict back on how fast the machine boots.
        assert getattr(clock, 'reads', 0), 'the control ran on a real clock'
    finally:
        _drain.kill_and_drain(process)


def test_the_boot_wait_names_a_client_that_stays_alive_and_never_boots(tmp):
    """A wedged client is named by the bound rather than waited on forever.

    Nothing else produces this state: every shipped fixture writes its boot
    marker in its first few statements and before anything slow, and every
    one of them dies at 60 seconds, well inside BOOT_DEADLINE, so the exit
    escape wins and the bound's own report is otherwise dead code. A client
    that starts and then wedges is the case the bound exists for, and a
    premature one refuses a healthy client with a message about the bound
    rather than about the client.

    This control's subject IS the expiry, so the wait is the thing under test
    here; no duration is compared against a threshold anywhere in it.
    """
    ready_path = Path(tmp) / 'wedged.ready'
    booted_path = Path(tmp) / 'wedged.booted'
    client = (
        'import sys, time\n'
        'print("started", flush=True)\n'
        'time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    message = ''
    try:
        _wait_for_path(process, booted_path, ready_path,
                       clock=_SteppingClock(10.0), boot_bound=1)
    except AssertionError as expiry:
        message = str(expiry)
    else:
        message = 'a wedged client was waited on instead of refused'
    finally:
        _drain.kill_and_drain(process)
    # The BOOT marker, not the ready path: that is also the check that the
    # two deadlines are distinct, since only the boot wait ran at all.
    expected = f'the client is alive and never published {booted_path.name}'
    assert message == expected, message


def test_the_boot_wait_names_a_client_that_exits_before_it_boots(tmp):
    """A client that dies before booting is named by its exit, not the bound.

    This is the other half of the boot wait's two escapes, and the one with
    no control of its own. Without the check the wait runs to its full bound
    and then reports that a client which is not alive was never published --
    a failure about the bound rather than about the client, naming neither
    the status nor anything the child said.
    """
    ready_path = Path(tmp) / 'dead.ready'
    booted_path = Path(tmp) / 'dead.booted'
    client = (
        'import sys\n'
        'sys.stderr.write("client refused to start\\n")\n'
        'sys.exit(3)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    message = ''
    try:
        # Reaped first, so the escape is reached by a client that is
        # already gone rather than racing one that has not exited yet.
        process.wait(timeout=10)
        _wait_for_path(process, booted_path, ready_path,
                       clock=_SteppingClock(10.0), boot_bound=1)
    except AssertionError as failure:
        message = str(failure)
    else:
        message = 'a client that exited was waited on'
    finally:
        _drain.kill_and_drain(process)
    assert message.startswith(
        'the client exited with 3 before booting'), message
    # M3: the child's own words, which is where an ordering violation
    # reports itself and what a reader needs to see it.
    assert 'client refused to start' in message, message


def test_the_publish_wait_names_a_client_that_dies_between_the_two(tmp):
    """A client that boots and then dies is named by its exit, not the bound.

    The boot wait's escape has a control of its own and this is the publish
    wait's, for the same reason: the check is worth nothing while nothing
    reaches it, and every other fixture here publishes one statement after
    it boots, so the loop exits on its condition without ever entering its
    body. The way in is a client that announces itself and then dies before
    publishing -- it boots, so the first loop is satisfied, and the second
    finds no file and a client that is already gone.
    """
    ready_path = Path(tmp) / 'half.ready'
    booted_path = Path(tmp) / 'half.booted'
    client = (
        'import sys\n'
        'from pathlib import Path\n'
        'print("started", flush=True)\n'
        'Path(sys.argv[2]).write_text("booted", encoding="ascii")\n'
        'assert Path(sys.argv[2]).exists(), "published before boot"\n'
        'sys.stderr.write("client gave up after announcing\\n")\n'
        'sys.exit(4)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', client, str(ready_path), str(booted_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    message = ''
    try:
        process.wait(timeout=10)
        _wait_for_path(process, booted_path, ready_path,
                       clock=_SteppingClock(0.01), boot_bound=1)
    except AssertionError as failure:
        message = str(failure)
    else:
        message = 'a client that died after announcing was waited on'
    finally:
        _drain.kill_and_drain(process)
    assert message.startswith(
        'the client exited with 4 before publishing'), message
    assert 'client gave up after announcing' in message, message


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
