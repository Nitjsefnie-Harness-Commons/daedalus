#!/usr/bin/env python3
"""Shared fixtures for pull-request body and workflow gate tests."""
import html
import sys
from pathlib import Path

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
