#!/usr/bin/env python3
"""Executable contracts for the privileged patch-coverage commenter."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflows import _workflow_triggers  # noqa: E402


_GH_ARTIFACT_STUB = r"""#!/usr/bin/env python3
import json
import os
import sys

response = json.loads(os.environ['STUB_RESPONSE'])
args = sys.argv[1:]
expression = args[args.index('--jq') + 1]
if os.environ.get('ASSERT_NO_COVERAGE_ENV'):
    leaked = sorted(
        name for name in os.environ if name.startswith('COVERAGE_'))
    if leaked:
        raise SystemExit('coverage environment leaked: ' + ','.join(leaked))
if expression == '.artifacts[]':
    for item in response.get('artifacts', []):
        print(json.dumps(item))
elif expression == '.[]':
    for item in response.values():
        print(json.dumps(item))
else:
    raise SystemExit('unexpected jq expression: ' + expression)
"""


_GH_COMMENT_STUB = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state_path = Path(os.environ['STUB_STATE'])
calls_path = Path(os.environ['STUB_CALLS'])
state = json.loads(state_path.read_text(encoding='utf-8'))
with calls_path.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(args) + chr(10))


def endpoint():
    for value in args:
        if value.startswith('repos/'):
            return value
    return ''


target = endpoint()
if '--jq' in args:
    expression = args[args.index('--jq') + 1]
    if target.endswith('/comments'):
        for comment in state:
            print(json.dumps(comment))
    elif '/commits/' in target:
        print('170')
    elif '/pulls/' in target:
        print(os.environ['CURRENT_HEAD'])
    elif expression:
        raise SystemExit('unexpected query endpoint: ' + target)
    raise SystemExit(0)

if '-X' not in args:
    raise SystemExit(0)
method = args[args.index('-X') + 1]
body_arg = next((value for value in args if value.startswith('body=@')), None)
body = Path(body_arg[6:]).read_text(encoding='utf-8') if body_arg else ''
if method == 'POST' and target.endswith('/comments'):
    state.append({
        'id': 1,
        'user': {'login': 'github-actions[bot]'},
        'body': body,
    })
elif method == 'PATCH' and '/issues/comments/' in target:
    comment_id = int(target.rsplit('/', 1)[1])
    for comment in state:
        if comment['id'] == comment_id:
            comment['body'] = body
state_path.write_text(json.dumps(state), encoding='utf-8')
"""


def _workflow():
    """Read the commenter workflow under test."""
    return (ROOT / '.github' / 'workflows' / 'coverage-comment.yml').read_text(
        encoding='utf-8')


def _run_block(workflow, step_name):
    """Extract one Actions run block as a standalone shell script."""
    marker = f'      - name: {step_name}\n'
    _, found, after = workflow.partition(marker)
    assert found, f'missing workflow step: {step_name}'
    _, found, after = after.partition('        run: |\n')
    assert found, f'{step_name} has no shell block'
    lines = []
    for line in after.splitlines():
        if line and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines) + '\n'


def _write_executable(path, content):
    """Write an executable test double."""
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


def _run_shell_block(workdir, script, env):
    """Run a workflow shell block with coverage disabled in its children."""
    # The stubs are Python subprocesses in deleted temp dirs; scrub CI's
    # collector so coverage combine in the coverage job cannot chase them.
    child_env = {
        name: value for name, value in env.items()
        if not name.startswith('COVERAGE_')
    }
    return subprocess.run(
        [shutil.which('bash'), '-c', script], cwd=workdir, env=child_env,
        capture_output=True, text=True, timeout=60)


def _run_artifact_check(tmp, response, extra_env=None):
    """Run artifact-presence shell against one endpoint-shaped fixture."""
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the workflow shell'
    workdir = Path(tmp) / 'artifact-check'
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    _write_executable(workdir / 'bin' / 'gh', _GH_ARTIFACT_STUB)
    output = workdir / 'github-output'
    output.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub',
        'REPO': 'owner/repo',
        'RUN_ID': '123',
        'GITHUB_OUTPUT': str(output),
        'STUB_RESPONSE': json.dumps(response),
    }
    if extra_env:
        env.update(extra_env)
    result = _run_shell_block(
        workdir,
        _run_block(_workflow(), 'Check for the comment artifact'), env)
    return result, output.read_text(encoding='utf-8')


def test_artifact_selection_executes_against_both_endpoint_shapes(tmp):
    """The wrapper is benign when empty and finds a real entry."""
    empty, output = _run_artifact_check(
        tmp, {'total_count': 0, 'artifacts': []})
    assert empty.returncode == 0, (empty.stdout, empty.stderr)
    assert 'present=true' not in output, output
    present, output = _run_artifact_check(
        tmp, {
            'total_count': 1,
            'artifacts': [{
                'name': 'diff-coverage-comment', 'expired': False,
            }],
        })
    assert present.returncode == 0, (present.stdout, present.stderr)
    assert 'present=true' in output, output


def test_workflow_harness_scrubs_coverage_environment(tmp):
    """Python workflow stubs never start a collector of their own."""
    result, output = _run_artifact_check(
        tmp, {'total_count': 0, 'artifacts': []}, extra_env={
            'ASSERT_NO_COVERAGE_ENV': '1',
            'COVERAGE_PROCESS_START': str(ROOT / 'pyproject.toml'),
            'COVERAGE_CONTEXT': 'workflow-contract',
        })
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'present=true' not in output, output


def _job_section(workflow, job, next_job):
    """Return one top-level jobs section without neighboring jobs."""
    _, marker, section = workflow.partition(f'\n  {job}:\n')
    assert marker, workflow
    section, marker, _ = section.partition(f'\n  {next_job}:\n')
    assert marker, workflow
    return section


def _step_section(workflow, step_name):
    """Return one named step without neighboring steps or jobs."""
    marker = f'      - name: {step_name}\n'
    _, found, after = workflow.partition(marker)
    assert found, f'missing workflow step: {step_name}'
    boundary = re.search(
        r'\n      - (?:name|uses):|\n  [A-Za-z0-9_-]+:', after)
    if boundary:
        after = after[:boundary.start()]
    return after


def _step_condition(workflow, step_name):
    """Return a named step's complete Actions condition."""
    section = _step_section(workflow, step_name)
    match = re.search(
        r'^        if:.*(?:\n          [^\n]*)*', section, re.MULTILINE)
    assert match, f'missing condition for workflow step: {step_name}'
    return match.group(0)


def test_merge_coordinates_are_pinned_and_have_a_parent(tmp):
    """Both producers use the event SHA and the diff has HEAD^1 available."""
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    coverage = _job_section(workflow, 'coverage', 'diff-coverage')
    diff = _job_section(workflow, 'diff-coverage', 'aggregate')
    checkouts = re.findall(
        r'actions/checkout@.*?\n(?P<body>.*?)(?=\n      - |\Z)',
        coverage, re.DOTALL)
    assert checkouts, coverage
    for checkout in checkouts:
        assert re.search(r'^\s+ref: \$\{\{ github\.sha \}\}\s*$',
                         checkout, re.MULTILINE), checkout
    checkouts = re.findall(
        r'actions/checkout@.*?\n(?P<body>.*?)(?=\n      - |\Z)',
        diff, re.DOTALL)
    assert checkouts, diff
    for checkout in checkouts:
        assert re.search(r'^\s+ref: \$\{\{ github\.sha \}\}\s*$',
                         checkout, re.MULTILINE), checkout
    body = checkouts[0]
    depth = re.search(r'^\s+fetch-depth: (\d+)\s*$', body, re.MULTILINE)
    assert depth and (depth.group(1) == '0' or int(depth.group(1)) >= 2), body
    assert 'git diff --unified=0 HEAD^1 HEAD' in diff, diff
    assert 'github.event.pull_request.base.sha' not in diff, diff


def _run_comment_block(tmp, block_name, *, state, current_head='B',
                       head_sha='B', pr_number='170', claimed='170',
                       body='### Coverage\n'):
    """Run one commenter block with a recording GitHub double."""
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the workflow shell'
    workdir = Path(tmp) / block_name.replace(' ', '-')
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    _write_executable(workdir / 'bin' / 'gh', _GH_COMMENT_STUB)
    state_path = workdir / 'state.json'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    calls = workdir / 'calls.jsonl'
    calls.write_text('', encoding='utf-8')
    output = workdir / 'github-output'
    output.write_text('', encoding='utf-8')
    (workdir / 'body.md').write_text(body, encoding='utf-8')
    (workdir / 'pr-number.txt').write_text(claimed + '\n', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub',
        'REPO': 'owner/repo',
        'PR_NUMBER': pr_number,
        'HEAD_SHA': head_sha,
        'CURRENT_HEAD': current_head,
        'EVENT_NUMBERS': json.dumps([int(pr_number)]),
        'GITHUB_OUTPUT': str(output),
        'STUB_STATE': str(state_path),
        'STUB_CALLS': str(calls),
    }
    result = _run_shell_block(
        workdir, _run_block(_workflow(), block_name), env)
    return (result, json.loads(state_path.read_text(encoding='utf-8')), calls,
            output)


def _writes(calls):
    """Return recorded mutating API calls."""
    return [
        line for line in calls.read_text(encoding='utf-8').splitlines()
        if '"-X"' in line and ('"POST"' in line or '"PATCH"' in line)
    ]


def test_trusted_destination_and_body_bound_are_executable(tmp):
    """Artifact claims cannot reroute or bypass the 60,000-byte bound."""
    mismatch, _state, calls, _output = _run_comment_block(
        tmp, 'Post or update the pull request comment', state=[],
        claimed='999')
    assert mismatch.returncode != 0, mismatch.stdout
    assert _writes(calls) == [], calls.read_text(encoding='utf-8')
    assert '/comments' not in calls.read_text(encoding='utf-8'), \
        calls.read_text(encoding='utf-8')

    oversized, _state, calls, _output = _run_comment_block(
        tmp, 'Post or update the pull request comment', state=[],
        body='x' * 60001)
    assert oversized.returncode != 0, oversized.stdout
    assert _writes(calls) == [], calls.read_text(encoding='utf-8')
    assert '/comments' not in calls.read_text(encoding='utf-8'), \
        calls.read_text(encoding='utf-8')

    script = _run_block(
        _workflow(), 'Post or update the pull request comment')
    assert not re.search(r'^\s*PR_NUMBER\s*=', script, re.MULTILINE), script


def test_a_success_then_b_failure_replaces_the_marker(tmp):
    """A failed newer run cannot leave the prior percentage looking current."""
    posted, state, calls, _output = _run_comment_block(
        tmp, 'Post or update the pull request comment', state=[],
        head_sha='A', current_head='A', body='**100.0%**')
    assert posted.returncode == 0, (posted.stdout, posted.stderr)
    assert len(_writes(calls)) == 1, calls.read_text(encoding='utf-8')
    marked, state, calls, _output = _run_comment_block(
        tmp, 'Mark missing patch coverage', state=state,
        head_sha='B', current_head='B')
    assert marked.returncode == 0, (marked.stdout, marked.stderr)
    assert len(_writes(calls)) == 1, calls.read_text(encoding='utf-8')
    assert 'Patch coverage was not measured for commit B.' in \
        state[0]['body'], state
    assert '**100.0%**' not in state[0]['body'], state


def test_a_success_then_b_current_cancelled_replaces_the_marker(tmp):
    """A current cancelled run marks the old percentage unavailable."""
    posted, state, calls, _output = _run_comment_block(
        tmp, 'Post or update the pull request comment', state=[],
        head_sha='A', current_head='A', body='**100.0%**')
    assert posted.returncode == 0, (posted.stdout, posted.stderr)
    assert len(_writes(calls)) == 1, calls.read_text(encoding='utf-8')

    artifact, output = _run_artifact_check(
        tmp, {'total_count': 0, 'artifacts': []})
    assert artifact.returncode == 0, (artifact.stdout, artifact.stderr)
    assert 'present=true' not in output, output
    marked, state, calls, _output = _run_comment_block(
        tmp, 'Mark missing patch coverage', state=state,
        head_sha='B', current_head='B')
    assert marked.returncode == 0, (marked.stdout, marked.stderr)
    assert len(_writes(calls)) == 1, calls.read_text(encoding='utf-8')
    assert 'Patch coverage was not measured for commit B.' in \
        state[0]['body'], state
    assert '**100.0%**' not in state[0]['body'], state

    jobs = _workflow().partition('\njobs:\n')[2]
    assert "github.event.workflow_run.event == 'pull_request'" in jobs, jobs
    assert 'github.event.workflow_run.conclusion' not in jobs, jobs


def test_a_rerun_of_a_cannot_overwrite_newer_b(tmp):
    """A stale rerun exits before any comment write."""
    posted, state, calls, _output = _run_comment_block(
        tmp, 'Post or update the pull request comment', state=[],
        head_sha='B', current_head='B', body='**50.0%**')
    assert posted.returncode == 0, (posted.stdout, posted.stderr)
    stale, _state, calls, output = _run_comment_block(
        tmp, 'Resolve the target pull request from the event', state=state,
        head_sha='A', current_head='B')
    assert stale.returncode == 0, (stale.stdout, stale.stderr)
    assert 'stale=true' in output.read_text(encoding='utf-8'), \
        output.read_text(encoding='utf-8')
    assert 'number=' not in output.read_text(encoding='utf-8'), \
        output.read_text(encoding='utf-8')
    assert len(_writes(calls)) == 0, calls.read_text(encoding='utf-8')


def test_commenter_runs_every_completed_run_and_orders_stale_gate(
        tmp):
    """Every completed run can mark stale, while older heads never post."""
    del tmp
    workflow = _workflow()
    jobs = workflow.partition('\njobs:\n')[2]
    assert "github.event.workflow_run.event == 'pull_request'" in jobs, jobs
    assert 'github.event.workflow_run.conclusion' not in jobs, jobs
    resolve = workflow.index('- name: Resolve the target pull request')
    download = workflow.index('- name: Download the comment artifact')
    post = workflow.index('- name: Post or update the pull request comment')
    missing = workflow.index('- name: Mark missing patch coverage')
    assert resolve < download < post, workflow
    assert resolve < missing, workflow
    resolve_script = _run_block(
        workflow, 'Resolve the target pull request from the event')
    assert 'HEAD_SHA' in resolve_script
    assert 'current_sha' in resolve_script
    for step_name in ('Mark missing patch coverage',
                      'Download the comment artifact',
                      'Post or update the pull request comment'):
        condition = _step_condition(workflow, step_name)
        assert "steps.pr.outputs.stale != 'true'" in condition, condition


def test_missing_marker_step_owns_its_artifact_and_stale_conditions(tmp):
    """Invalidation is enabled only for a current run missing its artifact."""
    del tmp
    condition = _step_condition(_workflow(), 'Mark missing patch coverage')
    assert "steps.artifact.outputs.present != 'true'" in condition, condition
    assert "steps.pr.outputs.stale != 'true'" in condition, condition


def test_diff_coverage_artifacts_cross_the_trusted_boundary(tmp):
    """The producer and trusted commenter must agree on one artifact each."""
    del tmp
    tests_workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    comment_workflow = (
        ROOT / '.github' / 'workflows' / 'coverage-comment.yml').read_text(
            encoding='utf-8')
    _, marker, coverage = tests_workflow.partition('\n  coverage:\n')
    assert marker, tests_workflow
    coverage, marker, _ = coverage.partition('\n  diff-coverage:\n')
    assert marker, tests_workflow
    _, marker, diff = tests_workflow.partition('\n  diff-coverage:\n')
    assert marker, tests_workflow
    diff, marker, aggregate = diff.partition('\n  aggregate:\n')
    assert marker, tests_workflow
    # Anchored to end of line, all four: unanchored, `coverage-xml` is a
    # prefix of `coverage-xml-does-not-exist` and the assertion would
    # certify a wiring that could never find its artifact.
    assert re.search(r'uses: actions/upload-artifact@.*\n\s+with:\n'
                     r'\s+name: coverage-xml[ \t]*$', coverage,
                     re.MULTILINE), coverage
    assert re.search(r'uses: actions/download-artifact@.*\n\s+with:\n'
                     r'\s+name: coverage-xml[ \t]*$', diff,
                     re.MULTILINE), diff
    upload = diff.partition('uses: actions/upload-artifact@')[2]
    assert re.search(r'with:\n\s+name: diff-coverage-comment[ \t]*$',
                     upload, re.MULTILINE), diff
    download = comment_workflow.partition(
        'uses: actions/download-artifact@')[2]
    assert re.search(r'with:\n\s+name: diff-coverage-comment[ \t]*$',
                     download, re.MULTILINE), comment_workflow
    assert 'run-id: ${{ github.event.workflow_run.id }}' in download, download
    assert 'github-token: ${{ github.token }}' in download, download
    assert 'pull-requests: write' not in diff, diff
    # Only the YAML decides what runs. The file's own prose explains that
    # it checks nothing out, so naming the action in a comment is not a
    # checkout step and must not read as one.
    yaml_only = '\n'.join(
        line for line in comment_workflow.splitlines()
        if not line.lstrip().startswith('#'))
    assert 'actions/checkout' not in yaml_only, (
        f'the commenting workflow checks something out:\n{yaml_only}')
    needs = aggregate.partition('needs:')[2].partition('runs-on:')[0]
    assert 'diff-coverage' not in needs, needs
    triggers = _workflow_triggers(comment_workflow)
    assert triggers.get('workflow_run') == [
        '    workflows: [tests]', '    types: [completed]'], triggers
    assert 'pull-requests: write' in comment_workflow, comment_workflow
    assert 'actions: read' in comment_workflow, comment_workflow


def test_the_coverage_commenter_guards_its_privileged_shell(tmp):
    """Pipefail, one run per head, and a destination it did not import."""
    del tmp
    workflow = (
        ROOT / '.github' / 'workflows' / 'coverage-comment.yml').read_text(
            encoding='utf-8')
    # Actions' default shell is `bash -e` without pipefail, so in
    # `x="$(gh api | jq ...)"` a failed API call reads as an empty answer.
    guarded = re.findall(r'run: \|\n\s+set -euo pipefail\n', workflow)
    assert len(guarded) == workflow.count('run: |') == 4, workflow
    assert 'cancel-in-progress: true' in workflow, workflow
    # The destination comes from the event; the artifact's copy is compared.
    assert 'repos/$REPO/commits/$HEAD_SHA/pulls' in workflow, workflow
    assert 'PR_NUMBER: ${{ steps.pr.outputs.number }}' in workflow, workflow
    assert 'repos/$REPO/issues/$PR_NUMBER/comments' in workflow, workflow
    assert '"$claimed" != "$PR_NUMBER"' in workflow, workflow
    assert '(.body // "")' in workflow, workflow


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
