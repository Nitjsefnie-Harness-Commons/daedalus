"""Stand-ins for teardown arms no launcher reaches.

The bounded teardown has arms no CI run takes: a process group already
gone, a `taskkill` that cannot run, a suite that ignores the request, a
launcher interrupted between the spawn and the wait. Nothing misbehaves
on a healthy runner, so each arm is reached here by standing in for
whatever misbehaves. Every stand-in RECORDS what it was asked to do --
which signal, which argv, which wait bound, in which order -- because the
string the subject returns is the subject's own account of the same
event, and an assertion on it alone is satisfied by code that sent
nothing. Nothing here starts a process or waits on a clock: the grace
window is spent by a clock that advances only when it is told to, so no
control here can pass because the machine was fast.
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

# Neither the launcher's own nor one this process can be holding, so a
# control that signals it is signalling a stand-in, not anything real.
GROUP = 424241
# The group the launcher itself runs in, NOT the child's by default: the
# two are the same only where a launch was not given its own session, and
# a control that means that case says so rather than reading the real one.
LAUNCHER_GROUP = GROUP + 1
# A bound a stand-in child outlives instantly: the subject's own
# parameter, not an assertion about elapsed time.
TINY_BOUND_S = 0.05


class Platform:
    """`sys` as the subject reads it: only `platform` is ever consulted."""

    def __init__(self, platform):
        self.platform = platform


class Child:
    """The suite's direct child, recording every call the teardown makes.

    `waits` carries the bound each `wait` was handed, so a teardown that
    reaped without one would show in the record; `killed` counts the
    direct kills. `stops_after` answers `poll` with an exit code after
    that many calls: a complying suite, stood in for without a grace
    window actually elapsing.
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

    `lookup_error`, `killpg_errors` and `kill_errors` are what make the
    tree misbehave, and `sent` is what proves which signals went out and
    in which order -- the one part of a tree kill no returned string can
    carry, since a string saying the escalation happened is produced by
    the same branch either way. `kill` is the single-pid send, the shape
    the Windows request takes. `launcher_group` is what `getpgrp`
    answers, a parameter rather than the process's own group because
    reading the real one makes the comparison a statement about the
    machine it runs on -- and `os.getpgrp()` is POSIX-only.
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
    """One signal by the two attributes the teardown reads off it.

    Name and number are the whole of what a stand-in carries, and it
    carries them itself because the members of the interpreter's own
    `signal` module are not available on every cell.
    """

    def __init__(self, name, number):
        self.name = name
        self.number = number

    def __repr__(self):
        return f'Signal({self.name!r}, {self.number})'


class Escalation:
    """`signal` as the bounded teardown reads it, on every cell.

    `_ask_and_insist` names three signals, one per route and phase, and
    reads each off its own `signal` global. Standing that global in makes
    every arm reachable everywhere: no interpreter this project targets
    has both `SIGKILL` and `CTRL_BREAK_EVENT`, so a read from the real
    module would raise inside the arm on some cells, and the subject's
    broad guard would make the record that exception instead of the
    outcome the control asserts on. `SIGTERM` is the real member because
    every platform defines it.
    """
    SIGTERM = signal.SIGTERM
    SIGKILL = Signal('SIGKILL', 9)
    CTRL_BREAK_EVENT = Signal('CTRL_BREAK_EVENT', 1)


class Clock:
    """`time` as the subject sees it, advancing only when it sleeps.

    The grace window is a deadline read off this clock, so a control that
    needs it to expire spends it in arithmetic rather than in real
    seconds. `slept` is the evidence that the window was actually
    entered: a fact about the subject's own loop, not elapsed time.
    """

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

    The refusals `discard_outputs` retries exist on one platform only, so
    they are built rather than waited for. `refuse` is how many calls
    refuse before the work is done for real, and `error` is what they
    raise. A call past `refuse` is the real `shutil.rmtree`, so running
    out ends as the machine would.
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
    real module, so the exception classes the subject catches and the
    file flags it passes are the ones it would really be handed. `runs`
    and `spawns` record the exact call each one was, kwargs included.
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
    themselves: those are shared with every other suite in this process,
    and a control that mutated one would be a control that changed the
    machine rather than the subject. A function's `__globals__` is
    accepted as well as a module, because a control that drives a
    function imported from elsewhere cannot name the module object it
    came from -- it has to reach the dict that function reads.
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

    `kill_process_tree` reads `signal.SIGKILL` only on the POSIX route:
    the Windows route answers before that line is reached, and Windows
    has no `SIGKILL` for a stand-in `sys` to route to either. So the arms
    this covers do not exist there, and the Windows arms the route does
    reach are pinned with the stand-in `signal` every cell can name.
    """
    if hasattr(signal, 'SIGKILL'):
        return
    _util.skip('signal.SIGKILL is POSIX-only, and the escalation arm is '
               'reached only after the Windows route declines')
