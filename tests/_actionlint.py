"""The verdict the `actionlint` job produces, reachable from a local suite.

Not a suite itself — run_tests.py only loads `test_*.py`.

The job lints every file under .github/workflows in both extensions GitHub
accepts and fails on a nonzero exit. Nothing local ran it, so a shellcheck
or schema finding in a tracked workflow sat in the tree unremarked by every
suite a contributor could run. The step's own shape is pinned elsewhere; this
is the verdict it produces.

Three refusals keep the local run from claiming more than it checked. An
absent binary skips, and says which version the job pins: a machine that
cannot run the linter has not verified anything, and reporting that as a
pass would be the defect wearing a green badge. An installed version that
is not the pinned one skips for the same reason — another binary is another
signal, not this job's verdict. An empty file set is refused rather than
linted, because a directory holding no workflows finds nothing, which is the
same silent pass the step's own emptiness check exists to prevent.

The version is read out of the actionlint job's `env` rather than written
here, so raising the pin is one edit and the two cannot drift.

The expansion is `Path.glob` over both extensions, which has what the step's
`nullglob` buys the shell: an extension nothing matches contributes nothing
rather than a literal pattern. It is Python rather than a shell script, so a
windows-latest leg expands the same set — there a bare `bash` is the WSL
launcher, not Git's.
"""
import shutil
import subprocess

import _util
from _wfgraph import _tests_yml
from _yamlsteps import complete_job_mapping

_ACTIONLINT = 'actionlint'
_ACTIONLINT_TIMEOUT = 300
# A whole extra job, to append to a real workflow: three variables and two
# arguments is the SC2183 a `run:` block earns for free.
_PLANTED_JOB = """
  planted-lint-finding:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf "%s %s %s\\n" one two
"""


def _assert_run_refuses(runs, marker):
    """A refused run raises, and what it raises carries the finding."""
    try:
        runs()
    except AssertionError as error:
        assert marker in str(error), error
        return
    raise AssertionError(f'the lint run reported {marker!r} as clean')


def _assert_run_skips(runs, *named):
    """A run that could not be this job's verdict skips, saying why."""
    try:
        runs()
    except _util.Skipped as error:
        reason = str(error)
        missing = [name for name in named if name not in reason]
        assert not missing, (missing, reason)
        return
    raise AssertionError('a run that could not lint reported a verdict')


def _pinned_actionlint_version():
    """The version the actionlint job pins, read from the job's own env."""
    job = complete_job_mapping(_tests_yml(), _ACTIONLINT) or {}
    env = job.get('env') or {}
    assert 'ACTIONLINT_VERSION' in env, env
    return env['ACTIONLINT_VERSION']


def _workflow_paths(root):
    """Every workflow under `root`, in both extensions GitHub accepts."""
    directory = root / '.github' / 'workflows'
    return sorted({*directory.glob('*.yml'), *directory.glob('*.yaml')})


def _installed_actionlint_version(binary):
    """What `actionlint --version` reports, its first line."""
    ran = subprocess.run([binary, '--version'], capture_output=True,
                         text=True, timeout=_ACTIONLINT_TIMEOUT)
    return ran.stdout.split('\n', 1)[0].strip()


def _run_actionlint(binary, files):
    """One lint run's exit code and combined output, or None if it did not run.

    No arguments is not a run: actionlint handed none lints its working
    directory, which is a verdict about a tree nobody named.
    """
    if not binary or not files:
        return None
    ran = subprocess.run([binary, *(str(path) for path in files)],
                         capture_output=True, text=True,
                         timeout=_ACTIONLINT_TIMEOUT)
    return ran.returncode, ran.stdout + ran.stderr


def _assert_actionlint_clean(binary, installed, pinned, files, returncode,
                             output):
    """Decide what one lint run means, from the facts it produced.

    Facts rather than a running, so every skip and refusal here is code a
    test reaches without the binary rather than a branch only a machine with
    actionlint installed reaches.
    """
    if not binary:
        _util.skip(
            f'{_ACTIONLINT} is not installed; the actionlint job pins '
            f'{pinned}, and a lint this machine cannot run is not a pass')
    assert files, (
        'no workflow files matched under .github/workflows; refusing to '
        'report a clean lint over an empty set')
    if installed != pinned:
        _util.skip(
            f'{_ACTIONLINT} {installed} is installed and the actionlint job '
            f'pins {pinned}; another binary is another signal, not this '
            "job's verdict")
    # The exit code, not a score and not a finding count: a linter that
    # exits nonzero has failed whatever it thinks of the tree.
    assert returncode == 0, f'{_ACTIONLINT} exited {returncode}:\n{output}'


def _lint_workflows(root):
    """Resolve the pinned actionlint, lint `root`'s workflows, decide."""
    pinned = _pinned_actionlint_version()
    binary = shutil.which(_ACTIONLINT)
    files = _workflow_paths(root)
    outcome = _run_actionlint(binary, files)
    _assert_actionlint_clean(
        binary, _installed_actionlint_version(binary) if binary else None,
        pinned, files,
        outcome[0] if outcome else None, outcome[1] if outcome else '')
