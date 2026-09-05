"""Publication scenarios driven through the tracked workflow script."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from _ghexpr import evaluate_if
from _yamlsteps import step_mappings
from _coverage_comment_steps import (
    GH_ARTIFACT_STUB, GH_COMMENT_STUB,
    CHECK_NAME, CHECK_EXTERNAL_PREFIX, GH_CHECK_STUB, CRLF_JQ_STUB,
)


def _runner(tmp, workflow_reader, extract_block, shell_runner,
                         write_executable):
    """Exercise the final publication block against a stateful API double."""
    def run(label='check', state=None, status='success', head_sha='a' * 40,
            run_url='https://github.com/owner/repo/actions/runs/7',
            extra_env=None):
        workdir = Path(tmp) / label
        (workdir / 'bin').mkdir(parents=True, exist_ok=True)
        write_executable(workdir / 'bin' / 'gh', GH_CHECK_STUB)
        real_jq = None
        needs_jq_stub = extra_env and any(
            name in extra_env for name in ('STUB_CRLF_IDS', 'STUB_RAW_IDS'))
        if needs_jq_stub:
            real_jq = shutil.which('jq')
            assert real_jq is not None, 'jq is required for CRLF tests'
            write_executable(workdir / 'bin' / 'jq', CRLF_JQ_STUB)
        state_path = workdir / 'state.json'
        if not state_path.exists():
            state_path.write_text(
                json.dumps({'checks': state or []}), encoding='utf-8')
        calls = workdir / 'calls.jsonl'
        calls.touch()
        env = {
            **os.environ,
            'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
            'GH_TOKEN': 'stub', 'REPO': 'owner/repo',
            'HEAD_SHA': head_sha, 'RUN_URL': run_url, 'STATUS': status,
            'STUB_STATE': str(state_path), 'STUB_CALLS': str(calls),
        }
        if real_jq:
            env['REAL_JQ'] = real_jq
        if extra_env:
            env.update(extra_env)
        script = extract_block(workflow_reader(), 'Publish coverage check')
        result = shell_runner(workdir, script, env)
        return result, json.loads(state_path.read_text(encoding='utf-8')), \
            calls, script

    return run


def call_list(path):
    return [json.loads(line) for line in path.read_text(
        encoding='utf-8').splitlines() if line]

def write_list(path):
    def is_write(args):
        return '-X' in args and args[args.index('-X') + 1] in (
            'POST', 'PATCH')
    return [args for args in call_list(path) if is_write(args)]

def fields(args):
    return dict(args[index + 1].split('=', 1)
                for index in range(len(args) - 1)
                if args[index] in ('-f', '--raw-field')
                and '=' in args[index + 1])

def _mapping(workflow_reader):
    workflow = workflow_reader()
    steps = step_mappings(workflow, 'comment')
    assert steps[-1]['name'] == 'Publish coverage check', steps
    step = steps[-1]
    assert step['if'] == 'always()', step
    assert step['env'] == {
        'GH_TOKEN': '${{ github.token }}',
        'REPO': '${{ github.repository }}',
        'HEAD_SHA': '${{ github.event.workflow_run.head_sha }}',
        'RUN_URL': '${{ github.server_url }}/${{ github.repository }}'
                   '/actions/runs/${{ github.run_id }}',
        'STATUS': '${{ job.status }}',
        'JOB_SKIPPED': '${{ steps.missing.outputs.skipped }}',
    }, step
    script = step['run']
    assert "-f name='coverage comment'" in script, script
    assert 'external_id="daedalus-coverage-comment/v1/$HEAD_SHA"' in script
    assert 'head_sha="$HEAD_SHA"' in script
    assert 'details_url="$RUN_URL"' in script
    assert 'case "$STATUS"' in script
    assert not any(name in script for name in (
        'body.md', 'pr-number.txt', 'artifact', 'github.workspace'))
    return step, steps


def _create(run):
    result, state, calls, _script = run()
    assert result.returncode == 0, (result.stdout, result.stderr)
    writes = write_list(calls)
    assert len(writes) == 1, calls.read_text(encoding='utf-8')
    write = writes[0]
    assert write[write.index('-X') + 1] == 'POST', write
    assert next(value for value in write if value.startswith('repos/')) == (
        'repos/owner/repo/check-runs'), write
    assert fields(write) == {
        'name': CHECK_NAME, 'head_sha': 'a' * 40, 'status': 'completed',
        'conclusion': 'success',
        'external_id': CHECK_EXTERNAL_PREFIX + 'a' * 40,
        'details_url': 'https://github.com/owner/repo/actions/runs/7',
    }, fields(write)
    assert state['checks'][0]['conclusion'] == 'success', state
    list_call = call_list(calls)[0]
    assert list_call[:3] == ['api', '--method', 'GET'], list_call
    assert list_call[list_call.index('-H') + 1] == (
        'Cache-Control: no-cache'), list_call
    assert 'filter=all' in list_call and 'per_page=100' in list_call, list_call
    assert '--paginate' in list_call and '--jq' in list_call, list_call
    for status in ('failure', 'cancelled'):
        result, state, calls, _script = run(label=status, status=status)
        assert result.returncode == 0, (status, result.stdout, result.stderr)
        assert state['checks'][0]['conclusion'] == status, state
        assert len(write_list(calls)) == 1, calls.read_text(
            encoding='utf-8')


def _duplicates(run):
    head_sha = 'b' * 40
    identity = CHECK_EXTERNAL_PREFIX + head_sha
    duplicate_state = [
        {'id': 41, 'name': CHECK_NAME, 'external_id': identity,
         'app': {'slug': 'github-actions'}, 'conclusion': 'failure'},
        {'id': 42, 'name': CHECK_NAME, 'external_id': identity,
         'app': {'slug': 'github-actions'}, 'conclusion': 'cancelled'},
        {'id': 43, 'name': CHECK_NAME, 'external_id': identity,
         'app': {'slug': 'other-app'}, 'conclusion': 'failure'},
        {'id': 44, 'name': 'other', 'external_id': identity,
         'app': {'slug': 'github-actions'}, 'conclusion': 'failure'},
        {'id': 45, 'name': CHECK_NAME, 'external_id': 'foreign',
         'app': {'slug': 'github-actions'}, 'conclusion': 'failure'},
    ]
    result, updated, calls, _script = run(
        label='duplicates', state=duplicate_state, head_sha=head_sha)
    assert result.returncode == 0, (result.stdout, result.stderr)
    writes = write_list(calls)
    assert [next(value for value in item if '/check-runs/' in value)
            for item in writes] == [
                'repos/owner/repo/check-runs/41',
                'repos/owner/repo/check-runs/42'], writes
    assert [check['conclusion'] for check in updated['checks']] == [
        'success', 'success', 'failure', 'failure', 'failure'], updated


def _crlf(run):
    head_sha = 'b' * 40
    identity = CHECK_EXTERNAL_PREFIX + head_sha
    crlf_state = [{
        'id': 41, 'name': CHECK_NAME, 'external_id': identity,
        'app': {'slug': 'github-actions'}, 'conclusion': 'failure',
    }]
    result, state, calls, _script = run(
        label='crlf', state=crlf_state, head_sha=head_sha,
        extra_env={'STUB_CRLF_IDS': '1'})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert state['checks'][0]['conclusion'] == 'success', state
    for source in (b'41\n', b'41\r\n'):
        output = subprocess.check_output(
            [sys.executable, '-c', CRLF_JQ_STUB, '-c',
             'import sys; sys.stdout.buffer.write(' + repr(source) + ')'],
            env=dict(os.environ, REAL_JQ=sys.executable, STUB_CRLF_IDS='1'))
        assert output == b'41\r\n', repr(output)
    for label, raw in (
            ('embedded-cr', '4\r1'), ('leading-cr', '\r41'),
            ('extra-cr', '41\r\r'), ('whitespace', '41 '),
            ('signed', '+41'), ('empty', ''), ('nondigit', '41x')):
        result, state, calls, _script = run(
            label=label, extra_env={'STUB_RAW_IDS': raw})
        assert result.returncode != 0, (label, result.stdout, result.stderr)
        assert not write_list(calls), (label, call_list(calls))
        assert state['checks'] == [], (label, state)


def _reruns(run):
    first, _state, calls, _script = run(label='repeat')
    assert first.returncode == 0, (first.stdout, first.stderr)
    second, _state, calls, _script = run(label='repeat', status='failure')
    assert second.returncode == 0, (second.stdout, second.stderr)
    third, state, calls, _script = run(label='repeat', status='failure')
    assert third.returncode == 0, (third.stdout, third.stderr)
    assert [item[item.index('-X') + 1] for item in write_list(calls)] == [
        'POST', 'PATCH', 'PATCH'], call_list(calls)
    assert len(state['checks']) == 1 and state['checks'][0]['conclusion'] == (
        'failure'), state
    failed, _state, calls, _script = run(label='rerun', status='failure')
    assert failed.returncode == 0, (failed.stdout, failed.stderr)
    succeeded, state, calls, _script = run(label='rerun', status='success')
    assert succeeded.returncode == 0, (succeeded.stdout, succeeded.stderr)
    assert [item[item.index('-X') + 1] for item in write_list(calls)] == [
        'POST', 'PATCH'], call_list(calls)
    assert len(state['checks']) == 1 and state['checks'][0]['conclusion'] == (
        'success'), state


def _failures(run):
    result, state, calls, _script = run(
        label='before-resolution', status='failure')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert state['checks'][0]['conclusion'] == 'failure', state
    assert len(write_list(calls)) == 1
    head_sha = 'c' * 40
    result, state, calls, _script = run(
        label='fork', head_sha=head_sha,
        extra_env={'HEAD_REPO': 'attacker/fork', 'PR_NUMBER': '999'})
    assert result.returncode == 0, (result.stdout, result.stderr)
    write = write_list(calls)[0]
    assert 'repos/owner/repo/check-runs' in write
    assert 'head_sha=' + head_sha in write
    assert state['checks'][0]['external_id'] == (
        CHECK_EXTERNAL_PREFIX + head_sha)
    result, state, calls, _script = run(label='unexpected', status='timed_out')
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not call_list(calls), call_list(calls)
    assert state['checks'] == [], state
    result, state, calls, _script = run(
        label='malformed', extra_env={'STUB_MALFORMED_JSON': '1'})
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not write_list(calls) and state['checks'] == [], state
    invalid = [
        {'id': 56, 'name': CHECK_NAME,
         'external_id': CHECK_EXTERNAL_PREFIX + 'a' * 40,
         'app': {'slug': 'github-actions'}},
        {'id': 'not-a-number', 'name': CHECK_NAME,
         'external_id': CHECK_EXTERNAL_PREFIX + 'a' * 40,
         'app': {'slug': 'github-actions'}},
    ]
    result, state, calls, _script = run(label='invalid', state=invalid)
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not write_list(calls) and state['checks'] == invalid, state
    failure_cases = (
        ('list-failure', {'STUB_FAIL_LIST': '1'}, []),
        ('create-failure', {'STUB_FAIL_CREATE': '1'}, []),
        ('patch-failure', {'STUB_FAIL_PATCH': '1'}, [{
            'id': 55, 'name': CHECK_NAME,
            'external_id': CHECK_EXTERNAL_PREFIX + 'a' * 40,
            'app': {'slug': 'github-actions'},
        }]),
    )
    for label, extra, initial in failure_cases:
        result, _state, calls, _script = run(
            label=label, state=initial, extra_env=extra)
        assert result.returncode != 0, (label, result.stdout, result.stderr)
        expected = 0 if label == 'list-failure' else 1
        assert len(write_list(calls)) == expected, (label, call_list(calls))


def _hostile(run, tmp):
    workdir = Path(tmp) / 'hostile-artifact'
    workdir.mkdir(parents=True)
    (workdir / 'body.md').write_text(
        '$(touch side-effect)\n${{ github.event.workflow_run.head_sha }}\n',
        encoding='utf-8')
    (workdir / 'pr-number.txt').write_text(
        'repos/attacker/check-runs/999\n', encoding='utf-8')
    result, state, calls, _script = run(label='hostile-artifact')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not (workdir / 'side-effect').exists()
    assert state['checks'][0]['name'] == CHECK_NAME, state
    assert all('attacker' not in value for args in call_list(calls)
               for value in args), call_list(calls)


def _orchestration(run, step, steps):
    conditions = {
        item['name']: item.get('if') for item in steps
        if item['name'] in ('Mark missing patch coverage',
                            'Download the comment artifact',
                            'Post or update the pull request comment')
    }
    scenarios = (
        ('artifact-present-success', 'true', 'false', 'success', 'success'),
        ('artifact-absent-failure', None, 'false', 'failure', 'failure'),
        ('failure-before-resolution', None, '', 'failure', 'failure'),
        ('failure-after-resolution', 'true', 'false', 'failure', 'failure'),
        ('stale-head', 'true', 'true', 'success', 'success'),
        ('cancellation', 'true', 'false', 'cancelled', 'cancelled'),
    )
    for label, present, stale, status, publish in scenarios:
        context = {
            'steps': {
                'artifact': {'outputs': {} if present is None else {
                    'present': present}},
                'pr': {'outputs': {'stale': stale}},
            },
            'status': {name: status == name for name in (
                'success', 'failure', 'cancelled')},
        }
        assert evaluate_if(step['if'], context) is True, label
        ready = status == 'success' and present == 'true' and stale != 'true'
        missing = status == 'success' and present != 'true' and stale != 'true'
        expected = {
            'Mark missing patch coverage': missing,
            'Download the comment artifact': ready,
            'Post or update the pull request comment': ready,
        }
        for name, condition in conditions.items():
            assert evaluate_if(condition, context) is expected[name], (
                label, name, condition)
        result, state, _calls, _script = run(
            label='orchestration-' + label, status=publish)
        assert result.returncode == 0, (label, result.stdout, result.stderr)
        assert state['checks'][0]['conclusion'] == publish, (label, state)


SCENARIOS = (_create, _duplicates, _crlf, _reruns, _failures)


def publication_contract(tmp, workflow_reader, extract_block, shell_runner,
                         write_executable):
    workflow = Path(tmp) / '.github/workflows/coverage-comment.yml'
    workflow.parent.mkdir(parents=True)
    original = workflow_reader().encode('utf-8')
    workflow.write_bytes(original)
    assert workflow.read_bytes() == original
    copied_reader = lambda: workflow.read_text(encoding='utf-8')
    run = _runner(tmp, copied_reader, extract_block, shell_runner,
                  write_executable)
    _absent_scenarios(tmp, run, copied_reader(), extract_block,
                      shell_runner, write_executable)
    step, steps = _mapping(copied_reader)
    for scenario in SCENARIOS:
        scenario(run)
    _hostile(run, tmp)
    _orchestration(run, step, steps)


ABSENT_SCENARIOS = (
    ('docs-only', [{'name': 'test', 'conclusion': 'success'},
                   {'name': 'coverage', 'conclusion': 'skipped'}], 0,
     'neutral'),
    ('no-coverage-job', [], 1, 'failure'),
    ('successful-coverage', [{'name': 'coverage', 'conclusion': 'success'}],
     1, 'failure'),
    ('failed-coverage', [{'name': 'coverage', 'conclusion': 'failure'}],
     1, 'failure'),
    ('cancelled-coverage', [{'name': 'coverage', 'conclusion': 'cancelled'}],
     1, 'failure'),
    ('other-skipped', [{'name': 'test', 'conclusion': 'skipped'}],
     1, 'failure'),
)


def _absent_scenario(tmp, run, workflow, extract_block, shell_runner,
                     write_executable, scenario, prior, fail_jobs=False):
    label, jobs, exit_code, conclusion = scenario
    workdir = Path(tmp) / f'absent-{label}-{len(prior)}-{fail_jobs}'
    (workdir / 'bin').mkdir(parents=True)
    output = workdir / 'github-output'
    output.touch()
    state = workdir / 'state.json'
    state.write_text(json.dumps(prior), encoding='utf-8')
    calls = workdir / 'calls.jsonl'
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub', 'REPO': 'owner/repo', 'RUN_ID': '123',
        'HEAD_SHA': 'a' * 40, 'CURRENT_HEAD': 'a' * 40, 'PR_NUMBER': '170',
        'GITHUB_OUTPUT': str(output), 'STUB_STATE': str(state),
        'STUB_CALLS': str(calls), 'STUB_JOBS': json.dumps(jobs),
        'STUB_RESPONSE': '{"total_count": 0, "artifacts": []}',
        'STUB_FAIL_JOBS': '1' if fail_jobs else '',
    }
    write_executable(workdir / 'bin' / 'gh', GH_ARTIFACT_STUB)
    artifact = shell_runner(workdir, extract_block(
        workflow, 'Check for the comment artifact'), env)
    assert artifact.returncode == 0, (artifact.stdout, artifact.stderr)
    assert output.read_text(encoding='utf-8') == ''
    write_executable(workdir / 'bin' / 'gh', GH_COMMENT_STUB)
    missing = shell_runner(workdir, extract_block(
        workflow, 'Mark missing patch coverage'), env)
    assert missing.returncode == (1 if fail_jobs else exit_code), (
        label, missing.returncode, missing.stdout, missing.stderr)
    if fail_jobs:
        assert 'jobs' in missing.stderr, missing.stderr
    else:
        assert any(args for args in call_list(calls)
                   if 'repos/owner/repo/actions/runs/123/jobs' in args)
    skipped = 'skipped=true' in output.read_text(encoding='utf-8')
    result, checks, _calls, _script = run(
        label=workdir.name + '-publish',
        status='failure' if missing.returncode else 'success',
        extra_env={'JOB_SKIPPED': 'true' if skipped else ''})
    assert result.returncode == 0, (result.stdout, result.stderr)
    check = checks['checks'][0]
    assert check['conclusion'] == ('failure' if fail_jobs else conclusion), (
        label, check)
    if conclusion == 'neutral' and not fail_jobs:
        assert check['output[summary]'] == (
            'Coverage was not measured for a documentation-only change.')


def _absent_scenarios(tmp, run, workflow, extract_block, shell_runner,
                      write_executable):
    prior = [{'id': 1, 'user': {'login': 'github-actions[bot]'},
              'body': '<!-- daedalus-diff-coverage -->old percentage'}]
    for scenario in ABSENT_SCENARIOS:
        for comments in ([], prior):
            _absent_scenario(tmp, run, workflow, extract_block, shell_runner,
                             write_executable, scenario, comments)
    _absent_scenario(tmp, run, workflow, extract_block, shell_runner,
                     write_executable, ABSENT_SCENARIOS[0], [], True)


EXPECTED_PUBLICATION_STEP = {
    'name': 'Publish coverage check',
    'if': 'always()',
    'env': {
        'GH_TOKEN': '${{ github.token }}',
        'REPO': '${{ github.repository }}',
        'HEAD_SHA': '${{ github.event.workflow_run.head_sha }}',
        'RUN_URL': '${{ github.server_url }}/${{ github.repository }}'
                   '/actions/runs/${{ github.run_id }}',
        'STATUS': '${{ job.status }}',
        'JOB_SKIPPED': '${{ steps.missing.outputs.skipped }}',
    },
    'run': r'''set -euo pipefail

case "$STATUS" in
  success|failure|cancelled) ;;
  *)
    echo "unexpected workflow-run conclusion: $STATUS" >&2
    exit 1
    ;;
esac

if [ "$STATUS" = success ] && [ "${JOB_SKIPPED:-}" = true ]; then
  STATUS=neutral
fi

external_id="daedalus-coverage-comment/v1/$HEAD_SHA"
if ! gh api --method GET -H 'Cache-Control: no-cache' --paginate \
  "repos/$REPO/commits/$HEAD_SHA/check-runs" \
  -f filter=all -f per_page=100 \
  --jq '.check_runs[]' > check-runs.json
then
  echo "listing coverage comment checks for $HEAD_SHA failed" >&2
  exit 1
fi
if ! jq -s --arg name 'coverage comment' \
  --arg external_id "$external_id" \
  'map(select(.name == $name and
    .external_id == $external_id and
    .app.slug == "github-actions") | .id) | .[]' \
  check-runs.json > check-ids.txt
then
  echo "decoding coverage comment checks failed" >&2
  exit 1
fi
while IFS= read -r check_id; do
  check_id="${check_id%$'\r'}"
  case "$check_id" in
    ''|*[!0-9]*)
      echo "coverage comment check id is not only digits:" \
        "$check_id" >&2
      exit 1
      ;;
  esac
done < check-ids.txt

write_check() {
  local method="$1"
  local target="$2"
  local -a args=(
    -X "$method" "$target"
    -f name='coverage comment'
    -f status=completed
    -f conclusion="$STATUS"
    -f external_id="$external_id"
    -f details_url="$RUN_URL"
  )
  if [ "$STATUS" = neutral ]; then
    local summary='Coverage was not measured for a '
    summary+='documentation-only change.'
    args+=( -f 'output[title]=Coverage not measured'
      -f "output[summary]=$summary" )
  fi
  if [ "$method" = POST ]; then
    args+=( -f head_sha="$HEAD_SHA" )
  fi
  if ! gh api "${args[@]}" >/dev/null
  then
    echo "publishing coverage comment check failed:" \
      "$method $target" >&2
    return 1
  fi
}

if [ ! -s check-ids.txt ]; then
  write_check POST "repos/$REPO/check-runs"
  echo "created coverage comment check for $HEAD_SHA"
else
  while IFS= read -r check_id; do
    check_id="${check_id%$'\r'}"
    write_check PATCH "repos/$REPO/check-runs/$check_id"
  done < check-ids.txt
  echo "updated coverage comment checks for $HEAD_SHA"
fi
''',
}
