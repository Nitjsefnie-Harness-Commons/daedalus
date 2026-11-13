#!/usr/bin/env python3
"""A test temporary directory its run left behind is removed by the next.

Every fixture lives under the test's own directory, passed as the parent,
so these controls never reclaim anything in the real temp directory.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# A real second process: it owns a directory until killed, never cleaning up.
_HOLDER = ('import sys; sys.path.insert(0, sys.argv[1]); import _util\n'
           'held = _util.OwnedTemporaryDirectory(\n'
           '    sys.argv[2], dir=sys.argv[3])\n'
           'print(held.name, flush=True)\nsys.stdin.read()\n')


def _aged(path, seconds):
    os.utime(path, (time.time() - seconds,) * 2)


def test_a_leftover_is_reclaimed_once_its_owner_is_gone_and_not_before(tmp):
    """Only the owner's death frees a recorded directory, whatever its age."""
    parent = Path(tmp)
    with subprocess.Popen(
            [sys.executable, '-c', _HOLDER, str(Path(__file__).parent),
             'owned_', tmp], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True) as child:
        try:
            assert child.stdout
            held = Path(child.stdout.readline().strip())
            assert held.is_absolute(), 'the holder announced no directory'
            _aged(held, 10 * _util.SUITE_BOUND.suite_timeout())
            _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
            assert child.poll() is None and held.is_dir(), 'owner reclaimed'
        finally:
            child.kill()
    _aged(held, 0)
    assert held.is_dir(), 'the kill must leave the leftover behind'
    _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert list(parent.iterdir()) == [], list(parent.iterdir())


def test_only_this_prefixs_stale_unrecorded_directories_are_reclaimed(tmp):
    """The runner reclaims exact names past the suite bound, nothing else."""
    parent, bound = Path(tmp), _util.SUITE_BOUND.suite_timeout()
    for name, age in (('owned_abcdefgh', bound + 60),
                      ('owned_younger1', bound - 60),
                      ('owned_base_abcdefgh', bound + 60),
                      ('owned_creating', 0)):
        (parent / name).mkdir()
        _aged(parent / name, age)
    # A record not yet written: its creator may still be taking the lock.
    (parent / 'owned_creating' / '.owner').touch()
    with mock.patch.object(tempfile, 'tempdir', tmp), \
            mock.patch.dict(os.environ, DAEDALUS_TEST_SUMMARY=''), \
            contextlib.redirect_stdout(io.StringIO()):
        assert _util.runner([], tmp_prefix='owned_') == 0
    left = sorted(path.name for path in parent.iterdir())
    assert left == ['owned_base_abcdefgh', 'owned_creating',
                    'owned_younger1'], left


def test_a_refused_reclaim_is_reported_and_the_run_goes_on(tmp):
    """One refusal is one stderr line; the rest go, and a later run retries."""
    parent, refused = Path(tmp), []
    for name in ('owned_stale001', 'owned_stale002'):
        (parent / name).mkdir()
        _aged(parent / name, 2 * _util.SUITE_BOUND.suite_timeout())
    real_rmtree = shutil.rmtree

    def refuse_first(path, *args, **kwargs):
        if not refused:
            refused.append(Path(path))
            raise PermissionError(13, 'held open', str(path))
        return real_rmtree(path, *args, **kwargs)

    report = io.StringIO()
    with mock.patch.object(shutil, 'rmtree', refuse_first), \
            contextlib.redirect_stderr(report):
        _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert list(parent.iterdir()) == refused, (list(parent.iterdir()),
                                               refused)
    assert report.getvalue().count('\n') == 1, report.getvalue()
    assert str(refused[0]) in report.getvalue(), report.getvalue()
    _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert list(parent.iterdir()) == [], list(parent.iterdir())


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
