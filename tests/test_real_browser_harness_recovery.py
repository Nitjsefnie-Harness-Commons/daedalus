#!/usr/bin/env python3
"""Browser-free controls for worker-absence recovery and diagnosis."""
import contextlib
import errno
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402
import _realbrowser_workers  # noqa: E402
from _repo import EXTENSION_ROOT  # noqa: E402
from test_real_browser_harness import (  # noqa: E402
    _ProcessDouble, _browser_requirements, _enter_fixture)


def _process_launches(recovery_failure=None):
    processes = [_ProcessDouble(), _ProcessDouble()]
    launches = []

    def popen(args, *, cwd, stdin, stdout, stderr):
        assert cwd == _realbrowser.ROOT, cwd
        assert stdin is subprocess.DEVNULL, stdin
        assert stdout is subprocess.DEVNULL, stdout
        assert stderr is subprocess.DEVNULL, stderr
        if launches:
            assert processes[0].terminated is False
        launches.append(list(args))
        if len(launches) == 2 and recovery_failure is not None:
            raise recovery_failure
        return processes[len(launches) - 1]

    return popen, processes, launches


def _ready_targets():
    page = {'webSocketDebuggerUrl': 'ws://page'}
    workers = [{
        'type': 'service_worker',
        'url': 'chrome-extension://controlled/background.js',
        'webSocketDebuggerUrl': 'ws://worker',
    }]
    return page, workers, '9222'


def _navigate(node, target, method, params):
    assert (node, target, method) == (
        'node-for-control', 'ws://page', 'Page.navigate')
    assert params == {
        'url': 'http://127.0.0.1:2/plain.html'}, params
    return {}


def _reached(node, browser, workers, port, worker_script):
    assert node == 'node-for-control', node
    assert browser == '/controlled/chromium', browser
    assert workers == _ready_targets()[1], workers
    assert port == '9222', port
    assert worker_script == 'background.js', worker_script
    return 'ws://worker'


def _configured(node, bridge_url, token, worker_target, devtools_port,
                worker_script, page_target, page_url):
    assert node == 'node-for-control', node
    assert bridge_url == 'http://127.0.0.1:1', bridge_url
    assert token == 'controltoken', token
    assert worker_target == 'ws://worker', worker_target
    assert devtools_port == '9222', devtools_port
    assert worker_script == 'background.js', worker_script
    assert page_target == 'ws://page', page_target
    assert page_url == 'http://127.0.0.1:2/plain.html', page_url
    yield node, page_target, 'controlled-tab'


@contextlib.contextmanager
def _recovery_runtime(tmp, waits, verdict, recovery_failure=None):
    popen, processes, launches = _process_launches(recovery_failure)
    wait_calls = []

    def wait_for_devtools(profile, process, declared_worker):
        wait_calls.append((Path(profile), process, declared_worker))
        outcome = waits[len(wait_calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    patches = (
        mock.patch.object(
            _realbrowser, 'browser_requirements', _browser_requirements),
        mock.patch.object(_realbrowser.subprocess, 'Popen', popen),
        mock.patch.object(
            _realbrowser, '_wait_for_devtools', wait_for_devtools),
        mock.patch.object(_realbrowser, 'cdp_call', _navigate),
        mock.patch.object(_realbrowser, '_reached_worker', _reached),
        mock.patch.object(_realbrowser, '_configured_fixture', _configured),
    )
    if verdict is not None:
        patches += (mock.patch.object(
            _realbrowser, '_worker_absence_verdict', verdict),)
    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        yield processes, launches, wait_calls


def _profile_args(launches):
    return [next(arg.split('=', 1)[1] for arg in launch
                 if arg.startswith('--user-data-dir='))
            for launch in launches]


def test_contention_relaunch_recovers_the_fixture(tmp):
    first_absence = _realbrowser.BrowserEnvironmentSkipped(
        'controlled first-launch worker absence')
    verdict = mock.Mock(return_value=(
        True, 'controlled contention evidence'))
    yielded = []
    with _recovery_runtime(
            tmp, [first_absence, _ready_targets()], verdict) as runtime:
        processes, launches, wait_calls = runtime
        with _enter_fixture(tmp) as fixture:
            yielded.append(fixture)

    assert yielded == [
        ('node-for-control', 'ws://page', 'controlled-tab')], yielded
    assert len(launches) == 2, launches
    profiles = _profile_args(launches)
    assert profiles == [
        str(Path(tmp) / 'chromium-profile'),
        str(Path(tmp) / 'chromium-profile-recovery'),
    ], profiles
    assert wait_calls == [
        (Path(profiles[0]), processes[0], 'background.js'),
        (Path(profiles[1]), processes[1], 'background.js'),
    ], wait_calls
    verdict.assert_called_once()
    assert [item.wait_timeouts for item in processes] == [[10], [10]]


def test_contention_relaunch_absence_remains_a_skip(tmp):
    first_absence = _realbrowser.BrowserEnvironmentSkipped(
        'controlled first-launch worker absence')
    recovery_absence = _realbrowser.BrowserEnvironmentSkipped(
        'controlled recovery worker absence')
    verdict = mock.Mock(return_value=(
        True, 'controlled contention evidence'))
    survived = None
    with _recovery_runtime(
            tmp, [first_absence, recovery_absence], verdict) as runtime:
        processes, launches, _wait_calls = runtime
        try:
            with _enter_fixture(tmp):
                raise AssertionError('fixture yielded after two absences')
        except _realbrowser.BrowserEnvironmentSkipped as why:
            survived = why

    assert survived is recovery_absence, survived
    assert 'controlled contention evidence' in str(survived), survived
    assert 'controlled recovery worker absence' in str(survived), survived
    assert 'recovery relaunch' in str(survived), survived
    assert len(launches) == 2, launches
    verdict.assert_called_once()
    assert [item.wait_timeouts for item in processes] == [[10], [10]]


def test_contention_recovery_launch_failure_is_not_worker_absence(tmp):
    first_absence = _realbrowser.BrowserEnvironmentSkipped(
        'controlled first-launch worker absence')
    recovery_failure = OSError(
        errno.ENOENT, 'controlled recovery launch failure')
    verdict = mock.Mock(return_value=(
        True, 'controlled contention evidence'))
    survived = None
    with _recovery_runtime(
            tmp, [first_absence], verdict,
            recovery_failure=recovery_failure) as runtime:
        processes, launches, _wait_calls = runtime
        try:
            with _enter_fixture(tmp):
                raise AssertionError('fixture yielded after launch failure')
        except _realbrowser.BrowserEnvironmentSkipped as why:
            survived = why

    assert survived is not None, survived
    assert 'controlled contention evidence' in str(survived), survived
    assert 'worker absent' not in str(survived), survived
    assert 'recovery browser could not be launched' in str(survived), survived
    assert len(launches) == 2, launches
    verdict.assert_called_once()
    assert [item.wait_timeouts for item in processes] == [[10], []]


def test_diagnosis_poll_exception_retires_both_browser_owners(tmp):
    first_absence = _realbrowser.BrowserEnvironmentSkipped(
        'controlled first-launch worker absence')
    poll_failure = RuntimeError('controlled diagnosis poll failure')
    survived = None
    with _recovery_runtime(tmp, [first_absence], None) as runtime:
        processes, launches, _wait_calls = runtime
        with mock.patch.object(
                _realbrowser_workers, '_listed_workers',
                side_effect=poll_failure):
            try:
                with _enter_fixture(tmp):
                    raise AssertionError(
                        'fixture yielded after diagnosis failure')
            except RuntimeError as why:
                survived = why

    assert survived is poll_failure, survived
    assert len(launches) == 2, launches
    assert [item.terminated for item in processes] == [True, True]
    assert [item.wait_timeouts for item in processes] == [[10], [10]]


def _repository_target():
    return {
        'type': 'service_worker',
        'url': 'chrome-extension://ours/background.js',
        'webSocketDebuggerUrl': 'ws://ours',
    }


def _control_target():
    return {
        'type': 'service_worker',
        'url': 'chrome-extension://control/control-worker.js',
        'webSocketDebuggerUrl': 'ws://control',
    }


class _PollClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.01
        return self.now


def _diagnosis(tmp, ours, control, poll=None):
    profile = Path(tmp) / 'control-profile'
    profile.mkdir()
    (profile / 'DevToolsActivePort').write_text(
        '9222\n', encoding='utf-8')
    process = _ProcessDouble()
    process.poll = mock.Mock(return_value=poll)
    launches = []
    evaluations = []
    ours = list(ours)
    control = list(control)

    def popen(args, *, cwd, stdin, stdout, stderr):
        assert cwd == _realbrowser.ROOT, cwd
        assert stdin is subprocess.DEVNULL, stdin
        assert stdout is subprocess.DEVNULL, stdout
        assert stderr is subprocess.DEVNULL, stderr
        launches.append(list(args))
        return process

    def listed(port):
        assert port == '9222', port
        return [_repository_target(), _control_target()]

    def evaluate(node, target, expression):
        assert node == 'node-for-control', node
        evaluations.append((target, expression))
        answers = ours if target == 'ws://ours' else control
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def version(browser):
        assert browser == '/controlled/chromium', browser
        return 'Chromium controlled version'

    target = _realbrowser_workers
    patches = (
        mock.patch.object(target.subprocess, 'Popen', popen),
        mock.patch.object(target, '_devtools_targets', listed),
        mock.patch.object(target, 'cdp_eval', evaluate),
        mock.patch.object(target, '_browser_version', version),
        mock.patch.object(target, 'WORKER_ABSENCE_DEADLINE', 0.05),
        mock.patch.object(target.time, 'time', _PollClock()),
        mock.patch.object(target.time, 'sleep', return_value=None),
    )
    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        try:
            outcome = target._worker_absence_verdict(
                'node-for-control', '/controlled/chromium', EXTENSION_ROOT,
                'background.js', tmp)
        except AssertionError as why:
            outcome = why
    return outcome, launches, process, evaluations


def test_control_answer_without_ours_preserves_source_guilt(tmp):
    outcome, _launches, _process, _evaluations = _diagnosis(
        tmp, [False], [True])
    assert outcome.__class__ is AssertionError, outcome
    message = str(outcome)
    assert str(EXTENSION_ROOT.resolve()) in message, message
    assert 'background.js' in message, message
    assert 'control extension' in message, message


def test_ours_answer_without_control_returns_contention(tmp):
    outcome, _launches, _process, evaluations = _diagnosis(
        tmp, [True], [False])
    assert outcome[0] is True, outcome
    assert 'contention' in outcome[1], outcome
    assert all(target != 'ws://control'
               for target, _expression in evaluations), evaluations


def test_control_answer_then_ours_answer_returns_contention(tmp):
    outcome, _launches, _process, _evaluations = _diagnosis(
        tmp, [False, False, True], [True])
    assert outcome[0] is True, outcome
    assert 'contention' in outcome[1], outcome


def test_control_answer_is_preserved_when_diagnosis_browser_exits(tmp):
    outcome, launches, process, _evaluations = _diagnosis(
        tmp, [False], [True], poll=1)
    assert outcome == (
        False,
        'the diagnosis browser exited after the control answered but before '
        'ours answered',
    ), outcome
    assert len(launches) == 1, launches
    assert process.wait_timeouts == [10], process.wait_timeouts


def test_neither_worker_answers_and_both_extensions_are_loaded(tmp):
    outcome, launches, process, _evaluations = _diagnosis(
        tmp, [False], [False])
    assert outcome == (
        False, 'the control extension produced no answering worker either'
    ), outcome
    assert len(launches) == 1, launches
    loaded = [arg for arg in launches[0]
              if arg.startswith('--load-extension=')]
    assert len(loaded) == 1, loaded
    assert str(EXTENSION_ROOT.resolve()) in loaded[0], loaded
    assert str((Path(tmp) / 'control-extension').resolve()) in loaded[0]
    assert process.wait_timeouts == [10], process.wait_timeouts


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowserrecovery_')


if __name__ == '__main__':
    raise SystemExit(main())
