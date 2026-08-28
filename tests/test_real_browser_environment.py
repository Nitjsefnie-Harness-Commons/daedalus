#!/usr/bin/env python3
"""Browser-free controls for real-browser environment classification."""
import errno
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _util  # noqa: E402
from test_real_browser_harness import (  # noqa: E402
    _browser_version, _enter_fixture, _fixture_runtime)


def _navigation_timeout(tmp, *, request_arrives, channel_answers,
                        seed_stale_request=False):
    timeout_type = _realbrowser.CDPTimeout
    calls = []

    def cdp_call(node, target, method, params):
        calls.append(method)
        assert (node, target) == ('node-for-control', 'ws://page')
        if method == 'Page.navigate':
            assert params == {'url': page_url}, params
            if request_arrives:
                with urllib.request.urlopen(page_url, timeout=2) as reply:
                    assert reply.status == 200, reply.status
            raise timeout_type('controlled navigation timeout')
        assert method == 'Runtime.evaluate', method
        if not channel_answers:
            raise timeout_type('controlled liveness timeout')
        return {'result': {'value': 1}}

    skipped = None
    failure = None
    with _realbrowser.eval_page_server() as pages:
        page_url = pages + '/plain.html'
        if seed_stale_request:
            with urllib.request.urlopen(page_url, timeout=2) as reply:
                assert reply.status == 200, reply.status
        with _fixture_runtime(
                tmp, cdp_call, subprocess_run=_browser_version):
            try:
                with _enter_fixture(tmp, page_url):
                    raise AssertionError('fixture yielded after timeout')
            except _util.Skipped as why:
                skipped = why
            except AssertionError as why:
                failure = why
    return skipped, failure, calls


def test_navigation_timeout_skips_when_cdp_channel_is_dead(tmp):
    skipped, failure, calls = _navigation_timeout(
        tmp, request_arrives=True, channel_answers=False)
    assert isinstance(skipped, _realbrowser.BrowserEnvironmentSkipped), skipped
    assert failure is None, failure
    assert calls == ['Page.navigate', 'Runtime.evaluate'], calls
    assert '/controlled/chromium' in str(skipped), skipped


def test_navigation_timeout_skips_when_live_channel_saw_no_new_request(tmp):
    skipped, failure, calls = _navigation_timeout(
        tmp, request_arrives=False, channel_answers=True,
        seed_stale_request=True)
    assert isinstance(skipped, _realbrowser.BrowserEnvironmentSkipped), skipped
    assert failure is None, failure
    assert calls == ['Page.navigate', 'Runtime.evaluate'], calls
    assert '/controlled/chromium' in str(skipped), skipped


def test_navigation_timeout_fails_when_channel_and_fixture_were_reached(tmp):
    skipped, failure, calls = _navigation_timeout(
        tmp, request_arrives=True, channel_answers=True)
    assert skipped is None, skipped
    assert failure is not None, 'live reached fixture did not fail'
    assert calls == ['Page.navigate', 'Runtime.evaluate'], calls


def _which_with(node):
    def which(name):
        return {'node': node,
                'chromium': '/controlled/chromium'}.get(name)

    return which


def test_node_interpreter_start_failure_is_environment_skip(tmp):
    bad_node = Path(tmp) / 'node'
    bad_node.write_text('#!/missing/node-loader\n', encoding='utf-8')
    bad_node.chmod(0o755)
    skipped = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(str(bad_node))):
        try:
            _realbrowser.browser_requirements()
        except _realbrowser.BrowserEnvironmentSkipped as why:
            skipped = why
    assert skipped is not None, 'unspawnable Node did not skip'
    assert str(bad_node) in str(skipped), skipped
    assert isinstance(skipped.__cause__, OSError), skipped.__cause__


def test_repository_node_probe_starts_and_terminates(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(_realbrowser, 'NODE_PROBE_TIMEOUT', 1):
        assert _realbrowser.browser_requirements() == (
            node, '/controlled/chromium')


def test_oversized_node_probe_is_harness_failure(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    failure = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(
                _realbrowser, 'NODE_WEBSOCKET_PROBE', ' ' * (1024 * 1024)):
        try:
            _realbrowser.browser_requirements()
        except Exception as why:  # noqa: BLE001
            failure = why
    assert failure.__class__ is AssertionError, failure
    assert isinstance(failure.__cause__, OSError), failure.__cause__


def test_nonterminating_node_probe_is_harness_failure(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    failure = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(
                _realbrowser, 'NODE_WEBSOCKET_PROBE', 'while (true) {}'), \
            mock.patch.object(_realbrowser, 'NODE_PROBE_TIMEOUT', 0.05):
        try:
            _realbrowser.browser_requirements()
        except Exception as why:  # noqa: BLE001
            failure = why
    assert failure.__class__ is AssertionError, failure
    assert isinstance(
        failure.__cause__, subprocess.TimeoutExpired), failure.__cause__


def test_browser_interpreter_start_failure_is_environment_skip(tmp):
    bad_browser = Path(tmp) / 'chromium'
    bad_browser.write_text('#!/missing/chromium-loader\n', encoding='utf-8')
    bad_browser.chmod(0o755)
    skipped = None
    with mock.patch.object(
            _realbrowser, 'browser_requirements',
            return_value=('node-for-control', str(bad_browser))):
        try:
            with _enter_fixture(tmp):
                raise AssertionError('unspawnable browser fixture yielded')
        except _realbrowser.BrowserEnvironmentSkipped as why:
            skipped = why
    assert skipped is not None, 'unspawnable browser did not skip'
    assert str(bad_browser) in str(skipped), skipped
    assert isinstance(skipped.__cause__, OSError), skipped.__cause__


def test_oversized_browser_command_is_harness_failure(tmp):
    too_large = OSError(errno.ENOENT, 'platform-dependent argument error')
    too_large.winerror = 206
    failure = None
    with mock.patch.object(
            _realbrowser, 'browser_requirements',
            return_value=('node-for-control', '/controlled/chromium')), \
            mock.patch.object(
                _realbrowser.subprocess, 'Popen', side_effect=too_large):
        try:
            with _enter_fixture(tmp):
                raise AssertionError('oversized browser command yielded')
        except Exception as why:  # noqa: BLE001
            failure = why
    assert failure.__class__ is AssertionError, failure
    assert failure.__cause__ is too_large, failure.__cause__


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='realbrowserenvironment_')


if __name__ == '__main__':
    raise SystemExit(main())
