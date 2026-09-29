#!/usr/bin/env python3
"""What `watch_all.py` does to the watchers it started, on the way out.

Two cases, and they are the only two places in the tree that read a watcher's
liveness: a hard kill, and the graceful signal that reaches the teardown a
kill never does. Both answer the same question, so both are here rather than
divided between a suite about the cost of an idle poll and a suite about the
double they borrow.

The liveness reading each one takes is a state rather than a sample, because
`tests/_fake_gh.py` holds every call open: a watcher is inside a call whose
answer has not been written, so it cannot have finished the poll, and while
the hold stands it cannot make a second one, so two logged calls are two
watchers. What the hold does NOT do is stop the aggregator from dying, and a
watcher dies with it whatever the hold says - `gh_client._exit_at_eof` calls
`os._exit(0)`, which ends a process from any thread. So the precondition is
read off the aggregator itself, and a failure names the side that moved
instead of reporting two dead pids. That was the signature of the one CI cell
this branch was opened for.
"""
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
import _watcher_waits as waits  # noqa: E402
from _watcher_fixtures import BRANCH  # noqa: E402
from _watcher_fixtures import PR  # noqa: E402
from _watcher_fixtures import idle_answers  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'


def _announces_pid(line):
    return 'watcher pid' in line


def _pid_alive(pid):
    """Whether a pid still names a process, on any platform CI runs."""
    if sys.platform.startswith('win'):
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = Path(f'/proc/{pid}/stat')
    if not state.exists():
        return True          # a POSIX host with no procfs to consult
    try:
        text = state.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False         # it exited between the two checks
    # An orphan nobody has reaped yet is a corpse, not a survivor.
    return text.rsplit(')', 1)[-1].split()[0] != 'Z'


class _Child(waits.ChildProcess):
    """A child the lifecycle suite starts from an argv and an environment."""

    def __init__(self, argv, env):
        super().__init__(argv, _util.child_coverage('scrub', environment=env))


def _aggregator(tmp, fake):
    return _Child([sys.executable, '-u', str(SKILL / 'watch_all.py'),
                   PR, BRANCH, '--log', str(Path(tmp) / 'watch.log'),
                   '--debounce', '1', '--max-hold', '5'], fake.env())


def _still_watching(parent):
    """The aggregator up at the moment the test signals it.

    A signal naming a process that has already gone does nothing, and by
    then the aggregator's own death has taken both watchers with it -
    through the watchdog, not this test's signal - so `await_gone` returns
    at once and the case passes without ever running its subject. The
    subject is not "the children were alive at the reading"; it is "this
    test's signal is what ended them".

    The gap between this check and the signal is the irreducible one: it is
    the width of one check, not the width of the test, and no precondition
    read can close it - a read is a sample, and a sample cannot be
    simultaneous with the thing it is about. What closing the gap buys is
    that anything landing in it is a red naming the aggregator rather than
    a pass that proved nothing.
    """
    assert parent.alive(), (
        f'the aggregator to still be watching when the test signals it '
        f'(exit {parent.proc.returncode}):\n{parent.captured()}')


def _held_at_the_reading(fake, parent):
    """Both watchers named, both inside a held call, aggregator still up.

    Returns the pids. Every wait here ends on something the process under
    test DID, and the last one is a precondition rather than a reading: a
    case that acts on an aggregator which has already gone proves nothing,
    and `os._exit` takes both watchers with it, so it is checked and named
    rather than inferred from the pids that follow.
    """
    waits.await_lines(parent.err, _announces_pid, 2,
                      'both children to announce their pid')
    pids = [int(line.rsplit(' ', 1)[-1]) for line in parent.err.lines
            if _announces_pid(line)]
    # The wait's own escape names the aggregator, because an aggregator that
    # went early is the failure it reports - `os._exit` takes both watchers
    # with it, and "2 gh call(s)" alone would not say which side moved.
    waits.await_calls(fake, 2, parent,
                      'the aggregator to make 2 gh call(s)')
    entered = fake.entered()
    assert len(entered) == 2, (
        f'both watchers to be held inside a call of their own: {entered}')
    assert parent.alive(), (
        f'the aggregator to still be watching:\n{parent.captured()}')
    return pids


def test_the_children_die_with_their_parent(tmp):
    fake = _fake_gh.FakeGh(tmp, idle_answers(), gate=True)
    parent = _aggregator(tmp, fake)
    try:
        pids = _held_at_the_reading(fake, parent)
        assert all(_pid_alive(pid) for pid in pids), (pids, parent.captured())
        _still_watching(parent)
        parent.proc.kill()
        parent.proc.wait(timeout=60)
        waits.await_gone(pids, parent, f'children {pids} to die with the '
                         f'parent', _pid_alive)
        assert not any(_pid_alive(pid) for pid in pids), (
            pids, parent.captured())
    finally:
        fake.open_gate()
        parent.stop()


def test_a_graceful_exit_leaves_no_children_behind(tmp):
    """The teardown path, which a hard kill never reaches."""
    fake = _fake_gh.FakeGh(tmp, idle_answers(), gate=True)
    parent = _aggregator(tmp, fake)
    try:
        pids = _held_at_the_reading(fake, parent)
        assert all(_pid_alive(pid) for pid in pids), (pids, parent.captured())
        _still_watching(parent)
        if sys.platform.startswith('win'):
            parent.proc.send_signal(
                getattr(signal, 'CTRL_BREAK_EVENT'))
        else:
            parent.proc.send_signal(signal.SIGINT)
        parent.proc.wait(timeout=60)
        waits.await_gone(pids, parent, f'children {pids} to leave with a '
                         f'graceful exit', _pid_alive)
        assert not any(_pid_alive(pid) for pid in pids), (
            pids, parent.captured())
    finally:
        fake.open_gate()
        parent.stop()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchlife_')


if __name__ == '__main__':
    raise SystemExit(main())
