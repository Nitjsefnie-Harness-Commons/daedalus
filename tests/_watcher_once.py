"""What one poll of a watcher costs, measured by running it.

A poll is the set of log entries sharing one poll marker - the watcher names
its own boundary, because a poll's width is data-dependent and only the
watcher knows it - or the whole log of a single `--once` invocation, which is
one poll because the process exited. Nothing is inferred and no clock is
read: the marker is in the log, so the log is complete by the time it is
read, and two identical calls inside one poll are two.

The figure is the largest call count in any one of the polls observed. Not
the first poll's: a bound that reads one poll bounds one poll, and a loop
that starts spending an extra request from its third poll on is then
measured at the width it began at - a number half the real cost, reported
as a measurement.

The module binds no fixture: a caller hands in the answers and the fake, so
the same harness measures this tree's watchers and the base commit's. It is
not a suite itself; `run_tests.py` only loads `test_*.py`. Its controls
live in tests/test_watcher_budget.py.
"""
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

import _util
from _watcher_waits import Stream
from _watcher_waits import await_polls

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'

# How many complete polls a measurement reads. Three is the first count a
# poll that starts growing later can be seen in: the second mutating a run
# is judged on is its third.
POLLS = 3


class Child:
    """A watcher process with both of its streams drained.

    It leads a process group of its own, which is what makes `stop` a
    cancellation rather than an abandonment.
    """

    def __init__(self, script, args, fake):
        self.argv = [sys.executable, '-u', str(script), *args]
        self.proc = subprocess.Popen(
            self.argv,
            env=_util.child_coverage('scrub', environment=fake.env()),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='replace', start_new_session=True)
        self.out = Stream()
        self.err = Stream()
        for pipe, sink in ((self.proc.stdout, self.out),
                           (self.proc.stderr, self.err)):
            threading.Thread(target=sink.pump, args=(pipe,),
                             daemon=True).start()

    def alive(self):
        return self.proc.poll() is None

    def captured(self):
        """Everything the child printed, for a wait's failure report."""
        return '\n'.join(self.out.lines + self.err.lines)

    def stop(self):
        if self.proc.poll() is None:
            cancel(self.proc)
        self.proc.wait(timeout=60)
        return self.proc.returncode


def cancel(proc):
    """Signal the whole group the child leads, so nothing outlives it.

    A kill names one process, and the `gh` a watcher had already spawned
    is not it: the orphan keeps running, and keeps appending to the call
    log a measurement is still reading, after the child it belonged to is
    gone. The group is every process the child started, so signalling it
    cancels the work. `start_new_session` made the child its own group
    leader, so the group id is its pid - and the child is unreaped here,
    so that pid is still its own and cannot have been handed to anyone
    else. A group already gone is the answer the kill wanted.
    """
    if sys.platform.startswith('win'):
        # Windows has no group to signal, so the tree is named instead.
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        proc.kill()


def once(script, args, fake, limit=60):
    """The calls one `--once` invocation made, read once it has exited.

    A nonzero exit is refused rather than counted, because a trial that died
    part-way through a poll reports calls it never spent.
    """
    done = subprocess.run(
        [sys.executable, '-u', str(script), *args, '--once'],
        env=_util.child_coverage('scrub', environment=fake.env()),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=limit)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    return fake.calls()


def planted(directory, name, *splices):
    """A runnable copy of the tracked watcher, each plant spliced in turn.

    A copy rather than an edit, so a defect sits in a real module and the
    tracked script is never a control's subject. Each splice is an
    `(anchor, plant)` pair, and each anchor must occur exactly once so the
    plant lands where the control means it to. Taking the planted text back
    out must give the tracked file byte for byte, which is what says no
    other line moved along with the plant. The copy carries `gh_client`
    beside it, because a script puts its own directory on `sys.path` and
    imports from there.
    """
    tracked = (SKILL / name).read_text(encoding='utf-8')
    text = tracked
    for anchor, plant in splices:
        assert text.count(anchor) == 1, (name, anchor)
        text = text.replace(anchor, plant + anchor)
    here = Path(directory)
    here.mkdir(parents=True, exist_ok=True)
    script = here / name
    script.write_text(text, encoding='utf-8')
    written = script.read_text(encoding='utf-8')
    for _, plant in reversed(splices):
        written = written.replace(plant, '', 1)
    assert written == tracked, name
    shutil.copy(SKILL / 'gh_client.py', here / 'gh_client.py')
    return script


def polls_in(calls):
    """`(poll marker, calls in that poll)` per poll, in the order polled.

    The boundary is the watcher's, so this reads rather than infers: a poll
    is the set of entries sharing one marker, and the number of such sets is
    how many polls the run made.
    """
    counts = {}
    for call in calls:
        marker = call.get('poll')
        counts[marker] = counts.get(marker, 0) + 1
    return list(counts.items())


def measure(script, args, fake, interval, polls=POLLS):
    """Calls per poll for one watcher, read from the loop that runs it.

    A poll is the set of log entries sharing one poll marker - the watcher
    names its own boundary, because a poll's width is data-dependent and
    only the watcher knows it. A `--once` invocation needs no marker: the
    process exiting IS the boundary, so its whole log is one poll, and
    `once` is where the base commit's scripts are measured, whose watchers
    predate the marker and cannot carry one.

    The figure is the LARGEST call count in any one of the polls observed,
    and the window is every one of them, not the first. A bound that reads
    a single poll bounds a single poll: a loop that starts spending an
    extra request from its third poll on is then measured at the width it
    began at and reported at half its real cost, which is a measurement
    the idle bound is meant to refuse.
    """
    child = Child(script, args + ['--interval', str(interval)], fake)
    try:
        # One marker more than the window needs, because a poll is only
        # finished once the next one has begun.
        await_polls(fake, polls + 1, child, f'{polls} poll(s)')
    finally:
        child.stop()
    windows = polls_in(fake.calls())
    in_flight = windows[-1][0]
    per_poll = max(width for _, width in windows[:-1])
    return per_poll, [call for call in fake.calls()
                      if call.get('poll') != in_flight]
