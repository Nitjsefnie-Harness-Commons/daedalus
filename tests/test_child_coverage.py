#!/usr/bin/env python3
"""The child_coverage declaration keeps or scrubs what it promises.

The guard that reads these declarations syntactically is pinned by
tests/test_coverage_environment.py; this suite pins what the helper does
at runtime.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


class _HidingEnvironment(dict):
    """A mapping whose iteration hides what its items() still carries."""

    def __iter__(self):
        return (name for name in dict.__iter__(self)
                if not name.startswith('COVERAGE_'))


def test_child_coverage_declares_scrub_and_keep(tmp):
    """The helper scrubs, keeps in a mapped tree, and rejects bad modes."""
    environment = {'COVERAGE_PROCESS_START': 'x', 'PATH': '/bin'}
    assert _util.child_coverage('scrub', environment) == {'PATH': '/bin'}
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
    """A keep outside the '*/tree' anchor fails where it is declared."""
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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
