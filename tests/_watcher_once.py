"""What one poll of a watcher costs, measured by running it.

A poll is the set of log entries sharing one poll marker - the watcher names
its own boundary, because a poll's width is data-dependent and only the
watcher knows it, and a `--once` invocation names its single poll like any
other. Nothing is inferred and no clock is read: the marker is in the log, so
the log is complete by the time it is read, and two identical calls inside
one poll are two.

The figure is the largest call count in any one of the polls observed. Not
the first poll's: a bound that reads one poll bounds one poll, and a loop
that starts spending an extra request from its third poll on is then
measured at the width it began at - a number half the real cost, reported
as a measurement.

The module binds no fixture: a caller hands in the answers and the fake, so
the same harness measures this tree's watchers and the base commit's. It is
not a suite itself; `run_tests.py` only loads `test_*.py`. Its controls live
in tests/test_watcher_budget.py, which judges the idle bounds, and in
tests/test_watcher_loop_budget.py, which judges the figure over a loop.

`trial` leaves one path unprotected and no caller reaches it: a trial that
hits its own timeout kills the watcher but not the `gh` children that watcher
had already spawned, and those keep the log path `fake.env()` handed them,
while every caller either hands `trial` a fresh fake or reads that log once.
"""
import itertools
import shutil
import subprocess
import sys
from pathlib import Path

import _util
from _watcher_waits import ChildProcess
from _watcher_waits import await_polls

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'

# How many complete polls a measurement reads. Three is the first count a
# poll that starts growing later can be seen in: the second mutating a run
# is judged on is its third.
POLLS = 3

# One fresh log path per measurement, so two measurements on one fake
# never hand the same next subject the same log.
_LOGS = itertools.count()


class Child(ChildProcess):
    """A watcher process, from the script and the answers to watch with."""

    def __init__(self, script, args, fake):
        super().__init__(
            [sys.executable, '-u', str(script), *args],
            _util.child_coverage('scrub', environment=fake.env()))


def trial(script, args, fake, limit=60):
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


def once(script, args, fake, limit=60):
    """`trial`, for a script that cannot name its own poll boundary.

    The base commit's watchers predate the marker, so their whole log is
    one poll and its length is that poll's cost. A script that does name
    its boundary is `measure`'s subject, and a trial of it is a loop that
    ran once: its count answers for one poll of a body the loop repeats,
    which is how a watcher spending two reports one. So this refuses
    rather than answers in `measure`'s place.
    """
    calls = trial(script, args, fake, limit)
    named = sorted({call['poll'] for call in calls if call.get('poll')})
    assert not named, (
        f'{Path(script).name} names its own poll boundary {named}, so a '
        f'trial of it is one poll of a repeating body: measure it with '
        f'measure()', named)
    return calls


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

    The number of such sets is how many polls the run made.
    """
    counts = {}
    for call in calls:
        marker = call.get('poll')
        counts[marker] = counts.get(marker, 0) + 1
    return list(counts.items())


def measure(script, args, fake, interval, polls=POLLS):
    """`(calls per poll, those polls' calls)`, read from the running loop.

    It also hands the next subject a call log of its own, so the figure
    is read before the log is re-pointed: the tree cancelled above still
    holds this log, so anything in it that outlived the cancellation
    keeps appending for as long as it runs, and the caller reads the
    next subject's calls from the same place. A path that did not exist
    until this moment is one no process of that tree has been given.
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
    seen = [call for call in fake.calls() if call.get('poll') != in_flight]
    # `_fake_gh.py` is another branch's, and handing out a log is its
    # whole contract. `env()` and `calls()` both read this at call time,
    # so the next subject writes and reads where it now points.
    fake.log = fake.dir / f'next-{next(_LOGS)}'
    return per_poll, seen
