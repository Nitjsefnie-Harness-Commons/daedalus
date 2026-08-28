#!/usr/bin/env python3
"""Shared fixtures for pull-request body and workflow gate tests."""
import html
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

GITHUB_ISSUE_101 = (
    '<a class="issue-link js-issue-link" '
    'data-error-text="Failed to load title" data-id="5232098400" '
    'data-permission-text="Title is private" '
    'data-url="https://github.com/Nitjsefnie-Harness-Commons/'
    'daedalus/issues/101" data-hovercard-type="issue" '
    'data-hovercard-url="/Nitjsefnie-Harness-Commons/daedalus/'
    'issues/101/hovercard" href="https://github.com/'
    'Nitjsefnie-Harness-Commons/daedalus/issues/101">#101</a>')

GITHUB_FOOTNOTE_MARKDOWN = (
    '## Summary\nOne sentence.\n\n'
    '## Related Issues and Pull Requests\nFixes #101\n\n'
    '## Changes\nThe behavior is pinned.[^1]\n\n'
    '[^1]: By a focused regression test.\n\n'
    '## Testing\nRan the suite.')
GITHUB_FOOTNOTE_HTML = (
    '<h2 dir="auto">Summary</h2>\n'
    '<p dir="auto">One sentence.</p>\n'
    '<h2 dir="auto">Related Issues and Pull Requests</h2>\n'
    f'<p dir="auto">Fixes {GITHUB_ISSUE_101}</p>\n'
    '<h2 dir="auto">Changes</h2>\n'
    '<p dir="auto">The behavior is pinned.<sup><a href="'
    '#user-content-fn-1-994fe5c1980496529dc0a26cdffba501" id="'
    'user-content-fnref-1-994fe5c1980496529dc0a26cdffba501" '
    'data-footnote-ref="" aria-describedby="footnote-label">1</a>'
    '</sup></p>\n<h2 dir="auto">Testing</h2>\n'
    '<p dir="auto">Ran the suite.</p>\n'
    '<section data-footnotes="" class="footnotes"><h2 '
    'id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>\n'
    '<ol dir="auto">\n<li id="user-content-fn-1-'
    '994fe5c1980496529dc0a26cdffba501">\n'
    '<p dir="auto">By a focused regression test. <a href="'
    '#user-content-fnref-1-994fe5c1980496529dc0a26cdffba501" '
    'data-footnote-backref="" aria-label="Back to reference 1" '
    'class="data-footnote-backref">↩</a></p>\n</li>\n</ol>\n'
    '</section>')

# These fragments are responses captured from GitHub's /markdown endpoint in
# GFM mode with Nitjsefnie-Harness-Commons/daedalus as the context.
GITHUB_HTML = {
    'nested_list': (
        '<ul dir="auto">\n<li>Tracking:\n<ul dir="auto">\n'
        f'<li>Fixes {GITHUB_ISSUE_101}</li>\n'
        '</ul>\n</li>\n</ul>'),
    'paragraph_continuation': (
        '<p dir="auto">This fixes the tracked bug,<br>\n'
        f'Fixes {GITHUB_ISSUE_101}</p>'),
    'escaped_backticks': (
        '<p dir="auto">Literal backticks: `Fixes '
        f'{GITHUB_ISSUE_101}`</p>'),
    'angle_prose': (
        '<p dir="auto">Fixes &lt;issue '
        f'{GITHUB_ISSUE_101}&gt;</p>'),
    'undefined_reference': (
        '<p dir="auto">[documentation][issue '
        f'{GITHUB_ISSUE_101}]</p>'),
    'malformed_inline': (
        '<p dir="auto">[documentation](issue '
        f'{GITHUB_ISSUE_101})</p>'),
    'balanced_destination': (
        '<p dir="auto"><a href="https://example.com/a_(b)#101" '
        'rel="nofollow">documentation</a></p>'),
    'quoted_attribute': (
        '<p dir="auto"><a title="1 &gt; 0" href="#101">'
        'documentation</a></p>'),
    'multiline_attribute': (
        '<p dir="auto"><a href="#101" title="documentation">'
        'docs</a></p>'),
    'image_destination': (
        '<p dir="auto"><a target="_blank" rel="noopener noreferrer" '
        'href=""><img src="" alt="documentation" '
        'style="max-width: 100%;"></a></p>'),
    'empty_image': (
        '<p dir="auto"><a target="_blank" rel="noopener noreferrer" '
        'href=""><img src="" alt="" '
        'style="max-width: 100%;"></a></p>'),
    'kbd_block': '<kbd>\n## literal heading\n</kbd>',
    'empty_list': '<ul dir="auto">\n<li></li>\n</ul>',
    'empty_ordered': '<ol dir="auto">\n<li></li>\n</ol>',
    'empty_quote': '<blockquote>\n</blockquote>',
    'link_definition': '',
    'inline_code': (
        '<p dir="auto"><code class="notranslate">Fixes #101'
        '</code></p>'),
    'fenced_code': (
        '<pre class="notranslate"><code class="notranslate">'
        'Fixes #101\n</code></pre>'),
    'indented_code': (
        '<pre class="notranslate"><code class="notranslate">'
        'Fixes #101\n</code></pre>'),
    'html_attribute': '<p dir="auto"><a href="#101">docs</a></p>',
    'inline_instruction': (
        '<ul dir="auto">\n<li>Changed <code class="notranslate">'
        '&lt;!-- required: bullet list of concrete changes — files, '
        'modules, behavior. --&gt;</code> to prose.</li>\n</ul>'),
    'indented_instruction': (
        '<pre class="notranslate"><code class="notranslate">'
        '&lt;!-- required: bullet list of concrete changes — files, '
        'modules, behavior. --&gt;\n</code></pre>\n<ul dir="auto">\n'
        '<li>A visible change</li>\n</ul>'),
    'named_anchor': '<p dir="auto"><a name="user-content-spot"></a></p>',
    'entity_instruction': (
        '<ul dir="auto">\n'
        '<li>The literal template marker is &lt;!-- optional --&gt;.</li>\n'
        '<li>One change.</li>\n</ul>'),
    'zero_size_image': (
        '<p dir="auto"><a target="_blank" rel="noopener noreferrer" '
        'href=""><img alt="" width="0" height="0" '
        'style="max-width: 100%;"></a></p>'),
}

GITHUB_MARKDOWN = {
    'nested_list': '- Tracking:\n    - Fixes #101',
    'paragraph_continuation': (
        'This fixes the tracked bug,\n    Fixes #101'),
    'escaped_backticks': r'Literal backticks: \`Fixes #101\`',
    'angle_prose': 'Fixes <issue #101>',
    'undefined_reference': '[documentation][issue #101]',
    'malformed_inline': '[documentation](issue #101)',
    'balanced_destination': (
        '[documentation](https://example.com/a_(b)#101)'),
    'quoted_attribute': (
        '<a title="1 > 0" href="#101">documentation</a>'),
    'multiline_attribute': (
        '<a\n href="#101"\n title="documentation">docs</a>'),
    'image_destination': '![documentation](#101)',
    'kbd_block': '<kbd>\n## literal heading\n</kbd>',
}

_GH_STUB = r"""#!/usr/bin/env python3
import json, os, pathlib, re, sys
fixtures = json.loads(pathlib.Path(os.environ['STUB_ISSUES']).read_text())
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
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
        sentinel = f'<p dir="auto">{marker.group(1)}</p>'
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
    for event in fixtures.get('_timeline', []):
        if 'event == "closed"' in query and event.get('event') != 'closed':
            continue
        login = event.get('actor', {}).get('login')
        if login is None and '// "__unreadable__"' in query:
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
    if 'pull_request' in issue and 'has("pull_request")' in query:
        raise SystemExit(0)
    if '.assignees' in query:
        for assignee in issue['assignees']:
            prefix = 'assignee:' if 'assignee:' in query else ''
            print(prefix + assignee['login'])
    else:
        print(json.dumps(issue))
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
    if (method == 'PATCH' and request[1] == endpoint
            and request[2:3] == ['-f']
            and request[3:4] in (['state=open'], ['state=closed'])
            and request[4:] == ['--silent']):
        raise SystemExit(0)
unsupported()
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
        **options):
    """Execute the real workflow shell against the controlled gh boundary."""
    supported = {
        'parser_crlf', 'comment_status', 'pull', 'history',
        'rendered_html', 'render_status', 'render_complete',
        'render_after_sentinel',
    }
    assert set(options) <= supported, sorted(set(options) - supported)
    parser_crlf = options.get('parser_crlf', False)
    comment_status = options.get('comment_status')
    pull = options.get('pull')
    history = options.get('history')
    rendered_html = options.get('rendered_html')
    render_status = options.get('render_status')
    render_complete = options.get(
        'render_complete', rendered_html is None)
    render_after_sentinel = options.get('render_after_sentinel', '')
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
    rendered_path = workdir / 'rendered.html'
    rendered_path.write_text(
        rendered_html if rendered_html is not None else _valid_html(repo=repo),
        encoding='utf-8')
    calls_path = workdir / 'calls.jsonl'
    calls_path.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'STUB_ISSUES': str(fixture_path),
        'STUB_CALLS': str(calls_path),
        'STUB_REAL_PYTHON': sys.executable,
        'STUB_RENDERED_HTML': str(rendered_path),
        'STUB_EXPECTED_BODY': body,
        'STUB_EXPECTED_REPO': repo,
        'STUB_RENDER_COMPLETE': '1' if render_complete else '0',
        'STUB_RENDER_AFTER_SENTINEL': render_after_sentinel,
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
    if render_status is not None:
        env['STUB_RENDER_STATUS'] = str(render_status)
    result = subprocess.run(
        [bash, '-c', _workflow_script()], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    calls = [json.loads(line) for line in calls_path.read_text(
        encoding='utf-8').splitlines()]
    return calls, result


def _run_complete_workflow(tmp, body, issues, **options):
    assert 'render_complete' not in options
    return _run_workflow(
        tmp, body, issues, render_complete=True, **options)


def _assert_unusable_render(tmp, body, rendered, **options):
    calls, result = _run_workflow(
        tmp, body, {'101': _issue('alice')},
        rendered_html=rendered, **options)
    assert result.returncode != 0, (result.stdout, result.stderr, calls)
    assert 'could not analyze' in result.stderr, result.stderr
    _assert_no_mutation(calls)


def _truncated_render_cases():
    return (
        ('empty', ''),
        ('plain', 'upstream returned plain text'),
        ('token', '<h2>Summary</h2><'),
        ('element', '<h2>Summary</h2><p>text'),
        ('heading', '<h2>Summary</h2><h2'),
        ('comment', '<h2>Summary</h2><!-- unfinished'),
        ('prefix', '<h2>Summary</h2><p>One sentence.</p>'),
        ('missing-headings', _html_body(
            ('Summary', _text_html('One sentence.')),
            ('Related Issues and Pull Requests',
             f'Fixes {_issue_html(101)}'))),
        ('last-content', _valid_html().replace(
            '<p dir="auto">Ran the suite.</p>', '')),
        ('middle-content', _valid_html(changes='')),
    )


def _sentinel_attack_cases():
    fake = 'pr-gate-sentinel-' + ('0' * 64)
    return (
        ('forged', f'{_valid_body()}\n{fake}\n',
         f'{_valid_html()}\n<p dir="auto">{fake}</p>', {}),
        ('nonterminal', _valid_body(), _valid_html(), {
            'render_complete': True,
            'render_after_sentinel': '\n<p dir="auto">late</p>'}),
    )


def _body_from(call):
    return next(arg[5:] for arg in call if arg.startswith('body='))


def _write_calls(calls):
    writes = []
    for call in calls:
        if not call:
            continue
        if call[0] != 'api':
            writes.append(call)
            continue
        if 'markdown' in call:
            continue
        method = 'GET'
        for flag in ('-X', '--method'):
            if flag in call:
                method = call[call.index(flag) + 1]
                break
        fields = {'-f', '-F', '--field', '--raw-field'}
        if method != 'GET' or fields.intersection(call):
            writes.append(call)
    return writes


def _issue_lookups(calls):
    pattern = re.compile(r'^repos/[^/]+/[^/]+/issues/[0-9]+$')
    return [call for call in calls
            if any(pattern.fullmatch(arg) for arg in call)]


def _assert_no_mutation(calls):
    assert _write_calls(calls) == [], calls


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
    writes = _write_calls(calls)
    assert writes == [comment, close], calls
    body = _body_from(comment)
    assert body.startswith(f'@{actor} — closing this automatically'), body
    assert CLOSE_MARKER in body, body
    assert comment == [
        'api', comment_endpoint, '-F', f'body={body}', '--silent'], comment
    assert re.search(r'#[0-9]', body) is None, body
    for reason in reasons:
        assert reason in body, body
    assert '**Related Issues and Pull Requests**' in body, body
    assert 'match the pull request template' in body, body
    assert '`Fixes #<issue>`' in body, body
    assert '/claim' in body, body
    assert 'reopen it automatically' in body, body
    assert 'Then reopen this same pull request.' not in body, body
    assert _normalise_method(close) == [
        'PATCH', 'api', close_endpoint,
        '-f', 'state=closed', '--silent'], close


def _assert_commented_not_closed(
        calls, *reasons, actor='alice', pr='99', repo='owner/repo'):
    comment_endpoint = f'repos/{repo}/issues/{pr}/comments'
    close_endpoint = f'repos/{repo}/pulls/{pr}'
    comment_calls = [call for call in calls if comment_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert not any(close_endpoint in call for call in calls), calls
    comment = comment_calls[0]
    assert _write_calls(calls) == [comment], calls
    body = _body_from(comment)
    assert body.startswith(
        f'@{actor} — this pull request needs changes, but it remains open.'
    ), body
    assert CLOSE_MARKER not in body, body
    for reason in reasons:
        assert reason in body, body
    assert '/claim' in body, body
    assert '**Related Issues and Pull Requests**' in body, body
    assert 'match the pull request template' in body, body
    assert 'reopen it automatically' not in body, body
    assert comment == [
        'api', comment_endpoint, '-F', f'body={body}', '--silent'], comment


def _assert_commented_then_reopened(
        calls, actor='alice', pr='99', repo='owner/repo'):
    comment_endpoint = f'repos/{repo}/issues/{pr}/comments'
    reopen_endpoint = f'repos/{repo}/pulls/{pr}'
    comment_calls = [call for call in calls if comment_endpoint in call]
    reopen_calls = [call for call in calls if reopen_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert len(reopen_calls) == 1, calls
    comment = comment_calls[0]
    reopen = reopen_calls[0]
    writes = _write_calls(calls)
    assert writes == [comment, reopen], calls
    body = _body_from(comment)
    assert body.startswith(f'@{actor} —'), body
    assert 'reopening it automatically' in body, body
    assert comment == [
        'api', comment_endpoint, '-F', f'body={body}', '--silent'], comment
    assert _normalise_method(reopen) == [
        'PATCH', 'api', reopen_endpoint,
        '-f', 'state=open', '--silent'], reopen


def _normalise_method(call):
    for flag in ('-X', '--method'):
        if flag in call:
            index = call.index(flag)
            return [call[index + 1], *call[:index], *call[index + 2:]]
    return ['GET', *call]


def _layout_body(*sections):
    return '\n\n'.join(
        f'## {title}\n{content}' for title, content in sections) + '\n'


def _html_body(*sections):
    return '\n'.join(
        f'<h2 dir="auto">{html.escape(title)}</h2>\n{content}'
        for title, content in sections)


def _issue_html(number, repo='owner/repo'):
    href = f'https://github.com/{repo}/issues/{number}'
    return f'<a href="{href}">#{number}</a>'


def _text_html(text):
    return f'<p dir="auto">{html.escape(text)}</p>' if text else ''


def _valid_body(references='Fixes #101'):
    return _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', references),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))


def _valid_html(references=None, changes=None, repo='owner/repo'):
    references = references or f'Fixes {_issue_html(101, repo)}'
    changes = changes if changes is not None else (
        '<ul dir="auto">\n<li>One change</li>\n</ul>')
    return _html_body(
        ('Summary', _text_html('One sentence.')),
        ('Related Issues and Pull Requests', references),
        ('Changes', changes),
        ('Testing', _text_html('Ran the suite.')))


def _closed_by(login):
    return [{'event': 'closed', 'actor': {'login': login}}]
