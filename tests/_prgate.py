#!/usr/bin/env python3
"""Shared fixtures for pull-request body and workflow gate tests."""
import html
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


PR_BODY = _util.load(
    ROOT / 'scripts' / 'ci' / 'pr_body.py', 'scripts.ci.pr_body')
TEMPLATE = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
    encoding='utf-8')
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


BOT = 'github-actions[bot]'
MARKER = '<!-- pr-gate -->'
CLOSED_MARKER = '<!-- pr-gate: closed -->'

GH_STUB = r'''#!/usr/bin/env python3
import json
import os
import re
import sys


def finish(status, data):
    reasons = {
        200: 'OK', 201: 'Created', 404: 'Not Found', 500: 'Error'}
    print(f'HTTP/2.0 {status} {reasons.get(status, "Response")}')
    print('content-type: application/json')
    print()
    if data is not None:
        print(json.dumps(data))
    raise SystemExit(0 if 200 <= status < 300 else 1)


def unsupported():
    print('unsupported', file=sys.stderr)
    raise SystemExit(2)


arguments = sys.argv[1:]
payload = None
if '--input' in arguments:
    index = arguments.index('--input')
    if index + 2 != len(arguments):
        unsupported()
    with open(arguments[index + 1], encoding='utf-8') as handle:
        payload = json.load(handle)
with open(os.environ['STUB_CALLS'], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps({'argv': arguments, 'input': payload}) + '\n')
if (len(arguments) < 5 or arguments[0] != 'api'
        or arguments[1:3] != ['--include', '-X']):
    unsupported()
method = arguments[3]
endpoint = arguments[4]
with open(os.environ['STUB_FIXTURES'], encoding='utf-8') as handle:
    fixtures = json.load(handle)
if fixtures.get('unparsable'):
    print('first gh failure line', file=sys.stderr)
    print('second gh failure line', file=sys.stderr)
    raise SystemExit(2)
if any(item in f'{method} {endpoint}' for item in fixtures.get('fail', [])):
    finish(500, {'message': 'fixture failure'})
if endpoint == 'repos/owner/repo/pulls/99':
    if method == 'GET':
        finish(200, fixtures['pull'])
    if method == 'PATCH':
        finish(200, {**fixtures['pull'], **payload})
if endpoint == 'markdown' and method == 'POST':
    finish(200, fixtures['rendered'])
page = re.fullmatch(
    r'repos/owner/repo/issues/99/(comments|timeline)'
    r'\?per_page=100&page=([0-9]+)', endpoint)
if page and method == 'GET':
    values = fixtures[page.group(1)]
    offset = (int(page.group(2)) - 1) * 100
    finish(200, values[offset:offset + 100])
issue = re.fullmatch(r'repos/owner/repo/issues/([0-9]+)', endpoint)
if issue and method == 'GET':
    value = fixtures['issues'].get(issue.group(1))
    finish(200, value) if value is not None else finish(404, None)
if endpoint == 'repos/owner/repo/issues/99/comments' and method == 'POST':
    finish(201, {'id': 100, **payload})
comment = re.fullmatch(
    r'repos/owner/repo/issues/comments/([0-9]+)', endpoint)
if comment and method == 'PATCH':
    finish(200, {'id': int(comment.group(1)), **payload})
unsupported()
'''


class _Response(NamedTuple):
    status: int
    data: object


def _issue(*assignees, pull_request=False):
    value = {'assignees': [{'login': login} for login in assignees]}
    if pull_request:
        value['pull_request'] = {}
    return value


class FakeApi:
    def __init__(self, *, pull, issues=None, comments=(), timeline=(),
                 rendered=None, fail=()):
        self.pull = pull
        self.issues = issues or {}
        self.comments = list(comments)
        self.timeline = list(timeline)
        self.rendered = _valid_html() if rendered is None else rendered
        self.fail = set(fail)
        self.calls = []
        self.writes = []

    def _failed(self, method, endpoint):
        target = f'{method} {endpoint}'
        return any(fragment in target for fragment in self.fail)

    def request(self, method, endpoint, payload=None):
        call = (method, endpoint, payload)
        self.calls.append(call)
        if method != 'GET' and endpoint != 'markdown':
            self.writes.append(call)
        if self._failed(method, endpoint):
            return _Response(500, None)
        if endpoint == 'repos/owner/repo/pulls/99':
            if method == 'GET':
                return _Response(200, self.pull)
            if method == 'PATCH':
                self.pull['state'] = payload['state']
                return _Response(200, self.pull)
        if endpoint == 'markdown' and method == 'POST':
            return _Response(200, self.rendered)
        if (endpoint == 'repos/owner/repo/issues/99/comments'
                and method == 'POST'):
            return _Response(201, {'id': 100, 'body': payload['body']})
        match = re.fullmatch(
            r'repos/owner/repo/issues/comments/([0-9]+)', endpoint)
        if match and method == 'PATCH':
            return _Response(200, {'id': int(match.group(1)),
                                   'body': payload['body']})
        match = re.fullmatch(
            r'repos/owner/repo/issues/([0-9]+)', endpoint)
        if match and method == 'GET':
            issue = self.issues.get(match.group(1))
            return _Response(200, issue) if issue is not None else _Response(
                404, None)
        raise AssertionError(f'unmodelled: {method} {endpoint} {payload!r}')

    def paginate(self, endpoint):
        self.calls.append(('GET', endpoint, None))
        if self._failed('GET', endpoint):
            return _Response(500, None)
        if endpoint == 'repos/owner/repo/issues/99/comments?per_page=100':
            return _Response(200, self.comments)
        if endpoint == 'repos/owner/repo/issues/99/timeline?per_page=100':
            return _Response(200, self.timeline)
        raise AssertionError(f'unmodelled: GET {endpoint}')


def _gate_module():
    path = ROOT / 'scripts' / 'ci' / 'pr_gate.py'
    if not path.is_file():
        return None
    return _util.load(path, 'scripts.ci.pr_gate')


def run_gate(api, body=None):
    gate = _gate_module()
    assert gate is not None, 'scripts/ci/pr_gate.py is not implemented'
    api.pull['body'] = body
    code = gate.run(api, 'owner/repo', '99', 'alice', TEMPLATE)
    return code, api.writes


def _comment_body(write):
    return write[2]['body']


def _assert_no_writes(writes):
    assert writes == [], writes


def _markdown_code_spans(text):
    runs = list(re.finditer(r'`+', text))
    spans = []
    index = 0
    while index < len(runs):
        opener = runs[index]
        close = next((candidate for candidate in runs[index + 1:]
                      if len(candidate.group()) == len(opener.group())), None)
        if close is None:
            break
        spans.append((opener.start(), close.end()))
        index = runs.index(close) + 1
    return spans
