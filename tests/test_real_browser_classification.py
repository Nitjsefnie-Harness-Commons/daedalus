#!/usr/bin/env python3
"""Browser-free mutation controls for fixture fault classification."""
import errno
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _util  # noqa: E402
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


def test_browser_exit_before_devtools_is_environment_skip(tmp):
    for exit_code in (1, 0):
        process = mock.Mock()
        process.poll.return_value = exit_code
        clock = mock.Mock(side_effect=(0, 0, 31))
        sleeper = mock.Mock()
        with mock.patch.object(_realbrowser.time, 'time', clock), \
                mock.patch.object(_realbrowser.time, 'sleep', sleeper):
            failure = _call_failure(
                lambda: _realbrowser._wait_for_devtools(tmp, process))
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
            actual = _realbrowser._wait_for_devtools(tmp, process)
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
            lambda: _realbrowser._wait_for_devtools(tmp, process))
    assert failure.__class__ is _realbrowser.BrowserEnvironmentSkipped, failure


def _worker_timeout_failure(tmp, reached):
    def navigate(node, target, method, params):
        del node, target, method, params
        return {}

    def unready(node, workers):
        del node, workers
        return None, reached, 'controlled worker timeout'

    with _fixture_runtime(
            tmp, navigate, subprocess_run=_browser_version), \
            mock.patch.object(_realbrowser, 'ready_worker', unready), \
            mock.patch.object(
                _realbrowser, '_devtools_targets', return_value=[]), \
            mock.patch.object(
                _realbrowser.time, 'time', side_effect=(0, 0, 31)), \
            mock.patch.object(_realbrowser.time, 'sleep'):
        return _fixture_failure(tmp)


def test_answering_unready_worker_is_repository_failure(tmp):
    failure = _worker_timeout_failure(tmp, True)
    assert failure.__class__ is AssertionError, failure


def test_unreachable_worker_is_environment_skip(tmp):
    failure = _worker_timeout_failure(tmp, False)
    assert failure.__class__ is _realbrowser.BrowserEnvironmentSkipped, failure


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
    failure = _delivery_timeout(lambda: _realbrowser.real_ext_command(
        'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}))
    assert failure.__class__ is AssertionError, failure


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
        actual = _realbrowser.real_ext_command(
            'http://127.0.0.1:1', 'controltoken',
            'controlled-command', {})
    assert actual is result, actual


def test_eval_delivery_timeout_is_repository_failure(tmp):
    del tmp
    failure = _delivery_timeout(lambda: _realbrowser.real_eval(
        'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
        'controlled-eval', '2 + 2'))
    assert failure.__class__ is AssertionError, failure


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
        actual = _realbrowser.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2')
    assert actual is result, actual
    assert len(reads) == 2, reads
    assert 'consume=1' in reads[-1], reads
    assert 'expected=controlled-generation' in reads[-1], reads


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='realbrowserclassification_')


if __name__ == '__main__':
    raise SystemExit(main())
