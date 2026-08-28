#!/usr/bin/env python3
"""Executable gh boundary for pull-request gate tests."""


TIMELINE_QUERY = (
    '.[] | select(.event == "closed")\n'
    '      | (.actor.login // "__unreadable__")')
COMMENTS_QUERY = (
    '.[]\n      | select(.user.login == "github-actions[bot]")\n'
    '      | select(.body | contains("<!-- pr-gate: close -->"))\n'
    '      | .id')
ISSUE_QUERY = (
    'if has("pull_request") then empty else\n'
    '      (.assignees[].login | "assignee:\\(.)") end')


GH_STUB = r"""#!/usr/bin/env python3
import json, os, pathlib, re, sys
fixtures = json.loads(pathlib.Path(os.environ['STUB_ISSUES']).read_text())
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
previous = [json.loads(line) for line in calls.read_text().splitlines()]
TIMELINE_QUERY = (
    '.[] | select(.event == "closed")\n'
    '      | (.actor.login // "__unreadable__")')
COMMENTS_QUERY = (
    '.[]\n      | select(.user.login == "github-actions[bot]")\n'
    '      | select(.body | contains("<!-- pr-gate: close -->"))\n'
    '      | .id')
ISSUE_QUERY = (
    'if has("pull_request") then empty else\n'
    '      (.assignees[].login | "assignee:\\(.)") end')
recorded = []
for arg in argv:
    if arg.startswith(('body=@', 'text=@')):
        body = pathlib.Path(arg[6:]).read_text(encoding='utf-8')
        recorded.append(arg[:5] + body)
    else:
        recorded.append(arg)
with calls.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(recorded) + chr(10))
if not argv or argv[0] != 'api':
    print('unsupported gh command', file=sys.stderr)
    raise SystemExit(2)
endpoint = next((arg for arg in argv
                 if arg == 'markdown' or arg.startswith('repos/')), '')
def unsupported():
    print('unsupported gh api call', file=sys.stderr)
    raise SystemExit(2)
if endpoint == 'markdown':
    submitted = pathlib.Path(argv[3][6:]).read_text(encoding='utf-8') \
        if len(argv) > 3 and argv[3].startswith('text=@') else ''
    expected = os.environ['STUB_EXPECTED_BODY']
    marker = re.fullmatch(
        re.escape(expected)
        + r'\n\n(pr-gate-sentinel-[0-9a-f]{64})\n', submitted)
    if (len(argv) != 8 or argv[:3] != ['api', 'markdown', '-F']
            or not argv[3].startswith('text=@')
            or argv[4] != '-f' or argv[5] != 'mode=gfm'
            or argv[6] != '-f'
            or argv[7] != 'context=' + os.environ['STUB_EXPECTED_REPO']
            or (submitted != expected and marker is None)):
        unsupported()
    status = int(os.environ.get('STUB_RENDER_STATUS', '200'))
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    rendered = pathlib.Path(os.environ['STUB_RENDERED_HTML']).read_text()
    if marker and os.environ.get('STUB_RENDER_COMPLETE', '1') == '1':
        token = marker.group(1)
        mangle = os.environ.get('STUB_SENTINEL_MANGLE', '')
        if mangle == 'comment':
            sentinel = (f'<p dir="auto">{token[:30]}'
                        f'<!-- injected -->{token[30:]}</p>')
        elif mangle == 'element':
            sentinel = (f'<p dir="auto">{token[:30]}'
                        f'<em>{token[30:]}</em></p>')
        else:
            sentinel = f'<p dir="auto">{token}</p>'
        footnotes = '\n<section data-footnotes="" class="footnotes">'
        if footnotes in rendered:
            rendered = rendered.replace(
                footnotes, f'\n{sentinel}{footnotes}', 1)
        else:
            rendered = f'{rendered}\n{sentinel}'
        rendered += os.environ.get('STUB_RENDER_AFTER_SENTINEL', '')
    print(rendered, end='')
    raise SystemExit(0)
if '/timeline?' in endpoint:
    if (len(argv) != 5 or argv[:2] != ['api', '--paginate']
            or argv[2] != endpoint or argv[3] != '--jq'):
        unsupported()
    status = fixtures.get('_timeline_status', 200)
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    if query != TIMELINE_QUERY:
        unsupported()
    for event in fixtures.get('_timeline', []):
        if event.get('event') != 'closed':
            continue
        login = event.get('actor', {}).get('login')
        if login is None:
            login = '__unreadable__'
        print(login or '')
    raise SystemExit(0)
if '/comments?' in endpoint:
    if (len(argv) != 5 or argv[:2] != ['api', '--paginate']
            or argv[2] != endpoint or argv[3] != '--jq'):
        unsupported()
    status = fixtures.get('_comments_status', 200)
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    if query != COMMENTS_QUERY:
        unsupported()
    for comment in fixtures.get('_comments', []):
        if comment['user']['login'] != 'github-actions[bot]':
            continue
        if '<!-- pr-gate: close -->' not in comment['body']:
            continue
        print(comment['id'])
    raise SystemExit(0)
parts = endpoint.split('/')
if len(parts) == 5 and parts[-2] == 'issues' and parts[-1].isdigit():
    if (len(argv) != 5 or argv[1] not in ('--include', '-i')
            or argv[2] != endpoint or argv[3] != '--jq'):
        unsupported()
    issue = fixtures.get(parts[-1])
    status = 404 if issue is None else issue.get('_http_status', 200)
    if '--include' in argv or '-i' in argv:
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
    if query != ISSUE_QUERY:
        unsupported()
    if 'pull_request' in issue:
        raise SystemExit(0)
    for assignee in issue['assignees']:
        print('assignee:' + assignee['login'])
    raise SystemExit(0)
if endpoint.endswith('/comments'):
    if (len(argv) != 5 or argv[:2] != ['api', endpoint]
            or argv[2] != '-F' or not argv[3].startswith('body=@')
            or argv[4] != '--silent'):
        unsupported()
    if os.environ.get('STUB_COMMENT_STATUS'):
        status = os.environ['STUB_COMMENT_STATUS']
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
if re.fullmatch(r'repos/[^/]+/[^/]+/pulls/[0-9]+', endpoint):
    method = 'GET'
    request = argv
    if len(argv) >= 3 and argv[1] in ('-X', '--method'):
        method = argv[2]
        request = [argv[0], *argv[3:]]
    if method == 'GET' and request == ['api', endpoint]:
        reads = sum(call == ['api', endpoint] for call in previous)
        statuses = fixtures.get('_pull_statuses')
        status = statuses[min(reads, len(statuses) - 1)] \
            if statuses else fixtures.get('_pull_status', 200)
        if status != 200:
            print(f'gh: HTTP {status}', file=sys.stderr)
            raise SystemExit(1)
        snapshots = fixtures['_pull_snapshots']
        print(json.dumps(snapshots[min(reads, len(snapshots) - 1)]))
        raise SystemExit(0)
    if (method == 'PATCH' and request[1] == endpoint
            and request[2:3] == ['-f']
            and request[3:4] in (['state=open'], ['state=closed'])
            and request[4:] == ['--silent']):
        raise SystemExit(0)
unsupported()
"""
