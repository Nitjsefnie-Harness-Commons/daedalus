"""The verdict the `actionlint` job produces, reachable from a local suite.

Not a suite itself — run_tests.py only loads `test_*.py`.

The job lints every file under .github/workflows in both extensions GitHub
accepts and fails on a nonzero exit. Nothing local ran it, so a shellcheck
or schema finding in a tracked workflow sat in the tree unremarked by every
suite a contributor could run. The step's own shape is pinned elsewhere; this
is the verdict it produces.

TWO BINARIES, BECAUSE actionlint REPORTS NOTHING WITHOUT shellcheck.
actionlint shells out to shellcheck for every `run:` block, and with
shellcheck off PATH it exits 0 and prints nothing over a workflow carrying a
real finding — the guard standing in for a lint and quietly running a weaker
one. The repository's own comment calls that block "where most real bugs
live". Nothing here installs or pins shellcheck, so there is no version to
match against and none is invented: its PRESENCE is required, and its absence
is a skip that names it.

FOUR REFUSALS, EACH WITH ITS OWN MARKER. Pinning the outcome of a branch is
not pinning the branch: a test asserting loose substrings is answered by a
neighbour's message the moment its own arm is removed, and the arm it meant to
prove can go missing behind a green run. Each reason therefore carries a
marker no other arm can produce, and each test pins its own.

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
from pathlib import Path

import _util
from _repo import ROOT
from _wfgraph import _tests_yml
from _yamlsteps import complete_job_mapping

# Two names for one word today, and two facts: the binary a local run
# resolves on PATH, and the job whose env carries the pin. Bound to one
# constant they could only ever move together.
_ACTIONLINT = 'actionlint'
_ACTIONLINT_JOB = 'actionlint'
_SHELLCHECK = 'shellcheck'
_ACTIONLINT_TIMEOUT = 300

_BINARY_ABSENT = 'actionlint-absent'
_VERSION = 'actionlint-version'
_SHELLCHECK_ABSENT = 'shellcheck-absent'
_NO_WORKFLOWS = 'no-workflows'

# What actionlint prints for a real finding, in the shape it prints it.
_PLANTED_FINDING = ('claim.yml:58:9: shellcheck reported issue in this '
                    'script: SC2183:warning:1:8: This format string has 3 '
                    'variables, but is passed 2 arguments [shellcheck]')
# A whole extra job, to append to a real workflow: three variables and two
# arguments is the SC2183 a `run:` block earns for free.
_PLANTED_JOB = """
  planted-lint-finding:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf "%s %s %s\\n" one two
"""


def _pinned_actionlint_version(job=None):
    """The version the actionlint job pins, read from the job's own env.

    `job` is a parameter so a test can hand this a mapping pinning some
    other version and see that the value comes out of it. Nothing here
    knows what the pin is.
    """
    if job is None:
        job = complete_job_mapping(_tests_yml(), _ACTIONLINT_JOB) or {}
    env = job.get('env') or {}
    assert 'ACTIONLINT_VERSION' in env, env
    return env['ACTIONLINT_VERSION']


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

    The fixture is here rather than in the suite so the suite's own lines
    carry the expectation and nothing else. The expectation is both
    extensions and neither literal pattern: an extension nothing matches
    contributes nothing, which is what the step's `nullglob` buys in the
    shell, and a `.txt` in the directory is not a workflow.
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

    `overrides` is a mapping a caller passes positionally rather than
    unpacked into this call: a `**`-carrying call is a bounded launch to
    the audit in `tests/test_repo_layout.py`, whatever it calls.

    The installed version defaults to the PIN read from the workflow, so
    raising the pin in `.github/workflows/tests.yml` needs no edit here and
    no fixture carries a version this branch wrote down.
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

    No arguments is not a run: actionlint handed none lints its working
    directory, which is a verdict about a tree nobody named. Nor is a run
    without shellcheck a run: that is the whole of what it would check.
    """
    if not binary or not shellcheck or not files:
        return None
    ran = subprocess.run([binary, *(str(path) for path in files)],
                         capture_output=True, text=True,
                         timeout=_ACTIONLINT_TIMEOUT)
    return ran.returncode, ran.stdout + ran.stderr


def _assert_actionlint_clean(facts):
    """Decide what one lint run means, from the facts it produced.

    Facts rather than a running, so every arm here is a test reaches
    without either binary rather than a branch only a machine with this
    toolchain installed reaches. One mapping rather than seven keywords, for
    the same reason `_facts` takes one: no call site in either direction
    unpacks a mapping into a call.
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
    # The exit code, not a score and not a finding count: a linter that
    # exits nonzero has failed whatever it thinks of the tree.
    assert returncode == 0, f'actionlint exited {returncode}:\n{output}'


def _lint_skips(overrides):
    """Drive one skip arm on its own facts, and return the reason it gave.

    Returning the reason rather than asserting on it leaves the marker to
    the calling test, which is what makes each arm's own reason the thing
    under test. An arm that did not skip has reported a verdict, and that is
    the refusal.
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
