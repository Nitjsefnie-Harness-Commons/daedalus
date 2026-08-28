#!/usr/bin/env python3
"""Rendered pull-request body reference parsing and its CLI."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    GITHUB_FOOTNOTE_HTML, GITHUB_FOOTNOTE_SENTINEL_HTML, GITHUB_HTML,
    GITHUB_ISSUE_101, PR_BODY, ROOT, TEMPLATE, _html_body, _issue_html,
    _text_html, _valid_body, _valid_html,
)


REPOSITORY = 'Nitjsefnie-Harness-Commons/daedalus'
RELATED = 'Related Issues and Pull Requests'


def _related(content, heading=RELATED):
    return _html_body((heading, content))


def _analysis_outcome(rendered):
    try:
        return PR_BODY.analyze(rendered, REPOSITORY, TEMPLATE)
    except ValueError as error:
        return type(error), str(error)


def test_parser_accepts_an_issue_anchor_in_the_rendered_section(tmp):
    del tmp
    rendered = _related(f'<p dir="auto">Fixes {GITHUB_ISSUE_101}</p>')
    assert PR_BODY.referenced_issues(rendered) == [101]


def test_parser_returns_nothing_for_missing_or_empty_sections(tmp):
    del tmp
    bodies = (
        _html_body(('Summary', f'Fixes {_issue_html(31)}')),
        '<p dir="auto">Fixes <a href="https://github.com/owner/repo/'
        'issues/31">#31</a></p>',
        _related(''),
    )
    assert [PR_BODY.referenced_issues(body) for body in bodies] == [
        [], [], [],
    ]


def test_parser_uses_the_first_matching_section_and_stops_at_a_heading(tmp):
    del tmp
    rendered = _html_body(
        ('Summary', _text_html('Summary.')),
        ('rELATED iSSUES AND pULL rEQUESTS',
         f'Fixes {_issue_html(32)}'),
        ('Changes', f'Fixes {_issue_html(33)}'),
        (RELATED, f'Fixes {_issue_html(34)}'))
    assert PR_BODY.referenced_issues(rendered) == [32]


def test_parser_accepts_rendered_heading_depth_emphasis_and_colon(tmp):
    del tmp
    rendered = (
        '<h3 dir="auto"><strong>Related Issues and Pull Requests'
        '</strong>:</h3>\n'
        f'<p dir="auto">Fixes {_issue_html(35)}</p>')
    assert PR_BODY.referenced_issues(rendered) == [35]


def test_github_visible_reference_fixtures_remain_visible(tmp):
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
        found = PR_BODY.referenced_issues(_related(GITHUB_HTML[name]))
        if found != [101]:
            failures.append((name, found))
    assert failures == [], failures


def test_github_nontext_reference_fixtures_remain_hidden(tmp):
    del tmp
    names = (
        'balanced_destination',
        'quoted_attribute',
        'multiline_attribute',
        'image_destination',
        'html_attribute',
        'inline_code',
        'fenced_code',
        'indented_code',
    )
    failures = []
    for name in names:
        found = PR_BODY.referenced_issues(_related(GITHUB_HTML[name]))
        if found:
            failures.append((name, found))
    assert failures == [], failures


def test_parser_filters_repository_and_pull_request_targets(tmp):
    del tmp
    rendered = _related(
        f'{_issue_html(40, REPOSITORY)} '
        f'{_issue_html(41, "another/project")} '
        '<a href="mailto:#43">#43</a> '
        '<a href="https://github.com/Nitjsefnie-Harness-Commons/'
        'daedalus/pull/42">#42</a>')
    assert PR_BODY.referenced_issues(rendered, REPOSITORY) == [40]


def test_parser_deduplicates_issue_anchors_in_document_order(tmp):
    del tmp
    rendered = _related(
        f'{_issue_html(3)} {_issue_html(1)} {_issue_html(3)} '
        f'{_issue_html(0)} {_issue_html(42)}')
    assert PR_BODY.referenced_issues(rendered, 'owner/repo') == [3, 1, 42]


def test_parser_ignores_issue_numbers_too_wide_for_github(tmp):
    del tmp
    number = '9' * 5000
    rendered = _related(
        f'<a href="https://github.com/owner/repo/issues/{number}">'
        f'#{number}</a>')
    assert PR_BODY.referenced_issues(rendered, 'owner/repo') == []


def test_parser_rejects_unusable_render_responses(tmp):
    del tmp
    cases = (
        '',
        'upstream returned plain text',
        '<h2>Summary</h2><',
        '<h2>Summary</h2><p>text',
        '<h2>Summary</h2><h2',
        '<h2>Summary</h2><!-- unfinished',
        '<h2>Summary',
        '<h2>Summary<h3>Nested</h3></h2>',
        '<h2>Summary</h2><p></div>',
    )
    accepted = []
    for rendered in cases:
        try:
            PR_BODY.referenced_issues(rendered)
        except ValueError as error:
            assert 'rendered HTML' in str(error), error
        else:
            accepted.append(rendered)
    assert accepted == [], accepted


def test_render_sentinel_is_removed_without_changing_analysis(tmp):
    del tmp
    sentinel = 'pr-gate-sentinel-' + ('a' * 64)
    marker = f'<p dir="auto">{sentinel}</p>'
    cases = (
        (_valid_html(), f'{_valid_html()}\n{marker}'),
        (GITHUB_FOOTNOTE_HTML, GITHUB_FOOTNOTE_SENTINEL_HTML),
    )
    for expected, rendered in cases:
        stripped = PR_BODY.strip_render_sentinel(rendered, sentinel)
        assert _analysis_outcome(stripped) == _analysis_outcome(expected)


def test_render_sentinel_preserves_other_top_level_analysis(tmp):
    del tmp
    sentinel = 'pr-gate-sentinel-' + ('b' * 64)
    marker = f'<p dir="auto">{sentinel}</p>'
    cases = (
        ('<hr>', f'<hr>\n{marker}'),
        ('<hr />', f'<hr />\n{marker}'),
        ('<!-- renderer note -->',
         f'<!-- renderer note -->\n{marker}'),
        (_valid_html(), f'{_valid_html()}\r\n{marker}'),
    )
    for expected, rendered in cases:
        stripped = PR_BODY.strip_render_sentinel(rendered, sentinel)
        assert _analysis_outcome(stripped) == _analysis_outcome(expected)


def test_render_sentinel_rejects_unusable_evidence(tmp):
    del tmp
    sentinel = 'pr-gate-sentinel-' + ('c' * 64)
    marker = f'<p dir="auto">{sentinel}</p>'
    cases = (
        ('invalid-shape', 'sentinel', marker),
        ('mismatched', sentinel, f'<div></p>{marker}'),
        ('duplicate', sentinel, f'{marker}\n{marker}'),
        ('comment', sentinel, f'<p>{sentinel[:30]}'
         f'<!-- injected -->{sentinel[30:]}</p>'),
        ('element', sentinel, f'<p>{sentinel[:30]}'
         f'<em>{sentinel[30:]}</em></p>'),
        ('processing', sentinel, f'<p>{sentinel[:30]}'
         f'<?evidence?>{sentinel[30:]}</p>'),
        ('declaration', sentinel, f'<p>{sentinel[:30]}'
         f'<!DOCTYPE html>{sentinel[30:]}</p>'),
        ('entity', sentinel, f'<p>&#112;{sentinel[1:]}</p>'),
        ('self-closing', sentinel, f'<p><br/>{sentinel}</p>'),
        ('unknown-declaration', sentinel,
         f'<p><![CDATA[evidence]]>{sentinel}</p>'),
    )
    accepted = []
    for name, value, rendered in cases:
        try:
            PR_BODY.strip_render_sentinel(rendered, value)
        except ValueError as error:
            assert 'render' in str(error), (name, error)
        else:
            accepted.append(name)
    assert accepted == [], accepted


def test_numeric_entities_do_not_hide_a_retained_comment(tmp):
    del tmp
    entities = (
        ('decimal', '&#60;', '&#62;'),
        ('lowercase', '&#x3c;', '&#x3e;'),
        ('uppercase', '&#X3C;', '&#X3E;'),
    )
    failures = []
    for name, left, right in entities:
        literal = f'{left}!-- optional --{right}'
        source = _valid_body().replace(
            '- One change', f'<!-- optional -->\n- Literal {literal}.')
        rendered = _valid_html(
            changes=f'<ul><li>Literal {literal}.</li></ul>')
        _, errors = PR_BODY.analyze(
            rendered, REPOSITORY, TEMPLATE, source)
        if 'Remove the template instruction comments.' not in errors:
            failures.append((name, errors))
    assert failures == [], failures


def test_parser_ignores_a_named_anchor_without_a_destination(tmp):
    del tmp
    rendered = _related(
        GITHUB_HTML['named_anchor']
        + f'<p>Fixes {_issue_html(101, REPOSITORY)}</p>')
    assert PR_BODY.referenced_issues(rendered, REPOSITORY) == [101]


def test_parser_rejects_a_self_closing_content_element(tmp):
    del tmp
    rendered = _related('<p/>')
    try:
        PR_BODY.referenced_issues(rendered, REPOSITORY)
    except ValueError as error:
        assert 'self-closing' in str(error), error
    else:
        raise AssertionError('a self-closing content element was accepted')


def test_parser_rejects_a_malformed_repository(tmp):
    del tmp
    rendered = _related('<p>No reference.</p>')
    try:
        PR_BODY.referenced_issues(rendered, 'missing-owner')
    except ValueError as error:
        assert 'owner/name' in str(error), error
    else:
        raise AssertionError('a malformed repository was accepted')


def test_cli_emits_tagged_analysis_from_rendered_html(tmp):
    del tmp
    rendered = _html_body(
        ('Summary', _text_html('A summary.')),
        (RELATED, f'Fixes {_issue_html(81, REPOSITORY)}'),
        ('Changes', '<ul><li>A change</li></ul>'),
        ('Testing', _text_html('Ran tests.')))
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         REPOSITORY,
         str(ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md')],
        input=rendered, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'issue:81\n', repr(result.stdout)


def test_cli_rejects_an_unusable_render_response(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         REPOSITORY,
         str(ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md')],
        input='not rendered HTML', text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert 'rendered HTML' in result.stderr, result.stderr


def test_cli_reports_an_unreadable_template(tmp):
    missing = Path(tmp) / 'missing-template.md'
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         REPOSITORY, str(missing)], input=_valid_html(), text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    assert 'could not analyze rendered HTML:' in result.stderr, result.stderr


def test_cli_reports_a_template_heading_without_an_instruction(tmp):
    template = Path(tmp) / 'template.md'
    template.write_text(
        '## Summary\n\n## Changes\n<!-- required: explain -->\n',
        encoding='utf-8')
    rendered = _html_body(
        ('Summary', _text_html('Summary.')),
        ('Changes', _text_html('Change.')))
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         REPOSITORY, str(template)],
        input=rendered, text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    assert 'Summary' in result.stderr, result.stderr
    assert 'instruction comment' in result.stderr, result.stderr


def test_cli_rejects_extra_arguments(tmp):
    del tmp
    script = str(ROOT / 'scripts' / 'ci' / 'pr_body.py')
    result = subprocess.run(
        [sys.executable, script, REPOSITORY, 'template', 'source', 'extra'],
        input='', text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    usage = (f'usage: {script} [--sentinel value] repository template '
             '[source-body]\n')
    assert result.stderr == usage, result.stderr


def test_cli_rejects_incomplete_sentinel_arguments(tmp):
    del tmp
    script = str(ROOT / 'scripts' / 'ci' / 'pr_body.py')
    sentinel = 'pr-gate-sentinel-' + ('d' * 64)
    usage = (f'usage: {script} [--sentinel value] repository template '
             '[source-body]\n')
    for arguments in (('--sentinel',), ('--sentinel', sentinel)):
        result = subprocess.run(
            [sys.executable, script, *arguments], input='', text=True,
            capture_output=True, timeout=30)
        assert result.returncode == 2, (arguments, result.stderr)
        assert result.stdout == '', repr(result.stdout)
        assert result.stderr == usage, (arguments, result.stderr)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbody_')


if __name__ == '__main__':
    raise SystemExit(main())
