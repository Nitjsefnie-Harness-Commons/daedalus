"""The verdict the `actionlint` job produces, reachable from a local suite.

Not a suite itself — run_tests.py only loads `test_*.py`.

The job lints every file under .github/workflows in both extensions GitHub
accepts and fails on a nonzero exit; the step's own shape is pinned in the
suite, this is the verdict it produces.

TWO BINARIES, BECAUSE actionlint REPORTS NOTHING WITHOUT shellcheck. With
shellcheck off PATH it exits 0 and prints nothing over a workflow carrying a
real finding — the guard standing in for a lint and quietly running a weaker
one. Nothing here installs or pins shellcheck, so there is no version to
match against and none is invented: its PRESENCE is required, and its absence
is a skip that names it.

FOUR REFUSALS, EACH WITH ITS OWN MARKER. Pinning the outcome of a branch is
not pinning the branch: loose substrings are answered by a neighbour's
message the moment their own arm is removed.

The expansion is `Path.glob`, which has what the step's `nullglob` buys the
shell, and is the same expansion on a windows-latest leg where a bare `bash`
is the WSL launcher.
"""
import re
import shutil
import subprocess
from pathlib import Path

import _util
from _repo import ROOT
from _wfgraph import _tests_yml
from _yamlsteps import complete_job_mapping

# tests/test_ci_workflows.py is at its 700-line ceiling; machinery goes here.
# Two names for one word today, and two facts: the binary a run resolves
# on PATH, and the job whose env carries the pin.
_ACTIONLINT = 'actionlint'
_ACTIONLINT_JOB = 'actionlint'
_SHELLCHECK = 'shellcheck'
_ACTIONLINT_TIMEOUT = 300

_BINARY_ABSENT = 'actionlint-absent'
_VERSION = 'actionlint-version'
_SHELLCHECK_ABSENT = 'shellcheck-absent'
_NO_WORKFLOWS = 'no-workflows'

# Appended to a real workflow; three variables and two arguments is its SC2183.
_PLANTED_JOB = """
  planted-lint-finding:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf "%s %s %s\\n" one two
"""


def _planted_finding():
    """What actionlint prints for a real finding, in the shape it prints it."""
    return ('claim.yml:58:9: shellcheck reported issue in this '
            'script: SC2183:warning:1:8: This format string has 3 '
            'variables, but is passed 2 arguments [shellcheck]')


def _pinned_actionlint_version(job=None):
    """The version the actionlint job pins, read from the job's own env.

    `job` is a parameter for a test to pin some other version; left out,
    this takes the route every real run takes, checked against `_pin`.
    """
    if job is None:
        job = complete_job_mapping(_tests_yml(), _ACTIONLINT_JOB) or {}
    env = job.get('env') or {}
    assert 'ACTIONLINT_VERSION' in env, env
    return env['ACTIONLINT_VERSION']


def _pin():
    """ACTIONLINT_VERSION, read out of the workflow's own bytes.

    A second reader on purpose, where `_yamlsteps` is the first: a pin
    written down rather than read has to disagree with something.
    """
    found = re.findall(r'^\s*ACTIONLINT_VERSION:\s*(.*?)\s*(?:#.*)?$',
                       _tests_yml(), re.MULTILINE)
    assert len(found) == 1, found
    return found[0].strip('\'"')


def _job_step(name):
    """One named step's `run:` text, from the actionlint job."""
    job = complete_job_mapping(_tests_yml(), _ACTIONLINT_JOB) or {}
    found = [step.get('run', '') for step in job.get('steps', [])
             if step.get('name') == name]
    assert len(found) == 1, found
    return found[0]


def _workflow_paths(root):
    """Every workflow under `root`, in both extensions GitHub accepts."""
    directory = root / '.github' / 'workflows'
    return sorted({*directory.glob('*.yml'), *directory.glob('*.yaml')})


def _expanded_names(tmp):
    """Name one file per extension under a throwaway tree, and expand it.

    The fixture lives here so the suite's lines carry the expectation and
    nothing else: both extensions, and neither literal pattern.
    """
    directory = Path(tmp) / 'tree' / '.github' / 'workflows'
    directory.mkdir(parents=True)
    for name in ('named.yml', 'named.yaml', 'named.txt'):
        (directory / name).write_text('', encoding='utf-8')
    return {path.name for path in _workflow_paths(directory.parents[1])}


def _planted_workflow_tree(tmp):
    """A copy of a tracked workflow, carrying a real shellcheck finding."""
    root = Path(tmp) / 'tree'
    directory = root / '.github' / 'workflows'
    directory.mkdir(parents=True)
    tracked = ROOT / '.github' / 'workflows' / 'claim.yml'
    (directory / tracked.name).write_text(
        tracked.read_text(encoding='utf-8') + _PLANTED_JOB, encoding='utf-8')
    return root


def _facts(overrides):
    """One lint run's facts, defaulted to a run that lints clean.

    `overrides` arrives positionally: a `**`-carrying call is a bounded
    launch to the audit in `tests/test_repo_layout.py`, whatever it calls.
    The installed version defaults to the PIN, so raising it needs no edit
    here and no fixture carries a version this branch wrote down.
    """
    pinned = _pinned_actionlint_version()
    facts = {'binary': _ACTIONLINT, 'shellcheck': _SHELLCHECK,
             'installed': pinned, 'pinned': pinned, 'files': ['workflows'],
             'returncode': 0, 'output': ''}
    facts.update(overrides)
    return facts


def _installed_actionlint_version(binary):
    """What `actionlint --version` reports, its first line."""
    ran = subprocess.run([binary, '--version'], capture_output=True,
                         text=True, timeout=_ACTIONLINT_TIMEOUT)
    return ran.stdout.split('\n', 1)[0].strip()


def _run_actionlint(binary, shellcheck, files):
    """One lint run's exit code and output, or None if it did not run.

    No arguments is not a run — actionlint handed none lints its working
    directory — and neither is a run without shellcheck, which is the whole
    of what it would check. That check is the second layer, the skip arm
    deciding first, so it fires only where the facts were wired wrongly,
    and it is what turns that wiring loud instead of clean.

    `binary` is resolved here rather than trusted, so a caller that passes
    a name instead of a path gets this same refusal rather than a
    `FileNotFoundError` out of `subprocess` — an exception a runner counts
    as a failure, which is the opposite of what every other arm here says.
    A launch that still cannot start is a refusal on the same terms.
    """
    resolved = shutil.which(binary) if binary else None
    if not resolved or not shellcheck or not files:
        return None
    try:
        ran = subprocess.run([resolved, *(str(path) for path in files)],
                             capture_output=True, text=True,
                             timeout=_ACTIONLINT_TIMEOUT)
    except OSError:
        return None
    return ran.returncode, ran.stdout + ran.stderr


def _resolved(name, marker):
    """One tool off PATH, or the refusal that stands in for it.

    A test that reaches the real binary resolves it the way production
    does. A caller that passed the bare name instead is the defect the
    two-directional proof just made visible: the guard would refuse
    correctly and the assertion would fail for a reason that has nothing
    to do with the guard.
    """
    found = shutil.which(name)
    if not found:
        _util.skip(f'{marker}: {name} is not installed, and the run-guard is '
                   'proven against the binary it resolves, not a name')
    return found


def _assert_unlaunchable_binary_is_a_refusal(tmp):
    """A binary that resolves but cannot start is a refusal, not a raise.

    `shutil.which` is satisfied by a file it can see and execute; a file
    whose interpreter is missing passes that check and fails at exec, with
    the same `FileNotFoundError` the twelve CI legs raised. Built rather
    than described, because a state nothing constructs is a state nothing
    pins.
    """
    directory = Path(tmp) / 'unlaunchable'
    directory.mkdir()
    bogus = directory / _ACTIONLINT
    bogus.write_text('#!/nonexistent/interpreter\n', encoding='utf-8')
    bogus.chmod(0o755)
    found = shutil.which(_ACTIONLINT, path=str(directory))
    assert found, 'the fixture did not resolve, so it proves nothing'
    assert _run_actionlint(found, _SHELLCHECK, [bogus]) is None


def _assert_run_guard_both_ways():
    """The run-guard's two directions, on the binaries production resolves.

    The absent half is what the guard decides first; the present half is
    what proves that absence is the shellcheck fact and not a guard that
    refuses every run. Both need the real tools, so a missing one skips
    naming itself rather than launching and being counted a failure.
    """
    binary = _resolved(_ACTIONLINT, _BINARY_ABSENT)
    shellcheck = _resolved(_SHELLCHECK, _SHELLCHECK_ABSENT)
    files = _workflow_paths(ROOT)
    assert _run_actionlint(binary, None, files) is None
    assert _run_actionlint(binary, shellcheck, files)


def _assert_actionlint_clean(facts):
    """Decide what one lint run means, from the facts it produced.

    Facts rather than a running, so every arm is a test reaches without
    either binary. One mapping rather than seven keywords, for the reason
    `_facts` takes one.
    """
    binary, shellcheck, installed = (
        facts['binary'], facts['shellcheck'], facts['installed'])
    pinned, files = facts['pinned'], facts['files']
    returncode, output = facts['returncode'], facts['output']
    if not binary:
        _util.skip(
            f'{_BINARY_ABSENT}: actionlint is not installed; the actionlint '
            f'job pins {pinned} and a lint this machine cannot run is not a '
            'pass')
    assert files, (
        f'{_NO_WORKFLOWS}: no workflow files matched under .github/workflows; '
        'refusing to report a clean lint over an empty set')
    if installed != pinned:
        _util.skip(
            f'{_VERSION}: actionlint {installed} is installed and the '
            f'actionlint job pins {pinned}; another binary is another '
            "signal, not this job's verdict")
    if not shellcheck:
        _util.skip(
            f'{_SHELLCHECK_ABSENT}: shellcheck is not installed, and '
            'actionlint reports nothing without it — every run: block goes '
            'unchecked while its exit code stays 0')
    # The exit code, not a score or a count: a nonzero exit is a red lint.
    assert returncode == 0, f'actionlint exited {returncode}:\n{output}'


def _lint_skips(overrides):
    """Drive one skip arm on its own facts, and return the reason it gave.

    Returning the reason leaves the marker to the calling test, which is
    what makes each arm's own reason the thing under test.
    """
    try:
        _assert_actionlint_clean(_facts(overrides))
    except _util.Skipped as error:
        return str(error)
    raise AssertionError(f'the run {overrides} reported a verdict')


def _lint_refuses(overrides):
    """The same for a refusal: return what the run raised, or fail."""
    try:
        _assert_actionlint_clean(_facts(overrides))
    except AssertionError as error:
        return str(error)
    raise AssertionError(f'the run {overrides} reported clean')


def _lint_workflows(root):
    """Resolve both binaries, lint `root`'s workflows, decide."""
    pinned = _pinned_actionlint_version()
    binary = shutil.which(_ACTIONLINT)
    shellcheck = shutil.which(_SHELLCHECK)
    files = _workflow_paths(root)
    outcome = _run_actionlint(binary, shellcheck, files)
    _assert_actionlint_clean({
        'binary': binary, 'shellcheck': shellcheck,
        'installed': (_installed_actionlint_version(binary)
                      if binary else None),
        'pinned': pinned, 'files': files,
        'returncode': outcome[0] if outcome else None,
        'output': outcome[1] if outcome else ''})
