#!/usr/bin/env python3
"""Browser-free mutation controls for fixture fault classification."""
import contextlib
import errno
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402
import _realbrowser_workers  # noqa: E402
import _util  # noqa: E402
from _deliveries import (  # noqa: E402
    real_eval, real_ext_command)
from _repo import EXTENSION_ROOT  # noqa: E402
from test_real_browser_harness import (  # noqa: E402
    _browser_version, _enter_fixture, _fixture_runtime)


def _call_failure(call):
    try:
        call()
    except (AssertionError, _util.Skipped) as failure:
        return failure
    raise AssertionError('controlled call did not raise')


def _fixture_failure(tmp):
    try:
        with _enter_fixture(tmp):
            raise AssertionError('controlled fixture unexpectedly yielded')
    except (AssertionError, _util.Skipped) as failure:
        return failure


def test_indeterminate_e2big_diagnostics_are_harness_failures(tmp):
    del tmp
    too_large = OSError(errno.E2BIG, 'controlled command-size refusal')
    outcomes = (
        OSError(errno.ENOENT, 'controlled diagnostic start failure'),
        subprocess.TimeoutExpired(['controlled-diagnostic'], 1),
        subprocess.CompletedProcess(['controlled-diagnostic'], 1),
    )
    for outcome in outcomes:
        behavior = ({'side_effect': outcome}
                    if isinstance(outcome, BaseException)
                    else {'return_value': outcome})
        with mock.patch.object(_realbrowser.subprocess, 'run', **behavior):
            failure = _call_failure(lambda: _realbrowser._raise_start_failure(
                'Node WebSocket probe', '/controlled/node', too_large))
        assert failure.__class__ is AssertionError, failure
        assert '/controlled/node' in str(failure), failure
        if isinstance(outcome, subprocess.CompletedProcess):
            assert failure.__cause__ is None, failure.__cause__


def test_missing_browser_requirements_are_environment_skip(tmp):
    del tmp
    with mock.patch.object(_realbrowser.shutil, 'which', return_value=None):
        failure = _call_failure(_realbrowser.browser_requirements)
    assert failure.__class__ is _realbrowser.BrowserEnvironmentSkipped, failure


def test_missing_declared_worker_is_repository_failure(tmp):
    extension = Path(tmp) / 'missing-worker-extension'
    extension.mkdir()
    missing_worker = extension / 'missing-worker.js'
    (extension / 'manifest.json').write_text(
        '{"background":{"service_worker":"missing-worker.js"}}',
        encoding='utf-8')

    def enter_fixture():
        with _realbrowser.real_extension_page(
                tmp, 'http://127.0.0.1:1', 'controltoken',
                'http://127.0.0.1:2/plain.html',
                extension_root=extension):
            raise AssertionError('missing-worker fixture unexpectedly yielded')

    launch = mock.Mock(side_effect=AssertionError(
        'browser launched before the declared worker was checked'))
    with mock.patch.object(
            _realbrowser, 'browser_requirements',
            return_value=('node-for-control', '/controlled/chromium')), \
            mock.patch.object(_realbrowser.subprocess, 'Popen', launch):
        failure = _call_failure(enter_fixture)
    assert failure.__class__ is AssertionError, failure
    assert str(missing_worker.resolve()) in str(failure), failure
    launch.assert_not_called()


def test_browser_exit_before_devtools_is_environment_skip(tmp):
    for exit_code in (1, 0):
        process = mock.Mock()
        process.poll.return_value = exit_code
        clock = mock.Mock(side_effect=(0, 0, 31))
        sleeper = mock.Mock()
        with mock.patch.object(_realbrowser.time, 'time', clock), \
                mock.patch.object(_realbrowser.time, 'sleep', sleeper):
            failure = _call_failure(
                lambda observed=process: _realbrowser._wait_for_devtools(
                    tmp, observed, 'background.js'))
        assert failure.__class__ is (
            _realbrowser.BrowserEnvironmentSkipped), failure
        assert clock.call_count == 2, (exit_code, clock.call_count)
        sleeper.assert_not_called()
        assert 'Chromium' in str(failure), failure


def test_live_browser_reaches_ready_devtools_targets(tmp):
    page = {'type': 'page', 'webSocketDebuggerUrl': 'ws://page'}
    worker = {
        'type': 'service_worker',
        'url': 'chrome-extension://controlled/background.js',
        'webSocketDebuggerUrl': 'ws://worker',
    }
    (Path(tmp) / 'DevToolsActivePort').write_text(
        '9222\n', encoding='utf-8')
    process = mock.Mock()
    process.poll.return_value = None
    try:
        with mock.patch.object(
                _realbrowser, '_devtools_targets',
                return_value=[page, worker]), \
                mock.patch.object(
                    _realbrowser.time, 'time', side_effect=(0, 0)):
            actual = _realbrowser._wait_for_devtools(
                tmp, process, 'background.js')
    except _realbrowser.BrowserEnvironmentSkipped as why:
        raise AssertionError(
            'live Chromium was classified as having exited') from why
    assert actual == (page, [worker], '9222'), actual


def test_devtools_start_deadline_is_environment_skip(tmp):
    process = mock.Mock()
    process.poll.return_value = None
    with mock.patch.object(
            _realbrowser.time, 'time', side_effect=(0, 31)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        failure = _call_failure(
            lambda: _realbrowser._wait_for_devtools(
                tmp, process, 'background.js'))
    assert failure.__class__ is _realbrowser.BrowserEnvironmentSkipped, failure


def _worker_timeout_failure(tmp, reached, verdict=None):
    def navigate(node, target, method, params):
        del node, target, method, params
        return {}

    def unready(node, workers):
        del node, workers
        return None, reached, 'controlled worker timeout'

    if verdict is None:
        verdict = mock.Mock(return_value=(False, 'controlled observation'))
    attempts = []

    def recording(*args):
        attempts.append(args)
        return verdict(*args)

    with _fixture_runtime(
            tmp, navigate, subprocess_run=_browser_version), \
            mock.patch.object(_realbrowser, 'ready_worker', unready), \
            mock.patch.object(
                _realbrowser, '_devtools_targets', return_value=[]), \
            mock.patch.object(
                _realbrowser, '_worker_absence_verdict', recording), \
            mock.patch.object(
                _realbrowser.time, 'time', side_effect=(0, 0, 31)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        return _fixture_failure(tmp), attempts


def test_answering_unready_worker_is_repository_failure(tmp):
    failure, attempts = _worker_timeout_failure(tmp, True)
    assert failure.__class__ is AssertionError, failure
    assert attempts == [], attempts


def test_unreachable_worker_is_environment_skip(tmp):
    failure, attempts = _worker_timeout_failure(tmp, False)
    environment = _realbrowser.BrowserEnvironmentSkipped
    assert failure.__class__ is environment, failure
    assert 'controlled worker timeout' in str(failure), failure
    assert 'this browser never let the extension worker be reached' in str(
        failure), failure
    # One diagnosis, carrying what it needs to name our source on a verdict.
    assert len(attempts) == 1, attempts
    node, browser, extension, worker_script, tmp_dir = attempts[0]
    assert node == 'node-for-control', node
    assert browser == '/controlled/chromium', browser
    assert extension == EXTENSION_ROOT.resolve(), extension
    assert worker_script == 'background.js', worker_script
    assert tmp_dir is tmp or Path(tmp_dir) == Path(tmp), tmp_dir


def test_control_extension_turns_worker_absence_into_failure(tmp):
    def guilty(*args):
        del args
        raise AssertionError(
            'controlled: our source, not the machine')

    failure, attempts = _worker_timeout_failure(tmp, False, verdict=guilty)
    assert failure.__class__ is AssertionError, failure
    assert 'controlled: our source, not the machine' in str(failure), failure
    assert len(attempts) == 1, attempts


def test_machine_skip_carries_what_the_diagnosis_observed(tmp):
    """The machine verdict carries what the diagnosis observed with it.

    A skip byte-identical to one where no diagnosis ran leaves the
    machine-blame claim unevidenced.
    """
    def observed(*args):
        del args
        return False, 'controlled: the control worker never answered either'

    failure, attempts = _worker_timeout_failure(tmp, False, verdict=observed)
    assert failure.__class__ is (
        _realbrowser.BrowserEnvironmentSkipped), failure
    assert 'this browser never let the extension worker be reached' in str(
        failure), failure
    assert 'controlled: the control worker never answered either' in str(
        failure), failure
    assert len(attempts) == 1, attempts


def _control_target():
    return {
        'type': 'service_worker',
        'url': 'chrome-extension://controlled/control-worker.js',
        'webSocketDebuggerUrl': 'ws://control',
    }


def _control_diagnosis(tmp, answers, clock, poll=None):
    """Run the real verdict against doubles for the browser and DevTools.

    `answers` are what the control's probe returns per evaluation, the last
    one repeating; the two-element form is how a poll that could not be read
    is followed by one that was. The port file is written the way Chromium
    writes it, since reading that file is part of what the verdict does.
    """
    profile = Path(tmp) / 'control-profile'
    profile.mkdir()
    (profile / 'DevToolsActivePort').write_text('9222\n', encoding='utf-8')
    process = mock.Mock()
    process.poll.return_value = poll
    launches = []
    remaining = list(answers)

    def popen(args, *, cwd, stdin, stdout, stderr):
        del cwd, stdin, stdout, stderr
        assert not launches, 'the diagnosis launched more than one browser'
        launches.append(list(args))
        return process

    def listed(port):
        del port
        return [_control_target()]

    def evaluate(node, target, expression):
        del node, target
        assert expression == _realbrowser.CONTROL_WORKER_PROBE, expression
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

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
    return outcome, launches, process


def _answered_diagnosis(tmp):
    # Finite like its siblings: a constant zero spins the verdict's deadline
    # loop forever on any mutation that blocks the answered path.
    return _control_diagnosis(tmp, [True], mock.Mock(side_effect=(0, 0, 31)))


def test_answering_control_worker_marks_worker_absence_our_failure(tmp):
    """A control worker that answers is a browser that proved the skill.

    The absence of our worker is also what a machine without MV3 support
    produces, so the verdict is only trustworthy once something else has
    demonstrated the capability. What is pinned here is that contract: the
    failure names our source and our declared script, never the machine.
    """
    outcome, _launches, _process = _answered_diagnosis(tmp)
    assert outcome.__class__ is AssertionError, outcome
    reported = str(outcome)
    assert str(EXTENSION_ROOT.resolve()) in reported, reported
    assert 'background.js' in reported, reported
    assert 'Chromium 151.0.7922.169 (controlled)' in reported, reported
    assert 'not the machine' in reported, reported


def test_control_diagnosis_launches_both_extensions_once(tmp):
    outcome, launches, process = _answered_diagnosis(tmp)
    assert outcome.__class__ is AssertionError, outcome
    process.terminate.assert_called_once()
    assert len(launches) == 1, launches
    loaded = [item for item in launches[0]
              if item.startswith('--load-extension=')]
    assert len(loaded) == 1, launches
    assert str(EXTENSION_ROOT.resolve()) in loaded[0], loaded
    control = str((Path(tmp) / 'control-extension').resolve())
    assert control in loaded[0], (control, loaded)


def test_unanswered_control_worker_leaves_the_skip_with_the_machine(tmp):
    """No control answer is a browser that never demonstrated anything."""
    outcome, launches, process = _control_diagnosis(
        tmp, [False], mock.Mock(side_effect=(0, 0, 31)))
    assert outcome[0] is False, outcome
    assert 'no answering worker either' in outcome[1], outcome
    process.terminate.assert_called_once()
    assert len(launches) == 1, launches


def test_control_browser_exit_ends_the_diagnosis_without_a_verdict(tmp):
    """A diagnosis browser that is gone cannot demonstrate anything."""
    outcome, launches, process = _control_diagnosis(
        tmp, [False], mock.Mock(side_effect=(0, 0)), poll=1)
    assert outcome[0] is False, outcome
    assert 'exited before any control worker' in outcome[1], outcome
    assert len(launches) == 1, launches
    process.terminate.assert_called_once()


def test_unreadable_control_answer_polls_again_instead_of_settling(tmp):
    """A transport failure is not an answer, and the next poll knows it."""
    outcome, _launches, process = _control_diagnosis(
        tmp, [AssertionError('controlled transport failure'), True],
        mock.Mock(side_effect=(0, 0, 0, 31)))
    assert outcome.__class__ is AssertionError, outcome
    process.terminate.assert_called_once()


def test_the_control_extension_satisfies_its_own_probe(tmp):
    """The verdict rests on the control's script reaching its flag."""
    node = shutil.which('node')
    if not node:
        _realbrowser_controls.control_requirement_missing(
            'Node is absent, so the control worker probe cannot be checked')
    control = _realbrowser._control_extension(tmp)
    source = (control / _realbrowser.CONTROL_WORKER_SCRIPT).read_text(
        encoding='utf-8')
    checked = subprocess.run(
        [node, '--check'], input=source, capture_output=True, text=True,
        timeout=10)
    assert checked.returncode == 0, (checked.returncode, checked.stderr)
    answer = subprocess.run(
        [node, '-e',
         source + '\nprocess.stdout.write(String('
                  + _realbrowser.CONTROL_WORKER_PROBE + '))'],
        capture_output=True, text=True, timeout=10)
    assert answer.returncode == 0, (answer.returncode, answer.stderr)
    assert answer.stdout == 'true', (answer.stdout, answer.stderr)


def test_the_control_probe_requirement_is_a_skip_not_a_failure(tmp):
    """A Node-less leg skips the probe control instead of failing it."""
    with mock.patch.object(shutil, 'which', return_value=None):
        failure = _call_failure(
            lambda: test_the_control_extension_satisfies_its_own_probe(tmp))
    assert failure.__class__ is (
        _realbrowser_controls.ControlRequirementSkipped), failure


def test_the_control_extension_is_loadable_and_cannot_collide_with_ours(tmp):
    control = _realbrowser._control_extension(tmp)
    ours = _realbrowser.declared_worker(EXTENSION_ROOT)
    assert _realbrowser.declared_worker(control) == (
        _realbrowser.CONTROL_WORKER_SCRIPT)
    assert _realbrowser.CONTROL_WORKER_SCRIPT != ours, ours
    listed = [
        {'type': 'service_worker',
         'url': f'chrome-extension://ours/{ours}',
         'webSocketDebuggerUrl': 'ws://ours'},
        {'type': 'service_worker', 'url': 'chrome-extension://theirs/x',
         'webSocketDebuggerUrl': 'ws://theirs'},
    ]
    assert _realbrowser._worker_targets(
        listed, _realbrowser.CONTROL_WORKER_SCRIPT) == [], listed


def test_no_browser_never_launches_a_diagnosis(tmp):
    """The diagnosis is reached only after the launch that already happened."""
    launches = []

    def popen(*args, **kwargs):
        launches.append(args)
        raise AssertionError('no browser was required here')

    missing = _realbrowser.BrowserEnvironmentSkipped('no browser on this box')

    def enter_fixture():
        with _enter_fixture(tmp):
            raise AssertionError('fixture yielded with no browser at all')

    with mock.patch.object(
            _realbrowser, 'browser_requirements', side_effect=missing), \
            mock.patch.object(_realbrowser.subprocess, 'Popen', popen):
        failure = _call_failure(enter_fixture)
    assert failure is missing, failure
    assert launches == [], launches


def _navigate(node, target, method, params):
    del node, target, method, params
    return {}


def test_ready_page_and_listed_tab_yield_fixture(tmp):
    page_url = 'http://127.0.0.1:2/plain.html'
    evaluations = []

    def successful_eval(node, target, expression):
        del node, target
        evaluations.append(expression)
        if 'chrome.storage.local.set' in expression:
            return True
        if expression == 'globalThis.__evalPageReady === true':
            return True
        if expression == 'registerAllTabs()':
            return None
        raise AssertionError('unexpected successful-fixture evaluation')

    moments = iter((0, 0, 0, 0, 0, 16, 16))

    def clock():
        return next(moments, 32)

    tabs = [{'tabId': 'controlled-tab', 'url': page_url}]
    with _fixture_runtime(tmp, _navigate) as (process, launches), \
            mock.patch.object(_realbrowser, 'cdp_eval', successful_eval), \
            mock.patch.object(
                _realbrowser._util, 'get_json', return_value=(200, tabs)), \
            mock.patch.object(_realbrowser.time, 'time', clock), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        with _enter_fixture(tmp, page_url) as actual:
            assert actual == (
                'node-for-control', 'ws://page', 'controlled-tab')
    assert len(launches) == 1, launches
    assert process.terminated is True, process.terminated
    assert any('__evalPageReady' in item for item in evaluations), evaluations
    assert evaluations[-1] == 'registerAllTabs()', evaluations


def test_worker_configuration_failure_is_repository_failure(tmp):
    def not_configured(node, target, expression):
        del node, target
        if 'chrome.storage.local.set' in expression:
            return False
        raise AssertionError('unexpected evaluation after configuration')

    with _fixture_runtime(tmp, _navigate), \
            mock.patch.object(_realbrowser, 'cdp_eval', not_configured), \
            mock.patch.object(
                _realbrowser_workers, 'cdp_eval', not_configured), \
            mock.patch.object(
                _realbrowser.time, 'time', side_effect=(0, 0, 0, 31)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        failure = _fixture_failure(tmp)
    assert failure.__class__ is AssertionError, failure
    assert failure.__cause__ is None, failure.__cause__


def test_page_readiness_timeout_is_repository_failure(tmp):
    def never_ready(node, target, expression):
        del node, target
        if 'chrome.storage.local.set' in expression:
            return True
        if '__evalPageReady' in expression:
            return False
        raise AssertionError('unexpected evaluation before readiness timeout')

    with _fixture_runtime(tmp, _navigate), \
            mock.patch.object(_realbrowser, 'cdp_eval', never_ready), \
            mock.patch.object(
                _realbrowser.time, 'time',
                side_effect=(0, 0, 0, 0, 0, 16)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        failure = _fixture_failure(tmp)
    assert failure.__class__ is AssertionError, failure
    assert failure.__cause__ is None, failure.__cause__


def test_tab_registration_timeout_is_repository_failure(tmp):
    def ready_but_unregistered(node, target, expression):
        del node, target
        if 'chrome.storage.local.set' in expression:
            return True
        if '__evalPageReady' in expression:
            return True
        if expression == 'registerAllTabs()':
            return None
        raise AssertionError('unexpected evaluation before tab timeout')

    with _fixture_runtime(tmp, _navigate), \
            mock.patch.object(
                _realbrowser, 'cdp_eval', ready_but_unregistered), \
            mock.patch.object(
                _realbrowser._util, 'get_json', return_value=(200, [])), \
            mock.patch.object(
                _realbrowser.time, 'time',
                side_effect=(0, 0, 0, 0, 0, 0, 0, 16)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        failure = _fixture_failure(tmp)
    assert failure.__class__ is AssertionError, failure
    assert failure.__cause__ is None, failure.__cause__


def _delivery_timeout(call):
    with mock.patch.object(
            _realbrowser._util, 'request',
            return_value=(200, '{"did":"controlled-delivery"}')), \
            mock.patch.object(
                _realbrowser._util, 'get_json', return_value=(200, {})), \
            mock.patch.object(
                _realbrowser.time, 'time', side_effect=(0, 21)):
        return _call_failure(call)


def test_extension_command_delivery_timeout_is_repository_failure(tmp):
    del tmp
    failure = _delivery_timeout(lambda: real_ext_command(
        'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}))
    assert failure.__class__ is AssertionError, failure


def test_extension_command_submission_failure_is_repository_failure(tmp):
    del tmp
    with mock.patch.object(
            _realbrowser._util, 'request',
            return_value=(503, 'controlled extension rejection')):
        failure = _call_failure(lambda: real_ext_command(
            'http://127.0.0.1:1', 'controltoken',
            'controlled-command', {}))
    assert failure.__class__ is AssertionError, failure
    assert '503' in str(failure), failure
    assert 'controlled extension rejection' in str(failure), failure


def test_extension_matching_delivery_returns_result(tmp):
    del tmp
    result = {
        'deliveryId': 'controlled-delivery',
        'resultGeneration': 'controlled-generation',
        'value': 4,
    }
    with mock.patch.object(
            _realbrowser._util, 'request',
            return_value=(200, '{"did":"controlled-delivery"}')), \
            mock.patch.object(
                _realbrowser._util, 'get_json', return_value=(200, result)), \
            mock.patch.object(
                _realbrowser.time, 'time', side_effect=(0, 0, 21)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        actual = real_ext_command(
            'http://127.0.0.1:1', 'controltoken',
            'controlled-command', {})
    assert actual is result, actual


def test_eval_delivery_timeout_is_repository_failure(tmp):
    del tmp
    failure = _delivery_timeout(lambda: real_eval(
        'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
        'controlled-eval', '2 + 2'))
    assert failure.__class__ is AssertionError, failure


def test_eval_submission_failure_is_repository_failure(tmp):
    del tmp
    with mock.patch.object(
            _realbrowser._util, 'request',
            return_value=(503, 'controlled eval rejection')):
        failure = _call_failure(lambda: real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'))
    assert failure.__class__ is AssertionError, failure
    assert '503' in str(failure), failure
    assert 'controlled eval rejection' in str(failure), failure


def test_eval_consume_failure_is_repository_failure(tmp):
    del tmp
    result = {
        'deliveryId': 'controlled-delivery',
        'resultGeneration': 'controlled-generation',
        'value': 4,
    }

    def get_json(url):
        if 'consume=1' in url:
            return 503, {'error': 'controlled consume rejection'}
        return 200, result

    with mock.patch.object(
            _realbrowser._util, 'request',
            return_value=(200, '{"did":"controlled-delivery"}')), \
            mock.patch.object(_realbrowser._util, 'get_json', get_json), \
            mock.patch.object(_realbrowser.time, 'time', side_effect=(0, 0)):
        failure = _call_failure(lambda: real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'))
    assert failure.__class__ is AssertionError, failure
    assert '503' in str(failure), failure
    assert 'controlled consume rejection' in str(failure), failure


def test_eval_matching_delivery_returns_and_consumes_result(tmp):
    del tmp
    result = {
        'deliveryId': 'controlled-delivery',
        'resultGeneration': 'controlled-generation',
        'value': 4,
    }
    reads = []

    def get_json(url):
        reads.append(url)
        if 'consume=1' in url:
            return 200, {'consumed': True}
        return 200, result

    with mock.patch.object(
            _realbrowser._util, 'request',
            return_value=(200, '{"did":"controlled-delivery"}')), \
            mock.patch.object(_realbrowser._util, 'get_json', get_json), \
            mock.patch.object(
                _realbrowser.time, 'time', side_effect=(0, 0, 21)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        actual = real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2')
    assert actual is result, actual
    assert len(reads) == 2, reads
    assert 'consume=1' in reads[-1], reads
    assert 'expected=controlled-generation' in reads[-1], reads


def test_hostile_page_setup_failure_is_repository_failure(tmp):
    @contextlib.contextmanager
    def bridge(*args, **kwargs):
        del args, kwargs
        yield 'http://127.0.0.1:1', Path('/controlled/docroot')

    @contextlib.contextmanager
    def pages():
        yield 'http://127.0.0.1:2'

    @contextlib.contextmanager
    def page(*args, **kwargs):
        del args, kwargs
        yield 'node-for-control', 'ws://page', 'controlled-tab'

    with mock.patch.object(_realbrowser._util, 'bridge', bridge), \
            mock.patch.object(_realbrowser, 'eval_page_server', pages), \
            mock.patch.object(_realbrowser, 'real_extension_page', page), \
            mock.patch.object(
                _realbrowser, 'cdp_eval', return_value='controlled poison'), \
            mock.patch.object(
                _realbrowser, 'real_eval',
                side_effect=AssertionError('eval ran after poisoned setup')):
        failure = _call_failure(lambda: _realbrowser.hostile_eval_matrix(tmp))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled poison' in str(failure), failure


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowserclassification_')


if __name__ == '__main__':
    raise SystemExit(main())
