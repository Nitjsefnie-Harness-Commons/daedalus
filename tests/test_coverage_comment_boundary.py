#!/usr/bin/env python3
"""Executable contracts for the coverage comment privilege boundary."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _coverage_comment_steps import EXPECTED_STEP_MAPPINGS  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflowrun import recorded_writes, run_step  # noqa: E402
from _yamlread import YAMLReadError, top_level_mapping  # noqa: E402
from _yamlsteps import step_mappings  # noqa: E402
import test_coverage_comment_workflow as commenter  # noqa: E402


_DOWNLOAD = (
    'actions/download-artifact@'
    '37930b1c2abaa49bbe596cd826c3c89aef350131'
)
_MARKER = '<!-- daedalus-diff-coverage -->\n'
_HEAD_SHA = 'B' * 40
_COMMENT_PREFIX = (
    _MARKER + f'\nPatch coverage for commit {_HEAD_SHA}.\n\n'
)
_PERMISSIONS = {'pull-requests': 'write', 'actions': 'read'}
_SENTINEL = 'PRIVILEGED_TOKEN_SENTINEL'
_HOSTILE_BODIES = {
    'bash': (
        '#!/bin/bash\n'
        'printf \'%s\' "$GH_TOKEN" > artifact-side-effect\n'
        'printf \'bash replaced body\\n\' > body.md\n'
    ),
    'javascript': (
        '#!/usr/bin/env node\n'
        "const fs = require('fs');\n"
        "fs.writeFileSync('artifact-side-effect', process.env.GH_TOKEN);\n"
        "fs.writeFileSync('body.md', 'javascript replaced body\\n');\n"
    ),
}


def _workflow():
    """Read the privileged workflow under test."""
    return (ROOT / '.github/workflows/coverage-comment.yml').read_text(
        encoding='utf-8')


def _assert_privileged_step_allowlist(workflow):
    """Require every decoded key and value of every privileged step."""
    steps = step_mappings(workflow, 'comment')
    assert isinstance(steps, list), 'privileged steps were not decoded'
    assert len(steps) == len(EXPECTED_STEP_MAPPINGS), (
        f'unsafe privileged step count: {len(steps)}')
    for actual, expected in zip(steps, EXPECTED_STEP_MAPPINGS):
        if actual == expected:
            continue
        differing = sorted(
            key for key in set(actual) | set(expected)
            if key not in actual or key not in expected
            or actual[key] != expected[key])
        raise AssertionError(
            f'unsafe privileged step mapping for {expected["name"]!r}: '
            f'differing keys {differing!r}')


def _assert_allowlist_refuses(workflow):
    """Require one hostile topology mutation to fail the allowlist."""
    try:
        _assert_privileged_step_allowlist(workflow)
    except (AssertionError, YAMLReadError):
        return
    raise AssertionError('unsafe privileged step mutation was accepted')


def _assert_privileged_permissions(workflow):
    """Require the complete decoded privileged permission mapping."""
    permissions = top_level_mapping(workflow, 'permissions')
    assert permissions == _PERMISSIONS, (
        f'unsafe privileged permissions: {permissions!r}')


def _assert_permissions_refused(workflow):
    """Require one hostile permission mutation to fail closed."""
    try:
        _assert_privileged_permissions(workflow)
    except (AssertionError, YAMLReadError):
        return
    raise AssertionError('unsafe privileged permission mutation was accepted')


def _run_hostile_post(tmp, label, body):
    """Run the real post shell with an executable-looking artifact body."""
    workdir = Path(tmp) / label
    (workdir / 'bin').mkdir(parents=True)
    commenter._write_executable(  # pylint: disable=protected-access
        workdir / 'bin' / 'gh',
        commenter._GH_COMMENT_STUB)  # pylint: disable=protected-access
    state_path = workdir / 'state.json'
    calls_path = workdir / 'calls.jsonl'
    state_path.write_text('[]', encoding='utf-8')
    calls_path.write_text('', encoding='utf-8')
    (workdir / 'body.md').write_text(body, encoding='utf-8')
    (workdir / 'pr-number.txt').write_text('170\n', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': _SENTINEL,
        'REPO': 'owner/repo',
        'PR_NUMBER': '170',
        'HEAD_SHA': _HEAD_SHA,
        'CURRENT_HEAD': _HEAD_SHA,
        'STUB_STATE': str(state_path),
        'STUB_CALLS': str(calls_path),
    }
    steps = step_mappings(_workflow(), 'comment')
    post = next(
        step for step in steps
        if step.get('name') == 'Post or update the pull request comment')
    result = run_step(workdir, post, env)
    state = json.loads(state_path.read_text(encoding='utf-8'))
    return result, state, calls_path, workdir


def test_hostile_artifact_bodies_remain_inert_text(tmp):
    """Executable-looking bodies are posted exactly and never executed."""
    for label, body in _HOSTILE_BODIES.items():
        result, state, calls, workdir = _run_hostile_post(
            tmp, label, body)
        assert result.returncode == 0, (result.stdout, result.stderr)
        side_effect = workdir / 'artifact-side-effect'
        assert not side_effect.exists(), (
            f'{label} artifact executed with the privileged token')
        assert len(state) == 1, state
        assert state[0]['body'] == _COMMENT_PREFIX + body, (
            label, state[0]['body'], body)
        writes = recorded_writes(calls)
        assert len(writes) == 1, calls.read_text(encoding='utf-8')


def test_privileged_steps_are_an_exact_allowlist(tmp):
    """Every field and unknown key is part of the privileged contract."""
    del tmp
    workflow = _workflow()
    _assert_privileged_step_allowlist(workflow)
    post_run = (
        '          PR_NUMBER: ${{ steps.pr.outputs.number }}\n'
        '        run: |\n')
    field_mutations = (
        '        shell: bash\n',
        '        working-directory: /tmp\n',
        '        continue-on-error: true\n',
        '        future-authority: enabled\n',
    )
    mutations = [
        workflow.replace(
            '          test -f body.md\n',
            '          bash body.md\n          test -f body.md\n', 1),
        workflow.replace(_DOWNLOAD, 'owner/other@' + '0' * 40, 1),
        workflow.replace(
            '          run-id: ${{ github.event.workflow_run.id }}\n',
            '          run-id: ${{ github.run_id }}\n', 1),
        workflow.replace(
            '          HEAD_SHA: ${{ github.event.workflow_run.head_sha }}\n',
            '          HEAD_SHA: ${{ github.sha }}\n', 1),
        workflow.replace('        id: artifact\n', '        id: other\n', 1),
        workflow.replace(
            "          steps.artifact.outputs.present == 'true'\n",
            '          always()\n', 1),
    ]
    mutations.extend(
        workflow.replace(post_run, post_run.replace(
            '        run: |\n', field), 1)
        for field in field_mutations)
    extra_step = (
        '      - name: Unexpected artifact consumer\n'
        '        run: wc -c body.md\n')
    mutations.append(workflow.replace(
        '    steps:\n', '    steps:\n' + extra_step, 1))
    for mutated in mutations:
        assert mutated != workflow, 'real privileged mapping was not mutated'
        _assert_allowlist_refuses(mutated)


def test_privileged_permissions_are_exactly_allowlisted(tmp):
    """No additional or widened scope can enter the privileged token."""
    del tmp
    workflow = _workflow()
    _assert_privileged_permissions(workflow)
    mutations = (
        workflow.replace(
            '  actions: read\n',
            '  actions: read\n  contents: write\n', 1),
        workflow.replace('  actions: read\n', '  actions: write\n', 1),
        workflow.replace(
            '  actions: read\n',
            '  actions: read\n  "cont\\x65nts": "wr\\x69te"\n', 1),
        workflow.replace(
            'permissions:\n  pull-requests: write\n  actions: read\n',
            'permissions: write-all\n', 1),
    )
    for mutated in mutations:
        assert mutated != workflow, 'real permission mapping was not mutated'
        _assert_permissions_refused(mutated)


def test_absent_artifact_output_enables_the_missing_marker(tmp):
    """An unset output is absent, and the real guard handles that value."""
    # pylint: disable-next=protected-access
    result, output = commenter._run_artifact_check(
        tmp, {'total_count': 0, 'artifacts': []})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert output == '', output
    context = {
        'steps': {
            'artifact': {'outputs': {}},
            'pr': {'outputs': {'stale': 'false'}},
        },
        'status': {
            'success': True,
            'failure': False,
            'cancelled': False,
        },
    }
    assert 'present' not in context['steps']['artifact']['outputs']
    condition = commenter._step_condition(  # pylint: disable=protected-access
        _workflow(), 'Mark missing patch coverage')
    assert evaluate_if(condition, context) is True, (condition, context)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
