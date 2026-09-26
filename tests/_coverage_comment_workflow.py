"""Run and read one block of the coverage-comment workflow.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/test_coverage_comment_workflow.py, which three suites
imported whole to reach these seven names. All three now import from
here, and so does that suite. A wrong reader here would fail every
caller at once, so tests/test_coverage_comment_boundary.py pins the two
refusals.

`write_executable` is the one copy of that helper: tests/_speedharness.py
imports it here rather than carrying a second body, and
tests/test_coverage_comment_boundary.py pins both halves of what it does.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _coverage_comment_steps import (  # noqa: E402
    GH_ARTIFACT_STUB, GH_COMMENT_STUB)
from _yamlread import step_scalar  # noqa: E402


def workflow():
    """Read the commenter workflow under test."""
    return (ROOT / '.github' / 'workflows' / 'coverage-comment.yml').read_text(
        encoding='utf-8')


def run_block(text, step_name):
    """Extract one Actions run block as a standalone shell script."""
    marker = f'      - name: {step_name}\n'
    _, found, after = text.partition(marker)
    assert found, f'missing workflow step: {step_name}'
    _, found, after = after.partition('        run: |\n')
    assert found, f'{step_name} has no shell block'
    lines = []
    for line in after.splitlines():
        if line and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines) + '\n'


def write_executable(path, content):
    """Write an executable test double."""
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


def run_shell_block(workdir, script, env):
    """Run a workflow shell block with coverage disabled in its children."""
    return subprocess.run(
        [_util.workflow_bash(), '-c', script], cwd=workdir,
        env=_util.child_coverage('scrub', env),
        capture_output=True, text=True, timeout=60)


def run_artifact_check(tmp, response, extra_env=None):
    """Run artifact-presence shell against one endpoint-shaped fixture."""
    workdir = Path(tmp) / 'artifact-check'
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    write_executable(workdir / 'bin' / 'gh', GH_ARTIFACT_STUB)
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
    result = run_shell_block(
        workdir,
        run_block(workflow(), 'Check for the comment artifact'), env)
    return result, output.read_text(encoding='utf-8')


def step_condition(text, step_name):
    """Return a named step's complete Actions condition."""
    condition = step_scalar(text, 'comment', step_name, 'if')
    assert condition is not None, f'missing condition for step: {step_name}'
    return condition


def run_comment_block(tmp, block_name, *, state, current_head='B',
                      head_sha='B', pr_number='170', claimed='170',
                      body='### Coverage\n', jobs=None,
                      run_conclusion=None):
    """Run one commenter block with a recording GitHub double."""
    workdir = Path(tmp) / block_name.replace(' ', '-')
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    write_executable(workdir / 'bin' / 'gh', GH_COMMENT_STUB)
    state_path = workdir / 'state.json'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    calls = workdir / 'calls.jsonl'
    calls.write_text('', encoding='utf-8')
    output = workdir / 'github-output'
    output.write_text('', encoding='utf-8')
    (workdir / 'body.md').write_text(body, encoding='utf-8')
    (workdir / 'pr-number.txt').write_bytes((claimed + '\n').encode('utf-8'))
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub',
        'REPO': 'owner/repo', 'HEAD_REPO': 'owner/repo', 'RUN_ID': '123',
        'PR_NUMBER': pr_number,
        'HEAD_SHA': head_sha,
        'CURRENT_HEAD': current_head,
        'EVENT_NUMBERS': json.dumps([int(pr_number)]),
        'GITHUB_OUTPUT': str(output),
        'STUB_STATE': str(state_path),
        'STUB_CALLS': str(calls),
        'STUB_JOBS': json.dumps(jobs or []),
        'RUN_CONCLUSION': run_conclusion or '',
    }
    result = run_shell_block(
        workdir, run_block(workflow(), block_name), env)
    return (result, json.loads(state_path.read_text(encoding='utf-8')), calls,
            output)
