#!/usr/bin/env python3
"""What the workflows in .github/ must keep doing, read as shell and as YAML.

A workflow fails silently when a detail is dropped — a gate that never runs
on the commit it is gating, a permission widened past what the job needs, a
release published before its checks finished. These tests read the workflow
files themselves, and run the /claim script as shell rather than trusting a
reading of it.
"""
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import job_mapping, job_scalar  # noqa: E402
from _workflows import _trigger_names  # noqa: E402


_GH_STUB = r"""#!/usr/bin/env python3
# Stands in for `gh`. State lives in $STUB_STATE: one assignee per line.
# Every call is appended to $STUB_CALLS so the test can assert what was sent.
import os, sys, pathlib
state = pathlib.Path(os.environ['STUB_STATE'])
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
with calls.open('a', encoding='utf-8') as handle:
    handle.write(' '.join(argv) + chr(10))
assignees = [x for x in state.read_text(encoding='utf-8').split() if x]
who = ''
for arg in argv:
    if arg.startswith('assignees[]='):
        who = arg.split('=', 1)[1]
if '-X' in argv and 'POST' in argv and any('/assignees' in a for a in argv):
    if os.environ.get('STUB_REFUSE') != '1' and who not in assignees:
        assignees.append(who)
    state.write_text(chr(10).join(assignees), encoding='utf-8')
elif '-X' in argv and 'DELETE' in argv and any('/assignees' in a for a in argv):
    state.write_text(
        chr(10).join(a for a in assignees if a != who), encoding='utf-8')
elif any(a.endswith('/comments') for a in argv):
    pass
elif '--jq' in argv:
    print(chr(10).join(assignees))
"""


def _claim_script():
    """The `run:` block of claim.yml, dedented, ready for bash."""
    workflow = (_util.ROOT / '.github' / 'workflows' / 'claim.yml').read_text(
        encoding='utf-8')
    _, marker, after = workflow.partition('        run: |\n')
    assert marker, 'claim.yml has no run block shaped as this test expects'
    lines = []
    for line in after.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line[10:])
    return chr(10).join(lines)


def _run_claim(tmp, body, assigned, actor='alice', refuse=False):
    """Run the claim script against a stubbed gh; return (assignees, calls)."""
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the claim workflow script'
    workdir = Path(tmp) / f'claim-{abs(hash((body, tuple(assigned), actor, refuse)))}'
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    stub = workdir / 'bin' / 'gh'
    stub.write_text(_GH_STUB, encoding='utf-8')
    stub.chmod(0o755)
    state = workdir / 'state'
    state.write_text(chr(10).join(assigned), encoding='utf-8')
    calls = workdir / 'calls'
    calls.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'STUB_STATE': str(state), 'STUB_CALLS': str(calls),
        'STUB_REFUSE': '1' if refuse else '0',
        'GH_TOKEN': 'stub', 'REPO': 'owner/repo', 'ISSUE': '1',
        'ACTOR': actor, 'BODY': body,
    }
    result = subprocess.run([bash, '-c', _claim_script()], env=env,
                            capture_output=True, text=True, timeout=60)
    return (
        [x for x in state.read_text(encoding='utf-8').split() if x],
        calls.read_text(encoding='utf-8'),
        result,
    )


def test_the_claim_command_assigns_only_its_own_commenter(tmp):
    """/claim and /unclaim, exercised as shell rather than read as YAML.

    The interesting cases are the refusals: a claim on an issue somebody else
    holds must not steal it, an unclaim from a non-assignee must not touch the
    assignee that is there, and an unclaim must remove exactly one login so a
    second assignee survives.
    """
    assigned, calls, result = _run_claim(tmp, '/claim', [])
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert assigned == ['alice'], assigned
    assert 'Assigned to @alice.' in calls, calls

    # Already held by someone else: no assignment call at all.
    assigned, calls, result = _run_claim(tmp, '/claim', ['bob'])
    assert assigned == ['bob'], assigned
    assert '-X POST' not in calls, calls
    assert 'already claimed by @bob' in calls, calls

    # Unclaim removes ONLY the commenter, leaving a co-assignee in place.
    assigned, calls, result = _run_claim(tmp, '/unclaim', ['alice', 'bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' in calls and 'assignees[]=alice' in calls, calls

    # Unclaim by a non-assignee changes nothing.
    assigned, calls, result = _run_claim(tmp, '/unclaim', ['bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' not in calls, calls

    # /release is the same command under the name the reference
    # implementation uses, and behaves identically in both directions.
    assigned, calls, result = _run_claim(tmp, '/release', ['alice', 'bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' in calls and 'assignees[]=alice' in calls, calls
    assigned, calls, result = _run_claim(tmp, '/release', ['bob'])
    assert assigned == ['bob'], assigned
    assert '-X DELETE' not in calls, calls

    # Whitespace and a Windows line ending still match exactly...
    assigned, _calls, _result = _run_claim(tmp, '  /claim\r\n', [])
    assert assigned == ['alice'], assigned

    # ...but a sentence containing the word is not a command, and must not
    # reach the API at all.
    assigned, calls, result = _run_claim(tmp, 'please /claim this for me', [])
    assert assigned == [], assigned
    assert calls == '', calls
    assert result.returncode == 0, (result.stdout, result.stderr)

    # GitHub silently ignoring an assignee is reported, not assumed away.
    assigned, calls, result = _run_claim(tmp, '/claim', [], refuse=True)
    assert assigned == [], assigned
    assert result.returncode != 0, result.stdout
    assert 'would not accept @alice' in calls, calls


def test_the_claim_workflow_keeps_its_least_privilege_shape(tmp):
    """The properties that fail silently if someone tidies them away."""
    del tmp
    workflow = (_util.ROOT / '.github' / 'workflows' / 'claim.yml').read_text(
        encoding='utf-8')
    # issues: write and nothing else — this job never reads the tree. Scoped
    # to the permissions block: the surrounding comments name other scopes to
    # say why they are absent, and a substring search would read those.
    _, marker, after = workflow.partition('\npermissions:\n')
    assert marker, workflow
    granted = []
    for line in after.splitlines():
        if not line.startswith('  ') or line.lstrip().startswith('#'):
            break
        granted.append(line.strip())
    assert granted == ['issues: write'], granted
    # Two claims racing must both be answered, so the group never cancels.
    assert 'cancel-in-progress: false' in workflow, workflow
    for guard in ('github.event.issue.pull_request == null',
                  "github.event.issue.state == 'open'",
                  "github.event.comment.user.type != 'Bot'"):
        assert guard in workflow, guard
    # Both names for giving an issue up reach the script, not just one.
    for command in ('/claim', '/unclaim', '/release'):
        assert f"contains(github.event.comment.body, '{command}')" in workflow, command
    # The body is attacker-controlled: it travels by environment, and the only
    # ${{ }} in the run block would be an injection.
    _, _, after = workflow.partition('        run: |')
    assert '${{' not in after, 'an expression is interpolated into the script'


def _tests_workflow():
    """Read the pull-request workflow whose producer job is untrusted."""
    return (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')


def test_coverage_job_publishes_distinct_python_and_javascript_metrics(_tmp):
    """Dropping capture, labels, either XML, or either gate must fail."""
    workflow = _tests_workflow()
    coverage = workflow.split('\n  coverage:\n', 1)[1]
    coverage = coverage.split('\n  diff-coverage:\n', 1)[0]
    capture = '${{ github.workspace }}/.node-v8-coverage'
    env = job_mapping(workflow, 'coverage', 'env')
    assert env.get('NODE_V8_COVERAGE') == capture, env
    wrong_scope = 'jobs:\n  coverage:\n    steps:\n      - env:\n' \
                  f'          NODE_V8_COVERAGE: {capture}\n'
    assert job_mapping(wrong_scope, 'coverage', 'env') is None
    assert 'inherit NODE_V8_COVERAGE' in coverage, coverage
    assert '- name: Python coverage summary' in coverage, coverage
    assert '- name: Python coverage gate' in coverage, coverage
    assert '- name: JavaScript coverage summary' in coverage, coverage
    assert '- name: JavaScript coverage gate' in coverage, coverage
    assert "echo '### Python coverage'" in coverage, coverage
    assert "echo '### JavaScript coverage'" in coverage, coverage
    assert 'Python statement coverage and JavaScript physical code-line' \
        in coverage, coverage
    assert 'coverage are separate metrics; do not add them together.' \
        in coverage, coverage
    upload = coverage.partition('name: coverage-xml')[2]
    upload = upload.partition('if-no-files-found: error')[0]
    assert re.search(r'^\s+coverage\.xml\s*$', upload, re.MULTILINE), upload
    assert re.search(r'^\s+javascript-coverage\.xml\s*$',
                     upload, re.MULTILINE), upload


def _assert_diff_coverage_permissions(workflow):
    """Require the producer job's complete decoded permission grant."""
    permissions = job_mapping(workflow, 'diff-coverage', 'permissions')
    assert permissions == {'contents': 'read'}, (
        f'unsafe decoded permissions: {permissions!r}')


def _assert_permissions_mutation_refused(workflow):
    """Require one widened real-workflow mutation to fail the contract."""
    try:
        _assert_diff_coverage_permissions(workflow)
    except AssertionError as error:
        assert 'unsafe decoded permissions' in str(error), str(error)
        return
    raise AssertionError('widened decoded permissions were accepted')


def test_diff_coverage_permissions_are_exactly_read_only(tmp):
    """The job running pull-request code has only contents read access."""
    del tmp
    _assert_diff_coverage_permissions(_tests_workflow())


def test_import_resolving_jobs_install_the_pinned_statement_analyzer(tmp):
    """Every import environment installs coverage.py from its sole pin."""
    del tmp
    pins = re.findall(r'^coverage==.*$',
                      (ROOT / 'requirements-test.txt').read_text(
                          encoding='utf-8'), re.MULTILINE)
    assert len(pins) == 1, pins
    workflow_dir = ROOT / '.github' / 'workflows'
    tests = _tests_workflow()
    lint = (workflow_dir / 'lint.yml').read_text(encoding='utf-8')
    release = (workflow_dir / 'release.yml').read_text(encoding='utf-8')

    def before(source, job, consumer):
        section = source.partition(f'\n  {job}:\n')[2].partition(consumer)
        return section[0] if section[1] else ''
    jobs = (
        ('diff-coverage', before(tests, 'diff-coverage',
                                 '- name: Measure the coverage')),
        ('suites', before(tests, 'suites', '- name: Run every suite')),
        ('pylint', before(lint, 'pylint', '- name: pylint')),
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


def test_permission_whitespace_mutation_is_refused(tmp):
    """Whitespace before the colon cannot hide pull-request write access."""
    del tmp
    workflow = _tests_workflow()
    mutated = workflow.replace(
        '      contents: read\n',
        '      contents: read\n      pull-requests : write\n', 1)
    assert mutated != workflow, 'real permission mapping was not mutated'
    _assert_permissions_mutation_refused(mutated)


def test_quoted_and_escaped_permission_keys_are_refused(tmp):
    """Equivalent decoded keys cannot hide a widened permission grant."""
    del tmp
    workflow = _tests_workflow()
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
    """Equivalent decoded mapping fields cannot hide the widened grant."""
    del tmp
    workflow = _tests_workflow()
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
    """Decoded writes and unrecognised permission names are refused."""
    del tmp
    workflow = _tests_workflow()
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
    workflow = (_util.ROOT / '.github' / 'workflows' / 'actionlint.yml').read_text(
        encoding='utf-8')
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


def test_only_this_repository_benchmarks_without_a_reviewer(tmp):
    """The speed job's environment is exactly this routing expression.

    The speed job is the only one that checks out a pull request's own head
    and runs it. Everything else about the job is containment applied after
    that decision — a read-only token, no secrets, `pull_request` rather than
    `pull_request_target` — so the environment is what decides whose code
    runs at all, and it is pinned whole: any edit to the event guard, to the
    repository comparison, to which name each branch selects, or to the
    scalar's chomping fails this one equality.

    What is pinned is the expression the workflow carries. Whether
    `fork-benchmark` actually requires a reviewer is repository
    configuration — an environment's protection rules, which GitHub recreates
    empty if the environment is deleted — and no test in this repository can
    see it.
    """
    del tmp
    expected = (
        "${{ github.event_name == 'pull_request'"
        ' && github.event.pull_request.head.repo.full_name'
        " != github.repository"
        " && 'fork-benchmark' || 'benchmark' }}")
    workflow = (ROOT / '.github' / 'workflows' / 'speed.yml').read_text(
        encoding='utf-8')
    actual = job_scalar(workflow, 'speed', 'environment')
    # Whitespace inside the expression collapses, so how the scalar is
    # wrapped is free; a trailing newline collapses to a space rather than
    # vanishing, so `>` in place of `>-` fails here instead of shipping a
    # newline in an environment name.
    assert actual is not None and re.sub(r'\s+', ' ', actual) == expected, (
        f'speed.yml routes the speed job by {actual!r}, '
        f'not by {expected!r}')


def test_the_speed_gate_throws_away_its_first_round(tmp):
    """The first suite a job runs is not one of the measured ones.

    A cold page cache is paid by whichever side goes first, and interleaving
    does not share it: across eight runs the baseline's first round exceeded
    its second by 19.14s on average and was the largest of the four totals
    every time, while the head's first round exceeded its second by 0.96s. In
    the same direction every run, so rounds do not average it out — it made
    every verdict about 3% optimistic, which is a gate reading low on exactly
    the regressions it exists to catch.

    Discarding is only discarding if the comparison cannot see it, so this
    pins both halves: a warm-up runs before the measured loop, and it writes
    outside the globs the comparison reads.
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'speed.yml').read_text(
        encoding='utf-8')
    _, marker, after = workflow.partition('- name: Run both suites, interleaved')
    assert marker, 'the timing step is not named the way this test finds it'
    step, _, rest = after.partition('- name: Compare')
    warmup = step.index('reports/warmup/')
    measured = step.index('reports/$side-$round')
    assert warmup < measured, 'the warm-up does not run before the measurement'

    # The comparison reads reports/base-* and reports/head-*; a warm-up
    # written as reports/base-0 would be picked up as a round.
    for glob in re.findall(r'ls -d (reports/\S+)', rest):
        assert not fnmatch.fnmatch('reports/warmup', glob), glob
        assert not fnmatch.fnmatch('reports/warmup/base', glob), glob

    # A ceiling that does not cover the extra runs kills the job for doing
    # them, which is the failure the warm-up would introduce.
    rounds = int(re.search(r'ROUNDS: "(\d+)"', workflow).group(1))
    ceiling = int(re.search(r'timeout-minutes: (\d+)', workflow).group(1))
    assert ceiling >= 15 * (rounds * 2 + 2), (ceiling, rounds)


def test_the_speed_gate_measures_a_pull_request_against_its_own_base(tmp):
    """Before merge, and against the base SHA rather than the last release.

    The gate ran on push alone, so a regression was measured only after it had
    landed. Two details make the pull-request half mean anything: the baseline
    is the exact base SHA — the last release would fold every commit merged
    since into the number and attribute all of it to whoever opened the pull
    request — and the candidate is the pull request's own head rather than the
    merge commit `actions/checkout` defaults to, which is a tree nobody
    authored and no reviewer can point at.
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'speed.yml').read_text(
        encoding='utf-8')
    triggers, _, jobs = workflow.partition('permissions:')
    assert '\n  pull_request:' in triggers, triggers
    # pull_request_target would run the proposed code with a writable token
    # and the base repository's secrets in reach. Checked against the trigger
    # block alone: the workflow's own comment says why it is not used, and a
    # whole-file search would match that comment.
    assert '\n  pull_request_target:' not in triggers, triggers
    assert 'contents: read' in workflow, workflow
    assert 'github.event.pull_request.base.sha' in jobs, jobs
    assert 'github.event.pull_request.head.sha' in jobs, jobs
    # Two open pull requests must not cancel each other.
    assert 'group: speed-${{ github.event.pull_request.number' in workflow

    # The base SHA is payload; it must travel by environment rather than be
    # expanded inside the script that consumes it.
    _, marker, after = workflow.partition('PR_BASE: ${{')
    assert marker, 'the base SHA does not reach the script by environment'
    script, _, _ = after.partition('- name: Check out this commit')
    _, _, body = script.partition('run: |')
    assert '${{' not in body, 'an expression is interpolated into the script'


def test_the_speed_gate_is_not_manually_dispatchable(tmp):
    """The one job whose checkout ref is a step output takes no manual run.

    Code scanning reports three `actions/cache-poisoning/poisonable-step`
    findings on this file, and each names the trigger rather than any cache
    input: `(workflow_dispatch)`. They are false positives — the query treats
    a dispatched run as holding the default branch's cache scope whatever ref
    it was started on, while a real run's scope follows the ref it was given.
    The trigger is dropped anyway rather than carrying three permanently
    dismissed alerts, so re-adding it reopens all three.

    Read through `_workflow_triggers` rather than by substring: `push` and
    `pull_request` are asserted present because without them the refusal above
    would be satisfied by a file that had stopped declaring triggers at all.
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'speed.yml').read_text(
        encoding='utf-8')
    names = _trigger_names(workflow)
    assert 'workflow_dispatch' not in names, sorted(names)
    assert 'repository_dispatch' not in names, sorted(names)
    assert 'push' in names, sorted(names)
    assert 'pull_request' in names, sorted(names)


def _pinned_actions():
    """{action path: {sha: [workflow names]}} over every `uses:` in the tree."""
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciworkflows_')


if __name__ == '__main__':
    raise SystemExit(main())
