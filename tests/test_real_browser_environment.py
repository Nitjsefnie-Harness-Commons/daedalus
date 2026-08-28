#!/usr/bin/env python3
"""Browser-free controls for real-browser environment classification."""
import errno
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _util  # noqa: E402
from test_real_browser_harness import _enter_fixture  # noqa: E402


def _which_with(node):
    def which(name):
        return {'node': node,
                'chromium': '/controlled/chromium'}.get(name)

    return which


def test_browser_environment_skip_has_runner_identity(tmp):
    del tmp
    assert issubclass(
        _realbrowser.BrowserEnvironmentSkipped, _util.Skipped)


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
    if not node:
        _util.skip('Node is absent, so its repository probe cannot be checked')
    capability = subprocess.run(
        [node, '-e',
         "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
        capture_output=True, text=True, timeout=10)
    assert capability.returncode in (0, 1), (
        capability.returncode, capability.stdout, capability.stderr)
    requirements = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(_realbrowser, 'NODE_PROBE_TIMEOUT', 1):
        try:
            requirements = _realbrowser.browser_requirements()
        except _realbrowser.BrowserEnvironmentSkipped as why:
            if capability.returncode == 0:
                raise AssertionError(
                    f'the repository probe skipped capable Node {node}'
                ) from why
            _util.skip('Node is present but lacks the required WebSocket')
    assert capability.returncode == 0, (
        'the repository probe accepted Node without WebSocket', node)
    assert requirements == (node, '/controlled/chromium'), requirements


def test_repository_worker_probe_matches_declared_functions(tmp):
    del tmp
    node = shutil.which('node')
    if not node:
        _util.skip('Node is absent, so the worker probe cannot be checked')

    def evaluate(declarations):
        program = (
            declarations + '\nprocess.stdout.write(String('
            + _realbrowser._WORKER_READY_PROBE + '));')
        result = subprocess.run(
            [node, '-e', program], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (
            node, result.returncode, result.stdout, result.stderr)
        return result.stdout

    declared = (
        'function loadConfig() {}\n'
        'function ensureKeepAlive() {}\n'
        'function startStream() {}')
    assert evaluate('') == 'false'
    assert evaluate(declared) == 'true'


def test_invalid_repository_node_probe_is_harness_failure(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    failure = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(
                _realbrowser, 'NODE_WEBSOCKET_PROBE', 'function {'):
        try:
            _realbrowser.browser_requirements()
        except Exception as why:  # noqa: BLE001
            failure = why
    assert failure.__class__ is AssertionError, failure


def test_zero_exit_invalid_node_probe_is_harness_failure(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    failure = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(_realbrowser, 'NODE_WEBSOCKET_PROBE', ''):
        try:
            _realbrowser.browser_requirements()
        except Exception as why:  # noqa: BLE001
            failure = why
    assert failure.__class__ is AssertionError, failure
    assert node in str(failure), failure


def test_node_probe_absent_token_is_environment_skip(tmp):
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    absent_probe = "process.stdout.write('websocket-absent')"
    skipped = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(
                _realbrowser, 'NODE_WEBSOCKET_PROBE', absent_probe):
        try:
            _realbrowser.browser_requirements()
        except _realbrowser.BrowserEnvironmentSkipped as why:
            skipped = why
    assert skipped is not None, 'absent WebSocket token did not skip'


def test_e2big_start_failure_skips_when_minimal_spawn_fails(tmp):
    del tmp
    too_large = OSError(errno.E2BIG, 'platform-dependent size refusal')
    calls = []

    def minimal_spawn(args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        raise OSError(errno.E2BIG, 'controlled minimal spawn refusal')

    skipped = None
    with mock.patch.object(_realbrowser.subprocess, 'run', minimal_spawn):
        try:
            _realbrowser._raise_start_failure(
                'Node WebSocket probe', '/controlled/node', too_large)
        except _realbrowser.BrowserEnvironmentSkipped as why:
            skipped = why
    assert skipped is not None, 'E2BIG did not produce an environment skip'
    assert skipped.__cause__ is too_large, skipped.__cause__
    assert calls[0][0] == [sys.executable, '-c', ''], calls
    assert calls[0][1].get('env') is None, calls


def test_e2big_start_failure_fails_when_minimal_spawn_succeeds(tmp):
    del tmp
    too_large = OSError(errno.E2BIG, 'platform-dependent size refusal')
    calls = []

    def minimal_spawn(args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        return subprocess.CompletedProcess(args, 0)

    failure = None
    with mock.patch.object(_realbrowser.subprocess, 'run', minimal_spawn):
        try:
            _realbrowser._raise_start_failure(
                'Node WebSocket probe', '/controlled/node', too_large)
        except Exception as why:  # noqa: BLE001
            failure = why
    assert failure.__class__ is AssertionError, failure
    assert failure.__cause__ is too_large, failure.__cause__
    assert calls[0][0] == [sys.executable, '-c', ''], calls
    assert calls[0][1].get('env') is None, calls


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
