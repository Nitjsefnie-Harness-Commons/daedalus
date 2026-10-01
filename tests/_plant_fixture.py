"""The arrangements both plant-helper suites need, defined once.

The privilege drop and the hand-over are how a mode bit is made to
bite here: root bypasses them, so a control that leans on one has to
run its child as a plain user. Defining the pair once is what lets the
suite that exercises the publish and the suite that reads its report
share the arrangement without either re-implementing the other's.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import _util
from _ratchet_fixture import _git

PLANT = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'plant.py'

_COMMITTED = b'VALUE = 1\n'
_FIXED = b'VALUE = 2  # the uncommitted fix, never staged\n'
_PLANTED = b'raise RuntimeError("the defect the guard exists to catch")\n'


def _out(result):
    return result.stdout.decode('utf-8', 'replace')


def _run_plant(*args):
    return subprocess.run([sys.executable, str(PLANT), *args],
                          capture_output=True, text=True, timeout=60,
                          env=_util.child_coverage('scrub'))


def _committed_repo(tmp, name='plantrepo'):
    """A committed repository holding one committed target file."""
    if shutil.which('git') is None:
        _util.skip('git is not on PATH')
    repo = Path(tmp) / name
    repo.mkdir(parents=True)
    target = repo / 'target.py'
    target.write_bytes(_COMMITTED)
    _git(repo, '-c', 'init.defaultBranch=main', 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    _git(repo, 'add', 'target.py')
    _git(repo, 'commit', '-qm', 'base')
    return target


def _say(result):
    return result.stdout + result.stderr


def _reported_state(output):
    """The state word the helper reported, not a word in its paths: the
    suite names each temp dir after its test function, so the path a test
    about a dirty target prints is full of that word anyway."""
    return output.rsplit(': ', 1)[-1].split(' against')[0].strip()


def _only_entry(store):
    entries = [item for item in Path(store).iterdir() if item.is_dir()]
    assert len(entries) == 1, entries
    return entries[0]


def _unreadable_as_bytes(entry):
    """Make a stored copy unreadable as bytes, on every platform.

    A directory where a file is expected refuses the open everywhere -
    IsADirectoryError on POSIX, PermissionError on Windows - so the class
    is the platform's and only OSError may be relied on. It is a claim
    about the OPEN, so it holds where the open is reached: the advice
    path has no presence guard, the restore path does.
    """

    payload = entry / 'bytes'
    payload.unlink()
    payload.mkdir()


def _drop_to_nobody():
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)


def _as_nobody(command):
    """Run `command` unprivileged, so the file mode bits bite - root
    bypasses them, which is why this route was enforced nowhere on a root
    runner. An arrangement that cannot drop privileges skips with the
    reason rather than erroring: a control that manufactures a red on
    correct code is the same defect as one that passes on broken code.
    """
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=60, preexec_fn=_drop_to_nobody,
                              env=_util.child_coverage('scrub'))
    except (OSError, subprocess.SubprocessError) as why:
        _util.skip(f'the privilege drop is unavailable here: {why!r}')


def _open_the_entry(store, target):
    """Hand the child every path it walks to publish, the target
    included: a suite's temporary root is 0700, and without the traverse
    bit the child reads a refusal where the route should have run."""
    entry = _only_entry(store)
    for directory in (target.parent, store, entry):
        for ancestor in (directory, *directory.parents):
            os.chmod(ancestor, os.stat(ancestor).st_mode | 0o005)
    # 0o700 makes the child the OWNER of each, so owner bits are all it
    # needs and group and other are nobody.
    for owned in (target.parent.parent, target.parent, store, entry,
                  *entry.iterdir(), target):
        os.chown(owned, 65534, 65534)
        os.chmod(owned, 0o700)
