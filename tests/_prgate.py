#!/usr/bin/env python3
"""Shared fixtures for pull-request body and workflow gate tests."""
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


PR_BODY = _util.load(ROOT / 'scripts' / 'ci' / 'pr_body.py')
SECTION = '## Related Issues and Pull Requests\n'
TEMPLATE = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
    encoding='utf-8')
BOT = 'github-actions[bot]'
CLOSE_MARKER = '<!-- pr-gate: close -->'
MARKER_COMMENT = [{'id': 7, 'user': {'login': BOT}, 'body': CLOSE_MARKER}]

_GH_STUB = r"""#!/usr/bin/env python3
import json, os, pathlib, re, sys
fixtures = json.loads(pathlib.Path(os.environ['STUB_ISSUES']).read_text())
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
recorded = []
for arg in argv:
    if arg.startswith('body=@'):
        body = pathlib.Path(arg[6:]).read_text(encoding='utf-8')
        recorded.append('body=' + body)
    else:
        recorded.append(arg)
with calls.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(recorded) + chr(10))
endpoint = next((arg for arg in argv if arg.startswith('repos/')), '')
if '/timeline?' in endpoint:
    status = fixtures.get('_timeline_status', 200)
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    for event in fixtures.get('_timeline', []):
        if 'event == "closed"' in query and event.get('event') != 'closed':
            continue
        login = event.get('actor', {}).get('login')
        if login is None and '// "__unreadable__"' in query:
            login = '__unreadable__'
        print(login or '')
    raise SystemExit(0)
if '/comments?' in endpoint:
    status = fixtures.get('_comments_status', 200)
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    user = re.search(r'\.user\.login *== *"([^"]+)"', query)
    marker = re.search(r'contains\("([^"]+)"\)', query)
    for comment in fixtures.get('_comments', []):
        if user and comment['user']['login'] != user.group(1):
            continue
        if marker and marker.group(1) not in comment['body']:
            continue
        print(comment['id'])
    raise SystemExit(0)
parts = endpoint.split('/')
if len(parts) == 5 and parts[-2] == 'issues' and parts[-1].isdigit():
    issue = fixtures.get(parts[-1])
    status = 404 if issue is None else issue.get('_http_status', 200)
    if '--include' in argv:
        reason = {200: 'OK', 404: 'Not Found'}.get(status, 'Error')
        print(f'HTTP/2.0 {status} {reason}')
        print('cache-control: private, max-age=60')
        print('content-type: application/json; charset=utf-8')
        print('x-github-media-type: github.v3; format=json')
        print()
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    if 'pull_request' in issue and 'has("pull_request")' in query:
        raise SystemExit(0)
    if '.assignees' in query:
        for assignee in issue['assignees']:
            prefix = 'assignee:' if 'assignee:' in query else ''
            print(prefix + assignee['login'])
    else:
        print(json.dumps(issue))
if endpoint.endswith('/comments') and os.environ.get('STUB_COMMENT_STATUS'):
    status = os.environ['STUB_COMMENT_STATUS']
    print(f'gh: HTTP {status}', file=sys.stderr)
    raise SystemExit(1)
"""

_CRLF_PYTHON_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == scripts/ci/pr_body.py ||
      "${1:-}" == */scripts/ci/pr_body.py ]]; then
  "$STUB_REAL_PYTHON" "$@" |
    "$STUB_REAL_PYTHON" -c 'import sys
data = sys.stdin.buffer.read().replace(b"\r\n", b"\n")
sys.stdout.buffer.write(data.replace(b"\n", b"\r\n"))'
else
  exec "$STUB_REAL_PYTHON" "$@"
fi
"""


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'pr-gate.yml').read_text(
        encoding='utf-8')


def _workflow_script():
    """The pr-gate.yml run block, dedented and ready for Bash."""
    _, marker, after = _workflow().partition('        run: |\n')
    assert marker, 'pr-gate.yml has no literal run block'
    first = after.splitlines()[0]
    indent = len(first) - len(first.lstrip())
    assert first.strip() and indent, 'pr-gate.yml run block has no body'
    lines = []
    for line in after.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        lines.append(line[indent:])
    return chr(10).join(lines)


def _issue(*assignees, pull_request=False):
    issue = {'assignees': [{'login': login} for login in assignees]}
    if pull_request:
        issue['pull_request'] = {'url': 'https://github.com/pulls/0'}
    return issue


def _run_workflow(
        tmp, body, issues, actor='alice', pr='99', repo='owner/repo',
        parser_crlf=False, comment_status=None, pull=None, history=None):
    """Execute the real workflow shell against the controlled gh boundary."""
    pull = pull or {}
    history = history or {}
    state = pull.get('state', 'open')
    merged = pull.get('merged', 'false')
    timeline = history.get('timeline', ())
    comments = history.get('comments', ())
    timeline_status = history.get('timeline_status')
    comments_status = history.get('comments_status')
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the pull-request body gate'
    workdir = Path(tmp) / 'workflow'
    (workdir / 'bin').mkdir(parents=True)
    stub = workdir / 'bin' / 'gh'
    stub.write_text(_GH_STUB, encoding='utf-8')
    stub.chmod(0o755)
    if parser_crlf:
        python_stub = workdir / 'bin' / 'python3'
        python_stub.write_text(_CRLF_PYTHON_STUB, encoding='utf-8')
        python_stub.chmod(0o755)
    fixture_path = workdir / 'issues.json'
    fixtures = dict(issues)
    fixtures['_timeline'] = list(timeline)
    fixtures['_comments'] = list(comments)
    if timeline_status is not None:
        fixtures['_timeline_status'] = timeline_status
    if comments_status is not None:
        fixtures['_comments_status'] = comments_status
    fixture_path.write_text(json.dumps(fixtures), encoding='utf-8')
    calls_path = workdir / 'calls.jsonl'
    calls_path.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'STUB_ISSUES': str(fixture_path),
        'STUB_CALLS': str(calls_path),
        'STUB_REAL_PYTHON': sys.executable,
        'GH_TOKEN': 'stub',
        'REPO': repo,
        'PR': pr,
        'ACTOR': actor,
        'BODY': body,
        'STATE': state,
        'MERGED': merged,
    }
    if comment_status is not None:
        env['STUB_COMMENT_STATUS'] = str(comment_status)
    result = subprocess.run(
        [bash, '-c', _workflow_script()], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    calls = [json.loads(line) for line in calls_path.read_text(
        encoding='utf-8').splitlines()]
    return calls, result


def _body_from(call):
    return next(arg[5:] for arg in call if arg.startswith('body='))


def _assert_no_mutation(calls):
    arguments = [arg for call in calls for arg in call]
    assert not any(arg.startswith(('body=', 'state='))
                   for arg in arguments), calls


def _assert_commented_then_closed(
        calls, *reasons, actor='alice', pr='99', repo='owner/repo'):
    comment_endpoint = f'repos/{repo}/issues/{pr}/comments'
    close_endpoint = f'repos/{repo}/pulls/{pr}'
    comment_calls = [call for call in calls if comment_endpoint in call]
    close_calls = [call for call in calls if close_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert len(close_calls) == 1, calls
    comment = comment_calls[0]
    close = close_calls[0]
    assert calls.index(comment) < calls.index(close), calls
    body = _body_from(comment)
    assert body.startswith(f'@{actor} — closing this automatically'), body
    assert CLOSE_MARKER in body, body
    assert '-F' in comment, comment
    assert re.search(r'#[0-9]', body) is None, body
    for reason in reasons:
        assert reason in body, body
    assert '**Related Issues and Pull Requests**' in body, body
    assert 'match the pull request template' in body, body
    assert '`Fixes #<issue>`' in body, body
    assert '/claim' in body, body
    assert 'reopen it automatically' in body, body
    assert 'Then reopen this same pull request.' not in body, body
    assert '-X' in close and close[close.index('-X') + 1] == 'PATCH', close
    assert 'state=closed' in close, close


def _assert_commented_then_reopened(
        calls, actor='alice', pr='99', repo='owner/repo'):
    comment = next(call for call in calls
                   if f'repos/{repo}/issues/{pr}/comments' in call)
    reopen = next(call for call in calls
                  if f'repos/{repo}/pulls/{pr}' in call)
    assert calls.index(comment) < calls.index(reopen), calls
    body = _body_from(comment)
    assert body.startswith(f'@{actor} —'), body
    assert 'reopening it automatically' in body, body
    assert '-X' in reopen and reopen[reopen.index('-X') + 1] == 'PATCH'
    assert 'state=open' in reopen, reopen


def _layout_body(*sections):
    return '\n\n'.join(
        f'## {title}\n{content}' for title, content in sections) + '\n'


def _valid_body(references='Fixes #101'):
    return _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', references),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))


def _closed_by(login):
    return [{'event': 'closed', 'actor': {'login': login}}]
