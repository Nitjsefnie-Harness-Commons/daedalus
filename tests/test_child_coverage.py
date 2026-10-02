#!/usr/bin/env python3
"""The child_coverage declaration keeps or scrubs what it promises.

The guard that reads these declarations syntactically is pinned by
tests/test_coverage_environment.py; this suite pins what the helper does
at runtime.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _noderun  # noqa: E402
import _util  # noqa: E402


class _HidingEnvironment(dict):
    """A mapping whose iteration hides what its items() still carries."""

    def __iter__(self):
        return (name for name in dict.__iter__(self)
                if not name.startswith('COVERAGE_'))


def test_child_coverage_declares_scrub_and_keep(tmp):
    """The helper scrubs, keeps in a mapped tree, and rejects bad modes."""
    environment = dict(os.environ)
    environment.update({'COVERAGE_PROCESS_START': 'x', 'PATH': '/bin'})
    scrubbed = {
        name: value for name, value in environment.items()
        if not name.startswith('COVERAGE_')
    }
    assert _util.child_coverage('scrub', environment) == scrubbed
    kept = _util.child_coverage('keep', environment, cwd=Path(tmp) / 'tree')
    assert kept == environment and kept is not environment
    try:
        _util.child_coverage('maybe')
    except ValueError:
        pass
    else:
        raise AssertionError("child_coverage accepted mode 'maybe'")


def test_child_coverage_rejects_a_leaking_scrub_result(tmp):
    """Scrub mode validates the environment it is about to return.

    The second delegate leaks through `items()`, which is what subprocess
    serializes, while hiding the same names from iteration.
    """
    del tmp

    def leaking_scrub(environment):
        return dict(environment)

    for delegate in (leaking_scrub, _HidingEnvironment):
        original = _util.coverage_free_environment
        _util.coverage_free_environment = delegate
        try:
            environment = {'COVERAGE_PROCESS_START': 'must-not-leak'}
            try:
                _util.child_coverage('scrub', environment)
            except ValueError as error:
                assert 'COVERAGE_PROCESS_START' in str(error), error
            else:
                raise AssertionError(
                    f'child_coverage returned a leaking scrub: {delegate}')
        finally:
            _util.coverage_free_environment = original


def test_child_coverage_keep_requires_a_mapped_tree(tmp):
    """A keep outside a mapped tree fails where it is declared.

    Two cwds are mapped and both are accepted: the checkout itself, and
    a tree under a `tree` component [tool.coverage.paths] maps back.
    """
    for cwd in (None, Path(tmp) / 'unmapped-runner',
                Path(tmp) / 'tree' / '..' / 'unmapped-runner'):
        try:
            _util.child_coverage('keep', {}, cwd=cwd)
        except ValueError as error:
            if cwd is not None:
                assert 'unmapped-runner' in str(error), error
        else:
            raise AssertionError(f'keep accepted cwd={cwd}')


def test_child_coverage_keep_refuses_a_scrubbed_environment(tmp):
    """A keep whose environment lost the collector is not keeping it."""
    os.environ['COVERAGE_GUARD_PROBE'] = 'present'
    try:
        _util.child_coverage(
            'keep', {'PATH': '/bin'}, cwd=Path(tmp) / 'tree')
    except ValueError as error:
        assert 'COVERAGE_GUARD_PROBE' in str(error), error
    else:
        raise AssertionError('keep accepted a scrubbed environment')
    finally:
        del os.environ['COVERAGE_GUARD_PROBE']


def test_child_coverage_scrubs_a_real_child(tmp):
    """The declared scrub removes every COVERAGE_* name from a child."""
    probe = Path(tmp) / 'coverage-env-probe.py'
    probe.write_text(
        'import json, os\n'
        'print(json.dumps(sorted(name for name in os.environ\n'
        "                           if name.startswith('COVERAGE_'))))\n",
        encoding='utf-8')
    parent = dict(os.environ)
    parent.update({
        'COVERAGE_PROCESS_START': 'synthetic-config',
        'COVERAGE_CONTEXT': 'coverage-environment-test',
    })
    result = subprocess.run(
        [sys.executable, str(probe)], cwd=tmp,
        env=_util.child_coverage('scrub', parent),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == '[]\n', result.stdout


def _node():
    """The node a real child runs under, or a named failure."""
    node = shutil.which('node')
    assert node, 'node is required to execute the harness'
    return node


def test_an_environment_the_caller_built_reaches_the_child(tmp):
    """`environment` is threaded, and a value only the caller holds arrives.

    `test_js_coverage.py` points `NODE_V8_COVERAGE` at a dumps directory it
    builds per test, so the child must be handed THAT environment rather
    than this process's. A launcher that accepted the parameter and then
    read `os.environ` would satisfy every signature-shaped check here and
    send the child to the wrong directory, so the assertion is on what the
    child actually saw.

    The other half is the same property read from the default: with no
    environment passed, the child gets THIS PROCESS's, so a value planted
    in `os.environ` rather than in the caller's dict reaches it too — and
    the two cannot agree by accident, because the last launch uses a third
    value and asserts that one instead.
    """
    source = "process.stdout.write(process.env.NODE_V8_COVERAGE || 'none');"
    caller_only = str(Path(tmp) / 'caller-only-dumps')
    environment = dict(os.environ)
    environment['NODE_V8_COVERAGE'] = caller_only
    result = _noderun.run_node_argv(
        _node(), ['-e', source], tmp,
        environment=_util.child_coverage('scrub', environment))
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == caller_only, result.stdout
    # And the same child launched with NO environment sees this process's,
    # which is what shows the assertion above is the parameter doing the
    # work rather than the value simply being in the air.
    os.environ['NODE_V8_COVERAGE'] = caller_only
    try:
        without = _noderun.run_node_argv(_node(), ['-e', source], tmp)
    finally:
        del os.environ['NODE_V8_COVERAGE']
    assert without.returncode == 0, (without.returncode, without.stderr)
    assert without.stdout == caller_only, (
        'a launch with no environment did not fall back to this process\'s')
    # A different value entirely, so the two halves cannot agree by luck.
    other = str(Path(tmp) / 'other-dumps')
    second = dict(os.environ)
    second['NODE_V8_COVERAGE'] = other
    third = _noderun.run_node_argv(
        _node(), ['-e', source], tmp,
        environment=_util.child_coverage('scrub', second))
    assert third.stdout == other, third.stdout
    # And the scrub runs over the CALLER's dict rather than over this
    # process's, which is the other half of what the docstring at
    # `tests/_noderun.py:260` claims. A coverage name planted in the
    # environment the caller supplies is stripped before the launch, and an
    # unrelated marker in the same dict still reaches the child — so this
    # is a scrub and not a wholesale replacement. A launcher that read
    # `os.environ` where the caller passed a dict would show 'none' for the
    # marker too, and that is the whole difference between the two.
    scrub_source = ("process.stdout.write(String("
                    "process.env.COVERAGE_PROCESS_START || 'none') + '|' + "
                    "String(process.env.DAEDALUS_ENV_MARKER || 'none'));")
    scrubbed = _noderun.run_node_argv(
        _node(), ['-e', scrub_source], tmp,
        environment=_util.child_coverage('scrub', {
            'PATH': os.environ.get('PATH', ''),
            'COVERAGE_PROCESS_START': str(Path(tmp) / 'dumps'),
            'DAEDALUS_ENV_MARKER': 'reached'}))
    assert scrubbed.returncode == 0, (scrubbed.returncode, scrubbed.stderr)
    assert scrubbed.stdout == 'none|reached', scrubbed.stdout
    # That launch handed the child PATH and nothing else, and the caller's
    # environment REPLACES the child's — so every name it did not copy was
    # removed as surely as a scrub removed one. `SystemRoot` is where Node
    # loads its CSPRNG provider, so on Windows that child aborts in
    # `ncrypto::CSPRNG` during `InitializeOncePerProcess`: an assertion about
    # entropy that is really about the environment. POSIX has no such floor,
    # which is why it is invisible on Linux and fatal on every
    # `windows-latest` leg.
    from unittest import mock  # noqa: E402
    hand_built = {'PATH': os.environ.get('PATH', '')}
    with mock.patch.object(sys, 'platform', 'win32'), \
            mock.patch.dict(os.environ, {'SYSTEMROOT': 'C:\\Windows'}):
        child = _util.child_coverage('scrub', dict(hand_built))
    assert child.get('SYSTEMROOT') == 'C:\\Windows', (
        'a Windows child was launched without the name its CSPRNG provider '
        'lives under')
    # Forced, not inherited: asserting the POSIX arm by RUNNING it pins the
    # host, and this control read 6/6 here and failed on every runner. The
    # platform this suite runs on is not the property under test.
    with mock.patch.object(sys, 'platform', 'linux'):
        posix = _util.child_coverage('scrub', dict(hand_built))
    assert 'SYSTEMROOT' not in posix, (
        'the platform boot floor was applied off Windows')


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
