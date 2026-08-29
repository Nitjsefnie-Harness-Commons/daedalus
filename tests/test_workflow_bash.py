#!/usr/bin/env python3
"""Contracts for resolving and using Bash in workflow-shell tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _wfgraph  # noqa: E402


def test_workflow_bash_resolves_path_and_fails_when_missing(tmp):
    """The helper owns PATH resolution and its missing-Bash failure."""
    del tmp
    original = _util.shutil.which
    try:
        sentinel = '/controlled/Git-for-Windows/bash.exe'
        seen = []

        def resolve(name):
            seen.append(name)
            return sentinel

        _util.shutil.which = resolve
        assert _util.workflow_bash() == sentinel
        assert seen == ['bash']

        _util.shutil.which = lambda name: None
        try:
            _util.workflow_bash()
        except AssertionError as error:
            assert str(error) == 'bash is required to execute the workflow shell'
        else:
            raise AssertionError('missing Bash must fail loudly')
    finally:
        _util.shutil.which = original


def test_workflow_shell_caller_uses_shared_bash_helper(tmp):
    """The workflow graph must not bypass the shared resolver."""
    del tmp
    calls = []
    original = _util.workflow_bash
    try:
        _util.workflow_bash = lambda: calls.append(True) or sys.executable
        result = _wfgraph._run_script(
            "print('caller-used', end='')", {}, through_bash=True)
    finally:
        _util.workflow_bash = original
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'caller-used', result.stdout
    assert calls == [True], calls


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='workflowbash_')


if __name__ == '__main__':
    raise SystemExit(main())
