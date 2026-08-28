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


def test_missing_browser_requirements_are_environment_skip(tmp):
    del tmp
    with mock.patch.object(_realbrowser.shutil, 'which', return_value=None):
        failure = _call_failure(_realbrowser.browser_requirements)
    assert failure.__class__ is _realbrowser.BrowserEnvironmentSkipped, failure


def test_browser_exit_before_devtools_is_environment_skip(tmp):
    process = mock.Mock()
    process.poll.return_value = 1
    failure = _call_failure(
        lambda: _realbrowser._wait_for_devtools(tmp, process))
    assert failure.__class__ is _realbrowser.BrowserEnvironmentSkipped, failure


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


def test_eval_delivery_timeout_is_repository_failure(tmp):
    del tmp
    failure = _delivery_timeout(lambda: _realbrowser.real_eval(
        'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
        'controlled-eval', '2 + 2'))
    assert failure.__class__ is AssertionError, failure


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='realbrowserclassification_')


if __name__ == '__main__':
    raise SystemExit(main())
