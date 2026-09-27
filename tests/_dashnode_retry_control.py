"""The Windows process-boundary doubles the dashboard behaviour suite drives.

Not a suite itself -- `run_tests.py` only loads `test_*.py`, so the
CASES stay in `tests/test_dashboard_behaviour.py` and only the doubles
they stand on live here.

`_dashnode`'s post-kill drain, its Windows synchronous-IO cancel and its
one outer-timeout retry are decided by what the child process does, not by
a clock a test can bound. These fakes decide it instead: every launch is a
recorded double, so an ordering claim reads the sequence rather than a
margin. It is about `_dashnode` rather than about the dashboard, and it
moved out of the behaviour suite by the 700-line ceiling.
"""
import contextlib
import subprocess
import sys
import threading
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

import _dashnode


class _ControlledReader:
    def __init__(self, name, native_id, events, *, finished=False,
                 stuck=False, pending_buffer=None, install_buffer=None):
        self.name, self.native_id, self.events = name, native_id, events
        self.cancelled, self.finished = threading.Event(), threading.Event()
        self.stuck = stuck
        self.pending_buffer = pending_buffer
        self.install_buffer = install_buffer
        if finished:
            self.finished.set()

    def cancel(self):
        self.events.append(('reader-cancel', self.name))
        self.cancelled.set()

    def is_alive(self):
        return not self.finished.is_set()

    def join(self, timeout):
        self.events.append(('reader-join', self.name, timeout))
        if self.cancelled.is_set() and not self.stuck:
            self.finished.set()
            if (self.pending_buffer is not None
                    and self.install_buffer is not None):
                self.install_buffer(self.pending_buffer)


class _ControlledPipe:
    def __init__(self, name, reader, events):
        self.name, self.reader, self.events = name, reader, events

    def close(self):
        self.events.append(('pipe-close', self.name))
        assert not self.reader.is_alive(), (
            f'{self.name} closed before its reader finished')


class _LiveReaderBuffer:
    def __init__(self, name, events):
        self.name, self.events = name, events

    def __bool__(self):
        self.events.append(('buffer-read', self.name))
        return False


class _ControlledProcess:
    def __init__(self, pid, command, outcomes, events, *, wait_succeeds=False,
                 held_readers=False, reader_buffers=None, stuck_reader=None,
                 late_buffers=None):
        self.pid, self.command = pid, command
        self.outcomes, self.events = list(outcomes), events
        self.wait_succeeds = wait_succeeds
        self.returncode = self.stdout = self.stderr = None
        held_readers |= reader_buffers is not None or late_buffers is not None
        if held_readers:
            buffers = reader_buffers or {}
            late = late_buffers or {}

            def reader(name, native_id):
                return _ControlledReader(
                    name, native_id, events, finished=name in buffers,
                    stuck=stuck_reader == name,
                    pending_buffer=late.get(name),
                    install_buffer=lambda chunks, name=name: setattr(
                        self, f'_{name}_buff', [chunks]))
            self.stdout_thread = reader('stdout', pid * 2)
            self.stderr_thread = reader('stderr', pid * 2 + 1)
            self.stdout = _ControlledPipe('stdout', self.stdout_thread, events)
            self.stderr = _ControlledPipe('stderr', self.stderr_thread, events)
            if reader_buffers is not None or late_buffers is not None:
                for name in ('stdout', 'stderr'):
                    value = ([buffers[name]] if name in buffers
                             else _LiveReaderBuffer(name, events))
                    setattr(self, f'_{name}_buff', value)

    def communicate(self, timeout):
        self.events.append(('communicate', self.pid, timeout))
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            return outcome(self)
        kind, self.returncode, stdout, stderr = outcome
        if kind == 'timeout':
            raise subprocess.TimeoutExpired(
                self.command, timeout, output=stdout, stderr=stderr)
        return stdout, stderr

    def kill(self):
        self.events.append(('kill', self.pid))
        self.returncode = -9

    def wait(self, timeout):
        self.events.append(('wait', self.pid, timeout))
        if self.wait_succeeds:
            return self.returncode
        raise subprocess.TimeoutExpired(self.command, timeout)


def _controlled_run(platform, *specs, before_popen=None):
    pending, events, diagnostic = list(specs), [], StringIO()
    clock = iter(value / 10 for value in range(100))

    def popen(command, **_options):
        pid, outcomes, *wait_options = pending.pop(0)
        options = wait_options[0] if wait_options else {}
        if before_popen:
            before_popen(pid, events)
        events.append(('popen', pid, tuple(command)))
        return _ControlledProcess(pid, command, outcomes, events, **options)

    def cancel_reader(thread):
        thread.cancel()

    with patch.object(sys, 'platform', platform), \
            patch.object(_dashnode.shutil, 'which', return_value='/node'), \
            patch.object(_dashnode.subprocess, 'Popen', popen), \
            patch.object(_dashnode, '_dashboard_child_gate',
                         contextlib.nullcontext), \
            patch.object(_dashnode, '_cancel_windows_synchronous_io',
                         cancel_reader, create=True), \
            patch.object(_dashnode.time, 'monotonic', lambda: next(clock)), \
            redirect_stderr(diagnostic):
        try:
            outcome = _dashnode.run_dashboard_node(
                _dashnode.DashboardNodeHarness('', 0))
        except AssertionError as failure:
            outcome = str(failure)
    return outcome, events, diagnostic.getvalue()


def _timeout(stdout='', stderr=''):
    return 'timeout', None, stdout, stderr


# `_outcome`, not `_result`: this module's leading underscore makes it a
# shared helper, so a name four other suites also declare would make THEM
# the re-implementation. This is the dashboard suite's own outcome shape.
def _outcome(code, stdout='', stderr=''):
    return 'result', code, stdout, stderr
