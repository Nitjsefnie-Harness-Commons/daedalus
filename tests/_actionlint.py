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
import os
import re
import shutil
import subprocess
from pathlib import Path

import _util
from _repo import ROOT
from _wfgraph import _tests_yml
from _yamlsteps import complete_job_mapping

# Machinery goes here, not in tests/test_ci_workflows.py: its line ceiling is
# the size gate's to own, and a number restated in a comment goes stale.
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
_UNLAUNCHABLE = 'actionlint-unlaunchable'

# Appended to a real workflow. The finding is chosen per platform, because
# the two doors are expected to catch different kinds and a fixture that
# planted one kind everywhere would be asserting a contract the Windows door
# does not have — that decision is what the platform-scoped flag buys, and
# it is only honest if the negative case says which kind it planted.
#
# POSIX: a shellcheck finding, because the integration is on there. This is
# SC2183, the shape the job's own workflow trips on a printf.
_PLANTED_SHELLCHECK_JOB = """
  planted-lint-finding:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf "%s %s %s\\n" one two
"""

# Windows: an actionlint-native EXPRESSION finding, which the binary reports
# with the shellcheck integration off. Verified by running it, not by
# reasoning: the planted `needs.nothing` reads as
#   `property "nothing" is not defined in object type {} [expression]`
# under both `-shellcheck=` and the integrated default.
_PLANTED_EXPRESSION_JOB = """
  planted-lint-finding:
    runs-on: ubuntu-latest
    steps:
      - if: ${{ needs.nothing.outputs.x == 1 }}
        run: echo hello
"""


def host_tool_name(name):
    """The file name a tool has on THIS host, for a fixture that writes one.

    One place, because a fixture that guesses is a fixture that is wrong on
    the platform it did not run on. `shutil.which` resolves a command plus
    a PATHEXT extension on Windows and only the bare name elsewhere, so a
    fixture written as `actionlint` proves nothing on nt — and one written
    as `actionlint.exe` proves nothing on posix. Every review round on this
    branch has found a fresh copy of that mistake, so the naming lives here
    and the fixtures ask.
    """
    return f'{name}.exe' if os.name == 'nt' else name


def planted_job():
    """The planted job, in the finding this host's lint is expected to catch.

    Windows runs the lint with the shellcheck integration OFF, so a planted
    shellcheck finding is one it must not be expected to catch — that is
    precisely what the platform-scoped decision trades away, and the POSIX
    legs keep every shellcheck finding. The test asserts which kind it
    planted rather than skipping: a door that could not find a defect would
    be a silent pass, and a skip is the exact failure this branch exists to
    close.
    """
    return _PLANTED_EXPRESSION_JOB if os.name == 'nt' else \
        _PLANTED_SHELLCHECK_JOB


def planted_finding_marker():
    """The text a real finding carries on this host, for the refusal to name.

    `[expression]` on Windows and `[shellcheck]` on POSIX, so the refusal a
    test asserts on is one this platform's lint can actually produce.
    """
    return ('property "nothing" is not defined in object type'
            if os.name == 'nt' else 'SC2183')


def _planted_finding():
    """What actionlint prints for a real finding, in the shape it prints it.

    The shape actionlint's own output takes, so a caller can hand a
    fabricated verdict to the arm that decides one without a lint running.
    """
    return ('claim.yml:58:9: shellcheck reported issue in this '
            'script: SC2183:warning:1:8: This format string has 3 '
            'variables, but is passed 2 arguments [shellcheck]')


def assert_planted_finding_matches_its_door():
    """The planted job and the marker a refusal is asserted on agree.

    Driven both ways, because the two are a PAIR and a test that only ever
    builds the pair its own platform produces cannot notice the other one
    drifting. On POSIX that pair is a shellcheck job and an `SC2183`
    marker; on Windows it is an expression job and a `not defined in
    object type` marker, because that is the door that runs with the
    shellcheck integration off.
    """
    seen = {}
    original = os.name
    try:
        for name in ('nt', 'posix'):
            os.name = name
            seen[name] = (planted_job(), planted_finding_marker())
    finally:
        os.name = original
    windows_job, windows_marker = seen['nt']
    assert 'needs.nothing' in windows_job, windows_job
    assert 'printf' not in windows_job, windows_job
    assert 'not defined in object type' in windows_marker, windows_marker
    posix_job, posix_marker = seen['posix']
    assert 'printf' in posix_job, posix_job
    assert 'needs.nothing' not in posix_job, posix_job
    assert posix_marker == 'SC2183', posix_marker


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
    """A copy of a tracked workflow, carrying a finding this host can catch.

    Which finding is `planted_job`'s business; what this builds is the tree
    the real lint is then asked about, so the negative case runs a real
    binary over real bytes rather than a fabricated verdict.
    """
    root = Path(tmp) / 'tree'
    directory = root / '.github' / 'workflows'
    directory.mkdir(parents=True)
    tracked = ROOT / '.github' / 'workflows' / 'claim.yml'
    (directory / tracked.name).write_text(
        tracked.read_text(encoding='utf-8') + planted_job(), encoding='utf-8')
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
             'returncode': 0, 'output': '', 'unlaunchable': False}
    facts.update(overrides)
    return facts


def _installed_actionlint_version(binary):
    """What `actionlint --version` reports, or None when it cannot start.

    None here is NOT the absent binary the version arm describes, and the
    two are kept apart deliberately: a machine whose actionlint is broken
    must not read as a machine with no actionlint. The caller pairs this
    None with the refusal that names the difference.
    """
    try:
        ran = subprocess.run([binary, '--version'], capture_output=True,
                             text=True, timeout=_ACTIONLINT_TIMEOUT)
    except OSError:
        return None
    return ran.stdout.split('\n', 1)[0].strip()


def shellcheck_integration_flag():
    """`-shellcheck=` on Windows, and nothing anywhere else.

    A lint result is a property of the workflow FILES. Every file is
    linted with the full shellcheck integration on the POSIX legs, so the
    Windows leg re-derives a verdict two other legs have already produced
    and finds nothing new — while paying for it in the currency that made
    the leg slow. actionlint spawns one shellcheck per `run:` block and
    this repository has 78 of them, so the integration costs 78 process
    launches of a 34 MB binary on a Windows runner. Measured on the POSIX
    host over the same twelve files: 449 ms integrated, 28 ms with the
    integration off, 75 ms with no shellcheck reachable at all.

    So the integration is switched off AT THE PLACE THAT OWNS IT —
    actionlint's own documented flag, which its `-help` describes as "if
    empty, shellcheck integration will be disabled" — rather than by
    withholding the binary from PATH. Nothing is withheld from anything:
    the installer still installs and publishes shellcheck on Windows, the
    residency guard still requires it to resolve, and the POSIX legs still
    lint with it. `os.name` is the predicate because it is the one
    `shutil.which` itself branches on, so the decision and the tool
    resolution cannot disagree about which platform this is.
    """
    return ['-shellcheck='] if os.name == 'nt' else []


def lint_scope():
    """What one run on this host actually checked, in the test's words.

    A reader deserves to be told rather than to infer it, so the scope is
    a value the suite asserts on rather than a fact buried in a flag.
    """
    return ('actionlint structural checks, shellcheck integration DISABLED '
            'on this platform — every workflow file is linted with the '
            'integration on the POSIX legs'
            if os.name == 'nt' else
            'actionlint with the shellcheck integration, every run: block')


def shellcheck_integration_by_platform():
    """The flag and the scope this host would use, for both platforms.

    Driven both ways rather than only on the platform the suite happens to
    run on: a control that asserts the flag only when it IS present proves
    nothing about the doors where it must not be, and the POSIX legs are
    where every shellcheck finding comes from. `os.name` is assigned rather
    than mocked because the predicate reads it, and restored in a `finally`
    because it is process-wide.
    """
    seen = {}
    original = os.name
    try:
        for name in ('nt', 'posix'):
            os.name = name
            seen[name] = (shellcheck_integration_flag(), lint_scope())
    finally:
        os.name = original
    return seen


def assert_integration_platform_scoped():
    """The integration is off on Windows and on everywhere else.

    Both directions in one place, so a control that only ever runs where
    the flag is present cannot pass while the POSIX legs have lost theirs.
    """
    for name, (flag, scope) in shellcheck_integration_by_platform().items():
        windows = name == 'nt'
        assert flag == (['-shellcheck='] if windows else []), f'{name}: {flag}'
        assert ('DISABLED' in scope) is windows, f'{name}: {scope}'


def assert_lint_covered(run):
    """One run happened, it read files, and it checked what this host does.

    The scope is asserted rather than reported, because a run whose
    coverage differs by platform is a fact a reader has to be able to see
    and not infer from a flag.
    """
    assert run['ran'] and run['files'], run
    assert run['scope'] == lint_scope(), run


def assert_pin_read_from_the_job():
    """The pin comes from the job's env, and nowhere else.

    Two directions for the same reason `_pinned_actionlint_version` takes a
    parameter: read from the job, and equal to what the job actually pins.
    """
    job = {'env': {'ACTIONLINT_VERSION': '9.9.9'}}
    assert _pinned_actionlint_version(job) == '9.9.9'
    assert _pinned_actionlint_version() == _pin()


def assert_other_version_is_refused():
    """A binary at a version the job does not pin is not a clean lint."""
    reason = _lint_skips({'installed': '1.6.0'})
    assert 'actionlint-version' in reason and '1.6.0' in reason, reason


def assert_empty_workflow_set_is_refused():
    """No files matched, so a clean verdict would be over nothing."""
    assert 'no-workflows' in _lint_refuses({'files': []})


def assert_expansion_covers_both_extensions(tmp):
    """One file per extension, and the expansion finds both of them."""
    assert _expanded_names(tmp) == {'named.yml', 'named.yaml'}


def assert_lint_step_covers_both_extensions():
    """The actionlint step's own arguments, read out of the workflow.

    The step is the one that gates the gates, so what it is HANDED is the
    fact: a workflow in GitHub's other accepted extension would start the
    job and be skipped by it, which is the silent-stop failure the
    workflow's own header says the other gates cannot catch. Read from the
    step rather than asserted here, so the two halves — the reason and the
    mechanism — can live in the files where each belongs.
    """
    _, marker, after = _tests_yml().partition('- name: actionlint\n')
    assert marker, 'the actionlint step is not named the way this reads it'
    step, _, _ = after.partition('- name: zizmor')
    for pattern in ('.github/workflows/*.yml', '.github/workflows/*.yaml'):
        assert pattern in step, (pattern, step)
    # An extension nothing matches must not reach actionlint as a literal
    # pattern, and a directory holding no workflows at all must not read as a
    # clean lint — both would be the same silent pass in a different place.
    assert 'nullglob' in step, step
    assert 'exit 1' in step, step


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
        ran = subprocess.run([resolved, *shellcheck_integration_flag(),
                              *(str(path) for path in files)],
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


def _unlaunchable_binary(tmp):
    """A `PATH` directory holding an executable that cannot be started.

    `shutil.which` is satisfied by a file it can see and execute; a file
    whose interpreter is missing passes that check and fails at exec, with
    the same `FileNotFoundError` the twelve CI legs raised. Built rather
    than described, because a state nothing constructs is a state nothing
    pins.
    """
    directory = Path(tmp) / 'unlaunchable'
    directory.mkdir()
    bogus = directory / host_tool_name(_ACTIONLINT)
    bogus.write_text('#!/nonexistent/interpreter\n', encoding='utf-8')
    bogus.chmod(0o755)
    found = shutil.which(_ACTIONLINT, path=str(directory))
    assert found, 'the fixture did not resolve, so it proves nothing'
    return found


def _assert_caller_refuses_unlaunchable(binary):
    """`_lint_workflows` refuses a present binary that cannot start.

    A skip would be wrong twice over: the skip arms are for a tool that is
    absent, and a machine with a broken actionlint must not read as a
    machine with no actionlint. So this control fails on a skip as loudly
    as it fails on a clean verdict — the failure it is closing raised
    straight through this caller in two review rounds.
    """
    try:
        _lint_workflows(ROOT, actionlint=binary)
    except _util.Skipped as skip:
        raise AssertionError(
            'the production caller skipped a present, unlaunchable binary: '
            f'{skip}') from None
    except AssertionError as error:
        assert _UNLAUNCHABLE in str(error), error
        return
    raise AssertionError('the production caller reported a clean lint from a '
                         'binary that cannot start')


def _assert_unlaunchable_binary_is_a_refusal(tmp):
    """A binary that resolves but cannot start is refused at both depths.

    Once against the guard, and once through `_lint_workflows`, which is
    where a launch site the guard does not cover would raise. Two review
    rounds found that class by reaching only the guard, so the second
    depth is the point rather than a duplicate.
    """
    found = _unlaunchable_binary(tmp)
    assert _run_actionlint(found, _SHELLCHECK, [found]) is None
    _assert_caller_refuses_unlaunchable(found)


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
    unlaunchable = facts['unlaunchable']
    if not binary:
        _util.skip(
            f'{_BINARY_ABSENT}: actionlint is not installed; the actionlint '
            f'job pins {pinned} and a lint this machine cannot run is not a '
            'pass')
    # Ahead of the version arm on purpose: a present binary that cannot
    # start reports no version, and read as a version mismatch it would be a
    # SKIP saying actionlint is absent, which is the opposite of the truth.
    assert not unlaunchable, (
        f'{_UNLAUNCHABLE}: {binary} is on PATH and cannot be started, so no '
        'lint ran and none is reported')
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


def _lint_workflows(root, actionlint=None):
    """Resolve both binaries, lint `root`'s workflows, decide.

    `actionlint` is a parameter so a test can hand this a binary that
    resolves and cannot start. `shutil.which` cannot tell that from a
    working one, so without the parameter a launch site this function
    does not guard is unreachable from any test — which is how the second
    one in this module stayed unguarded through two review rounds.
    """
    pinned = _pinned_actionlint_version()
    binary = (shutil.which(_ACTIONLINT) if actionlint is None else actionlint)
    shellcheck = shutil.which(_SHELLCHECK)
    files = _workflow_paths(root)
    outcome = _run_actionlint(binary, shellcheck, files)
    installed = _installed_actionlint_version(binary) if binary else None
    scope = lint_scope()
    _assert_actionlint_clean({
        'binary': binary, 'shellcheck': shellcheck,
        'installed': installed,
        'unlaunchable': bool(binary) and installed is None,
        'pinned': pinned, 'files': files,
        'returncode': outcome[0] if outcome else None,
        'output': outcome[1] if outcome else ''})
    return {'scope': scope, 'ran': outcome is not None,
            'files': [str(path) for path in files]}


def assert_the_cache_release_step_is_shaped_as_declared():
    """Decoded scalars, not substrings, so a dropped env or a narrowed
    condition cannot hide behind a lookalike; the step's own comment says
    why it exists."""
    steps = (complete_job_mapping(_tests_yml(), _ACTIONLINT_JOB)
             or {})['steps']
    matches = [
        (index, step) for index, step in enumerate(steps)
        if step.get('name')
        == 'Verify the actions/cache release annotations upstream']
    assert len(matches) == 1, matches
    index, step = matches[0]
    assert step.get('run') == 'python3 scripts/ci/cache_action_releases.py', (
        step)
    assert step.get('if') == '${{ !cancelled() }}', step
    assert step.get('env') == {'GH_TOKEN': '${{ github.token }}'}, step
    zizmor = [i for i, s in enumerate(steps) if s.get('name') == 'zizmor']
    assert zizmor and index > zizmor[0], (index, zizmor)
