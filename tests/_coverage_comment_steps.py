"""Exact decoded mappings and doubles for the coverage-comment workflow."""
import json
import os
from pathlib import Path

from _yamlsteps import step_mappings

CHECK_NAME = 'coverage comment'
CHECK_EXTERNAL_PREFIX = 'daedalus-coverage-comment/v1/'


GH_CHECK_STUB = r'''#!/usr/bin/env python3
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


def fields():
    result = {}
    for index, value in enumerate(args[:-1]):
        if value in ('-f', '--raw-field', '-F', '--field'):
            key, separator, field = args[index + 1].partition('=')
            if separator:
                result[key] = field
    return result


target = endpoint()
if target.endswith('/check-runs') and '-X' not in args:
    if os.environ.get('STUB_FAIL_LIST'):
        print('stub list failure', file=sys.stderr)
        raise SystemExit(23)
    if os.environ.get('STUB_MALFORMED_JSON'):
        print('{malformed', end='')
        raise SystemExit(0)
    for check in state.get('checks', []):
        print(json.dumps(check))
    raise SystemExit(0)


method = args[args.index('-X') + 1] if '-X' in args else ''
values = fields()
if method == 'POST' and target.endswith('/check-runs'):
    if os.environ.get('STUB_FAIL_CREATE'):
        print('stub create failure', file=sys.stderr)
        raise SystemExit(24)
    checks = state.setdefault('checks', [])
    next_id = max((int(check['id']) for check in checks), default=0) + 1
    checks.append({'id': next_id, 'name': values.get('name'),
                   'external_id': values.get('external_id'),
                   'app': {'slug': 'github-actions'}, **values})
elif method == 'PATCH' and '/check-runs/' in target:
    if os.environ.get('STUB_FAIL_PATCH'):
        print('stub patch failure', file=sys.stderr)
        raise SystemExit(25)
    check_id = target.rsplit('/', 1)[1]
    for check in state.get('checks', []):
        if str(check.get('id')) == check_id:
            check.update(values)
state_path.write_text(json.dumps(state), encoding='utf-8')
'''

EXPECTED_STEP_MAPPINGS = [
    {
        "name": "Check for the comment artifact",
        "id": "artifact",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            "RUN_ID": "${{ github.event.workflow_run.id }}",
        },
        "run": "set -euo pipefail\n"
        "\n"
        "# The lookup is its own command, and pipefail is load-bearing: "
        "in\n"
        '# `x="$(gh api ... | jq ...)"` the status is jq\'s, so a failed '
        "API\n"
        '# call would print `false` and be reported as "no artifact" — '
        "the\n"
        "# comment silently never appears and nothing goes red.\n"
        "if ! gh api -H 'Cache-Control: no-cache' --paginate \\\n"
        '  "repos/$REPO/actions/runs/$RUN_ID/artifacts" \\\n'
        "  --jq '.artifacts[]' > artifacts.json\n"
        "then\n"
        '  echo "listing the artifacts of run $RUN_ID failed" >&2\n'
        "  exit 1\n"
        "fi\n"
        "# Collect every paginated object into one array before "
        "selecting.\n"
        'present="$(jq -s \'any(.[]; .name == "diff-coverage-comment" '
        "and\n"
        "  .expired == false)' artifacts.json)\"\n"
        'case "$present" in\n'
        "  true) echo 'present=true' >> \"$GITHUB_OUTPUT\" ;;\n"
        "  false)\n"
        "    echo 'diff-coverage-comment is absent; nothing to post.'\n"
        "    ;;\n"
        "  *)\n"
        '    echo "unexpected artifact lookup result: $present" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n",
    },
    {
        "name": "Resolve the target pull request from the event",
        "id": "pr",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            "HEAD_SHA": "${{ github.event.workflow_run.head_sha }}",
            "HEAD_REPO": "${{ "
            "github.event.workflow_run.head_repository.full_name }}",
            "EVENT_NUMBERS": "${{ "
            "toJSON(github.event.workflow_run.pull_requests.*.number) "
            "}}",
        },
        "run": "set -euo pipefail\n"
        "\n"
        "# Deliberately before the download: the destination is settled\n"
        "# while nothing the pull request produced is on disk.\n"
        "numbers=\"$(printf '%s' \"$EVENT_NUMBERS\" | jq -r 'arrays | "
        ".[]' \\\n"
        '  | sort -u)"\n'
        'if [ -z "$numbers" ]; then\n'
        "  # A fork pull request arrives with an empty `pull_requests`, "
        "and\n"
        "  # the base repository does not associate its head commit with "
        "a\n"
        "  # pull request, so ask the head repository which one owns it.\n"
        "  if ! gh api -H 'Cache-Control: no-cache' --paginate \\\n"
        '    "repos/$HEAD_REPO/commits/$HEAD_SHA/pulls" \\\n'
        "    --jq '.[].number' > pulls.txt\n"
        "  then\n"
        '    echo "listing the pull requests for $HEAD_SHA failed" >&2\n'
        "    exit 1\n"
        "  fi\n"
        '  numbers="$(sort -u pulls.txt)"\n'
        "fi\n"
        "count=\"$(printf '%s\\n' \"$numbers\" | awk 'NF' | wc -l)\"\n"
        'if [ "$count" -ne 1 ]; then\n'
        '  echo "expected one pull request for $HEAD_SHA, found $count" '
        ">&2\n"
        "  printf '%s\\n' \"$numbers\" >&2\n"
        "  exit 1\n"
        "fi\n"
        'case "$numbers" in\n'
        "  ''|*[!0-9]*)\n"
        '    echo "resolved a non-numeric pull request: $numbers" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
        "if ! current_sha=\"$(gh api -H 'Cache-Control: no-cache' \\\n"
        '  "repos/$REPO/pulls/$numbers" --jq \'.head.sha\')"\n'
        "then\n"
        '  echo "reading the current head of pull request $numbers '
        'failed" \\\n'
        "    >&2\n"
        "  exit 1\n"
        "fi\n"
        'if [ -z "$current_sha" ]; then\n'
        '  echo "pull request $numbers has no current head SHA" >&2\n'
        "  exit 1\n"
        "fi\n"
        'echo "current_sha=$current_sha" >> "$GITHUB_OUTPUT"\n'
        'if [ "$current_sha" != "$HEAD_SHA" ]; then\n'
        '  echo "run $HEAD_SHA is stale; current pull request head is " '
        "\\\n"
        '    "$current_sha"\n'
        "  echo 'stale=true' >> \"$GITHUB_OUTPUT\"\n"
        "  exit 0\n"
        "fi\n"
        'echo "number=$numbers" >> "$GITHUB_OUTPUT"\n'
        "echo 'stale=false' >> \"$GITHUB_OUTPUT\"\n",
    },
    {
        "name": "Mark missing patch coverage",
        "if": "steps.artifact.outputs.present != 'true' && "
        "steps.pr.outputs.stale != 'true'",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            "HEAD_SHA": "${{ github.event.workflow_run.head_sha }}",
            "PR_NUMBER": "${{ steps.pr.outputs.number }}",
        },
        "run": "set -euo pipefail\n"
        "\n"
        "marker='<!-- daedalus-diff-coverage -->'\n"
        "{\n"
        "  printf '%s\\n\\n' \"$marker\"\n"
        "  printf 'Patch coverage was not measured for commit %s.\\n' \\\n"
        '    "$HEAD_SHA"\n'
        "} > comment.md\n"
        "if ! gh api -H 'Cache-Control: no-cache' --paginate \\\n"
        '  "repos/$REPO/issues/$PR_NUMBER/comments" \\\n'
        "  --jq '.[]' > comments.json\n"
        "then\n"
        '  echo "listing the comments on $PR_NUMBER failed" >&2\n'
        "  exit 1\n"
        "fi\n"
        'existing="$(\n'
        '  jq -s \'map(select(.user.login == "github-actions[bot]" and\n'
        '    ((.body // "") | startswith(\n'
        '      "<!-- daedalus-diff-coverage -->")))) |\n'
        "    .[0].id // empty' comments.json\n"
        ')"\n'
        "revalidate_head() {\n"
        "  if ! current_sha=\"$(gh api -H 'Cache-Control: no-cache' \\\n"
        '    "repos/$REPO/pulls/$PR_NUMBER" --jq \'.head.sha\')"\n'
        "  then\n"
        '    echo "reading the current head of pull request $PR_NUMBER" '
        "\\\n"
        '      "failed" >&2\n'
        "    exit 1\n"
        "  fi\n"
        '  if [ -z "$current_sha" ]; then\n'
        '    echo "pull request $PR_NUMBER has no current head SHA" >&2\n'
        "    exit 1\n"
        "  fi\n"
        '  if [ "$current_sha" != "$HEAD_SHA" ]; then\n'
        '    echo "run $HEAD_SHA is stale; current pull request head is " '
        "\\\n"
        '      "$current_sha"\n'
        "    return 1\n"
        "  fi\n"
        "}\n"
        'case "$existing" in\n'
        "  '') echo 'no patch-coverage marker to update' ;;\n"
        "  *[!0-9]*)\n"
        '    echo "comment id is not only digits: $existing" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "  *)\n"
        "    if ! revalidate_head; then\n"
        "      exit 0\n"
        "    fi\n"
        '    gh api -X PATCH "repos/$REPO/issues/comments/$existing" \\\n'
        "      -F body=@comment.md >/dev/null\n"
        '    echo "marked patch coverage as unavailable for $HEAD_SHA"\n'
        "    ;;\n"
        "esac\n",
    },
    {
        "name": "Download the comment artifact",
        "if": "steps.artifact.outputs.present == 'true' && "
        "steps.pr.outputs.stale != 'true'",
        "uses": (
            "actions/download-artifact@"
            "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
        ),
        "with": {
            "name": "diff-coverage-comment",
            "run-id": "${{ github.event.workflow_run.id }}",
            "github-token": "${{ github.token }}",
        },
    },
    {
        "name": "Post or update the pull request comment",
        "if": "steps.artifact.outputs.present == 'true' && "
        "steps.pr.outputs.stale != 'true'",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            "HEAD_SHA": "${{ github.event.workflow_run.head_sha }}",
            "PR_NUMBER": "${{ steps.pr.outputs.number }}",
        },
        "run": "set -euo pipefail\n"
        "\n"
        "test -f body.md\n"
        "test -f pr-number.txt\n"
        "\n"
        "# Checked, never obeyed. See the header: the producer is "
        "untrusted.\n"
        'claimed="$(cat pr-number.txt)"\n'
        'if [ "$claimed" != "$PR_NUMBER" ]; then\n'
        "  echo \"the artifact names pull request '$claimed' but the "
        'run" \\\n'
        "    \"belongs to '$PR_NUMBER'; refusing to post\" >&2\n"
        "  exit 1\n"
        "fi\n"
        "\n"
        "# The comment API caps a body at 65536 characters. A silently\n"
        "# truncated coverage report is a wrong coverage report, so an\n"
        "# oversized body is refused rather than trimmed to fit.\n"
        'size="$(wc -c < body.md)"\n'
        'if [ "$size" -gt 60000 ]; then\n'
        '  echo "body.md is $size bytes, past the comment limit" >&2\n'
        "  exit 1\n"
        "fi\n"
        "\n"
        "marker='<!-- daedalus-diff-coverage -->'\n"
        "{\n"
        "  printf '%s\\n\\n' \"$marker\"\n"
        "  printf 'Patch coverage for commit %s.\\n\\n' \"$HEAD_SHA\"\n"
        "  cat body.md\n"
        "} > comment.md\n"
        "if ! gh api -H 'Cache-Control: no-cache' --paginate \\\n"
        '  "repos/$REPO/issues/$PR_NUMBER/comments" \\\n'
        "  --jq '.[]' > comments.json\n"
        "then\n"
        '  echo "listing the comments on $PR_NUMBER failed" >&2\n'
        "  exit 1\n"
        "fi\n"
        "# Slurped, so pagination cannot yield one id per page, and the\n"
        "# body is defaulted, so a null one is not an abort mid-select.\n"
        'existing="$(\n'
        '  jq -s \'map(select(.user.login == "github-actions[bot]" and\n'
        '    ((.body // "") | startswith(\n'
        '      "<!-- daedalus-diff-coverage -->")))) |\n'
        "    .[0].id // empty' comments.json\n"
        ')"\n'
        "revalidate_head() {\n"
        "  if ! current_sha=\"$(gh api -H 'Cache-Control: no-cache' \\\n"
        '    "repos/$REPO/pulls/$PR_NUMBER" --jq \'.head.sha\')"\n'
        "  then\n"
        '    echo "reading the current head of pull request $PR_NUMBER" '
        "\\\n"
        '      "failed" >&2\n'
        "    exit 1\n"
        "  fi\n"
        '  if [ -z "$current_sha" ]; then\n'
        '    echo "pull request $PR_NUMBER has no current head SHA" >&2\n'
        "    exit 1\n"
        "  fi\n"
        '  if [ "$current_sha" != "$HEAD_SHA" ]; then\n'
        '    echo "run $HEAD_SHA is stale; current pull request head is " '
        "\\\n"
        '      "$current_sha"\n'
        "    return 1\n"
        "  fi\n"
        "}\n"
        'case "$existing" in\n'
        "  '')\n"
        "    if ! revalidate_head; then\n"
        "      exit 0\n"
        "    fi\n"
        '    gh api -X POST "repos/$REPO/issues/$PR_NUMBER/comments" \\\n'
        "      -F body=@comment.md >/dev/null\n"
        "    echo 'posted a new comment'\n"
        "    ;;\n"
        "  *[!0-9]*)\n"
        '    echo "comment id is not only digits: $existing" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "  *)\n"
        "    if ! revalidate_head; then\n"
        "      exit 0\n"
        "    fi\n"
        '    gh api -X PATCH "repos/$REPO/issues/comments/$existing" \\\n'
        "      -F body=@comment.md >/dev/null\n"
        '    echo "updated comment $existing"\n'
        "    ;;\n"
        "esac\n",
    },
]


def publication_contract(tmp, workflow_reader, extract_block, shell_runner,
                         write_executable):
    """Exercise the final publication block against a stateful API double."""
    def run(label='check', state=None, status='success', head_sha='a' * 40,
            run_url='https://github.com/owner/repo/actions/runs/7',
            extra_env=None):
        workdir = Path(tmp) / label
        (workdir / 'bin').mkdir(parents=True, exist_ok=True)
        write_executable(workdir / 'bin' / 'gh', GH_CHECK_STUB)
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
        if extra_env:
            env.update(extra_env)
        script = extract_block(workflow_reader(), 'Publish coverage check')
        result = shell_runner(workdir, script, env)
        return result, json.loads(state_path.read_text(encoding='utf-8')), \
            calls, script

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

    workflow = workflow_reader()
    steps = step_mappings(workflow, 'comment')
    assert steps[-1]['name'] == 'Publish coverage check', steps
    step = steps[-1]
    assert step['if'] == 'always()', step
    assert step['env'] == {
        'GH_TOKEN': '${{ github.token }}',
        'REPO': '${{ github.repository }}',
        'HEAD_SHA': '${{ github.event.workflow_run.head_sha }}',
        'RUN_URL': '${{ github.event.workflow_run.html_url }}',
        'STATUS': '${{ github.event.workflow_run.conclusion }}',
    }, step
    script = step['run']
    assert "-f name='coverage comment'" in script, script
    assert 'external_id="daedalus-coverage-comment/v1/$HEAD_SHA"' in script
    assert 'head_sha="$HEAD_SHA"' in script
    assert 'details_url="$RUN_URL"' in script
    assert 'case "$STATUS"' in script
    assert not any(name in script for name in (
        'body.md', 'pr-number.txt', 'artifact', 'github.workspace'))

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
    assert list_call[:3] == ['api', '-H', 'Cache-Control: no-cache'], list_call
    assert '--paginate' in list_call and '--jq' in list_call, list_call

    for status in ('failure', 'cancelled'):
        result, state, calls, _script = run(label=status, status=status)
        assert result.returncode == 0, (status, result.stdout, result.stderr)
        assert state['checks'][0]['conclusion'] == status, state
        assert len(write_list(calls)) == 1, calls.read_text(
            encoding='utf-8')

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
        'success', 'success', 'failure', 'failure'], updated

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


EXPECTED_PRIVILEGED_JOB_MAPPING = {
    'if': "github.event.workflow_run.event == 'pull_request'",
    'runs-on': 'ubuntu-latest',
    'timeout-minutes': '10',
    'steps': EXPECTED_STEP_MAPPINGS,
}

EXPECTED_WORKFLOW_MAPPING = {
    'name': 'coverage comment',
    'on': {
        'workflow_run': {
            'workflows': ['tests'],
            'types': ['completed'],
        },
    },
    'permissions': {
        'pull-requests': 'write',
        'actions': 'read',
        'checks': 'write',
    },
    'concurrency': {
        'group': (
            'coverage-comment-${{ '
            'github.event.workflow_run.head_repository.full_name }}-${{ '
            'github.event.workflow_run.head_branch }}'
        ),
        'cancel-in-progress': 'true',
    },
    'jobs': {
        'comment': EXPECTED_PRIVILEGED_JOB_MAPPING,
    },
}
