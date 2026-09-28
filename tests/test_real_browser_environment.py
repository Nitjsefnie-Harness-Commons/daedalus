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
import _realbrowser_controls  # noqa: E402
import _util  # noqa: E402
from _node_launch_routing import (  # noqa: E402
    SITE_HANG_MULTIPLE, node_bound_expiry)
from _realbrowser_fixture_controls import _enter_fixture  # noqa: E402


def _which_with(node):
    def which(name):
        return {'node': node,
                'chromium': '/controlled/chromium'}.get(name)

    return which


# The two children this suite launches, and a bound composed for each. Both
# programs are literals below — one exits on a global, one is function
# declarations and a single write — so neither can reach outside the suite
# and a bound here is for a WEDGED child, never a slow one. The samples are
# measured with the machine BUSY, because a bare 10 was measuring the
# runner's busyness and nothing else.
#
#   REPO_PROBE_SAMPLES_S     idle then busy, the capability probe
#   REPO_PROBE_SLOWEST_S     4.081  max of those
#   REPO_PROBE_DEADLINE_S    20     round(4.081 * 5)
#
#   WORKER_PROBE_SAMPLES_S   idle then busy, the worker probe
#   WORKER_PROBE_SLOWEST_S   2.574  max of those
#   WORKER_PROBE_DEADLINE_S  13     round(2.574 * 5)
#
# Neither site has a runtime stall control, and that is a stated limit rather
# than an oversight: each program is a literal inside the test function, so
# the only lever that could wedge the child is the `node` executable itself,
# and a `#!` stand-in is not runnable on the Windows legs. What holds their
# figures instead is the per-stem rule in `tests/test_node_launch_routing.py`,
# which names the module and the stem whose deadline is not composed. The
# walk itself derives the site POPULATION, and its allowance is per-module,
# so it does not read a bound added inside a module it already excuses.
REPO_PROBE_SAMPLES_S = (0.635, 0.326, 0.309, 0.312,
                        3.633, 4.081, 1.533, 0.238)
REPO_PROBE_SLOWEST_S = max(REPO_PROBE_SAMPLES_S)
REPO_PROBE_DEADLINE_S = round(REPO_PROBE_SLOWEST_S * SITE_HANG_MULTIPLE)
WORKER_PROBE_SAMPLES_S = (0.084, 0.108, 0.115, 0.201,
                          1.089, 1.174, 0.201, 2.574)
WORKER_PROBE_SLOWEST_S = max(WORKER_PROBE_SAMPLES_S)
WORKER_PROBE_DEADLINE_S = round(WORKER_PROBE_SLOWEST_S * SITE_HANG_MULTIPLE)


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
        _realbrowser_controls.control_requirement_missing(
            'Node is absent, so its repository probe cannot be checked')
    try:
        capability = subprocess.run(
            [node, '-e',
             "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
            capture_output=True, text=True, timeout=REPO_PROBE_DEADLINE_S)
    except subprocess.TimeoutExpired as why:
        raise node_bound_expiry(why, REPO_PROBE_DEADLINE_S) from why
    assert capability.returncode in (0, 1), (
        capability.returncode, capability.stdout, capability.stderr)
    requirements = None
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)):
        try:
            requirements = _realbrowser.browser_requirements()
        except _realbrowser.BrowserEnvironmentSkipped as why:
            if capability.returncode == 0:
                raise AssertionError(
                    f'the repository probe skipped capable Node {node}'
                ) from why
            _realbrowser_controls.control_requirement_missing(
                'Node is present but lacks the required WebSocket')
    assert capability.returncode == 0, (
        'the repository probe accepted Node without WebSocket', node)
    assert requirements == (node, '/controlled/chromium'), requirements


def test_repository_worker_probe_matches_declared_functions(tmp):
    del tmp
    node = shutil.which('node')
    if not node:
        _realbrowser_controls.control_requirement_missing(
            'Node is absent, so the worker probe cannot be checked')

    def evaluate(declarations):
        program = (
            declarations + '\nprocess.stdout.write(String('
            + _realbrowser._WORKER_READY_PROBE + '));')
        try:
            result = subprocess.run(
                [node, '-e', program], capture_output=True, text=True,
                timeout=WORKER_PROBE_DEADLINE_S)
        except subprocess.TimeoutExpired as why:
            raise node_bound_expiry(why, WORKER_PROBE_DEADLINE_S) from why
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
    """A wedged capability probe is the site's OWN named failure, with output.

    The interpreter started, so its fixed program failing to terminate is
    the harness's defect rather than a missing machine capability — and the
    distinction decides whether `BrowserEnvironmentSkipped` skips the whole
    real-browser surface, so the failure has to stay a test failure.

    It also has to say WHAT happened. A bare `TimeoutExpired` names the
    whole command and carries the child's output as bytes, and a child that
    stopped answering is exactly the case where its partial output is the
    only evidence there is. So the control plants a real stalling child at
    the real call site, at the site's own composed deadline — a shortened
    one would be proving a different number — and reads the line back.
    """
    from _node_launch_routing import NodeBoundExceeded
    del tmp
    node = shutil.which('node')
    assert node, 'Node is required to execute the probe control'
    stalling = ("process.stdout.write('the probe spoke before it wedged\\n');"
                " setInterval(() => {}, 1000);")
    with mock.patch.object(
            _realbrowser.shutil, 'which', _which_with(node)), \
            mock.patch.object(
                _realbrowser, 'NODE_WEBSOCKET_PROBE', stalling):
        try:
            _realbrowser.browser_requirements()
        except NodeBoundExceeded as failure:
            # The assertions live IN the handler, so a reader is shown the
            # classified failure itself rather than a variable that may or
            # may not still be unset when the block ends.
            assert failure.deadline_s == _realbrowser.NODE_PROBE_DEADLINE_S
            assert 'the probe spoke before it wedged' in failure.stdout, (
                failure.stdout)
            assert isinstance(failure.stdout, str), type(failure.stdout)
            return
    raise AssertionError('the probe that never terminates finished')


def test_a_stalled_minimal_spawn_is_the_detectors_own_failure(tmp):
    """The E2BIG probe's expiry is CLASSIFIED, and says what was diagnosed.

    This is the one site in the branch no control can watch by running a
    suite and letting a real launch fail, because it is only reached
    through a launch that failed with E2BIG. So the control reaches it
    directly, and asserts the three things the old message got wrong: the
    failure is the detector's own and not a bare `AssertionError`, it
    carries the deadline that actually fired, and it names the command the
    probe was diagnosing — because "the cause is undetermined" is false
    here, the cause is a `TimeoutExpired` with a known figure.
    """
    from _node_launch_routing import NodeBoundExceeded
    del tmp
    stalled = subprocess.TimeoutExpired(
        [sys.executable, '-c', ''], _realbrowser.MINIMAL_SPAWN_DEADLINE_S,
        output=b'partial')
    with mock.patch.object(_realbrowser.subprocess, 'run',
                           side_effect=stalled):
        try:
            _realbrowser._raise_start_failure(
                'Node WebSocket probe', '/controlled/node',
                OSError(errno.E2BIG, 'platform-dependent size refusal'))
        except NodeBoundExceeded as failure:
            assert failure.deadline_s == _realbrowser.MINIMAL_SPAWN_DEADLINE_S
            assert 'the cause is undetermined' not in str(failure), failure
            assert '/controlled/node' in str(failure), failure
            assert failure.stdout == 'partial', failure.stdout
            return
    raise AssertionError('the minimal spawn that never finishes finished')


def test_the_minimal_spawn_site_can_fire_at_all(tmp):
    """A REAL child, expired by a real deadline — the site's own figure.

    The control above plants the `TimeoutExpired` this site would raise, so
    it covers the CLASSIFICATION and the shared `_as_text` decoder — the
    one assertion there, `failure.stdout == 'partial'`, is about the
    decoder and nothing else: the real program produces no output at all.

    What that planted shape cannot show is whether the site FIRES. So the
    site's own composed deadline is shortened to 1ms and a real
    `python -c ''` is launched and genuinely outrun, and this asserts only
    what a real child can be asked about: the class it raises, the deadline
    that class reports, and the command it names. It deliberately asserts
    nothing about the child's output, because the real child writes none and
    a stand-in standing in for the site would be asserting a shape the site
    never produces.

    1ms is safe on any host and immune to a coarse clock: the control
    FAILS if the child ever finishes inside it, and the FASTEST of the 22
    samples in `MINIMAL_SPAWN_SAMPLES_S` is 0.209s — 209x the deadline — so
    a host that cannot lose that race is a host this control is reporting.
    """
    from _node_launch_routing import NodeBoundExceeded
    del tmp
    deadline = 0.001
    with mock.patch.object(_realbrowser, 'MINIMAL_SPAWN_DEADLINE_S',
                           deadline):
        try:
            # Nothing is mocked below this line: the child is a real
            # `python -c ''`, launched and killed by the real launcher.
            _realbrowser._raise_start_failure(
                'Node WebSocket probe', '/controlled/node',
                OSError(errno.E2BIG, 'controlled size refusal'))
        except NodeBoundExceeded as failure:
            assert failure.deadline_s == deadline, failure.deadline_s
            assert 'the cause is undetermined' not in str(failure), failure
            assert '/controlled/node' in str(failure), failure
            return
    raise AssertionError('the real minimal spawn finished inside 1ms')


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
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowserenvironment_')


if __name__ == '__main__':
    raise SystemExit(main())
