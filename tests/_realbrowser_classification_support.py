"""Diagnosis controls."""
import subprocess
from pathlib import Path
from unittest import mock

import _realbrowser
import _realbrowser_workers
from _repo import EXTENSION_ROOT


def _control_target():
    return {
        'type': 'service_worker',
        'url': 'chrome-extension://controlled/control-worker.js',
        'webSocketDebuggerUrl': 'ws://control',
    }


def control_diagnosis(tmp, answers, clock, poll=None):
    processes = []
    launches = []
    if answers and isinstance(answers[0], (list, tuple)):
        remaining = [list(item) for item in answers]
    else:
        remaining = [list(answers)]
    active_launch = 0

    def popen(args, *, cwd, stdin, stdout, stderr):
        nonlocal active_launch
        assert cwd == _realbrowser.ROOT, cwd
        assert stdin is subprocess.DEVNULL, stdin
        assert stdout is subprocess.DEVNULL, stdout
        assert stderr is subprocess.DEVNULL, stderr
        active_launch = len(launches)
        launches.append(list(args))
        profile = Path(next(
            item.split('=', 1)[1] for item in args
            if item.startswith('--user-data-dir=')))
        profile.mkdir()
        (profile / 'DevToolsActivePort').write_text(
            '9222\n', encoding='utf-8')
        process = mock.Mock()
        process.poll.return_value = poll
        processes.append(process)
        return process

    def listed(port):
        del port
        return [_control_target()]

    def evaluate(node, target, expression):
        del node, target
        assert expression == _realbrowser.CONTROL_WORKER_PROBE, expression
        answers_for_launch = remaining[min(active_launch, len(remaining) - 1)]
        return (answers_for_launch.pop(0)
                if len(answers_for_launch) > 1 else answers_for_launch[0])

    def describe(browser):
        del browser
        return 'Chromium 151.0.7922.169 (controlled)'

    def sleeper(seconds):
        del seconds

    target = _realbrowser_workers
    with mock.patch.object(target.subprocess, 'Popen', popen), \
            mock.patch.object(target, '_devtools_targets', listed), \
            mock.patch.object(target, 'cdp_eval', evaluate), \
            mock.patch.object(target, '_browser_version', describe), \
            mock.patch.object(target.time, 'time', clock), \
            mock.patch.object(target.time, 'sleep', sleeper):
        try:
            outcome = target._worker_absence_verdict(
                'node-for-control', '/controlled/chromium', EXTENSION_ROOT,
                'background.js', tmp)
        except AssertionError as why:
            outcome = why
    return outcome, launches, processes


def answered_diagnosis(tmp):
    return control_diagnosis(
        tmp, [[True], [True]],
        mock.Mock(side_effect=(0, 0, 31, 31, 31, 62)))
