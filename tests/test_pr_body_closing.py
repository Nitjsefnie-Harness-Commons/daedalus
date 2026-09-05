#!/usr/bin/env python3
"""Closing-reference collection in a rendered pull-request body.

Relocated from tests/test_pr_body.py so that suite stays under its size
ceiling; the closing channel is one grammar and reads as one suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    GITHUB_FOOTNOTE_HTML, GITHUB_HTML, PR_BODY, _html_body, _issue_html,
    _text_html, _valid_html,
)


REPOSITORY = 'Nitjsefnie-Harness-Commons/daedalus'
RELATED = 'related issues and pull requests'

# Captured from GitHub's /markdown endpoint in GFM mode with
# Nitjsefnie-Harness-Commons/daedalus as the context, for the source
# "Fixes #104\n\n## Fixes #105\n\nRan the suite.".
GITHUB_OUTSIDE_SECTIONS_HTML = (
    '<p dir="auto">Fixes <a class="issue-link js-issue-link" '
    'data-error-text="Failed to load title" data-id="5232205124" '
    'data-permission-text="Title is private" '
    'data-url="https://github.com/Nitjsefnie-Harness-Commons/daedalus/'
    'issues/104" data-hovercard-type="issue" '
    'data-hovercard-url="/Nitjsefnie-Harness-Commons/daedalus/issues/104/'
    'hovercard" href="https://github.com/Nitjsefnie-Harness-Commons/'
    'daedalus/issues/104">#104</a></p>\n'
    '<h2 dir="auto">Fixes <a class="issue-link js-issue-link" '
    'data-error-text="Failed to load title" data-id="5232282547" '
    'data-permission-text="Title is private" '
    'data-url="https://github.com/Nitjsefnie-Harness-Commons/daedalus/'
    'issues/105" data-hovercard-type="issue" '
    'data-hovercard-url="/Nitjsefnie-Harness-Commons/daedalus/issues/105/'
    'hovercard" href="https://github.com/Nitjsefnie-Harness-Commons/'
    'daedalus/issues/105">#105</a></h2>\n'
    '<p dir="auto">Ran the suite.</p>')

# Every captured rendering in tests/_prgate.py that carries an anchor in
# section content, against the answer closing_issues gives for it.
CAPTURED_CLOSING = (
    ('angle_prose', []),
    ('balanced_destination', []),
    ('empty_image', []),
    ('escaped_backticks', []),
    ('html_attribute', []),
    ('image_destination', []),
    ('malformed_inline', []),
    ('multiline_attribute', []),
    ('named_anchor', []),
    ('nested_list', [101]),
    ('paragraph_continuation', [101]),
    ('quoted_attribute', []),
    ('undefined_reference', []),
    ('zero_size_image', []),
)


def test_closing_issues_reads_the_governing_keyword(tmp):
    del tmp
    rendered = _valid_html(references=(
        f'References {_issue_html(101)}, Fixes {_issue_html(102)}'))
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.referenced_issues(body.sections) == [101, 102]
    assert PR_BODY.closing_issues(body) == [102]


def test_closing_issues_includes_keyword_list_continuation(tmp):
    del tmp
    cases = (
        (f'Fixes {_issue_html(101)}, {_issue_html(102)}', [101, 102]),
        (f'Fixes {_issue_html(101)} and {_issue_html(102)}', [101, 102]),
        (f'Fixes {_issue_html(101)}, {_issue_html(102)}, and '
         f'{_issue_html(103)}', [101, 102, 103]),
        (f'Fixes {_issue_html(101)}. References {_issue_html(102)}',
         [101]),
        (f'Fixes: {_issue_html(101)}', [101]),
        (f'resolved {_issue_html(101)}', [101]),
        (f'Closes {_issue_html(101)}', [101]),
        (f'closed {_issue_html(101)}', [101]),
        (f'fix {_issue_html(101)}', [101]),
        (f'fixed {_issue_html(101)}', [101]),
        (f'Resolve {_issue_html(101)}', [101]),
        (f'References {_issue_html(101)}, Fixes {_issue_html(102)}, '
         f'{_issue_html(103)}', [102, 103]),
        (f'Fixes #{101}', []),
        (f'Fixes::: {_issue_html(101)}', []),
        ('<table dir="auto"><tbody><tr><td>Fixes '
         f'{_issue_html(101)}</td><td>'
         f'{_issue_html(102)}</td></tr></tbody></table>', [101]),
        ('<p dir="auto">Fixes <code class="notranslate">x</code>'
         f'{_issue_html(101)}</p>', []),
        ('<p dir="auto"><code class="notranslate">Fixes</code> '
         f'{_issue_html(101)}</p>', [101]),
        ('<ul dir="auto">\n<li>Fixes '
         f'{_issue_html(101)}\n<ul dir="auto">\n<li>'
         f'{_issue_html(102)}</li>\n</ul>\n</li>\n</ul>', [101]),
        (f'Fixes {_issue_html(101)}\n<hr>\n{_issue_html(102)}', [101]),
    )
    for references, closing in cases:
        rendered = _valid_html(references=references)
        body = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(body) == closing, references


def test_every_closing_keyword_spelling_closes(tmp):
    del tmp
    spellings = (
        'close', 'closes', 'closed', 'fix', 'fixes', 'fixed',
        'resolve', 'resolves', 'resolved')
    assert PR_BODY._CLOSING_KEYWORDS == frozenset(spellings)
    for keyword in spellings:
        rendered = _valid_html(references=f'{keyword} {_issue_html(101)}')
        body = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(body) == [101], keyword


def test_closing_issues_checks_every_section(tmp):
    del tmp
    summary = f'<p dir="auto">Also fixes {_issue_html(104)}.</p>'
    rendered = _valid_html(
        references=f'References {_issue_html(101)}').replace(
            _text_html('One sentence.'), summary)
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.referenced_issues(body.sections) == [101]
    assert PR_BODY.closing_issues(body) == [104]


def test_closing_keyword_does_not_cross_a_section_boundary(tmp):
    del tmp
    rendered = _valid_html(
        references=_issue_html(101)).replace(
            _text_html('One sentence.'), _text_html('One sentence. Fixes'))
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(body) == []


def test_closing_list_does_not_cross_a_section_boundary(tmp):
    del tmp
    summary = f'<p dir="auto">Fixes {_issue_html(101)}</p>'
    rendered = _valid_html(
        references=_issue_html(104)).replace(
            _text_html('One sentence.'), summary)
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(body) == [101]


def test_section_boundary_resets_without_block_tags(tmp):
    del tmp
    rendered = (
        '<h2 dir="auto">Summary</h2>\n'
        'Fixes\n'
        '<h2 dir="auto">Related Issues and Pull Requests</h2>\n'
        f'{_issue_html(104)}\n')
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(body) == []
    rendered = (
        '<h2 dir="auto">Summary</h2>\n'
        f'Fixes {_issue_html(101)}\n'
        '<h2 dir="auto">Related Issues and Pull Requests</h2>\n'
        f'{_issue_html(104)}\n')
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(body) == [101]


def test_block_boundary_ends_a_closing_keyword_list(tmp):
    del tmp
    cases = (
        (f'<ul dir="auto">\n<li>Fixes {_issue_html(101)}</li>\n'
         f'<li>{_issue_html(102)}</li>\n</ul>', [101]),
        (f'<p dir="auto">Fixes {_issue_html(101)}</p>\n'
         f'<p dir="auto">{_issue_html(102)}</p>', [101]),
        (f'<p dir="auto">Fixes {_issue_html(101)}</p>\n<hr>\n'
         f'<p dir="auto">{_issue_html(102)}</p>', [101]),
        (f'<p dir="auto">Fixes</p>\n<p dir="auto">{_issue_html(101)}</p>',
         []),
        (f'<p dir="auto">Fixes {_issue_html(101)}, '
         f'{_issue_html(102)}</p>', [101, 102]),
    )
    for references, closing in cases:
        rendered = _valid_html(references=references)
        body = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(body) == closing, references


def test_keyword_inside_fenced_code_governs_no_anchor(tmp):
    del tmp
    rendered = _valid_html(references=(
        '<pre class="notranslate"><code class="notranslate">Fixes\n'
        '</code></pre>\n'
        f'<p dir="auto">{_issue_html(102)}</p>'))
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(body) == []


def test_preamble_closing_reference_is_collected(tmp):
    del tmp
    rendered = (
        f'<p dir="auto">Fixes {_issue_html(104)}</p>\n' + _valid_html())
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(body) == [104, 101]
    assert PR_BODY.referenced_issues(body.sections) == [101]
    assert [section.key for section in body.sections] == [
        'summary', RELATED, 'changes', 'testing']


def test_heading_closing_reference_is_collected(tmp):
    del tmp
    body = PR_BODY.parse_rendered(
        GITHUB_OUTSIDE_SECTIONS_HTML, REPOSITORY)
    assert PR_BODY.closing_issues(body) == [104, 105]
    assert [section.key for section in body.sections] == ['fixes #105']
    assert body.sections[0].issues == ()
    assert body.sections[0].links == ()


def test_closing_issues_reports_a_repeated_number_once(tmp):
    del tmp
    summary = f'<p dir="auto">Fixes {_issue_html(101)}.</p>'
    rendered = _valid_html().replace(
        _text_html('One sentence.'), summary)
    body = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert body.closing == (101, 101)
    assert PR_BODY.closing_issues(body) == [101]


def test_captured_renderings_pin_the_closing_answer(tmp):
    del tmp
    failures = []
    for name, expected in CAPTURED_CLOSING:
        body = PR_BODY.parse_rendered(
            _html_body(('Related Issues and Pull Requests',
                        GITHUB_HTML[name])),
            REPOSITORY)
        found = PR_BODY.closing_issues(body)
        if found != expected:
            failures.append((name, found, expected))
    body = PR_BODY.parse_rendered(GITHUB_FOOTNOTE_HTML, REPOSITORY)
    found = PR_BODY.closing_issues(body)
    if found != [101]:
        failures.append(('footnote_html', found, [101]))
    assert failures == [], failures


# A heading carries text of its own, and that text lands in the gap the
# next anchor reads. Each boundary case therefore uses a heading whose
# text cannot stand in for the boundary: an empty one, or one holding
# nothing but the anchor.
def test_preamble_keyword_does_not_govern_a_section_anchor(tmp):
    del tmp
    cases = (
        ('Fixes\n', []),
        (f'Fixes {_issue_html(101)}\n', [101]),
    )
    for preamble, expected in cases:
        rendered = (
            preamble
            + '<h2 dir="auto"></h2>\n'
            + f'{_issue_html(104)}\n')
        body = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(body) == expected, preamble


def test_heading_keyword_does_not_govern_a_paragraph_anchor(tmp):
    del tmp
    cases = (
        ('Fixes', []),
        (f'Fixes {_issue_html(101)}', [101]),
    )
    for heading, expected in cases:
        rendered = (
            f'<h2 dir="auto">{heading}</h2>\n'
            f'{_issue_html(104)}\n')
        body = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(body) == expected, heading


def test_paragraph_keyword_does_not_govern_a_heading_anchor(tmp):
    del tmp
    cases = (
        ('Fixes\n', []),
        (f'Fixes {_issue_html(101)}\n', [101]),
    )
    for content, expected in cases:
        rendered = (
            '<h2 dir="auto">Summary</h2>\n'
            + content
            + f'<h2 dir="auto">{_issue_html(104)}</h2>\n')
        body = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(body) == expected, content


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbodyclosing_')


if __name__ == '__main__':
    raise SystemExit(main())
