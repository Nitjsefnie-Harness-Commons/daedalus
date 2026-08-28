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
from _prgate_gh import GH_STUB as _GH_STUB  # noqa: E402
from _repo import ROOT  # noqa: E402


PR_BODY = _util.load(
    ROOT / 'scripts' / 'ci' / 'pr_body.py', 'scripts.ci.pr_body')
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
GITHUB_FOOTNOTE_SENTINEL_HTML = (
    '<h2 dir="auto">Summary</h2>\n<p dir="auto">One sentence.</p>\n'
    '<h2 dir="auto">Related Issues and Pull Requests</h2>\n'
    f'<p dir="auto">Fixes {GITHUB_ISSUE_101}</p>\n'
    '<h2 dir="auto">Changes</h2>\n<p dir="auto">The behavior is pinned.'
    '<sup><a href="#user-content-fn-1-d1601984b8b7bc49868fe3588f47dc29" '
    'id="user-content-fnref-1-d1601984b8b7bc49868fe3588f47dc29" '
    'data-footnote-ref="" aria-describedby="footnote-label">1</a></sup>'
    '</p>\n<h2 dir="auto">Testing</h2>\n'
    '<p dir="auto">Ran the suite.</p>\n<p dir="auto">pr-gate-sentinel-'
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</p>\n'
    '<section data-footnotes="" class="footnotes"><h2 '
    'id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>\n'
    '<ol dir="auto">\n<li id="user-content-fn-1-'
    'd1601984b8b7bc49868fe3588f47dc29">\n'
    '<p dir="auto">By a focused regression test. <a href="'
    '#user-content-fnref-1-d1601984b8b7bc49868fe3588f47dc29" '
    'data-footnote-backref="" aria-label="Back to reference 1" '
    'class="data-footnote-backref">↩</a></p>\n</li>\n</ol>\n</section>')

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
        'render_after_sentinel', 'render_sentinel_mangle',
        'current_pulls', 'pull_status', 'pull_statuses',
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
    render_sentinel_mangle = options.get('render_sentinel_mangle', '')
    current_pulls = options.get('current_pulls')
    pull_status = options.get('pull_status')
    pull_statuses = options.get('pull_statuses')
    pull = pull or {}
    history = history or {}
    state = pull.get('state', 'open')
    merged = pull.get('merged', 'false')
    timeline = history.get('timeline', ())
    comments = history.get('comments', ())
    timeline_status = history.get('timeline_status')
    comments_status = history.get('comments_status')
    if current_pulls is None:
        current_pulls = ({
            'body': body,
            'state': state,
            'merged': str(merged).casefold() == 'true',
        },)
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
    fixtures['_pull_snapshots'] = list(current_pulls)
    if timeline_status is not None:
        fixtures['_timeline_status'] = timeline_status
    if comments_status is not None:
        fixtures['_comments_status'] = comments_status
    if pull_status is not None:
        fixtures['_pull_status'] = pull_status
    if pull_statuses is not None:
        fixtures['_pull_statuses'] = list(pull_statuses)
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
        'STUB_EXPECTED_BODY': current_pulls[0]['body'] or '',
        'STUB_EXPECTED_REPO': repo,
        'STUB_RENDER_COMPLETE': '1' if render_complete else '0',
        'STUB_RENDER_AFTER_SENTINEL': render_after_sentinel,
        'STUB_SENTINEL_MANGLE': render_sentinel_mangle,
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


def _run_stub(tmp, *args):
    workdir = Path(tmp) / 'gh-headers'
    workdir.mkdir(exist_ok=True)
    stub = workdir / 'gh'
    stub.write_text(_GH_STUB, encoding='utf-8')
    calls_path = workdir / 'calls.jsonl'
    calls_path.write_text('', encoding='utf-8')
    fixture_path = workdir / 'issues.json'
    fixture_path.write_text(
        json.dumps({'1': _issue('alice')}), encoding='utf-8')
    env = {
        **os.environ,
        'STUB_ISSUES': str(fixture_path),
        'STUB_CALLS': str(calls_path),
    }
    return subprocess.run(
        [sys.executable, str(stub), *args], env=env,
        capture_output=True, text=True, timeout=30)


def _run_complete_workflow(tmp, body, issues, **options):
    assert 'render_complete' not in options
    return _run_workflow(
        tmp, body, issues, render_complete=True, **options)


def _assert_unusable_render(tmp, body, rendered, **options):
    options.setdefault('render_complete', True)
    calls, result = _run_workflow(
        tmp, body, {'101': _issue('alice')},
        rendered_html=rendered, **options)
    assert result.returncode != 0, (result.stdout, result.stderr, calls)
    assert 'could not analyze' in result.stderr, result.stderr
    _assert_no_mutation(calls)


def _truncated_render_cases():
    body = _valid_body()
    cases = (
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
    url = '[tracked issue](https://github.com/owner/repo/issues/101)'
    return tuple((name, body, rendered) for name, rendered in cases) + (
        ('middle-url', _valid_body(url), _valid_html(changes='')),
        ('unknown-source', _valid_body().replace('## Summary', '## Notes'),
         _valid_html()),)


def _sentinel_attack_cases():
    fake = 'pr-gate-sentinel-' + ('0' * 64)
    return (
        ('forged', f'{_valid_body()}\n{fake}\n',
         f'{_valid_html()}\n<p dir="auto">{fake}</p>', {
             'render_complete': False}),
        ('nonterminal', _valid_body(), _valid_html(), {
            'render_complete': True,
            'render_after_sentinel': '\n<p dir="auto">late</p>'}),
        ('commented', _valid_body(), _valid_html(), {
            'render_sentinel_mangle': 'comment'}),
        ('nested', _valid_body(), _valid_html(), {
            'render_sentinel_mangle': 'element'}),
    )


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
    writes = _write_calls(calls)
    comment_calls = [call for call in writes if comment_endpoint in call]
    close_calls = [call for call in writes if close_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert len(close_calls) == 1, calls
    comment = comment_calls[0]
    close = close_calls[0]
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
    writes = _write_calls(calls)
    comment_calls = [call for call in writes if comment_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert not any(close_endpoint in call for call in writes), calls
    comment = comment_calls[0]
    assert writes == [comment], calls
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
    writes = _write_calls(calls)
    comment_calls = [call for call in writes if comment_endpoint in call]
    reopen_calls = [call for call in writes if reopen_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert len(reopen_calls) == 1, calls
    comment = comment_calls[0]
    reopen = reopen_calls[0]
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
