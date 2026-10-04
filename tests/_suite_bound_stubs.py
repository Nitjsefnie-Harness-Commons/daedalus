"""Stand-ins for teardown arms no launcher reaches.

Each arm (a group already gone, a `taskkill` that cannot run, a suite
that ignores the request, an interrupted launch) is reached by standing
in for whatever misbehaves. Every stand-in RECORDS what it was asked to
do: an assertion on the subject's returned string alone is satisfied by
code that sent nothing.
"""
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# Neither the launcher's own nor one this process can be holding.
GROUP = 424241
# The launcher's own group, NOT the child's by default.
LAUNCHER_GROUP = GROUP + 1
# A bound a stand-in child outlives instantly, not an elapsed-time claim.
TINY_BOUND_S = 0.05


class Platform:
    """`sys` as the subject reads it: only `platform` is ever consulted."""

    def __init__(self, platform):
        self.platform = platform


class Child:
    """The suite's direct child, recording every call the teardown makes.

    `waits` carries each `wait`'s bound; `stops_after` answers `poll`
    with an exit code after that many calls: a complying suite, without
    a grace window elapsing.
    """

    def __init__(self, pid=GROUP, running=True, stops_after=None,
                 kill_error=None, wait_errors=()):
        self.pid = pid
        self.returncode = None
        self.killed = 0
        self.kill_error = kill_error
        self.waits = []
        self.wait_errors = list(wait_errors)
        self.polls = 0
        self._running = running
        self._stops_after = stops_after

    def poll(self):
        self.polls += 1
        if self._stops_after is not None and self.polls > self._stops_after:
            self._running = False
        return None if self._running else 0

    def kill(self):
        self.killed += 1
        if self.kill_error is not None:
            raise self.kill_error
        self._running = False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        self._running = False
        self.returncode = -9
        return self.returncode


class Signals:
    """`os` as the subject sees it, recording every signal it sends.

    `sent` proves which signals went out and in which order -- the one
    part of a tree kill no returned string can carry. `launcher_group`
    is what `getpgrp` answers, a parameter because `os.getpgrp()` is
    POSIX-only.
    """

    def __init__(self, group=GROUP, launcher_group=LAUNCHER_GROUP,
                 lookup_error=None, killpg_errors=None, kill_errors=None):
        self.group = group
        self.launcher_group = launcher_group
        self.lookup_error = lookup_error
        self.killpg_errors = killpg_errors or {}
        self.kill_errors = kill_errors or {}
        self.sent = []

    def __getattr__(self, name):
        return getattr(os, name)

    def getpgid(self, pid):
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.group

    def getpgrp(self):
        return self.launcher_group

    def killpg(self, group, sig):
        self.sent.append((group, sig))
        if sig in self.killpg_errors:
            raise self.killpg_errors[sig]

    def kill(self, pid, sig):
        self.sent.append((pid, sig))
        if sig in self.kill_errors:
            raise self.kill_errors[sig]


class Signal:
    """One signal by the two attributes the teardown reads off it; the
    interpreter's own `signal` members are not available on every cell."""

    def __init__(self, name, number):
        self.name = name
        self.number = number

    def __repr__(self):
        return f'Signal({self.name!r}, {self.number})'


class Escalation:
    """`signal` as the bounded teardown reads it, on every cell.

    No interpreter this project targets has both `SIGKILL` and
    `CTRL_BREAK_EVENT`, so the global is stood in for; a read from the
    real module would raise inside the arm on some cells.
    """
    SIGTERM = signal.SIGTERM
    SIGKILL = Signal('SIGKILL', 9)
    CTRL_BREAK_EVENT = Signal('CTRL_BREAK_EVENT', 1)


class Clock:
    """`time` as the subject sees it, advancing only when it sleeps; the
    grace window is spent in arithmetic, and `slept` proves it was entered."""

    def __init__(self):
        self.now = 0.0
        self.slept = 0.0

    def __getattr__(self, name):
        return getattr(time, name)

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds


class Removals:
    """`shutil` as the output cleanup reads it: refusals made, calls kept.

    `refuse` is how many calls refuse before the work is done for real;
    a call past it is the real `shutil.rmtree`.
    """

    def __init__(self, refuse=0, error=None):
        self.refuse = refuse
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        return getattr(shutil, name)

    def rmtree(self, path, *args, **kwargs):
        self.calls.append(str(path))
        if len(self.calls) <= self.refuse:
            if self.error is not None:
                raise self.error
            raise PermissionError(
                32, 'in use by another process', str(path))
        return shutil.rmtree(path, *args, **kwargs)


class Spawns:
    """`subprocess` as the subject sees it: no process is ever started.

    Only `run` and `Popen` are replaced; every other name comes from the
    real module. `runs` and `spawns` record the exact call each one was,
    kwargs included.
    """

    def __init__(self, child=None, spawn_error=None, run_error=None,
                 returncode=0):
        self.child = child
        self.spawn_error = spawn_error
        self.run_error = run_error
        self.returncode = returncode
        self.runs = []
        self.spawns = []
        # The subject spells it `subprocess.Popen`, so the stand-in does too.
        self.Popen = self._popen

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, argv, **kwargs):
        # The escalation's five kwargs are the only shapes a subject has
        # ever sent; anything else is unmodelled and refused, so a subject
        # that starts passing something new fails here rather than being
        # recorded green.
        if not set(kwargs) <= {'stdin', 'stdout', 'stderr', 'check',
                               'timeout'}:
            raise AssertionError(
                f'Spawns.run does not model {sorted(kwargs)}')
        self.runs.append((argv, kwargs))
        if self.run_error is not None:
            raise self.run_error
        return subprocess.CompletedProcess(argv, self.returncode)

    def _popen(self, argv, **kwargs):
        self.spawns.append((argv, kwargs))
        if self.spawn_error is not None:
            raise self.spawn_error
        return self.child


@contextlib.contextmanager
def swapped(module, **stand_ins):
    """The subject's own globals, stood in for, and put back afterwards.

    On the module under test rather than on `os`, `time` or `subprocess`
    themselves: those are shared with every other suite in this process.
    A function's `__globals__` is accepted too: a control driving an
    imported function has to reach the dict it reads.
    """
    namespace = module if isinstance(module, dict) else module.__dict__
    saved = {name: namespace[name] for name in stand_ins}
    namespace.update(stand_ins)
    try:
        yield
    finally:
        namespace.update(saved)


def require_sigkill():
    """End the control where `signal` has no escalation to name.

    `kill_process_tree` reads `signal.SIGKILL` only on the POSIX route;
    the Windows route answers before that line, so these arms are pinned
    with the stand-in `signal` instead.
    """
    if hasattr(signal, 'SIGKILL'):
        return
    _util.skip('signal.SIGKILL is POSIX-only, and the escalation arm is '
               'reached only after the Windows route declines')
