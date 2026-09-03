#!/usr/bin/env python3
"""Execute CI invariants that GitHub otherwise fails silently.

These tests parse workflows and execute /claim instead of trusting source
inspection.
"""
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import (_job_condition_runs, _job_if_expression,  # noqa: E402
                      _job_names, _job_section, _tests_yml)
from _ghexpr import evaluate_if  # noqa: E402
from _wfskip import implicit_skip_violations  # noqa: E402
from _wfskip_cases import suites_skip_violation  # noqa: E402
from _yamlread import job_mapping, step_scalar  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _workflows import _trigger_names  # noqa: E402


def _assert_diff_coverage_permissions(workflow):
    permissions = job_mapping(workflow, 'diff-coverage', 'permissions')
    assert permissions == {'contents': 'read'}, (
        f'unsafe decoded permissions: {permissions!r}')


def _assert_permissions_mutation_refused(workflow):
    try:
        _assert_diff_coverage_permissions(workflow)
    except AssertionError as error:
        assert 'unsafe decoded permissions' in str(error), str(error)
        return
    raise AssertionError('widened decoded permissions were accepted')


def test_diff_coverage_permissions_are_exactly_read_only(tmp):
    del tmp
    _assert_diff_coverage_permissions(_tests_yml())


def test_diff_coverage_runs_when_its_coverage_artifact_is_available(tmp):
    del tmp
    source = _tests_yml()
    cases = (
        {'name': 'all-success', 'event': 'pull_request', 'result': 'success',
         'success': True, 'expected': True},
        {'name': 'skipped ancestor', 'event': 'pull_request',
         'result': 'success', 'expected': True},
        {'name': 'cancelled PR', 'event': 'pull_request', 'result': 'success',
         'cancelled': True, 'expected': False},
        {'name': 'coverage skipped', 'event': 'pull_request',
         'result': 'skipped', 'expected': False},
        {'name': 'coverage failed', 'event': 'pull_request',
         'result': 'failure', 'failure': True, 'expected': False},
    )
    cases += tuple({'name': f'event {event}', 'event': event,
                    'result': 'success', 'success': True,
                    'expected': event == 'pull_request'}
                   for event in _trigger_names(source))
    for case in cases:
        status = {name: case.get(name, False)
                  for name in ('success', 'failure', 'cancelled')}
        context = {'github': {'event_name': case['event']},
                   'needs': {'coverage': {'result': case['result']}},
                   'status': status}
        expression = _job_if_expression(source, 'diff-coverage')
        actual = _job_condition_runs(source, 'diff-coverage', context=context)
        assert actual is case['expected'], {
            'case': case['name'], 'condition': expression,
            'expected': case['expected'], 'actual': actual,
            'status': status}


def test_skip_guard_uses_condition_behaviour_not_function_names(tmp):
    del tmp
    cases = {
        "github.event_name == 'pull_request' && 'cancelled()' != ''": True,
        'cancelled()': False,
        'failure()': False,
        'failure() || cancelled()': False,
        '!success()': False,
        "github.event_name == 'push'": False,
        None: True,
    }
    actual = {condition: suites_skip_violation(_tests_yml(), condition)
              for condition in cases}
    assert actual == cases, {'expected': cases, 'actual': actual}


def test_skippable_ancestors_cannot_implicitly_skip_dependants(tmp):
    del tmp
    violations = implicit_skip_violations(_tests_yml())
    assert not violations, '\n'.join(violations)


def test_import_resolving_jobs_install_the_pinned_statement_analyzer(tmp):
    del tmp
    pins = re.findall(r'^coverage==.*$',
                      (ROOT / 'requirements-test.txt').read_text(
                          encoding='utf-8'), re.MULTILINE)
    assert len(pins) == 1, pins
    workflow_dir = ROOT / '.github' / 'workflows'
    tests = _tests_yml()
    release = (workflow_dir / 'release.yml').read_text(encoding='utf-8')

    def before(source, job, consumer):
        section = source.partition(f'\n  {job}:\n')[2].partition(consumer)
        return section[0] if section[1] else ''
    measurement_jobs = []
    for name in _job_names(tests):
        steps = complete_job_mapping(tests, name).get('steps', [])
        consumers = [index for index, step in enumerate(steps)
                     if 'coverage_suites.py' in step.get('run', '')]
        if consumers:
            assert len(consumers) == 1, (name, consumers)
            prefix = '\n'.join(
                step.get('run', '') for step in steps[:consumers[0]])
            measurement_jobs.append((name, prefix))
    assert len(measurement_jobs) == 1, measurement_jobs
    jobs = (
        ('diff-coverage', before(tests, 'diff-coverage',
                                 '- name: Measure the coverage')),
        measurement_jobs[0],
        ('suites', before(tests, 'suites', '- name: Run every suite')),
        ('pylint', before(tests, 'pylint', '- name: pylint')),
        ('release', before(release, 'publish',
                           '- name: Run every suite before publishing')),
    )
    install = re.compile(
        r'pip install (?:-r|--requirement) requirements-test[.]txt')
    missing = [name for name, job in jobs if not install.search(job)]
    assert not missing, (
        'jobs that import diff_coverage do not install coverage.py from '
        f'requirements-test.txt: {", ".join(missing)}')
    for name, job in jobs:
        assert 'coverage==' not in job, f'{name} duplicated the version pin'


def test_the_speed_venvs_install_the_test_requirements(tmp):
    """The timed cells' virtualenvs can run every suite the cell selects.

    The coverage suites import `coverage`, which only requirements-test.txt
    carries, and a venv built without it empties those suites rather than
    failing them -- the shape the comparator's zero-suite guard exists for.
    """
    del tmp
    workflow = _tests_yml()
    _, marker, after = workflow.partition(
        '- name: Build one virtualenv per side\n')
    assert marker, 'the venv step is not named the way this test finds it'
    step, _, _ = after.partition('- name:')
    assert '-r "./${side}/requirements-test.txt"' in step, step


def test_permission_whitespace_mutation_is_refused(tmp):
    del tmp
    workflow = _tests_yml()
    mutated = workflow.replace(
        '      contents: read\n',
        '      contents: read\n      pull-requests : write\n', 1)
    assert mutated != workflow, 'real permission mapping was not mutated'
    _assert_permissions_mutation_refused(mutated)


def test_quoted_and_escaped_permission_keys_are_refused(tmp):
    del tmp
    workflow = _tests_yml()
    additions = (
        "      'pull-requests': write\n",
        '      "pull\\x2drequests": write\n',
    )
    for addition in additions:
        mutated = workflow.replace(
            '      contents: read\n',
            '      contents: read\n' + addition, 1)
        assert mutated != workflow, addition
        _assert_permissions_mutation_refused(mutated)


def test_quoted_and_escaped_permissions_fields_are_refused(tmp):
    del tmp
    workflow = _tests_yml()
    replacements = (
        "    'permissions':\n",
        '    "permis\\x73ions":\n',
    )
    for field in replacements:
        mutated = workflow.replace(
            '    permissions:\n      contents: read\n',
            field + '      contents: read\n'
            '      pull-requests: write\n', 1)
        assert mutated != workflow, field
        _assert_permissions_mutation_refused(mutated)


def test_permission_values_and_unknown_keys_fail_closed(tmp):
    del tmp
    workflow = _tests_yml()
    mutations = (
        workflow.replace('      contents: read\n',
                         '      contents: write\n', 1),
        workflow.replace('      contents: read\n',
                         '      contents: "wr\\x69te"\n', 1),
        workflow.replace(
            '      contents: read\n',
            '      contents: read\n      future-scope: read\n', 1),
    )
    for mutated in mutations:
        assert mutated != workflow, 'real permission mapping was not mutated'
        _assert_permissions_mutation_refused(mutated)


def test_actionlint_lints_every_workflow_extension_github_accepts(tmp):
    """The gate on the gates must not skip a workflow it triggered on.

    The job fires on every file under .github/workflows and zizmor scans the
    whole directory, but actionlint was handed `.github/workflows/*.yml`
    alone. A workflow using GitHub's other accepted extension would therefore
    start this gate and be skipped by it — the silent-stop failure mode the
    workflow's own header says the other gates cannot catch.
    """
    del tmp
    workflow = _tests_yml()
    _, marker, after = workflow.partition('- name: actionlint\n')
    assert marker, 'the actionlint step is not named the way this test finds it'
    step, _, _ = after.partition('- name: zizmor')
    for pattern in ('.github/workflows/*.yml', '.github/workflows/*.yaml'):
        assert pattern in step, (pattern, step)
    # An extension nothing matches must not reach actionlint as a literal
    # pattern, and a directory holding no workflows at all must not read as a
    # clean lint — both would be the same silent pass in a different place.
    assert 'nullglob' in step, step
    assert 'exit 1' in step, step


def test_the_audit_covers_every_python_dependency_surface(tmp):
    """pip-audit is handed each requirements file and every declared extra.

    The published wheel declares no dependencies, so `pip-audit .` over this
    project collects zero packages — an audit that can never fire. What the
    repository actually depends on is spread across the requirements files and
    the extras table, and a surface added to either without being added here
    would leave the gate green while going unchecked.
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'audit.yml').read_text(
        encoding='utf-8')
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', 'requirements*.txt'],
        capture_output=True, check=True, timeout=30)
    requirement_files = [
        os.fsdecode(path) for path in listed.stdout.split(b'\0') if path]
    assert requirement_files, 'no requirements file is tracked'
    for name in requirement_files:
        assert f'--requirement {name}' in workflow, name

    # The extras are read out of pyproject.toml rather than listed, so a
    # second extra cannot escape the audit by nobody remembering it here.
    assert "['optional-dependencies'].values()" in workflow, workflow
    generated = re.search(r'> (\S+-requirements\.txt)', workflow)
    assert generated, 'the workflow generates no extras file'
    assert f'--requirement {generated.group(1)}' in workflow, generated.group(1)
    # An empty generated file narrows the gate in silence: pip-audit accepts
    # it, the other surfaces still report clean, and the only third-party code
    # that runs in production goes unaudited.
    assert f'! -s {generated.group(1)}' in workflow, workflow


def _pinned_actions():
    used = {}
    pattern = re.compile(
        r'uses:\s*(?:>-\s*)?([\w.-]+/[\w./-]+)@([0-9a-f]{40})')
    for path in sorted((ROOT / '.github' / 'workflows').glob('*.yml')):
        for action, sha in pattern.findall(path.read_text(encoding='utf-8')):
            used.setdefault(action, {}).setdefault(sha, []).append(path.name)
    return used


def test_a_release_waits_for_the_gates_on_its_own_commit(tmp):
    """Publication reads the other gates instead of racing them.

    v0.19.0 went public two seconds before `tests` concluded and nine minutes
    before `speed` did: the tag started every workflow independently and the
    release never looked at any of them.

    The property that makes the wait a gate rather than a pause is that ZERO
    runs is a failure. "Nothing is pending" is true of a commit whose gates
    never ran, so a wait that only counted pending work would pass instantly
    on exactly the tag that deserved to be stopped.
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'release.yml').read_text(
        encoding='utf-8')
    _, marker, after = workflow.partition('- name: Wait for the gates')
    assert marker, 'the release does not wait for anything'
    step, _, _ = after.partition('- uses: actions/checkout')
    assert 'head_sha=$SHA' in step, step
    # Itself excluded, or the wait waits for its own run to finish.
    assert 'select(.name != "release")' in step, step
    assert '"$total" -eq 0' in step and 'exit 1' in step, step

    # The wait has to come before the expensive half and before anything is
    # published, or it is a report rather than a gate.
    order = [workflow.index('- name: Wait for the gates'),
             workflow.index('run: python run_tests.py'),
             workflow.index('softprops/action-gh-release')]
    assert order == sorted(order), order

    # A ceiling shorter than the wait would kill the job for being patient.
    ceiling = int(re.search(r'timeout-minutes: (\d+)', workflow).group(1))
    waited = int(re.search(r'deadline=\$\(\( \$\(date \+%s\) \+ (\d+) \* 60',
                           workflow).group(1))
    assert ceiling > waited, (ceiling, waited)


def test_a_release_attests_every_artifact_it_publishes(tmp):
    """SHA256SUMS says the files go together; provenance says where from.

    The checksum file is published by the same authority as the artifacts, so
    anything able to replace one could replace both. A build attestation is a
    signed statement naming the workflow, the commit and the runner, checkable
    against GitHub rather than against this repository's own word — and it is
    worth nothing if it covers fewer files than the release ships.
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'release.yml').read_text(
        encoding='utf-8')
    assert 'id-token: write' in workflow, workflow
    assert 'attestations: write' in workflow, workflow

    _, marker, after = workflow.partition(
        'uses: actions/attest-build-provenance@')
    assert marker, 'the release publishes no build provenance'
    attested, _, rest = after.partition('- name:')
    subjects = set(re.findall(r'dist/\S+', attested))
    published = set(re.findall(r'dist/\S+', rest))
    # SHA256SUMS describes the artifacts rather than being one, so it is the
    # single published path that is deliberately not a subject.
    assert published - subjects == {'dist/SHA256SUMS'}, (subjects, published)

    # The refusal has to come before the suite run and the build, or a rerun
    # spends twenty minutes to be told the release already exists.
    order = [workflow.index('already carries artifacts'),
             workflow.index('actions/checkout@'),
             workflow.index('run: python run_tests.py')]
    assert order == sorted(order), order


def test_the_wheel_job_proves_both_published_formats(tmp):
    """The sdist ships as unproven as the wheel is proven.

    release.yml checksums, attests and uploads dist/*.tar.gz, and the wheel
    job built that wheel alone — nothing installed the sdist, and nothing ran
    twine over either artifact. A tarball missing a file it needed would have
    gone public green, because a checksum and an attestation describe what was
    built, never whether it builds. There is no MANIFEST.in, so a clean
    install of the tarball is also the only thing that reads the file list
    setuptools inferred.
    """
    del tmp
    workflow = _tests_yml()

    build = step_scalar(workflow, 'wheel', 'Build the wheel and the sdist',
                        'run')
    assert build, 'the wheel job no longer builds anything under that name'
    # One build step produces both formats; the wheel-only flag is the exact
    # defect this job exists to keep out.
    assert 'python -m build\n' in build, build
    assert '--wheel' not in build, build
    # twine is a CI tool like any other here: pinned exactly, never floated.
    install = 'python -m pip install --upgrade pip build twine==7.0.0'
    assert install in build.splitlines(), build

    check = step_scalar(workflow, 'wheel', 'Check both artifacts render',
                        'run')
    assert check is not None, 'nothing renders the metadata of the artifacts'
    # Exact: a narrowed glob checks one format and `|| true` checks nothing.
    assert check == 'python -m twine check --strict dist/*', check

    wheel_smoke = step_scalar(workflow, 'wheel',
                              'Install it with no checkout in reach and run'
                              ' its entry point', 'run')
    assert wheel_smoke is not None, 'the wheel is installed by nothing'
    # The wheel alone: a tarball in this venv would prove the sdist twice
    # and the wheel not at all.
    assert wheel_smoke.splitlines() == [
        'python -m venv "$RUNNER_TEMP/probe"',
        '"$RUNNER_TEMP/probe/bin/pip" install dist/*.whl',
        'cd "$RUNNER_TEMP"',
        '"$RUNNER_TEMP/probe/bin/daedalus" --version',
    ], wheel_smoke

    smoke = step_scalar(workflow, 'wheel',
                        'Install the sdist, no checkout in reach, and run'
                        ' its entry point', 'run')
    assert smoke is not None, 'the sdist is installed by nothing'
    # The tarball alone: installing it beside the wheel would prove the wheel
    # a second time and leave the sdist's file list as untested as before.
    assert smoke.splitlines() == [
        'python -m venv "$RUNNER_TEMP/probe-sdist"',
        '"$RUNNER_TEMP/probe-sdist/bin/pip" install dist/*.tar.gz',
        'cd "$RUNNER_TEMP"',
        '"$RUNNER_TEMP/probe-sdist/bin/daedalus" --version',
    ], smoke

    # The job's whole step list in order: an added, removed or moved step is
    # a red event, and metadata still renders before either install.
    steps = complete_job_mapping(workflow, 'wheel')['steps']
    identities = [step.get('name') or step.get('uses', '').partition('@')[0]
                  for step in steps]
    assert identities == [
        'actions/checkout',
        'actions/setup-python',
        'Build the wheel and the sdist',
        'Check both artifacts render',
        'Install it with no checkout in reach and run its entry point',
        'Install the sdist, no checkout in reach, and run its entry point',
        'actions/upload-artifact',
    ], identities

    # A failed smoke test is the artifact worth keeping, for either format.
    # The window is the wheel job alone: past the job, a later preamble may
    # name dist/ paths of its own that are not this artifact's.
    wheel_job = '\n'.join(_job_section(workflow, 'wheel'))
    before, marker, after = wheel_job.partition(
        'name: artifacts-failed-smoke-test')
    assert marker, 'the failed-smoke artifact no longer carries both formats'
    assert 'if: ${{ failure() }}' in before, (
        'the failed-smoke artifact uploads on green runs')
    artifact, _, _ = after.partition('- name:')
    kept = [line.strip() for line in artifact.splitlines()]
    assert 'retention-days: 14' in kept, artifact
    assert re.findall(r'dist/\S+', artifact) == ['dist/*.whl',
                                                 'dist/*.tar.gz'], artifact


def test_one_action_family_is_pinned_to_one_version(tmp):
    """Two `uses:` lines from the same action must name the same commit.
    CodeQL refuses to run when `init` and `analyze` name different versions,
    and Dependabot treats them as two dependencies — so a bump arrived as two
    pull requests, each of which could only be red, and merging either one
    would have left main red until the other landed. The rule is wider than
    CodeQL: an action split across sub-paths is one component, whatever its
    package manager thinks.
    """
    del tmp
    families = {}
    for action, by_sha in _pinned_actions().items():
        owner, _, rest = action.partition('/')
        repo = rest.partition('/')[0]
        for sha, workflows in by_sha.items():
            families.setdefault(f'{owner}/{repo}', {}).setdefault(
                sha, []).extend(f'{name}:{action}' for name in workflows)
    assert families, 'no hash-pinned action found; has the pin convention moved?'
    for family, by_sha in sorted(families.items()):
        assert len(by_sha) == 1, (
            f'{family} is pinned to {len(by_sha)} different commits: '
            + '; '.join(f'{sha[:12]} in {sorted(set(where))}'
                        for sha, where in sorted(by_sha.items())))


def test_dependabot_groups_an_action_used_under_more_than_one_path(tmp):
    """A component Dependabot sees as several dependencies moves as one.
    Grouping is what makes the proposal a state CI can pass: ungrouped, each
    half of `github/codeql-action` arrives alone and neither can be green.
    """
    del tmp
    config = (ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8')
    patterns = re.findall(r'^\s*-\s*"([^"]+)"\s*$', config, re.MULTILINE)
    families = {}
    for action in _pinned_actions():
        owner, _, rest = action.partition('/')
        repo = rest.partition('/')[0]
        families.setdefault(f'{owner}/{repo}', set()).add(action)
    split = {family for family, paths in families.items() if len(paths) > 1}
    assert split, 'no action is used under more than one path any more'
    for family in sorted(split):
        assert any(fnmatch.fnmatch(family + '/x', pattern)
                   or fnmatch.fnmatch(family, pattern)
                   for pattern in patterns), (
            f'{family} is used under several paths, so Dependabot will open '
            f'one pull request per path; no group in dependabot.yml covers it')


def test_dependabot_watches_every_manifest_kind_the_repo_tracks(tmp):
    """Each dependency manifest in the tree has an ecosystem watching it.
    Dependabot covered `github-actions` alone while the Python pins — the mcp
    extra, the lint pins and the coverage pin — were frozen indefinitely. The
    check is keyed off what is tracked rather than off a remembered list, so a
    manifest of a new kind fails here instead of ageing unwatched.
    """
    del tmp
    config = (ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8')
    ecosystems = {
        'pyproject.toml': 'pip',
        'requirements-dev.txt': 'pip',
        'requirements-test.txt': 'pip',
        'package.json': 'npm',
        'Gemfile': 'bundler',
        'go.mod': 'gomod',
        'Cargo.toml': 'cargo',
        'Dockerfile': 'docker',
    }
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True,
        check=True, timeout=30)
    tracked = {os.fsdecode(path) for path in listed.stdout.split(b'\0') if path}
    required = {'github-actions'} if any(
        name.startswith('.github/workflows/') for name in tracked) else set()
    for name in tracked:
        ecosystem = ecosystems.get(os.path.basename(name))
        if ecosystem:
            required.add(ecosystem)
    assert required, 'the repository tracks no dependency manifest at all'
    for ecosystem in sorted(required):
        assert f'package-ecosystem: {ecosystem}' in config, ecosystem


_CACHE_JOBS = (
    # (job, the key's python component, the step a save must follow, the
    # step the restore must precede). `coverage` fixes its interpreter, the
    # other two read it from the matrix.
    ('suites', '${{ matrix.python }}', 'Run every suite',
     'Install the test dependencies and project'),
    ('coverage-matrix', '${{ matrix.python }}', 'Measure',
     'Install the coverage toolchain and the project'),
    ('coverage', '3.13', 'Install the coverage toolchain and the project',
     'Install the coverage toolchain and the project'),
)

# Every platform pip cache directory, so one spelling warms all three OSes;
# actions/cache ignores the two that do not exist on the running platform.
_PIP_CACHE_PATHS = (
    '~/.cache/pip', '~/Library/Caches/pip', '~\\AppData\\Local\\pip\\Cache')


def _cache_step(steps, action):
    """Return (index, step) for the job's one actions/cache/<action> step."""
    matches = [(index, step) for index, step in enumerate(steps)
               if step.get('uses', '').startswith(f'actions/cache/{action}@')]
    assert len(matches) == 1, f'expected one cache/{action} step: {matches}'
    return matches[0]


def _named_step_index(steps, name):
    """Return the index of the job's one step carrying this name."""
    matches = [index for index, step in enumerate(steps)
               if step.get('name') == name]
    assert len(matches) == 1, f'expected one {name!r} step: {matches}'
    return matches[0]


def test_the_matrix_jobs_declare_no_pip_cache_on_setup_python(tmp):
    """`cache: pip` saves in a post-job step that runs after untrusted code.

    That post step is the cache-poisoning shape issue #166 took out of the
    speed cells: on a pull_request run it writes a cache a later main run
    restores. These three jobs install the project and then run it, so they
    take the cache as two separate steps instead, and the event gate lives
    on the save half alone.
    """
    del tmp
    workflow = _tests_yml()
    for job, _python, _save_after, _install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        setup = [step for step in steps
                 if step.get('uses', '').startswith('actions/setup-python')]
        assert len(setup) == 1, (job, setup)
        declared = {'cache', 'cache-dependency-path'} & set(
            setup[0].get('with', {}))
        assert declared == set(), (job, sorted(declared))


def test_the_matrix_jobs_restore_the_pip_cache_before_they_install(tmp):
    """Restore is safe on every event: a pull request reads what main wrote.

    The key names the platform, the interpreter and the requirements hash,
    so a cell restores only a cache a same-platform cell of the same
    dependency set wrote, and the three jobs — which install the same
    dependency set — share one namespace rather than three.
    """
    del tmp
    workflow = _tests_yml()
    for job, python, _save_after, install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        restore_index, restore = _cache_step(steps, 'restore')
        assert re.fullmatch(r'actions/cache/restore@[0-9a-f]{40}',
                            restore['uses']), restore['uses']
        assert set(restore['with']['path'].splitlines()) == set(
            _PIP_CACHE_PATHS), (job, restore['with']['path'])
        key = restore['with']['key']
        for component in ('runner.os', python,
                          "hashFiles('requirements-*.txt')"):
            assert component in key, (job, component, key)
        assert restore_index < _named_step_index(steps, install), (
            job, restore_index, install)


def test_the_matrix_jobs_save_the_pip_cache_only_from_a_push_of_main(tmp):
    """The event gate between restore and save is the whole answer.

    A pull_request or workflow_dispatch run, or a push that is not main,
    restores and never writes; only a push of main, whose checkout the
    repository itself produced, may. Evaluated as Actions would evaluate it
    rather than substring-matched, so an `||` or a widened ref reads as the
    defect it is. The save also follows the step that runs the job's suites
    or measurement, so what a later run restores was put there by a run that
    actually ran them.
    """
    del tmp
    workflow = _tests_yml()
    for job, _python, save_after, _install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        restore_index, restore = _cache_step(steps, 'restore')
        save_index, save = _cache_step(steps, 'save')
        assert save['with']['key'] == restore['with']['key'], (job, save)
        assert save['with']['path'] == restore['with']['path'], (job, save)
        gate = save['if']
        for conjunct in ("github.event_name == 'push'",
                         "github.ref == 'refs/heads/main'"):
            assert conjunct in gate, (job, conjunct, gate)
        for github, expected in (
                ({'event_name': 'push', 'ref': 'refs/heads/main'}, True),
                ({'event_name': 'push', 'ref': 'refs/heads/other'}, False),
                ({'event_name': 'pull_request',
                  'ref': 'refs/pull/185/merge'}, False),
                ({'event_name': 'workflow_dispatch',
                  'ref': 'refs/heads/main'}, False),
        ):
            verdict = evaluate_if(gate, {
                'github': github,
                'status': {'success': True, 'failure': False,
                           'cancelled': False},
            })
            assert verdict is expected, (job, github, gate, verdict)
        assert save_index > _named_step_index(steps, save_after), (
            job, save_index, save_after)
        assert save_index > restore_index, (job, save_index, restore_index)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciworkflows_')


if __name__ == '__main__':
    raise SystemExit(main())
