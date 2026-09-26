"""Browser-free stand-ins for the real-browser fixture's own machinery.

Not a suite itself — run_tests.py only loads `test_*.py`.

These moved here out of tests/test_real_browser_harness.py, which the
classification, environment and recovery suites imported for them. Nothing
here launches anything: every process the fixture would spawn is a double, so
a control can drive the fixture's own error classification, launch flags and
navigation deadlines with no browser on the machine.
"""
import contextlib
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402


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
    workers = [{'type': 'service_worker',
                'url': 'chrome-extension://controlled/background.js',
                'webSocketDebuggerUrl': 'ws://worker'}]

    def wait_for_devtools(profile, process, declared_worker):
        assert Path(profile) == expected_profile, profile
        assert process is expected_process, process
        assert declared_worker == 'background.js', declared_worker
        return page, workers, '9222'

    return wait_for_devtools, page, workers


def _ready_worker(node, workers):
    assert node == 'node-for-control', node
    assert workers == [{'type': 'service_worker',
                        'url': 'chrome-extension://controlled/background.js',
                        'webSocketDebuggerUrl': 'ws://worker'}], workers
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
