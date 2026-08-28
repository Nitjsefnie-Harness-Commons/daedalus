#!/usr/bin/env python3
"""Rendered pull-request body reference parsing and its CLI."""
import subprocess
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    GITHUB_HTML, GITHUB_ISSUE_101, PR_BODY, ROOT, _html_body,
    _issue_html, _text_html,
)


REPOSITORY = 'Nitjsefnie-Harness-Commons/daedalus'
RELATED = 'Related Issues and Pull Requests'


class _CommentCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.comments = []

    def handle_comment(self, data):
        self.comments.append(data)


def _related(content, heading=RELATED):
    return _html_body((heading, content))


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


def test_parser_rejects_an_anchor_without_a_destination(tmp):
    del tmp
    rendered = _related('<p>Fixes <a>#101</a></p>')
    try:
        PR_BODY.referenced_issues(rendered, REPOSITORY)
    except ValueError as error:
        assert 'anchor' in str(error), error
    else:
        raise AssertionError('an anchor without a destination was accepted')


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
         '--instruction-fingerprints', str(missing)],
        input='', text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    assert 'could not read template:' in result.stderr, result.stderr


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


def test_cli_prints_template_instruction_fingerprints(tmp):
    del tmp
    template_path = ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md'
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         '--instruction-fingerprints',
         str(template_path)],
        input='', text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    fingerprints = result.stdout.splitlines()
    collector = _CommentCollector()
    collector.feed(template_path.read_text(encoding='utf-8'))
    collector.close()
    comments = collector.comments
    expected = []
    for comment in comments:
        lines = [line.strip() for line in comment.splitlines()
                 if line.strip()]
        expected.append(lines[0])
    assert all(fingerprints), fingerprints
    assert Counter(fingerprints) == Counter(expected), (
        fingerprints, expected)


def test_cli_rejects_extra_arguments(tmp):
    del tmp
    script = str(ROOT / 'scripts' / 'ci' / 'pr_body.py')
    result = subprocess.run(
        [sys.executable, script, REPOSITORY, 'template', 'source', 'extra'],
        input='', text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    usage = f'usage: {script} repository template [source-body]\n'
    assert result.stderr == usage, result.stderr


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbody_')


if __name__ == '__main__':
    raise SystemExit(main())
