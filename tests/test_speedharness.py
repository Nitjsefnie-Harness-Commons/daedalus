#!/usr/bin/env python3
"""The workflow-script runner's own runtime behaviour, executed not read back.

`run_workflow_script` is what every behavioural pin over a workflow step
runs through; the Windows arms of the two-phase tree kill are pinned
here too, beside the launchers that clean up through `_processtree`.
"""
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _noderun  # noqa: E402
import _processtree  # noqa: E402
import _speedharness  # noqa: E402
import _util  # noqa: E402
from _suite_bound_stubs import (  # noqa: E402
    Child, Escalation, Platform, Signals, Spawns, swapped)

# The Windows arms are driven on a pid and a bound distinct from every
# default `_processtree` carries (its settle bound defaults to 5), so a
# stand-in that models only the defaults cannot pass the rows below.
WINDOWS_PID = 4242
WINDOWS_TIMEOUT_S = 1.5


class GroupSpawns(Spawns):
    """`Spawns` with the Windows-only launch flag spelled as a stand-in."""

    CREATE_NEW_PROCESS_GROUP = object()


def _drive_windows_kill(gone_answer, **kwargs):
    """One Windows teardown arm, driven on stand-ins, on every cell.

    The stand-ins are swapped into the subject module's own namespace,
    never into `os` or `subprocess` themselves: a fake `win32` routes to
    the arms no POSIX cell reaches, and what the route signalled, probed
    and sent is recorded beside the record it returned.
    """
    signals = Signals(kill_errors=kwargs.pop('kill_errors', None))
    spawns = GroupSpawns(**kwargs)
    gone_calls = []

    def gone(pid, settle_s):
        gone_calls.append((pid, settle_s))
        return gone_answer

    with swapped(_processtree, sys=Platform('win32'), os=signals,
                 signal=Escalation, subprocess=spawns, process_is_gone=gone):
        record = _processtree.kill_process_tree(WINDOWS_PID,
                                                WINDOWS_TIMEOUT_S)
    return record, signals, spawns, gone_calls


def test_the_harness_timeout_kills_grandchildren_and_keeps_output(tmp):
    """A timeout keeps evidence and checks tree reaping per platform.

    POSIX records a native grandchild pid, so ``os.kill(pid, 0)`` observes
    that the recorded grandchild no longer exists. Windows records an MSYS
    pid in ``$!``, which is not a Windows pid and stays unprobed; the
    cleanup record there names the tree the two-phase kill ended, and the
    assertions below pin its invariant shape rather than the whole string.
    """
    pid_file = Path(tmp) / 'grandchild.pid'
    script = (
        "printf 'started\\n'; "
        'sleep 15 & echo $! > "$PWD/grandchild.pid"; '
        'wait')

    try:
        _speedharness.run_workflow_script(tmp, script, {}, timeout=2)
    except subprocess.TimeoutExpired as failure:
        output_files = getattr(failure, 'output_files', {})
        assert isinstance(output_files, dict) and output_files, failure
        stdout_path = Path(output_files['stdout'])
        assert 'started' in stdout_path.read_text(encoding='utf-8'), (
            stdout_path, failure)
        stdout = getattr(failure, 'stdout', None)
        assert stdout == 'started\n', stdout
        assert getattr(failure, 'output', None) == stdout, failure
        assert getattr(failure, 'stderr', None) == '', failure
        cleanup = getattr(failure, 'cleanup_diagnostic', None)
        assert isinstance(cleanup, str) and cleanup, failure
    else:
        raise AssertionError('the workflow unexpectedly completed')

    assert pid_file.exists(), pid_file
    if sys.platform == 'win32':
        text = str(cleanup)
        assert 'process tree' in text, text
        assert ('the tree did' in text
                or 'ignored the request' in text), text
        # The escalation-failure arms all say one of these two things; a
        # real run whose taskkill failed leaves the grandchild alive and
        # must go red, so neither substring may appear.
        assert 'may still be running' not in text, text
        assert 'could not run' not in text, text
        return
    pid = int(pid_file.read_text(encoding='utf-8'))
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f'grandchild {pid} is still alive')


def test_the_harness_bounds_cleanup_when_tree_kill_fails(tmp):
    """A failed tree kill still raises the original timeout with evidence.

    The kill-and-report sequence lives in `_processtree`, shared with the
    Node gate's launcher, so the seam this test forces the failure through
    is the shared one. The expected diagnostic is unchanged by that move,
    which is the point: one sequence, two callers, one description of what
    it did.
    """

    class FakeProcess:
        pid = 123

        def __init__(self):
            self.waits = []
            self.killed = False

        def wait(self, timeout=None):
            self.waits.append(timeout)
            if len(self.waits) < 3:
                raise subprocess.TimeoutExpired(['fake'], timeout)
            return 0

        def kill(self):
            self.killed = True

    process = FakeProcess()
    with mock.patch.object(_speedharness.subprocess, 'Popen',
                           return_value=process), \
            mock.patch.object(_processtree, '_kill_tree',
                              return_value='simulated tree-kill failure'):
        try:
            _speedharness.run_workflow_script(
                tmp, 'printf started', {}, timeout=0.01)
        except subprocess.TimeoutExpired as failure:
            assert failure.timeout == 0.01, failure
            cleanup = getattr(failure, 'cleanup_diagnostic', None)
            assert cleanup == (
                'simulated tree-kill failure; '
                'bounded reap timed out; fallback process kill requested; '
                'process reaped after fallback'), cleanup
            assert getattr(failure, 'output_files', None), failure
        else:
            raise AssertionError('the workflow unexpectedly completed')
    assert process.waits == [0.01, 5, 5], process.waits
    assert process.killed


def test_the_windows_route_names_every_arm_of_the_two_phase_kill(tmp):
    """Every teardown arm names what its request and its escalation did.

    Each row is a limb of the record the Windows route returns, reached
    through stand-ins so it is discriminated on every cell. Every row
    also asserts what the route DID: the request went to the driven pid,
    the grace got the driven bound, and the escalation went out with
    `taskkill_argv`'s exact argv and that bound.
    """
    del tmp
    request = (WINDOWS_PID, Escalation.CTRL_BREAK_EVENT)
    rows = [
        ('answered, tree ended, escalation exits 0',
         True, {},
         'process tree 4242 asked to stop and the tree did; '
         'the escalation reached what was still in it'),
        ('answered, tree ended, escalation found it gone',
         True, {'returncode': 128},
         'process tree 4242 asked to stop and the tree did; '
         'the escalation found the tree already gone '
         '(taskkill exited 128)'),
        ('answered, tree ignored it, escalation killed it',
         False, {},
         'process tree 4242 ignored the request and was killed by '
         'taskkill /F after 1.5 s of grace'),
        ('answered, tree ignored it, escalation exited nonzero',
         False, {'returncode': 1},
         'process tree 4242 ignored the request, and after 1.5 s of '
         'grace: taskkill /F exited 1, so the tree may still be running'),
        ('answered, tree ignored it, escalation gave up',
         False, {'run_error': subprocess.TimeoutExpired('taskkill', 1.5)},
         'process tree 4242 ignored the request, and after 1.5 s of '
         'grace: taskkill /F gave up after 1.5s, so the tree may still '
         'be running'),
        ('answered, tree ignored it, escalation could not run',
         False, {'run_error': OSError('taskkill is not on the path')},
         'process tree 4242 ignored the request, and after 1.5 s of '
         'grace: taskkill /F could not run: taskkill is not on the path'),
        ('request refused, escalation still went out',
         True, {'kill_errors': {Escalation.CTRL_BREAK_EVENT:
                                OSError('the event was refused')}},
         'CTRL_BREAK_EVENT failed: the event was refused; '
         'the escalation reached what was still in it'),
        ('pid already gone at the request',
         False, {'kill_errors': {Escalation.CTRL_BREAK_EVENT:
                                 ProcessLookupError()}},
         'process tree 4242 was already gone; '
         'the escalation reached what was still in it'),
    ]
    for label, gone_answer, kwargs, expected in rows:
        record, signals, spawns, gone_calls = _drive_windows_kill(
            gone_answer, **kwargs)
        assert record == expected, (label, record)
        assert signals.sent == [request], (label, signals.sent)
        assert len(spawns.runs) == 1, (label, len(spawns.runs))
        call_argv, call_kwargs = spawns.runs[0]
        assert call_argv == _processtree.taskkill_argv(WINDOWS_PID), (
            label, call_argv)
        assert call_kwargs['timeout'] == WINDOWS_TIMEOUT_S, (
            label, call_kwargs)
        assert gone_calls == [(WINDOWS_PID, WINDOWS_TIMEOUT_S)], (
            label, gone_calls)


def test_the_launchers_give_a_windows_tree_its_own_group(tmp):
    """Windows launches each harness child a process group of its own.

    The request phase is `CTRL_BREAK_EVENT` aimed at the tree's group,
    which only exists if the launch created one: without the flag every
    real request fails and the phase is dead code. The posix face is
    pinned beside it because the fork is the point -- a flag sent on
    both platforms would change every POSIX launch. Asserts name the one
    kwarg, never the whole call: an env= is not report material.
    """
    launches = (
        (_speedharness, lambda: _speedharness.run_workflow_script(
            tmp, 'printf ok', {}, timeout=5)),
        (_noderun, lambda: _noderun.run_node_argv(
            'node', ['--version'], cwd=tmp)),
    )
    faces = (
        ('win32', False, GroupSpawns.CREATE_NEW_PROCESS_GROUP),
        ('linux', True, 0),
    )
    for subject, launch in launches:
        for platform, session, flags in faces:
            spawns = GroupSpawns(child=Child())
            with swapped(subject, sys=Platform(platform),
                         subprocess=spawns):
                launch()
            assert len(spawns.spawns) == 1, len(spawns.spawns)
            kwargs = spawns.spawns[0][1]
            assert kwargs['start_new_session'] is session, (
                subject.__name__, platform, kwargs['start_new_session'])
            assert kwargs.get('creationflags') == flags, (
                subject.__name__, platform, kwargs.get('creationflags'))


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='speedharness_')


if __name__ == '__main__':
    raise SystemExit(main())
