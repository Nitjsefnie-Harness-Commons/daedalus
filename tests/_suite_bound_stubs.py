"""Stand-ins for the arms of `scripts/ci/suite_bound.py` no launcher reaches.

The bounded launch is the machinery a wedged-suite incident depends on, and
its teardown has arms no CI run takes: a process group that is already gone,
a `taskkill` that cannot run, a suite that ignores the request, a launcher
interrupted between the spawn and the wait. Nothing misbehaves on a healthy
runner, so each arm is reached here by standing in for whatever misbehaves.

Every stand-in RECORDS what it was asked to do -- which signal, which argv,
which wait bound, in which order -- because that record is the evidence. The
string the subject returns is the subject's own account of the same event,
and an assertion on it alone is satisfied by code that sent nothing.

Nothing here starts a process, and nothing here waits on a clock: the grace
window is spent by a clock that advances only when it is told to, so no
control here can pass because the machine was fast or fail because it was
slow.
"""
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# A process group that is neither the launcher's own nor one this process can
# be holding, so a control that intends to signal it is signalling a stand-in
# rather than anything real.
GROUP = 424241
# The group the launcher itself runs in, which by default is NOT the child's:
# the two are the same only where a launch was not given its own session, and
# a control that means that case says so rather than reading the real one.
LAUNCHER_GROUP = GROUP + 1
# A bound small enough that a stand-in child outlives it instantly. It is the
# subject's own parameter, not an assertion about elapsed time.
TINY_BOUND_S = 0.05


class Platform:
    """`sys` as the subject reads it: only `platform` is ever consulted."""

    def __init__(self, platform):
        self.platform = platform


class Child:
    """The suite's direct child, recording every call the teardown makes.

    `waits` carries the bound each `wait` was handed, so a teardown that
    reaped without one would show in the record; `killed` counts the direct
    kills. `stops_after` answers `poll` with an exit code after that many
    calls, which is how a suite that complies with the request is stood in
    for without a grace window actually elapsing.
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

    `lookup_error` and `killpg_errors` are what make the tree misbehave, and
    `sent` is what proves which signals went out and in which order -- the
    one part of a tree kill no returned string can carry, since a string
    saying the escalation happened is produced by the same branch either
    way.

    `launcher_group` is what `getpgrp` answers, and it is a parameter rather
    than the process's own group because this stand-in owns both sides of the
    comparison: the case a control means is the two being EQUAL, and reading
    the real one makes that a statement about the machine it runs on -- and an
    `os.getpgrp()` call is a POSIX-only API a Windows leg does not have.
    """

    def __init__(self, group=GROUP, launcher_group=LAUNCHER_GROUP,
                 lookup_error=None, killpg_errors=None):
        self.group = group
        self.launcher_group = launcher_group
        self.lookup_error = lookup_error
        self.killpg_errors = killpg_errors or {}
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


class Clock:
    """`time` as the subject sees it, advancing only when it sleeps.

    The grace window is a deadline read off this clock, so a control that
    needs it to expire spends it in arithmetic rather than in real seconds.
    `slept` is the evidence that the window was actually entered, which is
    a fact about the subject's own loop and not about elapsed time.
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


class Spawns:
    """`subprocess` as the subject sees it: no process is ever started.

    Only `run` and `Popen` are replaced. Every other name comes from the
    real module, so the exception classes the subject catches and the file
    flags it passes are the ones it would really be handed. `runs` and
    `spawns` record the exact call each one was, keyword arguments included.
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
    themselves: those are shared with every other suite in this process, and
    a control that mutated one would be a control that changed the machine
    rather than the subject.
    """
    saved = {name: getattr(module, name) for name in stand_ins}
    module.__dict__.update(stand_ins)
    try:
        yield
    finally:
        module.__dict__.update(saved)


def require_sigkill():
    """End the control where `signal` has no escalation to name.

    `kill_process_tree` reads `signal.SIGKILL` only on the POSIX route:
    `_taskkill` answers on Windows before that line is reached, and Windows
    has no `SIGKILL` for a stand-in `sys` to route to either. So the arms
    this covers do not exist there, and the control that does exist for
    Windows -- the `taskkill` route and its refusals -- runs on every cell.
    """
    if hasattr(signal, 'SIGKILL'):
        return
    _util.skip('signal.SIGKILL is POSIX-only, and the escalation arm is '
               'reached only after _taskkill declines on Windows')
