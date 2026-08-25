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


_GH_ARTIFACT_STUB = r"""#!/usr/bin/env python3
import json
import os
import sys

response = json.loads(os.environ['STUB_RESPONSE'])
args = sys.argv[1:]
expression = args[args.index('--jq') + 1]
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


def _run_artifact_check(tmp, response):
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
    result = subprocess.run(
        [bash, '-c', _run_block(
            _workflow(), 'Check for the comment artifact')],
        cwd=workdir, env=env, capture_output=True, text=True, timeout=60)
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


def _job_section(workflow, job, next_job):
    """Return one top-level jobs section without neighboring jobs."""
    _, marker, section = workflow.partition(f'\n  {job}:\n')
    assert marker, workflow
    section, marker, _ = section.partition(f'\n  {next_job}:\n')
    assert marker, workflow
    return section


def test_merge_coordinates_are_pinned_and_have_a_parent(tmp):
    """Both producers use the event SHA and the diff has HEAD^1 available."""
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    coverage = _job_section(workflow, 'coverage', 'diff-coverage')
    diff = _job_section(workflow, 'diff-coverage', 'aggregate')
    checkout = re.search(
        r'actions/checkout@.*?\n(?P<body>.*?)(?=\n      - |\Z)',
        coverage, re.DOTALL)
    assert checkout, coverage
    assert re.search(r'^\s+ref: \$\{\{ github\.sha \}\}\s*$',
                     checkout.group('body'), re.MULTILINE), checkout.group(0)
    checkout = re.search(
        r'actions/checkout@.*?\n(?P<body>.*?)(?=\n      - |\Z)',
        diff, re.DOTALL)
    assert checkout, diff
    body = checkout.group('body')
    assert re.search(r'^\s+ref: \$\{\{ github\.sha \}\}\s*$',
                     body, re.MULTILINE), body
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
    result = subprocess.run(
        [bash, '-c', _run_block(_workflow(), block_name)], cwd=workdir,
        env=env, capture_output=True, text=True, timeout=60)
    return result, json.loads(state_path.read_text(encoding='utf-8')), calls, output


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
    assert 'Patch coverage was not measured for commit B.' in state[0]['body'], \
        state
    assert '**100.0%**' not in state[0]['body'], state


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
    assert len(_writes(calls)) == 0, calls.read_text(encoding='utf-8')


def test_commenter_runs_completed_non_cancelled_runs_and_orders_stale_gate(tmp):
    """Failure runs can mark stale, while older heads never reach posting."""
    del tmp
    workflow = _workflow()
    jobs = workflow.partition('\njobs:\n')[2]
    assert "conclusion != 'cancelled'" in jobs, jobs
    assert "conclusion == 'success'" not in jobs, jobs
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
    assert "steps.pr.outputs.stale != 'true'" in workflow, workflow


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
