#!/usr/bin/env python3
"""A test temporary tree a run left behind is reclaimed by the next run."""
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

TESTS = str(Path(__file__).resolve().parent)
sys.path.insert(0, TESTS)
import _realbrowser  # noqa: E402
import _teardown  # noqa: E402
import _util  # noqa: E402

BOUND = _util.SUITE_BOUND.suite_timeout()
# A real second process owns a directory until killed, never cleaning up.
_HOLDER = ('import sys; sys.path.insert(0, sys.argv[1]); import _util\n'
           'held = _util.OwnedTemporaryDirectory(sys.argv[2], dir=sys.argv[3])'
           '\nprint(held.name, flush=True)\nsys.stdin.read()\n')


def _aged(path, seconds):
    os.utime(path, (time.time() - seconds,) * 2)


def test_a_recorded_leftover_goes_when_its_owner_dies_and_not_before(tmp):
    with subprocess.Popen([sys.executable, '-c', _HOLDER, TESTS,
                           'owned_', tmp], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout
            held = Path(child.stdout.readline().strip())
            assert held.is_absolute(), 'the holder announced no directory'
            _aged(held, 10 * BOUND)
            _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
            assert child.poll() is None and held.is_dir(), 'owner reclaimed'
        finally:
            child.kill()
    _aged(held, 0)
    _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert not os.listdir(tmp), os.listdir(tmp)


def test_an_owner_without_its_lock_or_cleaning_up_is_not_reclaimed(tmp):
    with mock.patch.object(_teardown, '_lock', return_value=False):
        unlocked = _util.OwnedTemporaryDirectory('owned_', dir=tmp)
    cleaning = _util.OwnedTemporaryDirectory('owned_', dir=tmp)
    cleaning._release()  # the first step of its own cleanup
    _teardown.reclaim_tempdirs('owned_', tmp)
    assert sorted(os.listdir(tmp)) == sorted(
        os.path.basename(owner.name) for owner in (unlocked, cleaning))
    unlocked.cleanup()
    cleaning.cleanup()


def test_a_refused_own_cleanup_is_reported_and_taken_by_the_next_run(tmp):
    owner = _util.OwnedTemporaryDirectory('owned_', dir=tmp,
                                          refusal_warns=True)
    with mock.patch.object(shutil, 'rmtree', side_effect=PermissionError(
            13, 'held open')), contextlib.redirect_stdout(
                io.StringIO()) as report:
        owner.cleanup()
    warned = [line for line in report.getvalue().split('\n') if 'WARN' in line]
    assert len(warned) == 1 and owner.name in warned[0], warned
    _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert not os.listdir(tmp), os.listdir(tmp)


def test_the_runner_reclaims_exactly_stale_names_and_reports_refusals(tmp):
    kept = ['outside', 'owned_base_abcdefgh', 'owned_creating',
            'owned_link0001', 'owned_younger1']
    now = time.time()  # the one clock the reclaim reads, frozen below
    for name, age in (('owned_base_abcdefgh', BOUND + 1), ('outside', 0),
                      ('owned_creating', 0), ('owned_younger1', BOUND - 1),
                      ('owned_stale001', BOUND + 1),
                      ('owned_stale_02', BOUND + 1)):
        os.mkdir(os.path.join(tmp, name))
        os.utime(os.path.join(tmp, name), (now - age, now - age))
    # An unwritten record: its creator may still be taking the lock.
    Path(tmp, 'owned_creating', '.owner').touch()
    # A link is never followed, even to a free, written record.
    Path(tmp, 'outside', '.owner').write_text('1', encoding='utf-8')
    os.symlink(Path(tmp, 'outside'), Path(tmp, 'owned_link0001'),
               target_is_directory=True)
    # The first removal is refused; the second finds its tree already gone.
    rmtree = mock.Mock(wraps=shutil.rmtree, side_effect=[
        PermissionError(13, 'held open'), FileNotFoundError(2, 'gone')] + [
            mock.DEFAULT] * 20)
    with mock.patch.object(tempfile, 'tempdir', tmp), \
            mock.patch.object(time, 'time', return_value=now), \
            mock.patch.dict(os.environ, DAEDALUS_TEST_SUMMARY=''), \
            mock.patch.object(shutil, 'rmtree', rmtree), \
            contextlib.redirect_stdout(io.StringIO()) as report:
        assert _util.runner([], tmp_prefix='owned_') == 0
    refused, gone = (os.path.basename(call.args[0])
                     for call in rmtree.call_args_list[:2])
    left = sorted(os.listdir(tmp))
    assert left == sorted(kept + [refused, gone]), left
    warned = [line for line in report.getvalue().split('\n') if 'WARN' in line]
    assert len(warned) == 1 and refused[0] in warned[0], warned
    _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert sorted(os.listdir(tmp)) == kept, os.listdir(tmp)


def test_every_browser_launch_keeps_chromium_temp_files_in_the_run(tmp):
    with mock.patch.object(subprocess, 'Popen',
                           side_effect=OSError(2, 'no browser')) as popen:
        for profile in (Path(tmp, 'profile'), Path(tmp, 'case', 'profile')):
            with contextlib.suppress(_realbrowser.BrowserEnvironmentSkipped):
                _realbrowser._launch_and_reach('node', 'chromium', 'x',
                                               profile, 'w.js',
                                               'http://127.0.0.1/', [])
        with contextlib.suppress(OSError):
            _realbrowser._worker_absence_verdict(
                'node', 'chromium', Path(tmp), 'w.js', tmp)
    launched = [call.kwargs['env'] for call in popen.call_args_list]
    root = str(Path(tmp).parent)  # the run's own root, which owns `tmp`
    expected = dict(os.environ, **dict.fromkeys(
        ('TMPDIR', 'TMP', 'TEMP', 'MAC_CHROMIUM_TMPDIR'), root))
    # Chromium's singleton socket, TMPDIR/org.chromium.Chromium.XXXXXX/
    # SingletonSocket, must fit sun_path: under 108 bytes on Linux, at most
    # 253 on macOS (process_singleton_posix.cc); Windows has none.
    limit = {'linux': 107, 'darwin': 253}.get(sys.platform, float('inf'))
    assert len(root + '/org.chromium.Chromium.XXXXXX/SingletonSocket') <= limit
    # Never the environment itself in a message: it carries credentials.
    assert len(launched) == 3, len(launched)
    for env in launched:
        assert env == expected, sorted(
            k for k in {*env, *expected} if env.get(k) != expected.get(k))


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
