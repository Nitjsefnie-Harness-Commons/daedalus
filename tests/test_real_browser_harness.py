#!/usr/bin/env python3
"""Browser-free controls for the real-browser fixture machinery."""
import base64
import contextlib
import hashlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _evalpages  # noqa: E402
import _realbrowser  # noqa: E402
import _util  # noqa: E402
import test_real_browser_eval as _real_browser_eval  # noqa: E402


class _ProcessDouble:
    def __init__(self):
        self.terminated = False
        self.wait_timeouts = []

    def terminate(self):
        assert not self.terminated, 'process terminated twice'
        self.terminated = True

    def wait(self, *, timeout):
        assert self.terminated, 'process waited before termination'
        self.wait_timeouts.append(timeout)
        return 0

    def kill(self):
        raise AssertionError('fixture unexpectedly killed its browser')


def _popen_double(tmp):
    process = _ProcessDouble()
    launches = []

    def popen(args, *, cwd, stdin, stdout, stderr):
        assert cwd == _realbrowser.ROOT, cwd
        assert stdin is subprocess.DEVNULL, stdin
        assert stdout is subprocess.DEVNULL, stdout
        assert stderr is subprocess.DEVNULL, stderr
        assert not launches, 'fixture launched more than one browser'
        launches.append(list(args))
        return process

    profile = Path(tmp) / 'chromium-profile'
    return popen, process, launches, profile


def _browser_requirements():
    return 'node-for-control', '/controlled/chromium'


def _browser_version(args, *, capture_output, text, timeout):
    assert args == ['/controlled/chromium', '--version'], args
    assert capture_output is True, capture_output
    assert text is True, text
    assert timeout == 15, timeout
    return types.SimpleNamespace(
        returncode=0, stdout='Chromium 151.0.7922.169\n', stderr='')


def _devtools_ready(expected_profile, expected_process):
    page = {'webSocketDebuggerUrl': 'ws://page'}
    workers = [{
        'type': 'service_worker',
        'url': 'chrome-extension://controlled/background.js',
        'webSocketDebuggerUrl': 'ws://worker',
    }]

    def wait_for_devtools(profile, process):
        assert Path(profile) == expected_profile, profile
        assert process is expected_process, process
        return page, workers, '9222'

    return wait_for_devtools, page, workers


def _ready_worker(node, workers):
    assert node == 'node-for-control', node
    assert workers == [{
        'type': 'service_worker',
        'url': 'chrome-extension://controlled/background.js',
        'webSocketDebuggerUrl': 'ws://worker',
    }], workers
    return 'ws://worker', True, None


def _configured_worker(node, target, expression):
    assert node == 'node-for-control', node
    assert target == 'ws://worker', target
    assert 'chrome.storage.local.set' in expression, expression
    assert 'startStream()' in expression, expression
    return True


@contextlib.contextmanager
def _fixture_runtime(tmp, cdp_call, *, subprocess_run=None):
    popen, process, launches, profile = _popen_double(tmp)
    wait_for_devtools, _page, _workers = _devtools_ready(profile, process)
    patches = [
        mock.patch.object(
            _realbrowser, 'browser_requirements', _browser_requirements),
        mock.patch.object(_realbrowser.subprocess, 'Popen', popen),
        mock.patch.object(
            _realbrowser, '_wait_for_devtools', wait_for_devtools),
        mock.patch.object(_realbrowser, 'ready_worker', _ready_worker),
        mock.patch.object(_realbrowser, 'cdp_eval', _configured_worker),
        mock.patch.object(_realbrowser, 'cdp_call', cdp_call),
    ]
    if subprocess_run is not None:
        patches.append(mock.patch.object(
            _realbrowser.subprocess, 'run', subprocess_run))
    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        yield process, launches


def _enter_fixture(tmp, page_url='http://127.0.0.1:2/plain.html'):
    return _realbrowser.real_extension_page(
        tmp, 'http://127.0.0.1:1', 'controltoken',
        page_url)


def _successful_run_recorder(recorded):
    def run(args, *, cwd, capture_output, text, timeout):
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        assert len(args) == 7, args
        assert args[:3] == [
            'node-for-control', '-e', _evalpages.CDP_CALL_HARNESS], args
        assert args[3:6] == [
            'ws://target', 'Runtime.evaluate', '{"value": 4}'], args
        recorded.append((list(args), timeout))
        return types.SimpleNamespace(returncode=0, stdout='{}', stderr='')

    return run


def test_browser_launch_passes_basic_password_store_flag(tmp):
    def stop_after_launch(profile, process):
        assert Path(profile) == Path(tmp) / 'chromium-profile', profile
        assert isinstance(process, _ProcessDouble), process
        raise _util.Skipped('launch captured')

    popen, process, launches, _profile = _popen_double(tmp)
    with mock.patch.object(
            _realbrowser, 'browser_requirements', _browser_requirements), \
            mock.patch.object(_realbrowser.subprocess, 'Popen', popen), \
            mock.patch.object(
                _realbrowser, '_wait_for_devtools', stop_after_launch):
        try:
            with _enter_fixture(tmp):
                raise AssertionError('fixture yielded after capture stop')
        except _util.Skipped as skipped:
            assert str(skipped) == 'launch captured', skipped

    assert len(launches) == 1, launches
    args = launches[0]
    assert args[0] == '/controlled/chromium', args
    assert args[-1] == 'about:blank', args
    assert args.count('--password-store=basic') == 1, args
    assert process.terminated is True
    assert process.wait_timeouts == [10], process.wait_timeouts


def test_cdp_call_derives_both_deadline_carriers_from_constant(tmp):
    del tmp
    recorded = []
    run = _successful_run_recorder(recorded)
    original_deadline = getattr(
        _realbrowser, 'CDP_RESPONSE_DEADLINE_MS', None)
    assert original_deadline == 10000, original_deadline
    with mock.patch.object(
            _realbrowser, 'CDP_RESPONSE_DEADLINE_MS', 4321), \
            mock.patch.object(_realbrowser.subprocess, 'run', run):
        assert _realbrowser.cdp_call(
            'node-for-control', 'ws://target', 'Runtime.evaluate',
            {'value': 4}) == {}

    assert len(recorded) == 1, recorded
    args, subprocess_timeout = recorded[0]
    assert args[-1] == '4321', args
    assert subprocess_timeout == 9.321, subprocess_timeout
    assert subprocess_timeout > 4.321, subprocess_timeout


def test_cdp_harness_uses_passed_deadline(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the CDP harness control'
    probe = r"""
global.WebSocket = class {
  constructor(target) {
    if (target !== 'ws://controlled') throw new Error('unexpected target');
  }
  addEventListener(name, callback) {
    if (!['open', 'message', 'error'].includes(name)) {
      throw new Error('unexpected event: ' + name);
    }
    if (typeof callback !== 'function') {
      throw new Error('non-function callback');
    }
  }
  close() {}
  send() { throw new Error('the probe never opens the socket'); }
};
global.setTimeout = (callback, delay) => {
  if (typeof callback !== 'function') throw new Error('non-function timer');
  process.stdout.write(String(delay));
  return 1;
};
global.clearTimeout = () => {};
""" + _evalpages.CDP_CALL_HARNESS
    result = subprocess.run(
        [node, '-e', probe, 'ws://controlled', 'Page.navigate', '{}', '4321'],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert result.stdout == '4321', result.stdout
    assert result.stderr == '', result.stderr


def test_cdp_response_timeout_has_distinct_assertion_subtype(tmp):
    del tmp
    timeout_exit_code = getattr(
        _realbrowser, 'CDP_TIMEOUT_EXIT_CODE', None)
    assert timeout_exit_code is not None, 'timeout exit code is unnamed'

    def timed_out(args, *, cwd, capture_output, text, timeout):
        assert len(args) == 7, args
        assert args[-1] == '10000', args
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        assert timeout == 15, timeout
        return types.SimpleNamespace(
            returncode=timeout_exit_code, stdout='',
            stderr='CDP response timed out\n')

    failure = None
    with mock.patch.object(_realbrowser.subprocess, 'run', timed_out):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except AssertionError as why:
            failure = why
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert timeout_type is not None, 'cdp_call has no distinct timeout type'
    assert issubclass(timeout_type, AssertionError), timeout_type
    assert failure.__class__ is timeout_type, failure.__class__
    assert 'CDP response timed out' in str(failure), failure


@contextlib.contextmanager
def _silent_websocket_peer():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    errors = []

    def serve():
        try:
            connection, _address = listener.accept()
            with connection:
                connection.settimeout(2)
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        raise AssertionError('WebSocket handshake ended early')
                    request += chunk
                headers = request.decode('ascii').split('\r\n')
                key = next(
                    line.split(':', 1)[1].strip() for line in headers
                    if line.lower().startswith('sec-websocket-key:'))
                digest = hashlib.sha1(
                    (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
                    .encode('ascii')).digest()
                accepted = base64.b64encode(digest).decode('ascii')
                response = (
                    'HTTP/1.1 101 Switching Protocols\r\n'
                    'Upgrade: websocket\r\n'
                    'Connection: Upgrade\r\n'
                    f'Sec-WebSocket-Accept: {accepted}\r\n\r\n')
                connection.sendall(response.encode('ascii'))
                time.sleep(0.2)
        except Exception as why:  # noqa: BLE001
            errors.append(why)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield f'ws://127.0.0.1:{port}'
    finally:
        listener.close()
        thread.join(timeout=2)
    assert not thread.is_alive(), 'silent WebSocket peer did not stop'
    assert not errors, errors


def test_real_cdp_harness_timeout_is_classified_by_exit_code(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the CDP harness control'
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert timeout_type is not None, 'cdp_call has no timeout type'

    failure = None
    with mock.patch.object(_realbrowser, 'CDP_RESPONSE_DEADLINE_MS', 50), \
            _silent_websocket_peer() as target:
        try:
            _realbrowser.cdp_call(node, target, 'Runtime.evaluate', {})
        except AssertionError as why:
            failure = why
    assert failure.__class__ is timeout_type, failure


def test_outer_subprocess_deadline_is_cdp_timeout(tmp):
    del tmp

    def outer_timeout(args, *, cwd, capture_output, text, timeout):
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        raise subprocess.TimeoutExpired(
            args, timeout, output='', stderr='outer deadline')

    failure = None
    with mock.patch.object(_realbrowser.subprocess, 'run', outer_timeout):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except Exception as why:  # noqa: BLE001
            failure = why
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert failure.__class__ is timeout_type, failure


def test_cdp_non_timeout_failure_stays_plain_assertion(tmp):
    del tmp

    def websocket_failed(args, *, cwd, capture_output, text, timeout):
        assert len(args) == 7, args
        assert args[-1] == '10000', args
        assert cwd == _realbrowser.ROOT, cwd
        assert capture_output is True, capture_output
        assert text is True, text
        assert timeout == 15, timeout
        return types.SimpleNamespace(
            returncode=1, stdout='', stderr='CDP websocket failed\n')

    failure = None
    with mock.patch.object(_realbrowser.subprocess, 'run', websocket_failed):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except AssertionError as why:
            failure = why
    assert failure.__class__ is AssertionError, failure.__class__
    assert 'CDP websocket failed' in str(failure), failure


def test_first_navigation_timeout_skips_when_exact_fixture_url_answers(tmp):
    timeout_type = getattr(_realbrowser, 'CDPTimeout', AssertionError)
    environment_type = getattr(
        _realbrowser, 'BrowserEnvironmentSkipped', ())
    requested = []
    original_get = _realbrowser._EvalPageHandler.do_GET

    def first_navigation(node, target, method, params):
        assert (node, target, method) == (
            'node-for-control', 'ws://page', 'Page.navigate')
        assert params == {'url': page_url}, params
        raise timeout_type('CDP response timed out')

    def record_get(handler):
        requested.append(handler.path)
        return original_get(handler)

    skipped = None
    with mock.patch.object(
            _realbrowser._EvalPageHandler, 'do_GET', record_get), \
            _realbrowser.eval_page_server() as pages:
        page_url = pages + '/never-ready.html'
        with _fixture_runtime(
                tmp, first_navigation, subprocess_run=_browser_version) as (
                    process, launches):
            try:
                with _enter_fixture(tmp, page_url):
                    raise AssertionError(
                        'fixture yielded after navigation timeout')
            except _util.Skipped as why:
                skipped = why
            except AssertionError as failure:
                raise AssertionError(
                    'an answered fixture URL stayed a failure') from failure

    assert len(launches) == 1, launches
    assert process.terminated is True
    assert skipped is not None, 'first navigation timeout did not skip'
    assert isinstance(skipped, environment_type), skipped.__class__
    assert requested == ['/never-ready.html'], requested
    assert '/controlled/chromium' in str(skipped), skipped
    assert 'Chromium 151.0.7922.169' in str(skipped), skipped


def test_first_navigation_timeout_fails_when_exact_fixture_url_stalls(tmp):
    timeout_type = getattr(_realbrowser, 'CDPTimeout', AssertionError)
    original_get = _realbrowser._EvalPageHandler.do_GET
    requested = []

    def first_navigation(node, target, method, params):
        assert (node, target, method) == (
            'node-for-control', 'ws://page', 'Page.navigate')
        assert params == {'url': page_url}, params
        raise timeout_type('CDP response timed out')

    def stall_performance_page(handler):
        requested.append(handler.path)
        if handler.path == '/performance-poison.html':
            time.sleep(0.1)
        else:
            original_get(handler)

    skipped = None
    failure = None
    with mock.patch.object(_realbrowser, 'CDP_RESPONSE_DEADLINE_MS', 50), \
            mock.patch.object(
                _realbrowser._EvalPageHandler, 'do_GET',
                stall_performance_page), \
            _realbrowser.eval_page_server() as pages:
        page_url = pages + '/performance-poison.html'
        with _fixture_runtime(
                tmp, first_navigation, subprocess_run=_browser_version) as (
                    process, launches):
            try:
                with _enter_fixture(tmp, page_url):
                    raise AssertionError(
                        'fixture yielded after navigation timeout')
            except _util.Skipped as why:
                skipped = why
            except AssertionError as why:
                failure = why

    assert len(launches) == 1, launches
    assert process.terminated is True
    assert requested == ['/performance-poison.html'], requested
    assert skipped is None, skipped
    assert failure is not None, 'a stalled fixture URL did not fail'


def test_fixture_probe_derives_bound_and_uses_exact_url(tmp):
    del tmp
    requested = []

    def no_reply(url, *, timeout):
        requested.append((url, timeout))
        raise OSError('controlled fixture silence')

    page_url = 'http://127.0.0.1:43210/exact-fixture.html'
    with mock.patch.object(
            _realbrowser, 'CDP_RESPONSE_DEADLINE_MS', 4321), \
            mock.patch.object(
                _realbrowser.urllib.request, 'urlopen', no_reply):
        assert _realbrowser._fixture_url_answered(page_url) is False
    assert requested == [(page_url, 4.321)], requested


@contextlib.contextmanager
def _controlled_bridge(*args, **kwargs):
    del args, kwargs
    yield 'http://127.0.0.1:1', Path('/controlled/docroot')


@contextlib.contextmanager
def _controlled_pages():
    yield 'http://127.0.0.1:2'


def _run_broken_worker_control(tmp, page_fixture):
    with mock.patch.object(
            _real_browser_eval, 'browser_requirements', lambda: None), \
            mock.patch.object(
                _real_browser_eval._util, 'bridge', _controlled_bridge), \
            mock.patch.object(
                _real_browser_eval, 'eval_page_server', _controlled_pages), \
            mock.patch.object(
                _real_browser_eval, 'real_extension_page', page_fixture):
        return _real_browser_eval \
            .test_a_worker_that_loads_broken_is_a_failure_not_a_skip(tmp)


class _RaisingContext:
    def __init__(self, failure):
        self.failure = failure

    def __enter__(self):
        raise self.failure

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback


def test_broken_worker_wrapper_preserves_environment_skip(tmp):
    environment_type = getattr(
        _realbrowser, 'BrowserEnvironmentSkipped', None)
    assert environment_type is not None, 'environment skip has no identity'

    def environment_skip(*args, **kwargs):
        del args, kwargs
        return _RaisingContext(
            environment_type('controlled browser environment timeout'))

    survived = None
    try:
        _run_broken_worker_control(tmp, environment_skip)
    except environment_type as skipped:
        survived = skipped
    assert survived is not None, 'the wrapper swallowed an environment skip'


def test_broken_worker_wrapper_still_fails_for_broken_extension(tmp):
    def broken_extension(*args, **kwargs):
        del args, kwargs
        return _RaisingContext(AssertionError(
            'the extension service worker never finished loading'))

    try:
        _run_broken_worker_control(tmp, broken_extension)
    except _util.Skipped as skipped:
        raise AssertionError(
            'the broken-worker wrapper excused a broken extension'
        ) from skipped


def test_first_navigation_non_timeout_failure_stays_failure(tmp):
    def first_navigation(node, target, method, params):
        assert (node, target, method) == (
            'node-for-control', 'ws://page', 'Page.navigate')
        assert params == {
            'url': 'http://127.0.0.1:2/plain.html'}, params
        raise AssertionError('CDP rejected navigation')

    skipped = None
    failure = None
    with _fixture_runtime(
            tmp, first_navigation, subprocess_run=_browser_version) as (
                process, launches):
        try:
            with _enter_fixture(tmp):
                raise AssertionError(
                    'fixture yielded after navigation failure')
        except _util.Skipped as why:
            skipped = why
        except AssertionError as why:
            failure = why

    assert len(launches) == 1, launches
    assert process.terminated is True
    assert skipped is None, skipped
    assert str(failure) == 'CDP rejected navigation', failure


def test_post_configuration_navigation_timeout_stays_failure(tmp):
    timeout_type = getattr(_realbrowser, 'CDPTimeout', AssertionError)
    calls = []

    def navigate(node, target, method, params):
        assert (node, target, method) == (
            'node-for-control', 'ws://page', 'Page.navigate')
        assert params == {
            'url': 'http://127.0.0.1:2/plain.html'}, params
        calls.append((node, target, method, params))
        if len(calls) == 1:
            return {}
        if len(calls) == 2:
            raise timeout_type('post-configuration CDP timeout')
        raise AssertionError('unexpected third navigation')

    skipped = None
    failure = None
    with _fixture_runtime(tmp, navigate) as (process, launches):
        try:
            with _enter_fixture(tmp):
                raise AssertionError('fixture yielded after second timeout')
        except _util.Skipped as why:
            skipped = why
        except AssertionError as why:
            failure = why

    assert len(launches) == 1, launches
    assert len(calls) == 2, calls
    assert process.terminated is True
    assert skipped is None, skipped
    assert failure.__class__ is timeout_type, failure.__class__
    assert str(failure) == 'post-configuration CDP timeout', failure


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='realbrowserharness_')


if __name__ == '__main__':
    raise SystemExit(main())
