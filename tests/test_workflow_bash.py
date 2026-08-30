#!/usr/bin/env python3
"""Contracts for resolving and using Bash in workflow-shell tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _wfgraph  # noqa: E402


def test_workflow_bash_takes_the_first_existing_candidate(tmp):
    """The helper owns PATH resolution and its missing-Bash failure."""
    del tmp
    original = _util.bash_candidates
    try:
        first = Path(sys.executable) / 'nowhere' / 'bash'
        second = Path(sys.executable)

        def candidates(*args, **kwargs):
            return [str(first), str(second)]

        _util.bash_candidates = candidates
        assert _util.workflow_bash() == str(second)

        _util.bash_candidates = lambda *args, **kwargs: []
        try:
            _util.workflow_bash()
        except AssertionError as error:
            assert str(error) == 'bash is required to execute the workflow shell'
        else:
            raise AssertionError('missing Bash must fail loudly')
    finally:
        _util.bash_candidates = original


def test_windows_candidates_prefer_git_bash_over_the_wsl_launcher(tmp):
    """System32 ahead of Git on PATH must not resolve to the WSL launcher.

    The WSL launcher starts a Linux VM before it says anything, so every
    workflow-shell spawn paid seconds of boot: on windows-latest the suites
    went from about five minutes to more than thirteen, with no defect to
    show for it. The ordering is what the fix is; this is its pin.
    """
    del tmp
    real = {'C:\\Windows\\System32\\bash.exe',
            'C:\\Program Files\\Git\\bin\\bash.exe'}

    def exists(path):
        return path in real

    git_bash = 'C:\\Program Files\\Git\\bin\\bash.exe'
    ordered = _util.bash_candidates(
        'C:\\Windows\\System32;C:\\Program Files\\Git\\bin', windows=True,
        exists=exists, program_files='C:\\Program Files')
    assert ordered[0] == git_bash, ordered
    assert ordered.index(git_bash) < ordered.index(
        'C:\\Windows\\System32\\bash.exe'), ordered


def test_windows_candidates_rank_the_wsl_staging_launcher_last(tmp):
    """The Store-style WSL alias is a launcher too, not a Git Bash."""
    del tmp
    staging = 'C:\\Users\\dev\\AppData\\Local\\Microsoft\\WindowsApps'
    real = {staging + '\\bash.exe',
            'C:\\Program Files\\Git\\usr\\bin\\bash.exe'}

    def exists(path):
        return path in real

    ordered = _util.bash_candidates(
        staging, windows=True, exists=exists,
        program_files='C:\\Program Files',
        local_app_data='C:\\Users\\dev\\AppData\\Local')
    assert ordered[0] == 'C:\\Program Files\\Git\\usr\\bin\\bash.exe', ordered
    assert ordered[-1] == staging + '\\bash.exe', ordered


def test_windows_candidates_still_resolve_through_the_wsl_launcher(tmp):
    """A machine with only the WSL launcher still resolves.

    Degrading is the point: the suite that needed Bash then fails with its
    own signal instead of the resolver raising, which is the difference
    between a leg's real result and a resolver error.
    """
    del tmp

    def exists(path):
        return path == 'C:\\Windows\\System32\\bash.exe'

    assert _util.bash_candidates(
        'C:\\Windows\\System32', windows=True,
        exists=exists) == ['C:\\Windows\\System32\\bash.exe']


def test_non_windows_candidates_keep_path_order(tmp):
    """Away from Windows the resolution is the PATH order it always was."""
    del tmp
    present = {'/usr/bin/bash', '/bin/bash'}
    assert _util.bash_candidates(
        '/usr/bin:/bin:/usr/local/bin', windows=False,
        exists=present.__contains__) == ['/usr/bin/bash', '/bin/bash']
    assert _util.bash_candidates(
        '/usr/bin:/bin:/usr/local/bin', windows=False,
        exists={'/bin/bash'}.__contains__) == ['/bin/bash']


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
