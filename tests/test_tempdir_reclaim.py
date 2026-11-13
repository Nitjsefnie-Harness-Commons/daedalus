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


def test_the_runner_reclaims_exactly_stale_names_and_reports_refusals(tmp):
    kept = ['owned_base_abcdefgh', 'owned_creating', 'owned_younger1']
    for name, age in zip(kept + ['owned_stale001', 'owned_stale002'],
                         (BOUND + 60, 0, BOUND - 60, BOUND + 60, BOUND + 60)):
        os.mkdir(os.path.join(tmp, name))
        _aged(os.path.join(tmp, name), age)
    # An unwritten record: its creator may still be taking the lock.
    Path(tmp, 'owned_creating', '.owner').touch()
    rmtree = mock.Mock(wraps=shutil.rmtree, side_effect=[
        PermissionError(13, 'held open')] + [mock.DEFAULT] * 20)
    with mock.patch.object(tempfile, 'tempdir', tmp), \
            mock.patch.dict(os.environ, DAEDALUS_TEST_SUMMARY=''), \
            mock.patch.object(shutil, 'rmtree', rmtree), \
            contextlib.redirect_stdout(io.StringIO()) as report:
        assert _util.runner([], tmp_prefix='owned_') == 0
    refused = [os.path.basename(rmtree.call_args_list[0].args[0])]
    left = sorted(os.listdir(tmp))
    assert left == sorted(kept + refused), left
    warned = [line for line in report.getvalue().split('\n') if 'WARN' in line]
    assert len(warned) == 1 and refused[0] in warned[0], warned
    _util.OwnedTemporaryDirectory('owned_', dir=tmp).cleanup()
    assert sorted(os.listdir(tmp)) == kept, os.listdir(tmp)


def test_both_browser_launches_keep_chromium_temp_files_in_the_run(tmp):
    with mock.patch.object(subprocess, 'Popen',
                           side_effect=OSError(2, 'no browser')) as popen:
        with contextlib.suppress(_realbrowser.BrowserEnvironmentSkipped):
            _realbrowser._launch_and_reach('node', 'chromium', 'x',
                                           Path(tmp, 'profile'), 'w.js',
                                           'http://127.0.0.1/', [])
        with contextlib.suppress(OSError):
            _realbrowser._worker_absence_verdict(
                'node', 'chromium', Path(tmp), 'w.js', tmp)
    launched = [call.kwargs['env'] for call in popen.call_args_list]
    root = str(Path(tmp).parent)  # the run's own root, which owns `tmp`
    expected = dict(os.environ, **dict.fromkeys(
        ('TMPDIR', 'TMP', 'TEMP', 'MAC_CHROMIUM_TMPDIR'), root))
    # Linux Chromium aborts once TMPDIR/org.chromium.Chromium.XXXXXX/
    # SingletonSocket reaches the 108-byte sun_path.
    assert len(root + '/org.chromium.Chromium.XXXXXX/SingletonSocket') < 108
    # Never the environment itself in a message: it carries credentials.
    assert len(launched) == 2, len(launched)
    for env in launched:
        assert env == expected, sorted(
            k for k in {*env, *expected} if env.get(k) != expected.get(k))


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
