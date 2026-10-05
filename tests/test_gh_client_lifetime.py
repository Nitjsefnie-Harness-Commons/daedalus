#!/usr/bin/env python3
"""The liveness pipe a watcher child is handed, and what ends it.

`gh_client.spawn_watched` and `gh_client.watch_parent` are the half of the
module that has no `ci_wait` in it: the untracked watchers the skill ships
rest on this guarantee, and nothing measured it. The guarantee is the
pipe's, not a parent id's - a pid against `os.getppid()` is re-parented on
POSIX and historical on Windows, and a process-group kill orphans rather
than ends - so the rows here are its two directions, on a real child: write
end open and child alive, write end closed and gone. The platform arms are
not faked: `os.name` is never patched, because patching it would claim a
runtime this run is not. The coverage matrix unions the three platforms, so
a row written for the platform it runs on measures the `nt` arms there and
the POSIX arms here; what the Windows half cannot see is stated in the PR
body. Five rows reach inside the module, direct calls - a stray `os._exit`
here takes the suite down: the `watch_parent` refusals and the four ending
rows on `_end_inflight`, `_INFLIGHT_LOCK`, `_INFLIGHT` and `REAP_LIMIT`.
"""
import contextlib
import errno
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from _watcher_fixtures import RUNS_QUERY  # noqa: E402
from _watcher_fixtures import runs_page  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'

# The bound a child is given to end after its parent closed the pipe: a
# ceiling on a hang, not a margin on a result.
LIFETIME = 60

# What a child writes once armed - and all an unmarked child writes: a
# second line would sit in a pipe that row never drains.
ARMED = 'watching'

# Tells the child to save coverage before it ends: `os._exit` runs no
# atexit hook, so without this its covered lines read as never run. The
# wrapper saves, then calls the real `os._exit`.
SAVE = 'DAEDALUS_SAVE_BEFORE_EXIT'

CHILD = '''import os, sys, time
if os.environ.get({save!r}):
    import coverage
    _measure = coverage.Coverage()
    _measure.start()
    _ending_process = os._exit

    def _exit(code):
        _measure.stop()
        _measure.save()
        _ending_process(code)
    os._exit = _exit
sys.path.insert(0, {skill!r})
import gh_client
gh_client.watch_parent()
print({armed!r}, flush=True)
time.sleep(300)
'''

REPORT = ('import os, sys; sys.stdout.write('
          'os.environ.get("DAEDALUS_WATCH_PARENT_FD", ""))')

# A child asked to watch a name that is not a pipe; `regular` is opened
# by the child itself, a descriptor this process opened not being one
# the child would have, so the row measures the failure branch.
REFUSER = '''import os, sys
sys.path.insert(0, {skill!r})
import gh_client
name = sys.argv[1]
if name == 'regular':
    name = str(os.open(sys.argv[2], os.O_RDONLY))
os.environ[gh_client.PARENT_WATCH_ENV] = name
gh_client.watch_parent()
print('accepted', flush=True)
'''

# The poller child: a watcher holding one `gh` call in flight against
# the fake, so EOF finds the direct child the ending reaches.
GH_POLLER = '''import os, sys, time
sys.path.insert(0, {skill!r})
import gh_client
gh_client.watch_parent()
print({armed!r}, flush=True)
while True:
    try:
        gh_client.graphql(gh_client.RUNS_QUERY)
    except Exception:
        pass
    time.sleep(0.05)
'''

# The spawn-shim child: the poller with `subprocess.Popen` replaced by
# a subclass that records the child's pid the moment the real spawn
# returns, then holds until a gate file appears - the mid-spawn window.
SHIM_POLLER = '''import os, subprocess, sys, time
sys.path.insert(0, {skill!r})
_real_popen = subprocess.Popen

class HeldPopen(_real_popen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with open({pidfile!r}, 'w', encoding='utf-8') as handle:
            handle.write(str(self.pid))
        while not os.path.exists({gate!r}):
            time.sleep(0.02)

subprocess.Popen = HeldPopen
import gh_client
gh_client.watch_parent()
print({armed!r}, flush=True)
while True:
    try:
        gh_client.graphql(gh_client.RUNS_QUERY)
    except Exception:
        pass
    time.sleep(0.05)
'''

# What the marked ending writes before it calls the real one: `os._exit`
# flushes nothing, so this line is the one witness the ending ran.
ENDING = 'ending'

# The marked poller: the ending wrapped; `_exit_at_eof` reads the
# module global at call time, so the wrap holds.
MARKED_POLLER = '''import os, sys, time
sys.path.insert(0, {skill!r})
import gh_client
_real_end = gh_client._end_inflight

def _marked_end():
    sys.stdout.write({mark!r})
    sys.stdout.flush()
    _real_end()

gh_client._end_inflight = _marked_end
gh_client.watch_parent()
print({armed!r}, flush=True)
while True:
    try:
        gh_client.graphql(gh_client.RUNS_QUERY)
    except Exception:
        pass
    time.sleep(0.05)
'''


def _client():
    return _util.load(SKILL / 'gh_client.py', 'gh_client_lifetime')


def _watcher_child(tmp):
    """What `ci_watch.py` does at the top of `main`, in two lines."""
    path = os.path.join(tmp, 'watched_child.py')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(CHILD.format(skill=str(SKILL), armed=ARMED, save=SAVE))
    return path


def _still_open(descriptor):
    try:
        os.fstat(descriptor)
    except (OSError, ValueError):
        return False
    return True


def _sentinel():
    """A descriptor, and the two the next `os.pipe` will hand out.
    `os.pipe` allocates the two lowest free descriptors on both POSIX
    and the Windows CRT, so a descriptor held open just before the call
    names the pair exactly."""
    held = os.open(os.devnull, os.O_RDONLY)
    return held, held + 1, held + 2


def _probe_agrees_with_the_pipe():
    """Whether the check above can tell a closed descriptor from an open
    one: without this, a row asserting `not _still_open(n)` passes just
    as happily when `n` was never open at all. A pipe held then closed
    answers both ways."""
    read_fd, write_fd = os.pipe()
    assert _still_open(write_fd), write_fd
    os.close(read_fd)
    os.close(write_fd)
    assert not _still_open(write_fd), write_fd


def _ended(child):
    if child.poll() is None:
        child.kill()
    return child.wait(timeout=LIFETIME)


def test_a_spawn_hands_the_child_the_pipe_and_holds_the_other_end(tmp):
    """One pipe per child: the child is handed the read end in its
    environment, this process keeps the write end, and its own copy of
    the read end is closed. The near miss is `env`: `spawn_watched`
    takes it as a replacement, not an addition, so the read-back is
    two-sided, one name short of this process's."""
    client = _client()
    report = [sys.executable, '-c', REPORT]
    seen = {}
    child, write_fd = client.spawn_watched(
        report, env=dict(os.environ, DAEDALUS_MARK='set'),
        stdout=subprocess.PIPE, text=True)
    try:
        seen['value'] = child.communicate(timeout=LIFETIME)[0]
        assert child.returncode == 0, child.returncode
    finally:
        # In the `finally`: the row below reads descriptor numbers a
        # leaked write end would move.
        os.close(write_fd)
        _ended(child)

    handed = int(seen['value'])
    assert handed > 0, seen['value']
    if os.name != 'nt':
        # This process's own descriptor; on Windows it is the HANDLE's
        # number, which no `fstat` here can speak about.
        assert not _still_open(handed), handed

    # `PATH` is the one name the scoped environment leaves out; the
    # interpreter being absolute, it is never looked up.
    assert 'PATH' in os.environ
    scoped = {name: value for name, value in os.environ.items()
              if name != 'PATH'}
    scoped['DAEDALUS_MARK'] = 'set'
    probe = ('import os,sys; sys.stdout.write('
             'os.environ.get("DAEDALUS_MARK", "unset") + "|" + ('
             '"inherited" if "PATH" in os.environ else "clean"))')
    marked, write_fd = client.spawn_watched(
        [sys.executable, '-c', probe],
        env=scoped,
        stdout=subprocess.PIPE, text=True)
    try:
        assert marked.communicate(timeout=LIFETIME)[0] == 'set|clean'
    finally:
        os.close(write_fd)
        _ended(marked)


def test_the_child_ends_when_the_write_end_closes_and_not_before(tmp):
    """The guarantee, both directions, on a real child: a byte down the
    pipe is not the parent's death and must not end the child; then the
    write end closes and the child ends - the only event a session-line
    watcher of hours is ended by. The interval between the assertions is
    the allowed not-yet shape, over a window far longer than ending a
    process takes; the claim after is bounded so a hang fails."""
    client = _client()
    child, write_fd = client.spawn_watched(
        [sys.executable, _watcher_child(tmp)],
        env=dict(os.environ, **{SAVE: '1'}),
        stdout=subprocess.PIPE, text=True)
    closed = False
    try:
        armed = child.stdout.readline().strip()
        assert armed == ARMED, (
            f'the child never armed its watcher: {armed!r}')
        os.write(write_fd, b'x')
        time.sleep(1.0)
        assert child.poll() is None, 'a byte ended the child'
        os.close(write_fd)
        closed = True
        assert child.wait(timeout=LIFETIME) == 0, child.returncode
    finally:
        if not closed:
            os.close(write_fd)
        _ended(child)


def test_a_spawn_that_could_not_start_leaks_no_descriptor(tmp):
    """The refusal path, and both halves of it: the exception is the
    caller's to see, and the write end is closed before it travels - a
    caller that caught the failure and carried on would hold a pipe to a
    process that does not exist, one the watcher never gives back.

    Both ends are read back by number, from a descriptor held open
    across the call so the numbers are known; a row that checked only
    the write end would pass a module that gave neither back."""
    client = _client()
    _probe_agrees_with_the_pipe()
    absent = os.path.join(tmp, 'gh-that-was-never-installed')
    sentinel, read_fd, write_fd = _sentinel()
    try:
        try:
            client.spawn_watched([absent])
        except OSError as exc:
            assert exc.errno == errno.ENOENT, exc
        else:
            raise AssertionError('a spawn of nothing returned a child')
        assert not _still_open(read_fd), 'the failed spawn kept its read end'
        assert not _still_open(write_fd), (
            'the failed spawn kept its write end')
    finally:
        os.close(sentinel)


def test_watch_parent_started_by_hand_leaves_the_process_alone(tmp):
    """`watch_all.py`, the one watcher that does not call
    `watch_parent`, is the process the other two watch - arming the pipe
    there would have it watching itself. Nothing in this process sets
    the name the pipe travels under, so the call returns without
    starting a thread reading a descriptor nobody will close."""
    del tmp
    client = _client()
    before = {thread.name for thread in threading.enumerate()}
    client.watch_parent()
    assert {thread.name for thread in threading.enumerate()} == before


def test_a_name_that_is_not_an_inherited_pipe_is_refused(tmp):
    """Every way the name can fail to be a pipe, each ending the watcher
    rather than carrying on: not a descriptor at all, not a number, and
    a perfectly good descriptor that belongs to a file, where a child
    that accepted it would read somebody's file to its end and exit on
    the next close that was nobody's parent. Each runs in a child, the
    only place the third can be driven: accepting it starts the thread
    whose `os._exit` would take this process down."""
    client = _client()
    plain = os.path.join(tmp, 'not-a-pipe')
    with open(plain, 'w', encoding='utf-8') as handle:
        handle.write('x')
    refuser = os.path.join(tmp, 'refused_watcher.py')
    with open(refuser, 'w', encoding='utf-8') as handle:
        handle.write(REFUSER.format(skill=str(SKILL)))
    for name in ('regular', 'not-a-number', '99999999'):
        done = subprocess.run(
            [sys.executable, refuser, name, plain],
            capture_output=True, text=True, timeout=LIFETIME)
        assert done.returncode != 0, (name, done.stdout)
        assert client.PARENT_WATCH_ENV in done.stderr, (name, done.stderr)


def _poller_child(tmp):
    """The watcher the regression row runs: one that keeps gh in flight."""
    path = os.path.join(tmp, 'gh_poller.py')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(GH_POLLER.format(skill=str(SKILL), armed=ARMED))
    return path


def _pid_gone(pid, bound=LIFETIME):
    """Bounded linear wait for a pid to leave the process table."""
    deadline = time.monotonic() + bound
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _marked_poller_child(tmp):
    """The observed ending's watcher: the poller, ending wrapped."""
    path = os.path.join(tmp, 'marked_poller.py')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(MARKED_POLLER.format(skill=str(SKILL), armed=ARMED,
                                          mark=ENDING + '\n'))
    return path


def _alive_on_windows(pid):
    """Whether Windows answers the pid alive: `os.kill(pid, 0)` is not a
    probe there - signal 0 is CTRL_C_EVENT, aimed at the caller's own
    console - so liveness is asked of `OpenProcess`, a pid it cannot
    open reading as gone."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def test_the_in_flight_gh_ends_with_its_watcher(tmp):
    """Issue 1389's defect on the real surfaces: a watcher process with
    a `gh` call held in flight by the fake's gate, and the write end
    closing under it - the `gh` process must end with its watcher, and
    the pid from the fake's call log is what shows it. Off Windows:
    there the `gh` is `cmd.exe`, whose python grandchild the ending's
    direct-child kill does not reach."""
    if os.name == 'nt':
        _util.skip('the gh process on Windows is cmd.exe, and the pid this '
                   'row can learn names its python grandchild instead')
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {RUNS_QUERY: runs_page([])}, gate=True)
    pid = None
    child, write_fd = client.spawn_watched(
        [sys.executable, _poller_child(tmp)],
        env=fake.env(dict(os.environ, **{SAVE: '1'})),
        stdout=subprocess.PIPE, text=True)
    closed = False
    try:
        armed = child.stdout.readline().strip()
        assert armed == ARMED, (
            f'the child never armed its watcher: {armed!r}')
        deadline = time.monotonic() + LIFETIME
        while not any(entry['stage'] == 'entered'
                      for entry in fake.stages()):
            assert time.monotonic() < deadline, (
                'the gh call never reached the hold')
            time.sleep(0.05)
        pid = fake.calls()[-1]['pid']
        os.close(write_fd)
        closed = True
        assert child.wait(timeout=LIFETIME) == 0, child.returncode
        assert _pid_gone(pid), (
            f'the gh child outlived its watcher: {pid}')
    finally:
        fake.open_gate()
        if pid is not None and not _pid_gone(pid, LIFETIME):
            os.kill(pid, signal.SIGKILL)
        if not closed:
            os.close(write_fd)
        _ended(child)


def test_on_windows_an_eof_ends_a_watcher_blocked_in_a_gh_call(tmp):
    """The Windows arm of the in-flight EOF ending: a watcher held inside
    a `gh` call when the pipe closes ends through the module's own
    ending - the wrapper this child installs writes the line then calls
    `_end_inflight`, witnessing the production ending - and the process
    is gone at the row's own bound. Not pinned: the gh process's death,
    the pid the fake logs naming the python grandchild under `cmd.exe`,
    read only so cleanup can end it. The POSIX twin, which skips the
    legs this row completes, is
    `test_the_in_flight_gh_ends_with_its_watcher`.
    """
    if os.name != 'nt':
        _util.skip('the POSIX twin test_the_in_flight_gh_ends_with_its_'
                   'watcher proves this; this row is its Windows leg')
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {RUNS_QUERY: runs_page([])}, gate=True)
    pid = None
    child, write_fd = client.spawn_watched(
        [sys.executable, _marked_poller_child(tmp)],
        env=fake.env(dict(os.environ)),
        stdout=subprocess.PIPE, text=True)
    closed = False
    try:
        armed_box = []
        reader = threading.Thread(
            target=lambda: armed_box.append(child.stdout.readline()),
            daemon=True)
        reader.start()
        reader.join(LIFETIME)
        if reader.is_alive():
            child.kill()
            raise AssertionError(
                f'no arm within the {LIFETIME}s bound; '
                f'fake stages={fake.stages()}')
        armed = armed_box[0].strip()
        assert armed == ARMED, (
            f'the child never armed its watcher: {armed!r}')
        deadline = time.monotonic() + LIFETIME
        while not any(entry['stage'] == 'entered'
                      for entry in fake.stages()):
            assert time.monotonic() < deadline, (
                'the gh call never reached the hold')
            time.sleep(0.05)
        pid = fake.calls()[-1]['pid']
        os.close(write_fd)
        closed = True
        try:
            out = child.communicate(timeout=LIFETIME)[0]
        except subprocess.TimeoutExpired as expired:
            child.kill()
            partial = expired.stdout
            if isinstance(partial, bytes):
                partial = partial.decode('utf-8', 'replace')
            raise AssertionError(
                'the watcher outlived the EOF: the ending marker '
                f'seen={ENDING in (partial or "")}, '
                f'fake stages={fake.stages()}') from expired
        captured = armed + (out or '')
        assert child.returncode == 0, child.returncode
        assert ARMED in captured and ENDING in captured, captured
    finally:
        fake.open_gate()
        if pid is not None and _alive_on_windows(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        if not closed:
            os.close(write_fd)
        _ended(child)


def test_an_ending_without_a_child_in_flight_is_a_no_op(tmp):
    """The ending runs at EOF even between two `gh` calls, so an empty
    slot must reach the exit rather than raise on it."""

    del tmp
    client = _client()
    with client._INFLIGHT_LOCK:
        client._end_inflight()
    assert client._INFLIGHT is None


def test_an_ending_over_a_child_already_gone_is_a_no_op(tmp):
    """A `gh` that completed before the EOF sits in the slot as a
    finished process; the ending over it must be the harmless thing that
    is - kill sends nothing at a reaped child, the wait returns at
    once."""

    del tmp
    client = _client()
    done = subprocess.Popen([sys.executable, '-c', 'pass'])
    done.wait()
    # `setattr`: the module was executed from a path, as in the answers
    # suite.
    setattr(client, '_INFLIGHT', done)
    with client._INFLIGHT_LOCK:
        client._end_inflight()
    assert client._INFLIGHT is None
    assert done.returncode == 0


def test_the_reap_bound_ends_the_ending_without_the_child(tmp):
    """The reap runs on the thread that must reach `os._exit`, so it is
    bounded, stood in for the way the timing rule prescribes: a real
    child whose reaping never completes; the record names the bound,
    and the ending returns anyway."""

    client = _client()
    seen = {}

    class Stuck(subprocess.Popen):
        def wait(self, timeout=None):
            seen['bound'] = timeout
            bound = float(timeout or client.REAP_LIMIT)
            time.sleep(bound)
            raise subprocess.TimeoutExpired(self.args, bound)

    child = Stuck([sys.executable, '-c', 'import time; time.sleep(120)'])
    setattr(client, '_INFLIGHT', child)
    done = []

    def ending():
        with client._INFLIGHT_LOCK:
            client._end_inflight()
        done.append(True)

    worker = threading.Thread(target=ending, daemon=True)
    worker.start()
    worker.join(client.REAP_LIMIT * 2)
    assert done, 'the ending never returned from the reap'
    assert seen['bound'] == client.REAP_LIMIT, seen
    assert client._INFLIGHT is None
    subprocess.Popen.wait(child, timeout=LIFETIME)


def _shim_child(tmp, gate, pidfile):
    """The watcher whose spawn is held just after the real one returns."""
    path = os.path.join(tmp, 'shimmed_watcher.py')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(SHIM_POLLER.format(skill=str(SKILL), armed=ARMED,
                                        gate=gate, pidfile=pidfile))
    return path


def test_an_eof_during_the_spawn_waits_out_the_registration(tmp):
    """EOF landing between the gh child's birth and its registration must
    not strand it. The child's Popen is shimmed to hold after the real
    spawn returns, so the write end can close while the slot is still
    empty; the ending must wait out the shim, take the registered child
    and kill it. Without the lock the ending reads the empty slot and
    leaves, and the mid-spawn gh outlives the process. The mid-row
    still-here claim is the allowed not-yet kind, over a window far
    longer than ending a process."""
    if os.name == 'nt':
        _util.skip('the gh process on Windows is cmd.exe, and the pid this '
                   'row can learn names its python grandchild instead')
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {RUNS_QUERY: runs_page([])}, gate=True)
    shim_gate = os.path.join(tmp, 'shim-gate')
    pidfile = os.path.join(tmp, 'shim-pid')
    pid = None
    child, write_fd = client.spawn_watched(
        [sys.executable, _shim_child(tmp, shim_gate, pidfile)],
        env=fake.env(dict(os.environ, **{SAVE: '1'})),
        stdout=subprocess.PIPE, text=True)
    closed = False
    released = False
    try:
        armed = child.stdout.readline().strip()
        assert armed == ARMED, (
            f'the child never armed its watcher: {armed!r}')
        deadline = time.monotonic() + LIFETIME
        while not os.path.exists(pidfile):
            assert time.monotonic() < deadline, 'the gh never started'
            time.sleep(0.05)
        with open(pidfile, encoding='utf-8') as handle:
            pid = int(handle.read())
        os.close(write_fd)
        closed = True
        time.sleep(1.0)
        if child.poll() is not None:
            raise AssertionError(
                f'the mid-spawn gh outlived its watcher: {pid}')
        with open(shim_gate, 'w', encoding='utf-8') as handle:
            handle.write('go')
        released = True
        assert child.wait(timeout=LIFETIME) == 0, child.returncode
        assert _pid_gone(pid), (
            f'the gh child outlived its watcher: {pid}')
    finally:
        if not released:
            with open(shim_gate, 'w', encoding='utf-8') as handle:
                handle.write('go')
        fake.open_gate()
        if pid is not None and not _pid_gone(pid, LIFETIME):
            os.kill(pid, signal.SIGKILL)
        if not closed:
            os.close(write_fd)
        _ended(child)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='ghclientlife_')


if __name__ == '__main__':
    raise SystemExit(main())
