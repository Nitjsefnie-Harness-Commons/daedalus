#!/usr/bin/env python3
"""Rendered pull-request body analysis."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    GITHUB_FOOTNOTE_HTML, GITHUB_HTML, PR_BODY, TEMPLATE, _html_body,
    _issue_html, _layout_body, _text_html, _valid_body, _valid_html,
)


REPOSITORY = 'Nitjsefnie-Harness-Commons/daedalus'
RELATED = 'related issues and pull requests'


def test_parser_splits_sections_and_collects_links_and_issues(tmp):
    del tmp
    sections = PR_BODY.parse_rendered(_valid_html(), 'owner/repo')
    assert [section.key for section in sections] == [
        'summary', RELATED, 'changes', 'testing']
    related = sections[1]
    assert related.issues == (101,)
    assert related.links == (
        'https://github.com/owner/repo/issues/101',)


def test_parser_accepts_heading_depth_emphasis_and_colon(tmp):
    del tmp
    rendered = (
        '<h3><em>Related Issues and Pull Requests:</em></h3>\n'
        f'<p>Fixes {_issue_html(101)}</p>')
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert [section.key for section in sections] == [RELATED]


def test_parser_ignores_github_footnotes(tmp):
    del tmp
    sections = PR_BODY.parse_rendered(GITHUB_FOOTNOTE_HTML, REPOSITORY)
    assert 'footnotes' not in [section.key for section in sections]
    assert PR_BODY.referenced_issues(sections) == [101]


def test_parser_rejects_unusable_html(tmp):
    del tmp
    unusable = (
        '',
        'plain text',
        '<h2>Summary</h2><p>text',
        '<h2>Summary</h2><h2',
        '<p>a</b>',
        '<h2>Summary<h3>x</h3></h2>',
        '<h2>Summary</h2><div/>',
    )
    accepted = []
    for rendered in unusable:
        try:
            PR_BODY.parse_rendered(rendered, 'owner/repo')
        except ValueError as error:
            assert 'rendered HTML' in str(error), error
        else:
            accepted.append(rendered)
    assert accepted == [], accepted


def test_parser_rejects_unfinished_heading(tmp):
    del tmp
    try:
        PR_BODY.parse_rendered('<h2>Summary', 'owner/repo')
    except ValueError as error:
        assert 'unfinished heading' in str(error), error
    else:
        raise AssertionError('unfinished heading was accepted')


def test_parser_rejects_malformed_repositories(tmp):
    del tmp
    rendered = '<h2>Summary</h2><p>text</p>'
    accepted = []
    for repository in ('owner', 'a/b/c', '/'):
        try:
            PR_BODY.parse_rendered(rendered, repository)
        except ValueError as error:
            assert 'owner/name' in str(error), error
        else:
            accepted.append(repository)
    assert accepted == [], accepted


def test_visible_reference_fixtures_remain_visible(tmp):
    del tmp
    names = (
        'nested_list',
        'paragraph_continuation',
        'escaped_backticks',
        'angle_prose',
        'undefined_reference',
        'malformed_inline',
    )
    failures = []
    for name in names:
        sections = PR_BODY.parse_rendered(
            _html_body(('Related Issues and Pull Requests',
                        GITHUB_HTML[name])),
            REPOSITORY)
        found = PR_BODY.referenced_issues(sections)
        if found != [101]:
            failures.append((name, found))
    assert failures == [], failures


def test_nontext_reference_fixtures_remain_hidden(tmp):
    del tmp
    names = (
        'inline_code',
        'fenced_code',
        'indented_code',
        'html_attribute',
        'balanced_destination',
        'quoted_attribute',
        'multiline_attribute',
        'image_destination',
    )
    failures = []
    for name in names:
        sections = PR_BODY.parse_rendered(
            _html_body(('Related Issues and Pull Requests',
                        GITHUB_HTML[name])),
            REPOSITORY)
        found = PR_BODY.referenced_issues(sections)
        if found:
            failures.append((name, found))
        if name == 'html_attribute':
            assert sections[0].links == ('#101',)
    assert failures == [], failures


def test_parser_ignores_named_anchor_without_href(tmp):
    del tmp
    rendered = _html_body(
        ('Related Issues and Pull Requests', GITHUB_HTML['named_anchor']))
    sections = PR_BODY.parse_rendered(rendered, REPOSITORY)
    assert sections[0].links == ()
    assert PR_BODY.referenced_issues(sections) == []


def test_numeric_character_references_contribute_visible_text(tmp):
    del tmp
    rendered = _html_body(
        ('Summary', '<p>&#35;101 &#x23;101</p>'))
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert sections[0].text.strip() == '#101 #101'


def test_related_helpers_return_empty_without_related_section(tmp):
    del tmp
    sections = PR_BODY.parse_rendered(
        _html_body(('Summary', '<p>text</p>')), 'owner/repo')
    actual = (
        PR_BODY.referenced_issues(sections),
        PR_BODY.related_links(sections),
    )
    assert actual == ([], []), actual


def test_issue_number_language(tmp):
    del tmp
    too_wide = '/owner/repo/issues/' + ('9' * 20)
    www_host = 'www.' + 'github.com'
    credentialed = 'user@' + 'github.com'
    cases = (
        ('https://github.com/owner/repo/issues/101', 101),
        ('HTTPS://GITHUB.COM/Owner/Repo/issues/101', 101),
        ('https://github.com:443/owner/repo/issues/101', 101),
        ('http://github.com:80/owner/repo/issues/101', 101),
        ('//github.com/owner/repo/issues/101', 101),
        ('/owner/repo/issues/101', 101),
        ('https://github.com/owner/repo/issues/101?x=1#c', 101),
        (f'https://{www_host}/owner/repo/issues/101', None),
        ('https://github.com:8443/owner/repo/issues/101', None),
        ('https://github.com:notaport/owner/repo/issues/101', None),
        (f'https://{credentialed}/owner/repo/issues/101', None),
        ('owner/repo/issues/101', None),
        ('//owner/repo/issues/101', None),
        ('/other/repo/issues/101', None),
        ('/owner/repo/pull/101', None),
        ('/owner/repo/issues/0', None),
        (too_wide, None),
        ('ftp://github.com/owner/repo/issues/101', None),
        ('', None),
    )
    failures = []
    for href, expected in cases:
        found = PR_BODY.issue_number(href, 'owner/repo')
        if found != expected:
            failures.append((href, found, expected))
    assert failures == [], failures


def _layout_errors(rendered, template=TEMPLATE):
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    return PR_BODY.layout_errors(sections, template)


def test_layout_accepts_required_sections_without_optional_footer(tmp):
    del tmp
    assert _layout_errors(_valid_html()) == []


def test_layout_reports_each_missing_or_empty_section(tmp):
    del tmp
    rendered = _html_body(
        ('Related Issues and Pull Requests', f'Fixes {_issue_html(91)}'),
        ('Changes', ''),
        ('Testing', _text_html('Ran the suite.')),
        ('Breaking Changes', ''))
    errors = _layout_errors(rendered)
    assert 'Required section "Summary" is missing.' in errors
    assert 'Section "Changes" is empty.' in errors
    assert 'Section "Breaking Changes" is empty.' in errors


def test_layout_rejects_constructs_that_render_as_empty(tmp):
    del tmp
    for name in (
            'empty_list', 'empty_ordered', 'empty_quote',
            'link_definition'):
        rendered = _valid_html(changes=GITHUB_HTML[name])
        assert 'Section "Changes" is empty.' in _layout_errors(
            rendered), name


def test_layout_treats_an_image_as_section_content(tmp):
    del tmp
    images = (
        '<p dir="auto"><img src="diagram.png" '
        'alt="migration diagram"></p>',
        '<p dir="auto"><img src="diagram.png"></p>',
        '<p dir="auto"><img src="diagram.png"/></p>',
    )
    for image in images:
        rendered = _valid_html() + _html_body(('Breaking Changes', image))
        assert _layout_errors(rendered) == [], image


def test_layout_treats_invisible_text_as_empty(tmp):
    del tmp
    invisible = (
        '\u200b', '\ufe0f', '\u180b', '\u180c', '\u180d', '\u180f',
        '\u034f', '\u115f', '\u1160', '\u3164', '\uffa0', '\u2800')
    for character in invisible:
        rendered = _valid_html(
            changes=f'<p dir="auto">{character}</p>')
        assert 'Section "Changes" is empty.' in _layout_errors(
            rendered), f'U+{ord(character):04X}'


def test_layout_treats_empty_or_invisible_images_as_empty(tmp):
    del tmp
    for image in (
            GITHUB_HTML['empty_image'],
            GITHUB_HTML['zero_size_image']):
        rendered = _valid_html(changes=image)
        assert 'Section "Changes" is empty.' in _layout_errors(
            rendered), image


def test_layout_parses_html_image_dimension_values(tmp):
    del tmp
    cases = (
        ('width="00"', True),
        ('height="000"', True),
        ('width=" 0"', True),
        ('width="0px"', True),
        ('width="0.5"', False),
        ('height="0.5"', False),
        ('width="0.0"', True),
        ('width=".5"', False),
        ('width="0."', True),
        ('width="00.000x"', True),
        ('width="10"', False),
        ('width="01"', False),
        ('width="+0"', False),
        ('width="-0"', False),
        ('width=""', False),
    )
    failures = []
    for attribute, expected_empty in cases:
        image = f'<p><img src="diagram.png" alt="" {attribute}></p>'
        errors = _layout_errors(_valid_html(changes=image))
        empty = 'Section "Changes" is empty.' in errors
        if empty != expected_empty:
            failures.append((attribute, empty, expected_empty))
    assert failures == [], failures


def test_layout_rejects_a_template_heading_without_a_rule(tmp):
    del tmp
    template = '## Summary\n\n## Changes\n<!-- required: explain -->\n'
    rendered = _html_body(
        ('Summary', _text_html('Summary.')),
        ('Changes', _text_html('Change.')))
    try:
        _layout_errors(rendered, template)
    except ValueError as error:
        assert 'Summary' in str(error), error
        assert 'instruction comment' in str(error), error
    else:
        raise AssertionError('a section without a template rule was ignored')


def test_layout_reports_unknown_duplicate_and_out_of_order_sections(tmp):
    del tmp
    rendered = _html_body(
        ('Summary', _text_html('First.')),
        ('Changes', _text_html('Too early.')),
        ('Notes', _text_html('Unknown.')),
        ('Summary', _text_html('Again.')),
        ('Related Issues and Pull Requests', f'Fixes {_issue_html(91)}'),
        ('Testing', _text_html('Ran the suite.')))
    errors = _layout_errors(rendered)
    assert 'Section `Notes` is not defined by the template.' in errors
    assert 'Section "Summary" appears more than once.' in errors
    assert ('Section "Related Issues and Pull Requests" is out of order.'
            in errors)


def test_layout_counts_rendered_code_as_content(tmp):
    del tmp
    rendered = _valid_html(changes=GITHUB_HTML['inline_code'])
    assert _layout_errors(rendered) == []


def test_layout_ignores_heading_shaped_text_in_raw_html(tmp):
    del tmp
    rendered = _valid_html(changes=GITHUB_HTML['kbd_block'])
    assert _layout_errors(rendered) == []


def test_unknown_section_reasons_escape_adversarial_names(tmp):
    del tmp
    name = 'x`#1'
    rendered = _valid_html() + _html_body(
        (name, _text_html('Unknown.')))
    errors = _layout_errors(rendered)
    reason = 'Section ``x`#1`` is not defined by the template.'
    assert reason in errors
    assert '#1' not in reason.replace('``x`#1``', '')


def test_code_span_pads_backtick_boundaries_and_whitespace(tmp):
    del tmp
    cases = (
        ('`start', '`` `start ``'),
        ('end`', '`` end` ``'),
        ('   ', '`     `'),
    )
    failures = [
        (text, PR_BODY.code_span(text), expected)
        for text, expected in cases
        if PR_BODY.code_span(text) != expected
    ]
    assert failures == [], failures


def test_retains_instruction_comment(tmp):
    del tmp
    comment = re.search(r'<!--.*?-->', TEMPLATE, re.DOTALL).group(0)
    assert PR_BODY.retains_instruction_comment(comment, TEMPLATE)
    crlf = comment.replace('\n', '\r\n')
    assert PR_BODY.retains_instruction_comment(crlf, TEMPLATE)
    assert not PR_BODY.retains_instruction_comment(_valid_body(), TEMPLATE)
    prose = comment.removeprefix('<!--').removesuffix('-->')
    assert not PR_BODY.retains_instruction_comment(prose, TEMPLATE)


def _may_reference(source, related_html):
    sections = PR_BODY.parse_rendered(
        _valid_html(references=related_html), 'owner/repo')
    return PR_BODY.related_may_reference(source, sections, TEMPLATE)


def test_related_may_reference_accepts_reference_shapes(tmp):
    del tmp
    anchor = '<p dir="auto"><a href="#101">reference</a></p>'
    www_host = 'www.' + 'github.com'
    enterprise_host = 'ghe.' + 'example'
    cases = (
        ('hash', '#101', anchor),
        ('uppercase-gh', 'GH-101', anchor),
        ('lowercase-gh', 'gh-7', anchor),
        ('repository-hash', 'owner/repo#101', anchor),
        ('absolute-issue',
         'https://github.com/owner/repo/issues/101', anchor),
        ('default-port',
         'https://github.com:443/owner/repo/issues/101', anchor),
        ('www-http',
         f'http://{www_host}/owner/repo/issues/101', anchor),
        ('root-relative', '/owner/repo/issues/101', anchor),
        ('markdown-link', '[x](/owner/repo/issues/101)', anchor),
        ('reference-definition',
         '[x]: https://github.com/owner/repo/issues/101', anchor),
        ('angle-autolink',
         '<https://github.com/owner/repo/pull/5>', anchor),
        ('enterprise-host',
         f'https://{enterprise_host}/o/r/issues/5', anchor),
        ('percent-encoding',
         'https://github.com/owner/repo/%69ssues/101', anchor),
        ('numeric-entity', '&#35;101', _text_html('#101')),
        ('named-entity', '&num;101', _text_html('#101')),
        ('code-span', '`#101`', GITHUB_HTML['inline_code']),
        ('fenced-code', '```\n#101\n```', GITHUB_HTML['fenced_code']),
    )
    failures = []
    for name, spelling, rendered in cases:
        source = _valid_body(references=spelling)
        sections = PR_BODY.parse_rendered(
            _valid_html(references=rendered), 'owner/repo')
        full = PR_BODY.related_may_reference(source, sections, TEMPLATE)
        raw = PR_BODY.related_may_reference(source, [], TEMPLATE)
        if not full or not raw:
            failures.append((name, full, raw))
    assert failures == [], failures


def test_related_may_reference_accepts_decorated_related_heading(tmp):
    del tmp
    source = _valid_body().replace(
        '## Related Issues and Pull Requests',
        '### **Related Issues and Pull Requests:**')
    assert PR_BODY.related_may_reference(source, [], TEMPLATE)


def test_related_may_reference_rejects_missing_related_reference(tmp):
    del tmp
    cases = (
        ('prose', _valid_body('see the tracker'),
         _text_html('see the tracker')),
        ('summary-only', _layout_body(
            ('Summary', 'Mentions #101.'),
            ('Related Issues and Pull Requests', 'see the tracker'),
            ('Changes', '- One change'),
            ('Testing', 'Ran the suite.')),
         _text_html('see the tracker')),
        ('empty', '', _text_html('see the tracker')),
    )
    failures = []
    for name, source, rendered in cases:
        found = _may_reference(source, rendered)
        if found:
            failures.append(name)
    source = _layout_body(
        ('Summary', 'Mentions #101.'),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))
    if _may_reference(source, _text_html('see the tracker')):
        failures.append('no-related-heading')
    assert failures == [], failures


def test_related_may_reference_stops_at_template_heading(tmp):
    del tmp
    source = _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', 'see the tracker'),
        ('Changes', '#101'),
        ('Testing', 'Ran the suite.'))
    assert not _may_reference(source, _text_html('see the tracker'))


def test_related_may_reference_keeps_non_template_heading_in_region(tmp):
    del tmp
    source = _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', 'see the tracker'),
        ('Notes', '#101'),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))
    assert PR_BODY.related_may_reference(source, [], TEMPLATE)


def test_related_may_reference_accepts_rendered_link_alone(tmp):
    del tmp
    rendered = _valid_html(
        references='<p dir="auto"><a href="#101">reference</a></p>')
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.related_may_reference('nothing', sections, TEMPLATE)


def test_closing_issues_reads_the_governing_keyword(tmp):
    del tmp
    rendered = _valid_html(references=(
        f'References {_issue_html(101)}, Fixes {_issue_html(102)}'))
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.referenced_issues(sections) == [101, 102]
    assert PR_BODY.closing_issues(sections) == [102]


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
    )
    for references, closing in cases:
        rendered = _valid_html(references=references)
        sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(sections) == closing, references


def test_every_closing_keyword_spelling_closes(tmp):
    del tmp
    spellings = (
        'close', 'closes', 'closed', 'fix', 'fixes', 'fixed',
        'resolve', 'resolves', 'resolved')
    assert PR_BODY._CLOSING_KEYWORDS == frozenset(spellings)
    for keyword in spellings:
        rendered = _valid_html(references=f'{keyword} {_issue_html(101)}')
        sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(sections) == [101], keyword


def test_closing_issues_checks_every_section(tmp):
    del tmp
    summary = f'<p dir="auto">Also fixes {_issue_html(104)}.</p>'
    rendered = _valid_html(
        references=f'References {_issue_html(101)}').replace(
            _text_html('One sentence.'), summary)
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.referenced_issues(sections) == [101]
    assert PR_BODY.closing_issues(sections) == [104]


def test_closing_keyword_does_not_cross_a_section_boundary(tmp):
    del tmp
    rendered = _valid_html(
        references=_issue_html(101)).replace(
            _text_html('One sentence.'), _text_html('One sentence. Fixes'))
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(sections) == []


def test_closing_list_does_not_cross_a_section_boundary(tmp):
    del tmp
    summary = f'<p dir="auto">Fixes {_issue_html(101)}</p>'
    rendered = _valid_html(
        references=_issue_html(104)).replace(
            _text_html('One sentence.'), summary)
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(sections) == [101]


def test_section_boundary_resets_without_block_tags(tmp):
    del tmp
    rendered = (
        '<h2 dir="auto">Summary</h2>\n'
        f'Fixes {_issue_html(101)}\n'
        '<h2 dir="auto">Related Issues and Pull Requests</h2>\n'
        f'{_issue_html(104)}\n')
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(sections) == [101]


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
        sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
        assert PR_BODY.closing_issues(sections) == closing, references


def test_keyword_inside_fenced_code_governs_no_anchor(tmp):
    del tmp
    rendered = _valid_html(references=(
        '<pre class="notranslate"><code class="notranslate">Fixes\n'
        '</code></pre>\n'
        f'<p dir="auto">{_issue_html(102)}</p>'))
    sections = PR_BODY.parse_rendered(rendered, 'owner/repo')
    assert PR_BODY.closing_issues(sections) == []


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbody_')


if __name__ == '__main__':
    raise SystemExit(main())
