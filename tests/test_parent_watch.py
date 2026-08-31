#!/usr/bin/env python3
"""A bridge fixture child follows its parent process lifetime."""
import ctypes
import ctypes.wintypes
import contextlib
import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

WATCH_ENV = 'DAEDALUS_PARENT_WATCH_FD'
WAIT_TIMEOUT = 90
RUNAWAY_CALL_LIMIT = 1000
_PARENT_WATCH = _util._PARENT_WATCH


class _InjectedFailure(Exception):
    pass


class _PidProcess:
    """The bridge grandchild, exposed through the poll contract tests need."""

    def __init__(self, pid):
        self.pid = pid
        self._handle = None
        self._wait = None
        self._close = None
        if os.name == 'nt':
            win_dll = getattr(ctypes, 'WinDLL')
            kernel32 = win_dll('kernel32', use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (
                ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
                ctypes.wintypes.DWORD)
            open_process.restype = ctypes.wintypes.HANDLE
            self._wait = kernel32.WaitForSingleObject
            self._wait.argtypes = (
                ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD)
            self._wait.restype = ctypes.wintypes.DWORD
            self._close = kernel32.CloseHandle
            self._close.argtypes = (ctypes.wintypes.HANDLE,)
            self._close.restype = ctypes.wintypes.BOOL
            self._handle = open_process(0x00100000, False, pid)
            if not self._handle:
                win_error = getattr(ctypes, 'WinError')
                raise win_error(ctypes.get_last_error())

    def poll(self):
        if os.name == 'nt':
            result = self._wait(self._handle, 0)
            if result == 0:
                return 0
            assert result == 0x102, result
            return None
        stat = Path(f'/proc/{self.pid}/stat')
        if stat.exists():
            try:
                fields = stat.read_text(
                    encoding='ascii').rsplit(')', 1)[1].split()
                if fields[0] == 'Z':
                    return 0
            except (IndexError, OSError):
                # A malformed snapshot falls through to the portable probe.
                pass
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0
        return None

    def kill(self):
        os.kill(self.pid, signal.SIGTERM)

    def close(self):
        if self._handle:
            self._close(self._handle)
            self._handle = None


def _child_info(proc):
    drained = _util.drain_lines(proc)
    deadline = time.time() + _util.COLD_START_TIMEOUT
    seen = 0
    while True:
        for line in drained[seen:]:
            seen += 1
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        if proc.poll() is not None:
            raise AssertionError(
                f'parent exited before reporting child: {drained!r}')
        assert time.time() < deadline, (
            f'parent did not report child: {drained!r}')
        time.sleep(0.05)


def _start_parent(tmp, watched):
    args = [
        sys.executable,
        str(Path(__file__).resolve()),
        '--parent',
        str(tmp),
        'watched' if watched else 'unwatched',
    ]
    proc = subprocess.Popen(
        args, cwd=_util.ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        return proc, _child_info(proc)
    except BaseException:
        _stop(proc)
        raise


def _port_accepts(info):
    host, port = info['base'].removeprefix('http://').rsplit(':', 1)
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, int(port))) == 0


def _process_diagnostic(proc, info=None):
    details = [f'pid={proc.pid}', f'alive={proc.poll() is None}']
    if info is not None:
        details.extend([
            f"port_accepts={_port_accepts(info)}",
            f"parent_watch={info['parent_watch']}",
        ])
    return ' '.join(details)


def _wait_for_exit(proc, info=None, timeout=WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            raise AssertionError(
                'process did not exit: ' + _process_diagnostic(proc, info))
        time.sleep(0.01)


def _stop(proc, info=None):
    if proc is None or proc.poll() is not None:
        return
    proc.kill()
    _wait_for_exit(proc, info)


def _assert_released(child, info):
    deadline = time.monotonic() + WAIT_TIMEOUT
    while _port_accepts(info):
        if time.monotonic() >= deadline:
            raise AssertionError(
                'bridge port remained open: '
                + _process_diagnostic(child, info))
        time.sleep(0.01)
    docroot = Path(info['docroot'])
    shutil.rmtree(docroot)
    assert not docroot.exists()


def _fd_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def _assert_fd_closed(fd):
    assert not _fd_open(fd), f'file descriptor {fd} remains open'


def _cleanup_spawn(proc, write_fd):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            _wait_for_exit(proc)
        except AssertionError:
            proc.kill()
            _wait_for_exit(proc)
    if write_fd is not None and _fd_open(write_fd):
        os.close(write_fd)


@contextlib.contextmanager
def _record_watch_pipe():
    real_pipe = os.pipe
    captured = []

    def recording_pipe():
        ends = real_pipe()
        if not captured:
            captured.extend(ends)
        return ends

    os.pipe = recording_pipe
    try:
        yield captured
    finally:
        os.pipe = real_pipe
        for descriptor in captured:
            if _fd_open(descriptor):
                os.close(descriptor)


def _spawn_short_child(env, args=None):
    return _PARENT_WATCH.spawn(
        args or [sys.executable, '-c', 'pass'], cwd=_util.ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def _assert_fixture_failure_cleanup(tmp, stage):
    parent_watch = _PARENT_WATCH
    real_spawn = parent_watch.spawn
    real_drain = _util.drain_lines
    real_timeout = _util.startup_timeout
    captured = {}

    def recording_spawn(*args, **kwargs):
        proc, write_fd = real_spawn(*args, **kwargs)
        captured.update(proc=proc, write_fd=write_fd)
        return proc, write_fd

    def fail(*_args, **_kwargs):
        raise _InjectedFailure(stage)

    class FailingAppend:
        @staticmethod
        def append(_proc):
            raise _InjectedFailure(stage)

    parent_watch.spawn = recording_spawn
    proc_out = FailingAppend() if stage == 'proc_out.append' else None
    if stage == 'drain_lines':
        _util.drain_lines = fail
    if stage == 'startup_timeout':
        _util.startup_timeout = fail
    try:
        try:
            with _util.bridge(tmp, proc_out=proc_out):
                if stage == 'yield body':
                    raise _InjectedFailure(stage)
        except _InjectedFailure:
            pass
        else:
            raise AssertionError(f'{stage} did not raise')
        assert captured['proc'].poll() is not None, (
            f"{stage} left pid {captured['proc'].pid} alive")
        _assert_fd_closed(captured['write_fd'])
    finally:
        parent_watch.spawn = real_spawn
        _util.drain_lines = real_drain
        _util.startup_timeout = real_timeout
        _cleanup_spawn(captured.get('proc'), captured.get('write_fd'))


def test_bridge_fixture_child_exits_when_its_parent_is_killed(tmp):
    """Removing the fixture process must not leave its bridge behind."""
    if not hasattr(os, 'pipe'):
        _util.skip('os.pipe is unavailable')
    parent = None
    child = None
    info = None
    try:
        parent, info = _start_parent(tmp, watched=True)
        child = _PidProcess(info['pid'])
        parent.kill()
        _wait_for_exit(parent)
        _wait_for_exit(child, info)
        _assert_released(child, info)
    finally:
        _stop(parent)
        _stop(child, info)
        if child is not None:
            child.close()


def test_unwatched_bridge_survives_its_parent(tmp):
    """Normal server starts remain independent of the launching process."""
    parent = None
    child = None
    info = None
    try:
        parent, info = _start_parent(tmp, watched=False)
        child = _PidProcess(info['pid'])
        parent.kill()
        _wait_for_exit(parent)
        assert child.poll() is None
        status, health = _util.get_json(info['base'] + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
    finally:
        _stop(parent)
        _stop(child, info)
        if child is not None:
            child.close()


def test_bounded_wait_reports_live_child_port_and_watch_state(tmp):
    class LiveProcess:
        pid = 424242

        def __init__(self):
            self.polls = iter((None, 0))
            self.calls = 0

        def poll(self):
            self.calls += 1
            if self.calls > RUNAWAY_CALL_LIMIT:
                raise AssertionError('process double exceeded call limit')
            return next(self.polls, 0)

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        info = {
            'base': f'http://127.0.0.1:{listener.getsockname()[1]}',
            'parent_watch': 'enabled',
        }
        process = LiveProcess()
        diagnostic = None
        try:
            _wait_for_exit(process, info, timeout=0)
        except AssertionError as exc:
            diagnostic = str(exc)
        else:
            raise AssertionError('live process wait did not expire')
    assert diagnostic is not None
    assert 'pid=424242' in diagnostic, diagnostic
    assert 'port_accepts=True' in diagnostic, diagnostic
    assert 'parent_watch=enabled' in diagnostic, diagnostic
    assert process.poll() is not None, 'process double did not terminate'


def test_bounded_port_wait_requires_expiry_failure(tmp):
    global _port_accepts, WAIT_TIMEOUT

    class ExitedProcess:
        pid = 434343

        @staticmethod
        def poll():
            return 0

    docroot = Path(tmp) / 'docroot'
    docroot.mkdir()
    info = {
        'base': 'http://127.0.0.1:1',
        'docroot': str(docroot),
        'parent_watch': 'enabled',
    }
    states = iter((True, False))
    calls = 0

    def finite_port_accepts(_info):
        nonlocal calls
        calls += 1
        if calls > RUNAWAY_CALL_LIMIT:
            raise AssertionError('port double exceeded call limit')
        return next(states, False)

    real_port_accepts = _port_accepts
    real_timeout = WAIT_TIMEOUT
    diagnostic = None
    try:
        _port_accepts = finite_port_accepts
        WAIT_TIMEOUT = 0
        try:
            _assert_released(ExitedProcess(), info)
        except AssertionError as exc:
            diagnostic = str(exc)
        else:
            raise AssertionError('open port wait did not expire')
    finally:
        _port_accepts = real_port_accepts
        WAIT_TIMEOUT = real_timeout
        if docroot.exists():
            shutil.rmtree(docroot)
    assert diagnostic is not None
    assert 'bridge port remained open: pid=434343' in diagnostic, diagnostic
    assert not finite_port_accepts(info), 'port double did not close'


def test_spawn_failure_propagates_and_closes_the_writer(tmp):
    missing = str(Path(tmp) / 'missing')
    with _record_watch_pipe() as descriptors:
        try:
            _spawn_short_child(dict(os.environ), [missing])
        except FileNotFoundError:
            pass
        else:
            raise AssertionError('spawn with a missing executable succeeded')
        assert len(descriptors) == 2, descriptors
        _assert_fd_closed(descriptors[0])
        _assert_fd_closed(descriptors[1])


def test_start_rejects_a_regular_file_descriptor(tmp):
    regular_fd = os.open(Path(tmp) / 'regular', os.O_RDWR | os.O_CREAT)
    child_env = dict(os.environ)
    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
    startupinfo = None
    read_handle = None
    if os.name == 'nt':
        msvcrt = importlib.import_module('msvcrt')
        read_handle = msvcrt.get_osfhandle(regular_fd)
        set_handle_inheritable = getattr(os, 'set_handle_inheritable')
        set_handle_inheritable(read_handle, True)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.lpAttributeList = {'handle_list': [read_handle]}
        child_env[WATCH_ENV] = str(read_handle)
    else:
        child_env[WATCH_ENV] = str(regular_fd)
    code = (
        'from daedalus_bridge import parent_watch\n'
        'try:\n'
        '    parent_watch.start()\n'
        'except SystemExit:\n'
        '    raise SystemExit(17)\n'
        'raise SystemExit(23)\n'
    )
    proc = None
    try:
        if os.name == 'nt':
            proc = subprocess.Popen(
                [sys.executable, '-c', code], cwd=_util.ROOT,
                env=child_env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                startupinfo=startupinfo)
        else:
            proc = subprocess.Popen(
                [sys.executable, '-c', code], cwd=_util.ROOT,
                env=child_env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                pass_fds=(regular_fd,))
    finally:
        if os.name == 'nt':
            set_handle_inheritable(read_handle, False)
        os.close(regular_fd)
    try:
        output, _ = proc.communicate(timeout=WAIT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        output, _ = proc.communicate(timeout=WAIT_TIMEOUT)
        raise AssertionError(
            f'regular-descriptor probe did not exit: {output!r}') from exc
    assert proc.returncode == 17, (proc.returncode, output)


def test_spawn_preserves_the_callers_environment(tmp):
    env = dict(os.environ)
    original = dict(env)
    proc = write_fd = None
    try:
        proc, write_fd = _spawn_short_child(env)
        _wait_for_exit(proc)
        assert env == original
    finally:
        _cleanup_spawn(proc, write_fd)


def test_successful_spawn_closes_the_parent_read_end(tmp):
    proc = write_fd = None
    with _record_watch_pipe() as descriptors:
        try:
            proc, write_fd = _spawn_short_child(dict(os.environ))
            assert len(descriptors) == 2, descriptors
            _assert_fd_closed(descriptors[0])
        finally:
            _cleanup_spawn(proc, write_fd)


def test_bridge_cleans_up_when_proc_out_append_fails(tmp):
    _assert_fixture_failure_cleanup(tmp, 'proc_out.append')


def test_bridge_cleans_up_when_drain_lines_fails(tmp):
    _assert_fixture_failure_cleanup(tmp, 'drain_lines')


def test_bridge_cleans_up_when_startup_timeout_fails(tmp):
    _assert_fixture_failure_cleanup(tmp, 'startup_timeout')


def test_bridge_exceptional_exit_cleans_child_and_writer(tmp):
    _assert_fixture_failure_cleanup(tmp, 'yield body')


def _watched_parent(tmp):
    spawned = []
    with _util.bridge(tmp, proc_out=spawned) as (base, docroot):
        print(json.dumps({
            'pid': spawned[0].pid,
            'base': base,
            'docroot': str(docroot),
            'parent_watch': 'enabled',
        }), flush=True)
        threading.Event().wait()


def _unwatched_parent(tmp):
    docroot = Path(tmp) / 'docroot'
    docroot.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop(WATCH_ENV, None)
    env.update({
        'DAEDALUS_DIR': str(docroot),
        'DAEDALUS_PORT': '0',
        'DAEDALUS_MCP_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    proc = subprocess.Popen(
        [sys.executable, str(_util.ROOT / 'server.py')],
        cwd=_util.ROOT, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        port = _util.await_listening_line(proc, _util.drain_lines(proc))
        base = f'http://127.0.0.1:{port}'
        print(json.dumps({
            'pid': proc.pid,
            'base': base,
            'docroot': str(docroot),
            'parent_watch': 'disabled',
        }), flush=True)
        threading.Event().wait()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _parent_main(tmp, mode):
    if mode == 'watched':
        _watched_parent(tmp)
    else:
        _unwatched_parent(tmp)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--parent':
        _parent_main(sys.argv[2], sys.argv[3])
    else:
        sys.exit(_util.runner(_util.collect(dict(locals()))))
