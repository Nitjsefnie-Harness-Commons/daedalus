"""What one poll of a watcher costs, measured by running one.

A poll IS one `--once` invocation, so the figure is the exact number of
requests in that invocation's call log. Nothing is inferred and no clock is
read: the process is gone before the log is, so the log is complete, and
two identical calls inside one poll are two - the case a figure read off a
repeated sequence could not tell from two polls of one call each.

The module binds no fixture: a caller hands in the answers and the fake, so
the same harness measures this tree's watchers and the base commit's. It is
not a suite itself; `run_tests.py` only loads `test_*.py`. Its controls
live in tests/test_watcher_budget.py.
"""
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import _fake_gh
import _util
from _watcher_waits import Stream
from _watcher_waits import await_calls

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'


class Child:
    """A watcher process with both of its streams drained."""

    def __init__(self, script, args, fake):
        self.argv = [sys.executable, '-u', str(script), *args]
        self.proc = subprocess.Popen(
            self.argv,
            env=_util.child_coverage('scrub', environment=fake.env()),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='replace')
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
            self.proc.kill()
        self.proc.wait(timeout=60)
        return self.proc.returncode


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


def measure(script, args, fake, interval):
    """Calls per poll for one watcher: what one `--once` run asked for.

    `interval` is passed so the printed hourly figure names the tick the
    operator would set; `--once` reads no clock and spends no time asleep.
    """
    seen = once(script, args + ['--interval', str(interval)], fake)
    return len(seen), seen


def planted(directory, name, anchor, plant):
    """A runnable copy of the tracked watcher, `plant` spliced above `anchor`.

    A copy rather than an edit, so a defect sits in a real module and the
    tracked script is never a control's subject. Removing the planted text
    must give the tracked file back byte for byte, which is what says no
    other line moved along with the plant. The copy carries `gh_client`
    beside it, because a script puts its own directory on `sys.path` and
    imports from there.
    """
    tracked = (SKILL / name).read_text(encoding='utf-8')
    assert tracked.count(anchor) == 1, (name, anchor)
    here = Path(directory)
    here.mkdir(parents=True, exist_ok=True)
    script = here / name
    script.write_text(tracked.replace(anchor, plant + anchor),
                      encoding='utf-8')
    written = script.read_text(encoding='utf-8')
    assert written.replace(plant, '', 1) == tracked, name
    shutil.copy(SKILL / 'gh_client.py', here / 'gh_client.py')
    return script


def loop_requests(directory, script, args, count, answers, interval):
    """The loop's first `count` requests, read once they are logged."""
    fake = _fake_gh.FakeGh(directory, answers)
    child = Child(script, args + ['--interval', str(interval)], fake)
    try:
        logged = await_calls(fake, count, child, f'{count} gh call(s)')
    finally:
        child.stop()
    return [call['request'] for call in logged[:count]]


def once_versus_loop(directory, script, args, answers, interval):
    """One trial run's requests, and the loop's first two of them.

    `N` is what the trial asked for and the loop is read against it, chunk
    for chunk, with no poll boundary anywhere: the loop's first `N` requests
    and its next `N` must both BE the trial's `N`. Chunking rather than one
    prefix is what gives the comparison teeth without inventing a boundary -
    a loop whose poll spends a different number of requests stops lining up
    with `N` at the second chunk.

    What a prefix cannot reach, and this does not claim to: a loop poll that
    spends the trial's requests and then repeats the last of them, invisible
    to any comparison of a fixed-length prefix. That shape is what
    `test_a_poll_asking_the_same_question_twice_costs_two` pins on the
    measure itself.
    """
    trial = [call['request'] for call in
             once(script, args, _fake_gh.FakeGh(directory, answers))]
    polled = loop_requests(Path(directory) / 'loop', script, args,
                           2 * len(trial), answers, interval)
    return trial, polled
