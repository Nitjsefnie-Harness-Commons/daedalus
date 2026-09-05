"""Exact decoded mappings and doubles for the coverage-comment workflow."""
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
method_flag = '-X' if '-X' in args else '--method'
method = args[args.index(method_flag) + 1] if method_flag in args else ''
values = fields()
if target.endswith('/check-runs') and method in ('', 'GET'):
    if os.environ.get('STUB_FAIL_LIST'):
        print('stub list failure', file=sys.stderr)
        raise SystemExit(23)
    if os.environ.get('STUB_MALFORMED_JSON'):
        print('{malformed', end='')
        raise SystemExit(0)
    checks = state.get('checks', [])
    if values.get('filter') != 'all':
        checks = checks[-1:]
    for check in checks:
        if os.environ.get('STUB_CRLF_IDS'):
            sys.stdout.buffer.write(
                (json.dumps(check) + '\r\n').encode('utf-8'))
        else:
            print(json.dumps(check))
    raise SystemExit(0)
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
    if 'head_sha' in values:
        print('head_sha is not valid on PATCH', file=sys.stderr)
        raise SystemExit(26)
    check_id = target.rsplit('/', 1)[1]
    for check in state.get('checks', []):
        if str(check.get('id')) == check_id:
            check.update(values)
state_path.write_text(json.dumps(state), encoding='utf-8')
'''
CRLF_JQ_STUB = r'''#!/usr/bin/env python3
import os
import subprocess
import sys
raw = os.environ.get('STUB_RAW_IDS')
if raw is not None:
    output = raw.encode('utf-8') + b'\n'
else:
    result = subprocess.run([os.environ['REAL_JQ'], *sys.argv[1:]],
                            capture_output=True)
    output = result.stdout
    if os.environ.get('STUB_CRLF_IDS'):
        output = output.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    sys.stderr.buffer.write(result.stderr)
    if result.returncode:
        raise SystemExit(result.returncode)
sys.stdout.buffer.write(output)
'''
EXPECTED_STEP_MAPPINGS = (
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
        "    ;;\nesac\n",
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
    {'name': 'Mark missing patch coverage',
     'if': "steps.artifact.outputs.present != 'true' && "
           "steps.pr.outputs.stale != 'true'",
     'env': {'GH_TOKEN': '${{ github.token }}',
             'REPO': '${{ github.repository }}',
             'HEAD_SHA': '${{ github.event.workflow_run.head_sha }}',
             'PR_NUMBER': '${{ steps.pr.outputs.number }}',
             'RUN_ID': '${{ github.event.workflow_run.id }}'},
     'run': 'set -euo pipefail\n'
            '\n'
            'exit_code=1\n'
            "if ! gh api -H 'Cache-Control: no-cache' --paginate \\\n"
            '  "repos/$REPO/actions/runs/$RUN_ID/jobs" \\\n'
            "  --jq '.jobs[]' > jobs.json\n"
            'then\n'
            '  echo "listing the jobs of run $RUN_ID failed" >&2\n'
            '  exit 1\n'
            'fi\n'
            'if jq -se \'any(.[]; .name == "coverage" and\n'
            '  .conclusion == "skipped")\' jobs.json >/dev/null\n'
            'then\n'
            '  echo \'skipped=true\' >> "$GITHUB_OUTPUT"\n'
            '  exit_code=0\n'
            'fi\n'
            '\n'
            "marker='<!-- daedalus-diff-coverage -->'\n"
            '{\n'
            '  printf \'%s\\n\\n\' "$marker"\n'
            "  printf 'Patch coverage was not measured for commit "
            "%s.\\n' \\\n"
            '    "$HEAD_SHA"\n'
            '} > comment.md\n'
            "if ! gh api -H 'Cache-Control: no-cache' --paginate \\\n"
            '  "repos/$REPO/issues/$PR_NUMBER/comments" \\\n'
            "  --jq '.[]' > comments.json\n"
            'then\n'
            '  echo "listing the comments on $PR_NUMBER failed" >&2\n'
            '  exit 1\n'
            'fi\n'
            'existing="$(\n'
            '  jq -s \'map(select(.user.login == "github-actions[bot]" '
            'and\n'
            '    ((.body // "") | startswith(\n'
            '      "<!-- daedalus-diff-coverage -->")))) |\n'
            "    .[0].id // empty' comments.json\n"
            ')"\n'
            'revalidate_head() {\n'
            '  if ! current_sha="$(gh api -H \'Cache-Control: no-cache\' '
            '\\\n'
            '    "repos/$REPO/pulls/$PR_NUMBER" --jq \'.head.sha\')"\n'
            '  then\n'
            '    echo "reading the current head of pull request '
            '$PR_NUMBER" \\\n'
            '      "failed" >&2\n'
            '    exit 1\n'
            '  fi\n'
            '  if [ -z "$current_sha" ]; then\n'
            '    echo "pull request $PR_NUMBER has no current head SHA" '
            '>&2\n'
            '    exit 1\n'
            '  fi\n'
            '  if [ "$current_sha" != "$HEAD_SHA" ]; then\n'
            '    echo "run $HEAD_SHA is stale; current pull request head '
            'is " \\\n'
            '      "$current_sha"\n'
            '    return 1\n'
            '  fi\n'
            '}\n'
            'case "$existing" in\n'
            "  '') echo 'no patch-coverage marker to update'; exit "
            '"$exit_code" ;;\n'
            '  *[!0-9]*)\n'
            '    echo "comment id is not only digits: $existing" >&2\n'
            '    exit 1\n'
            '    ;;\n'
            '  *)\n'
            '    if ! revalidate_head; then\n'
            '      exit 0\n'
            '    fi\n'
            '    gh api -X PATCH "repos/$REPO/issues/comments/$existing" '
            '\\\n'
            '      -F body=@comment.md >/dev/null\n'
            '    echo "marked patch coverage as unavailable for '
            '$HEAD_SHA"\n'
            '    exit "$exit_code"\n'
            '    ;;\n'
            'esac\n',
     'id': 'missing'},
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
)


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


def complete_workflow_expectations(publication_step):
    steps = EXPECTED_STEP_MAPPINGS + (publication_step,)
    job = {**EXPECTED_PRIVILEGED_JOB_MAPPING, 'steps': list(steps)}
    workflow = {
        **EXPECTED_WORKFLOW_MAPPING,
        'jobs': {**EXPECTED_WORKFLOW_MAPPING['jobs'], 'comment': job},
    }
    return steps, job, workflow


GH_ARTIFACT_STUB = r"""#!/usr/bin/env python3
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


GH_COMMENT_STUB = r"""#!/usr/bin/env python3
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
    elif target.endswith('/jobs'):
        assert target == 'repos/owner/repo/actions/runs/123/jobs', target
        assert '--paginate' in args, args
        assert 'Cache-Control: no-cache' in args, args
        assert expression == '.jobs[]', expression
        if os.environ.get('STUB_FAIL_JOBS'):
            raise SystemExit('stub jobs lookup failure')
        for job in json.loads(os.environ.get('STUB_JOBS', '[]')):
            print(json.dumps(job))
    elif '/commits/' in target:
        raise SystemExit('unexpected commits query: ' + target)
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
