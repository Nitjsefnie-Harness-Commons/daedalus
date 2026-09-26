#!/usr/bin/env python3
"""Browser-free controls for the real-browser fixture machinery."""
import base64
import contextlib
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _evalpages  # noqa: E402
import _realbrowser_broken_worker as _broken_worker  # noqa: E402
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402
import _realbrowser_workers as _WORKERS  # noqa: E402
import _util  # noqa: E402
from _realbrowser_fixture_controls import (  # noqa: E402
    _browser_requirements, _browser_version, _enter_fixture,
    _fixture_runtime, _popen_double, _ProcessDouble)


def test_the_process_double_keeps_the_refusals_a_popen_has(tmp):
    """A shared double is only a model of Popen while it keeps its refusals.

    Two suites read this double, so a wrong one now fails nobody in
    particular: a browser is waited on only after it was terminated, is
    terminated once, and is never killed. Each refusal is a teardown order
    the fixture has to keep, and each is otherwise asserted nowhere.
    """
    del tmp
    process = _ProcessDouble()
    try:
        process.wait(timeout=10)
    except AssertionError as refusal:
        assert 'waited before termination' in str(refusal), refusal
    else:
        raise AssertionError('the double waited before termination')

    process.terminate()
    assert process.wait(timeout=10) == 0
    assert process.wait_timeouts == [10], process.wait_timeouts
    try:
        process.terminate()
    except AssertionError as refusal:
        assert 'terminated twice' in str(refusal), refusal
    else:
        raise AssertionError('the double accepted a second termination')

    try:
        process.kill()
    except AssertionError as refusal:
        assert 'unexpectedly killed' in str(refusal), refusal
    else:
        raise AssertionError('the double let the fixture kill its browser')


def test_cdp_eval_preserves_typed_evaluation_failure(tmp):
    del tmp
    response = {
        'exceptionDetails': {'text': 'controlled evaluation failure'},
        'result': {'value': 'must not be returned'},
    }
    failure = None
    with mock.patch.object(
            _WORKERS, 'cdp_call', return_value=response):
        try:
            _realbrowser.cdp_eval(
                'node-for-control', 'ws://worker', 'controlled probe')
        except _realbrowser.CDPEvaluationError as why:
            failure = why
    assert failure.__class__ is _realbrowser.CDPEvaluationError, failure
    assert failure.response is response, failure.response


def test_repository_worker_probe_exception_counts_as_reached(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the worker probe control'
    checks = []

    def evaluate(_node, target, method, params):
        assert (_node, target, method) == (
            node, 'ws://worker', 'Runtime.evaluate')
        check = subprocess.run(
            [node, '--check'], input=params['expression'],
            capture_output=True, text=True, timeout=10)
        checks.append(check)
        if check.returncode:
            return {'exceptionDetails': {'text': 'probe did not parse'}}
        return {'result': {'value': False}}

    workers = [{'webSocketDebuggerUrl': 'ws://worker'}]
    with mock.patch.object(_WORKERS, 'cdp_call', evaluate):
        target, reached, reason = _realbrowser.ready_worker(node, workers)
        assert checks[-1].returncode == 0, checks[-1]
        assert target is None, target
        assert reached is True, reason

        with mock.patch.object(
                _WORKERS, '_WORKER_READY_PROBE', 'function {'):
            target, reached, reason = _realbrowser.ready_worker(node, workers)
        assert checks[-1].returncode != 0, checks[-1]
        assert target is None, target
        assert reached is True, reason


def test_worker_transport_failure_stays_unreached(tmp):
    del tmp

    def unanswered(*args, **kwargs):
        del args, kwargs
        raise AssertionError('controlled transport failure')

    workers = [{'webSocketDebuggerUrl': 'ws://worker'}]
    with mock.patch.object(_WORKERS, 'cdp_call', unanswered):
        target, reached, reason = _realbrowser.ready_worker(
            'node-for-control', workers)
    assert target is None, target
    assert reached is False, reason


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
    def stop_after_launch(profile, process, declared_worker):
        assert Path(profile) == Path(tmp) / 'chromium-profile', profile
        assert isinstance(process, _ProcessDouble), process
        assert declared_worker == 'background.js', declared_worker
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
    assert (args[0], args[-1]) == ('/controlled/chromium', 'about:blank'), args
    assert args.count('--password-store=basic') == 1, args
    assert process.wait_timeouts == [10], process.wait_timeouts


def test_cdp_call_derives_both_deadline_carriers_from_constant(tmp):
    del tmp
    recorded = []
    run = _successful_run_recorder(recorded)
    original_deadline = getattr(_WORKERS, 'CDP_RESPONSE_DEADLINE_MS', None)
    assert original_deadline == 10000, original_deadline
    with mock.patch.object(_WORKERS, 'CDP_RESPONSE_DEADLINE_MS', 4321), \
            mock.patch.object(_WORKERS.subprocess, 'run', run):
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
    with mock.patch.object(_WORKERS.subprocess, 'run', timed_out):
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


# Above the harness child's outer bound (deadline/1000 + 5) and level with
# the production probe budget; the join waits two quanta plus slack.
PEER_PATIENCE = 10


@contextlib.contextmanager
def _silent_websocket_peer():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(PEER_PATIENCE)
    port = listener.getsockname()[1]
    record = {'served': False, 'errors': []}

    def serve():
        try:
            connection, _address = listener.accept()
            with connection:
                connection.settimeout(PEER_PATIENCE)
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        raise AssertionError('WebSocket handshake ended early')
                    request += chunk
                key = next(
                    line.split(':', 1)[1].strip()
                    for line in request.decode('ascii').split('\r\n')
                    if line.lower().startswith('sec-websocket-key:'))
                accept = base64.b64encode(hashlib.sha1(
                    (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
                    .encode('ascii')).digest()).decode('ascii')
                connection.sendall((
                    'HTTP/1.1 101 Switching Protocols\r\n'
                    'Upgrade: websocket\r\n'
                    'Connection: Upgrade\r\n'
                    f'Sec-WebSocket-Accept: {accept}\r\n\r\n'
                ).encode('ascii'))
                record['served'] = True
                time.sleep(0.2)
        except Exception as why:  # noqa: BLE001
            record['errors'].append(why)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield f'ws://127.0.0.1:{port}', record
    finally:
        listener.close()
        thread.join(timeout=2 * PEER_PATIENCE + 1)
    assert not thread.is_alive(), 'silent WebSocket peer did not stop'


def test_real_cdp_harness_timeout_is_classified_by_exit_code(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the CDP harness control'
    timeout_type = getattr(_realbrowser, 'CDPTimeout', None)
    assert timeout_type is not None, 'cdp_call has no timeout type'

    records = []
    for _attempt in range(2):
        failure = None
        deadline = mock.patch.object(
            _WORKERS, 'CDP_RESPONSE_DEADLINE_MS', 50)
        with deadline, _silent_websocket_peer() as (target, record):
            records.append(record)
            try:
                _realbrowser.cdp_call(node, target, 'Runtime.evaluate', {})
            except AssertionError as why:
                failure = why
        if record['served']:
            assert not record['errors'], record
            break
    else:
        raise AssertionError(
            f'the silent peer never served the harness child: {records}')
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
    with mock.patch.object(_WORKERS.subprocess, 'run', outer_timeout):
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
    with mock.patch.object(_WORKERS.subprocess, 'run', websocket_failed):
        try:
            _realbrowser.cdp_call(
                'node-for-control', 'ws://target', 'Runtime.evaluate', {})
        except AssertionError as why:
            failure = why
    assert failure.__class__ is AssertionError, failure.__class__
    assert 'CDP websocket failed' in str(failure), failure


def test_fixture_converts_only_post_configuration_environment_skips(tmp):
    environment_type = _realbrowser.BrowserEnvironmentSkipped
    before = environment_type('controlled pre-configuration failure')
    popen, _process, launches, _profile = _popen_double(tmp)
    survived = None
    verdict = mock.Mock(return_value=(False, 'controlled observation'))
    with mock.patch.object(
            _realbrowser, 'browser_requirements', _browser_requirements), \
            mock.patch.object(_realbrowser.subprocess, 'Popen', popen), \
            mock.patch.object(
                _realbrowser, '_wait_for_devtools', side_effect=before), \
            mock.patch.object(
                _realbrowser, '_worker_absence_verdict', verdict):
        try:
            with _enter_fixture(tmp):
                raise AssertionError('fixture yielded before configuration')
        except _util.Skipped as why:
            survived = why
    assert len(launches) == 1, launches
    assert survived is before, survived
    assert verdict.call_count == 1, verdict.call_count

    def navigate(node, target, method, params):
        del node, target, method, params
        return {}

    def skip_after_configuration(node, target, expression):
        del node, target
        if 'chrome.storage.local.set' in expression:
            return True
        raise environment_type('controlled post-configuration failure')

    failure = None
    with _fixture_runtime(tmp, navigate) as (process, launches), \
            mock.patch.object(
                _realbrowser, 'cdp_eval', skip_after_configuration):
        try:
            with _enter_fixture(tmp):
                raise AssertionError('fixture yielded after configuration')
        except environment_type as why:
            raise AssertionError(
                'post-configuration environment skip escaped') from why
        except AssertionError as why:
            failure = why
    assert len(launches) == 1 and process.terminated is True, launches
    assert failure.__class__ is AssertionError, failure
    assert isinstance(failure.__cause__, environment_type), failure.__cause__


@contextlib.contextmanager
def _controlled_bridge(*args, **kwargs):
    del args, kwargs
    yield 'http://127.0.0.1:1', Path('/controlled/docroot')


@contextlib.contextmanager
def _controlled_pages():
    yield 'http://127.0.0.1:2'


def _run_broken_worker_control(tmp, page_fixture):
    """Drive the shared property with one of the fixture's own verdicts."""
    with mock.patch.object(
            _broken_worker, 'browser_requirements', lambda: None), \
            mock.patch.object(
                _broken_worker, 'real_extension_page', page_fixture), \
            _controlled_bridge() as (bridge_url, _docroot), \
            _controlled_pages() as pages:
        _broken_worker.assert_broken_worker_is_a_failure(
            tmp, bridge_url, pages)


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


def test_broken_worker_wrapper_turns_a_skip_into_a_failure(tmp):
    """The verdict in between the other two is the one that used to be lost.

    The environment skip must survive and the extension's own failure must be
    reported; neither of those says what happens to a plain skip, and a plain
    skip is what the fixture used to reach for. `run_controls` guards only
    `ControlRequirementSkipped`, so a skip escaping this path is reported as a
    skip — a green that means the property was not exercised at all.
    """
    excised = _util.Skipped('the worker never finished loading')

    def skipped(*args, **kwargs):
        del args, kwargs
        return _RaisingContext(excised)

    try:
        _run_broken_worker_control(tmp, skipped)
    except _util.Skipped as survived:
        raise AssertionError(
            'the broken-worker control excused a broken extension as a skip'
        ) from survived
    except AssertionError as failure:
        assert failure.__cause__ is excised, failure.__cause__
        assert 'reported as an environment skip' in str(failure), failure
    else:
        raise AssertionError('a skip on a broken extension was accepted')


def test_first_navigation_non_timeout_failure_stays_failure(tmp):
    def first_navigation(node, target, method, params):
        assert (node, target, method) == (
            'node-for-control', 'ws://page', 'Page.navigate')
        assert params == {'url': 'http://127.0.0.1:2/plain.html'}, params
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


def test_first_navigation_timeout_fails_with_arrival_observation(tmp):
    timeout_type = _realbrowser.CDPTimeout

    def exercise(request_arrives):
        calls = []
        failure = None

        def first_navigation(node, target, method, params):
            assert (node, target, method) == (
                'node-for-control', 'ws://page', 'Page.navigate')
            assert params == {'url': page_url}, params
            calls.append(method)
            if request_arrives:
                with urllib.request.urlopen(page_url, timeout=2) as reply:
                    assert reply.status == 200, reply.status
            raise timeout_type('controlled navigation timeout')

        with _realbrowser.eval_page_server() as pages:
            page_url = pages + '/plain.html'
            if not request_arrives:
                with urllib.request.urlopen(page_url, timeout=2) as reply:
                    assert reply.status == 200, reply.status
            with _fixture_runtime(
                    tmp, first_navigation,
                    subprocess_run=_browser_version):
                try:
                    with _enter_fixture(tmp, page_url):
                        raise AssertionError('fixture yielded after timeout')
                except _util.Skipped as why:
                    raise AssertionError(
                        'first navigation timeout was skipped') from why
                except AssertionError as why:
                    failure = why

        assert calls == ['Page.navigate'], calls
        return page_url, failure

    failures = {
        request_arrives: exercise(request_arrives)
        for request_arrives in (False, True)
    }

    failure_type = getattr(
        _realbrowser, 'FirstNavigationTimeout', None)
    assert failure_type is not None, 'timeout has no observation-bearing type'
    required_owners = [
        'the browser', 'the CDP transport', 'this repository', 'the machine']
    for request_arrives, (page_url, failure) in failures.items():
        assert failure.__class__ is failure_type, failure
        record = {'page_url': failure.page_url,
                  'request_arrived': failure.request_arrived,
                  'candidate_owners': list(failure.candidate_owners),
                  'selected_owner': failure.selected_owner}
        assert record == {'page_url': page_url,
                          'request_arrived': request_arrives,
                          'candidate_owners': required_owners,
                          'selected_owner': None}, record
        assert str(failure) == (
            f'{failure_type.__name__}: '
            + json.dumps(record, sort_keys=True)), str(failure)


def test_post_configuration_navigation_timeout_stays_failure(tmp):
    timeout_type = getattr(_realbrowser, 'CDPTimeout', AssertionError)
    calls = []

    def navigate(node, target, method, params):
        assert (node, target, method) == (
            'node-for-control', 'ws://page', 'Page.navigate')
        assert params == {'url': 'http://127.0.0.1:2/plain.html'}, params
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
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowserharness_')


if __name__ == '__main__':
    raise SystemExit(main())
