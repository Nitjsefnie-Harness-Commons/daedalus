#!/usr/bin/env python3
"""The liveness pipe a watcher child is handed, and what ends it.

`gh_client.spawn_watched` and `gh_client.watch_parent` are the half of
the module that has no `ci_wait` in it: `watch_all.py` hands its two
watcher children the pipe one call returns, and `ci_watch.py` and
`pr_comment_watch.py` call `watch_parent` before their first poll. None
of those three is tracked - they are the untracked watchers the skill
ships to the box, and the coverage omissions that name them are for the
tools rather than the product - so the guarantee they all rest on was
measured by nothing at all.

That guarantee is the pipe's, not a parent id's: a pid compared with
`os.getppid()` is re-parented on POSIX and historical on Windows, so a
pid check protects a child on one platform and not the other, and a
process-group kill orphans rather than ends. So the rows here are the
two directions of it, both on a real child: the process holding the
write end is alive and the child is alive, and the write end closes and
the child is gone. A reader that ended the child on the first byte
would pass the second row and fail the first; a reader that never ended
it would fail both.

The platform arms are not faked. `os.name` is never patched, because
patching it would claim a runtime this run is not: the module imports
`msvcrt` and builds a `STARTUPINFO` under it, and neither import exists
on POSIX. The coverage matrix measures one interpreter on Linux, macOS
and Windows and unions the three, so a row written for the platform it
runs on measures the `nt` arms there and the POSIX arms here. What the
Windows half of a POSIX run cannot see is stated in the PR body rather
than papered over.

One row reaches inside the module, and it is the two refusals
`watch_parent` makes before it starts anything - a direct call because
the process running them is this one, and a thread that reached
`os._exit` from inside it would take the suite down with it.
"""
import errno
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'

# The bound a child is given to end after its parent closed the pipe. It
# is a ceiling on a hang, not a margin: nothing here asserts that
# something did NOT happen inside a window this short, and the one row
# that does say so says which kind of wait it is.
LIFETIME = 60

# What the child writes once its watcher is armed, and the whole of what
# it writes: a second line would sit in a pipe this suite never drains.
ARMED = 'watching'

# The environment name that tells the child to record what it measured
# before it ends. `_exit_at_eof` ends the process with `os._exit`, which
# runs no atexit hook and therefore no coverage save, so without this the
# three lines it exists to cover read as never run. The wrapper saves and
# then calls the real `os._exit`, so the process still ends exactly the
# way the module says it does - what is measured is the same ending, not
# a different one.
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

# A child asked to watch a name that is not a pipe. `regular` is opened
# by the child itself rather than handed down a descriptor, because a
# descriptor this process opened is not one the child would have: PEP
# 446 made inherited descriptors opt-in, and a row that quietly measured
# the wrong branch on the platform where that opt-in is not available
# would be a row that could not fail there.
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


def _client():
    return _util.load(SKILL / 'gh_client.py', 'gh_client_lifetime')


def _watcher_child(tmp):
    """The watcher `ci_watch.py` is: arm the pipe, then go on polling."""
    path = os.path.join(tmp, 'watched_child.py')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(CHILD.format(skill=str(SKILL), armed=ARMED, save=SAVE))
    return path


def _still_open(descriptor):
    """Whether this process still holds `descriptor` open."""
    try:
        os.fstat(descriptor)
    except (OSError, ValueError):
        return False
    return True


def _sentinel():
    """A descriptor, and the two the next `os.pipe` will hand out.

    `os.pipe` allocates the two lowest free descriptors, so a descriptor
    held open immediately before the call names the pair exactly. That
    is what lets a row below read whether `spawn_watched` gave both ends
    back without knowing what numbers they were: the numbers are the
    sentinel and the two after it, on every platform whose descriptors
    are allocated lowest-first, and both POSIX and the Windows CRT are.
    """
    held = os.open(os.devnull, os.O_RDONLY)
    return held, held + 1, held + 2


def _probe_agrees_with_the_pipe():
    """Whether the check above can tell a closed descriptor from an open one.

    Without this, a row asserting `not _still_open(n)` passes just as
    happily when `n` was never open at all - which is what a reader
    would be told by a suite whose own arithmetic was wrong. A pipe this
    process holds and then closes answers both ways, here, before any
    row leans on the answer.
    """
    read_fd, write_fd = os.pipe()
    assert _still_open(write_fd), write_fd
    os.close(read_fd)
    os.close(write_fd)
    assert not _still_open(write_fd), write_fd


def _ended(child):
    """The child's exit code, killing it first if it is still running."""
    if child.poll() is None:
        child.kill()
    return child.wait(timeout=LIFETIME)


def test_a_spawn_hands_the_child_the_pipe_and_holds_the_other_end(tmp):
    """One pipe per child, and both ends accounted for: the child is
    handed the read end in its environment and this process keeps the
    write end, while its own copy of the read end is closed - a parent
    still holding the read end could not tell the child's death from
    its own, and one that had already given the write end up would see
    the child end the moment it was born.

    The near miss is the environment the caller passed. `spawn_watched`
    takes `env` as a replacement rather than an addition, because a
    watcher child is spawned with the environment it is to run under;
    a reader that merged the caller's over this process's own would hand
    every child this process's variables, which is the opposite of what
    a caller that named one asked for.
    """
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
        _ended(child)
    os.close(write_fd)

    handed = int(seen['value'])
    assert handed > 0, seen['value']
    if os.name != 'nt':
        # The value is the descriptor this process created, and it is
        # closed here. On Windows it is the inherited HANDLE's own
        # number, which no `fstat` in this process can speak about -
        # so on that leg this half is the weaker one, and the row below
        # is what proves the child received a descriptor it could read.
        assert not _still_open(handed), handed

    marked, write_fd = client.spawn_watched(
        [sys.executable, '-c',
         'import os,sys; sys.stdout.write(os.environ.get('
         '"DAEDALUS_MARK", "unset"))'],
        env=dict(os.environ, DAEDALUS_MARK='set'),
        stdout=subprocess.PIPE, text=True)
    try:
        assert marked.communicate(timeout=LIFETIME)[0] == 'set'
    finally:
        _ended(marked)
    os.close(write_fd)


def test_the_child_ends_when_the_write_end_closes_and_not_before(tmp):
    """The guarantee, both directions, on a real child. A byte written
    down the pipe is not the parent's death and must not end the child:
    the thread reads until the pipe reports end of file, so a reader
    that ended the process on the first thing it read would take a
    watcher down over a byte another process happened to send. Then the
    write end closes, the last copy of it goes with it, and the child
    ends - which is the only event a session-line watcher of hours can
    be ended by.

    The interval between the two assertions is the shape this skill
    allows: a claim that something did NOT happen yet, given a window
    far longer than ending a process could take, so a loaded runner
    cannot fail correct code. The claim after it is the other kind - a
    thing that must have happened - and it is bounded so a hang is a
    failure rather than a job that never finishes.
    """
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
    """The refusal path, and both halves of it. The exception the launch
    raised is the caller's to see - a spawn that swallowed it would hand
    back a child that was never born - and the write end is closed
    before it travels, because a caller that caught the failure and
    carried on would otherwise hold a pipe to a process that does not
    exist, and every one of those is a descriptor the watcher runs for
    hours never gives back.

    Both ends of that pipe are read back by number, from a descriptor
    held open across the call so the numbers are known: the read end is
    what the success path closes too, and it is here as the near miss -
    a row that checked only the write end would pass a module that gave
    neither back, which is the leak that would keep a child alive
    forever.
    """
    client = _client()
    _probe_agrees_with_the_pipe()
    absent = os.path.join(tmp, 'gh-that-was-never-installed')
    sentinel, read_fd, write_fd = _sentinel()
    try:
        client.spawn_watched([absent])
    except OSError as exc:
        assert exc.errno == errno.ENOENT, exc
    else:
        raise AssertionError('a spawn of nothing returned a child')
    assert not _still_open(read_fd), 'the failed spawn kept its read end'
    assert not _still_open(write_fd), 'the failed spawn kept its write end'
    os.close(sentinel)


def test_watch_parent_started_by_hand_leaves_the_process_alone(tmp):
    """Two of the three watchers call `watch_parent` at the top of
    `main`, and one of them - `watch_all.py` - does not: it is the
    process the other two are watching, so arming the pipe there would
    have it watching itself. Nothing in this process sets the name the
    pipe travels under, which is what this row is: the call returns
    without starting a thread, and a reader that started one anyway
    would leave a thread reading a descriptor nobody will ever close -
    harmless in a test, and in a launcher a daemon that outlives the
    thing it was watching.
    """
    del tmp
    client = _client()
    before = {thread.name for thread in threading.enumerate()}
    client.watch_parent()
    assert {thread.name for thread in threading.enumerate()} == before


def test_a_name_that_is_not_an_inherited_pipe_is_refused(tmp):
    """Every way the name can fail to be a pipe, and each one ends the
    watcher rather than carrying on: a number that is not a descriptor
    at all, a word that is not a number, and - the one that is easy to
    get wrong - a perfectly good descriptor that belongs to a file. The
    last is the near miss that matters, because a child that accepted it
    would read somebody's file to its end and exit on the next close
    that was nobody's parent, which is the parent's death arriving on
    an unrelated schedule.

    Each one runs in a child, which is the only place the third can be
    driven at all: accepting it starts the very thread whose `os._exit`
    would take this process down with it, so an in-process row could
    never be shown to fail - a `watch_parent` that stopped checking
    would have ended the suite with a zero exit code and a passing
    summary. In the child the same mistake is a zero exit code, which
    is what the row reads.
    """
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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='ghclientlife_')


if __name__ == '__main__':
    raise SystemExit(main())
