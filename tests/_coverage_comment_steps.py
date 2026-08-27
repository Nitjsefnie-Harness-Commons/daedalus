"""Exact decoded mappings for every privileged coverage-comment step."""

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
            "37930b1c2abaa49bbe596cd826c3c89aef350131"
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
