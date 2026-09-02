#!/usr/bin/env python3
"""Executable contracts for the coverage comment privilege boundary."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _workflowrun  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _coverage_comment_steps import (  # noqa: E402
    EXPECTED_PRIVILEGED_JOB_MAPPING,
    EXPECTED_STEP_MAPPINGS,
    EXPECTED_WORKFLOW_MAPPING,
)
from _repo import ROOT  # noqa: E402
from _wfpins import WorkflowPinError, pinned_action  # noqa: E402
from _yamlread import YAMLReadError, top_level_mapping  # noqa: E402
from _yamlsteps import (  # noqa: E402
    complete_job_mapping,
    step_mappings,
    workflow_mapping,
)
import test_coverage_comment_workflow as commenter  # noqa: E402


_DOWNLOAD_ACTION = 'actions/download-artifact'
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


def test_workflow_step_shells_are_resolved_through_path(tmp):
    """Default and declared shell programs are resolved before execution."""
    calls = []
    real_run = _workflowrun.subprocess.run

    def capture_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '', '')

    _workflowrun.subprocess.run = capture_run
    try:
        _workflowrun.run_step(tmp, {'run': 'true'}, os.environ)
        _workflowrun.run_step(
            tmp, {'run': 'true', 'shell': 'bash --noprofile {0}'},
            os.environ)
    finally:
        _workflowrun.subprocess.run = real_run

    resolved = shutil.which('bash')
    assert resolved is not None and os.path.isabs(resolved), resolved
    programs = [command[0] for command, _kwargs in calls]
    assert programs == [resolved, resolved], (
        f'shell executables were not PATH-resolved: {programs!r}')
    script_path = str(Path(tmp) / 'workflow-step.sh')
    assert calls[0][0][1:] == ['-e', script_path], calls[0]
    assert calls[1][0][1:] == ['--noprofile', script_path], calls[1]


def test_workflow_step_rejects_an_unresolved_shell(tmp):
    """A missing shell is rejected before a bare invocation is attempted."""
    missing = 'daedalus-missing-workflow-shell'
    assert shutil.which(missing) is None, missing
    calls = []
    message = ''
    real_run = _workflowrun.subprocess.run

    def capture_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '', '')

    _workflowrun.subprocess.run = capture_run
    try:
        try:
            _workflowrun.run_step(
                tmp, {'run': 'true', 'shell': f'{missing} {{0}}'},
                os.environ)
        except FileNotFoundError as exc:
            message = str(exc)
        else:
            raise AssertionError(
                f'unresolved shell {missing!r} reached subprocess.run')
    finally:
        _workflowrun.subprocess.run = real_run

    assert missing in message, message
    assert not calls, calls


def test_workflow_step_composes_container_shell_and_environment(tmp):
    """Workflow, job, then step containers define effective execution."""
    calls = []
    real_run = _workflowrun.subprocess.run

    def capture_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '', '')

    workflow = {
        'defaults': {'run': {'shell': 'bash --noprofile {0}'}},
        'env': {'WORKFLOW_ONLY': 'workflow', 'SHARED': 'workflow'},
    }
    job = {
        'defaults': {'run': {'shell': 'bash --norc {0}'}},
        'env': {
            'JOB_ONLY': 'job',
            'SHARED': 'job',
            'BASH_ENV': 'hook.sh',
        },
    }
    step = {'run': 'true', 'env': {'SHARED': '${{ github.token }}'}}
    env = {**os.environ, 'SHARED': 'step'}
    _workflowrun.subprocess.run = capture_run
    try:
        try:
            _workflowrun.run_step(
                tmp, {'run': 'true'}, os.environ,
                workflow=workflow, job={})
            _workflowrun.run_step(
                tmp, step, env, workflow=workflow, job=job)
            _workflowrun.run_step(
                tmp, {**step, 'shell': 'bash --posix {0}'}, env,
                workflow=workflow, job=job)
        except TypeError as error:
            raise AssertionError(
                'workflow/job execution containers are not resolved'
            ) from error
    finally:
        _workflowrun.subprocess.run = real_run

    resolved = shutil.which('bash')
    assert resolved is not None and os.path.isabs(resolved), resolved
    script_path = str(Path(tmp) / 'workflow-step.sh')
    assert [call[0] for call in calls] == [
        [resolved, '--noprofile', script_path],
        [resolved, '--norc', script_path],
        [resolved, '--posix', script_path],
    ], calls
    workflow_env = calls[0][1]['env']
    assert workflow_env['WORKFLOW_ONLY'] == 'workflow', workflow_env
    assert workflow_env['SHARED'] == 'workflow', workflow_env
    job_env = calls[1][1]['env']
    assert job_env['WORKFLOW_ONLY'] == 'workflow', job_env
    assert job_env['JOB_ONLY'] == 'job', job_env
    assert job_env['SHARED'] == 'step', job_env
    assert job_env['BASH_ENV'] == 'hook.sh', job_env


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
        _assert_privileged_container_allowlist(workflow)
    except (AssertionError, YAMLReadError):
        return
    raise AssertionError('unsafe privileged container mutation was accepted')


def _assert_exact_mapping(actual, expected, owner):
    """Require exact keys and values while naming the differing keys."""
    if actual == expected:
        return
    assert isinstance(actual, dict), (
        f'unsafe privileged {owner} mapping: {actual!r}')
    differing = sorted(
        key for key in set(actual) | set(expected)
        if key not in actual or key not in expected
        or actual[key] != expected[key])
    raise AssertionError(
        f'unsafe privileged {owner} mapping: differing keys {differing!r}')


def _assert_privileged_container_allowlist(workflow):
    """Require complete decoded job and workflow container mappings."""
    job = complete_job_mapping(workflow, 'comment')
    _assert_exact_mapping(
        job, EXPECTED_PRIVILEGED_JOB_MAPPING, 'job')
    decoded_workflow = workflow_mapping(workflow)
    _assert_exact_mapping(
        decoded_workflow, EXPECTED_WORKFLOW_MAPPING, 'workflow')


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


def _run_hostile_post(tmp, label, body, workflow=None):
    """Run the real post shell with an executable-looking artifact body."""
    workflow = _workflow() if workflow is None else workflow
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
    steps = step_mappings(workflow, 'comment')
    post = next(
        step for step in steps
        if step.get('name') == 'Post or update the pull request comment')
    decoded_workflow = workflow_mapping(workflow)
    job = decoded_workflow['jobs']['comment']
    result = _workflowrun.run_step(
        workdir, post, env, workflow=decoded_workflow, job=job)
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
        writes = _workflowrun.recorded_writes(calls)
        assert len(writes) == 1, calls.read_text(encoding='utf-8')


def test_hostile_post_resolves_job_shell_and_environment(tmp):
    """The hostile runtime observes the job's inherited execution state."""
    workflow = _workflow()
    anchor = '    timeout-minutes: 10\n'
    shell_field = (
        '    defaults:\n'
        '      run:\n'
        "        shell: bash -c 'printf \"%s\" \"$GH_TOKEN\" > "
        'artifact-side-effect; bash "$1"\' -- {0}\n')
    shell_workflow = workflow.replace(
        anchor, anchor + shell_field, 1)
    assert shell_workflow != workflow, shell_workflow
    try:
        shell_result = _run_hostile_post(
            tmp, 'job-shell', 'ordinary coverage body\n',
            workflow=shell_workflow)
    except TypeError as error:
        raise AssertionError(
            'hostile post harness does not accept decoded containers'
        ) from error
    result, state, calls, workdir = shell_result
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (workdir / 'artifact-side-effect').read_text(
        encoding='utf-8') == _SENTINEL
    assert len(state) == 1, state
    assert len(_workflowrun.recorded_writes(calls)) == 1

    env_field = (
        '    env:\n'
        '      BASH_ENV: ${{ github.workspace }}/body.md\n')
    env_workflow = workflow.replace(anchor, anchor + env_field, 1)
    assert env_workflow != workflow, env_workflow
    result, state, calls, workdir = _run_hostile_post(
        tmp, 'job-env', _HOSTILE_BODIES['bash'],
        workflow=env_workflow)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (workdir / 'artifact-side-effect').read_text(
        encoding='utf-8') == _SENTINEL
    assert 'bash replaced body\n' in state[0]['body'], state
    assert len(_workflowrun.recorded_writes(calls)) == 1


def test_commits_query_is_refused_loudly(tmp):
    """A commits query fails loudly instead of naming a pull request."""
    workdir = Path(tmp) / 'commits-query'
    (workdir / 'bin').mkdir(parents=True)
    commenter._write_executable(  # pylint: disable=protected-access
        workdir / 'bin' / 'gh',
        commenter._GH_COMMENT_STUB)  # pylint: disable=protected-access
    state_path = workdir / 'state.json'
    calls_path = workdir / 'calls.jsonl'
    output_path = workdir / 'github-output'
    state_path.write_text('[]', encoding='utf-8')
    calls_path.write_text('', encoding='utf-8')
    output_path.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': _SENTINEL,
        'REPO': 'owner/repo', 'HEAD_REPO': 'owner/repo',
        'HEAD_SHA': _HEAD_SHA, 'CURRENT_HEAD': _HEAD_SHA,
        'EVENT_NUMBERS': '',
        'GITHUB_OUTPUT': str(output_path),
        'STUB_STATE': str(state_path),
        'STUB_CALLS': str(calls_path),
    }
    resolve = commenter._run_block(  # pylint: disable=protected-access
        _workflow(), 'Resolve the target pull request from the event')
    result = _workflowrun.run_step(workdir, {'run': resolve}, env)
    published = output_path.read_text(encoding='utf-8')
    assert not published.strip(), published
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert 'unexpected commits query' in result.stderr, result.stderr
    assert not result.stdout.strip(), result.stdout


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
        workflow.replace(
            pinned_action(workflow, _DOWNLOAD_ACTION),
            'owner/other@' + '0' * 40, 1),
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


def test_privileged_workflow_and_job_are_exact_allowlists(tmp):
    """Every complete workflow and privileged-job key is contracted."""
    del tmp
    workflow = _workflow()
    _assert_privileged_container_allowlist(workflow)
    job_anchor = '    timeout-minutes: 10\n'
    job_fields = (
        ('permissions', (
            '    permissions:\n'
            '      contents: write\n'
            '      pull-requests: write\n'
            '      actions: read\n')),
        ('defaults', (
            '    defaults:\n'
            '      run:\n'
            "        shell: bash -c 'bash \"$1\"' -- {0}\n")),
        ('env', (
            '    env:\n'
            '      BASH_ENV: ${{ github.workspace }}/body.md\n')),
        ('container', '    container: attacker/image\n'),
        ('services', '    services: {}\n'),
        ('strategy', '    strategy: {}\n'),
        ('outputs', '    outputs: {}\n'),
        ('unknown', '    future-authority: enabled\n'),
    )
    mutations = [
        (f'job {label}', workflow.replace(
            job_anchor, job_anchor + field, 1))
        for label, field in job_fields
    ]
    workflow_fields = (
        ('env', 'env:\n  BASH_ENV: body.md\n'),
        ('defaults', (
            'defaults:\n'
            '  run:\n'
            '    shell: bash\n')),
        ('unknown', 'future-authority: enabled\n'),
    )
    top_anchor = 'name: coverage comment\n'
    mutations.extend(
        (f'workflow {label}', workflow.replace(
            top_anchor, top_anchor + field, 1))
        for label, field in workflow_fields
    )
    for label, mutated in mutations:
        assert mutated != workflow, f'{label} did not mutate the workflow'
        try:
            _assert_allowlist_refuses(mutated)
        except AssertionError as error:
            raise AssertionError(f'{label}: {error}') from error


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


def _pin_refused(workflow, action, detail):
    """Require an unusable pin to raise rather than return a guess."""
    try:
        pinned_action(workflow, action)
    except WorkflowPinError as error:
        assert detail in str(error), (str(error), workflow)
        return
    raise AssertionError(f'{detail!r} was accepted as a pin in {workflow!r}')


def test_pinned_action_reads_the_real_workflow_pin(tmp):
    """The real workflow yields one pin for the anchors to splice."""
    del tmp
    workflow = _workflow()
    pin = pinned_action(workflow, _DOWNLOAD_ACTION)
    assert pin.startswith(_DOWNLOAD_ACTION + '@'), pin
    assert workflow.count(pin) == 1, pin


def test_pinned_action_refuses_an_unpinned_action(tmp):
    """An action the text never pins cannot yield a splice string."""
    del tmp
    _pin_refused(
        '        uses: actions/download-artifact@v8\n',
        _DOWNLOAD_ACTION, 'no pin')


def test_pinned_action_refuses_conflicting_pins(tmp):
    """Two spellings of one action leave no single anchor to mutate."""
    del tmp
    _pin_refused(
        f'  uses: {_DOWNLOAD_ACTION}@' + 'a' * 40 + '\n'
        f'  uses: {_DOWNLOAD_ACTION}@' + 'b' * 40 + '\n',
        _DOWNLOAD_ACTION, 'conflicting pins')


def test_pinned_action_folds_a_pin_repeated_verbatim(tmp):
    """The same pin written twice is one pin, whatever text carries it."""
    del tmp
    pin = f'{_DOWNLOAD_ACTION}@' + 'f' * 40
    assert pinned_action(f'  uses: {pin}\n  uses: {pin}\n',
                         _DOWNLOAD_ACTION) == pin


def test_pinned_action_folds_a_pin_repeated_across_a_file(tmp):
    """One action pinned at several sites is one pin, not a conflict."""
    del tmp
    text = (ROOT / '.github/workflows/tests.yml').read_text(encoding='utf-8')
    pin = pinned_action(text, _DOWNLOAD_ACTION)
    assert pin.startswith(_DOWNLOAD_ACTION + '@'), pin
    assert text.count(pin) > 1, 'tests.yml no longer repeats the pin'


def test_pinned_action_refuses_a_partial_identity(tmp):
    """Only a whole action name, not a longer one ending in it, is a pin."""
    del tmp
    _pin_refused(
        f'  uses: other-{_DOWNLOAD_ACTION}@' + 'c' * 40 + '\n',
        _DOWNLOAD_ACTION, 'no pin')


def test_pinned_action_refuses_a_malformed_sha(tmp):
    """Exactly forty lowercase hex digits, standing alone, or no pin."""
    del tmp
    _pin_refused(
        f'  uses: {_DOWNLOAD_ACTION}@' + 'd' * 41 + '\n',
        _DOWNLOAD_ACTION, 'no pin')
    _pin_refused(
        f'  uses: {_DOWNLOAD_ACTION}@' + 'e' * 40 + '-rc1\n',
        _DOWNLOAD_ACTION, 'no pin')
    _pin_refused(
        f'  uses: {_DOWNLOAD_ACTION}@' + 'A' * 40 + '\n',
        _DOWNLOAD_ACTION, 'no pin')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
