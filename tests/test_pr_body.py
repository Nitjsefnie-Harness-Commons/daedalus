#!/usr/bin/env python3
"""Rendered pull-request body reference parsing and its CLI."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    GITHUB_HTML, GITHUB_ISSUE_101, PR_BODY, ROOT, _html_body,
    _issue_html, _text_html,
)


REPOSITORY = 'Nitjsefnie-Harness-Commons/daedalus'
RELATED = 'Related Issues and Pull Requests'


def _related(content, heading=RELATED):
    return _html_body((heading, content))


def test_parser_accepts_an_issue_anchor_in_the_rendered_section(tmp):
    del tmp
    rendered = _related(f'<p dir="auto">Fixes {GITHUB_ISSUE_101}</p>')
    assert PR_BODY.referenced_issues(rendered) == [101]


def test_parser_returns_nothing_for_empty_or_missing_sections(tmp):
    del tmp
    bodies = (
        None,
        '',
        _html_body(('Summary', f'Fixes {_issue_html(31)}')),
        '<p dir="auto">Fixes <a href="https://github.com/owner/repo/'
        'issues/31">#31</a></p>',
        _related(''),
    )
    assert [PR_BODY.referenced_issues(body) for body in bodies] == [
        [], [], [], [], [],
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


def test_parser_rejects_a_non_html_render_response(tmp):
    del tmp
    try:
        PR_BODY.referenced_issues('upstream returned plain text')
    except ValueError as error:
        assert 'rendered HTML' in str(error), error
    else:
        raise AssertionError('a non-HTML render response was accepted')


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


def test_cli_prints_template_instruction_fingerprints(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         '--instruction-fingerprints',
         str(ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md')],
        input='', text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert 'The PR Report Contract, rendered as a form.' in lines
    assert any(line.startswith('required: 1-2 sentences') for line in lines)
    assert any(line.startswith('optional, last line.') for line in lines)


def test_cli_rejects_extra_arguments(tmp):
    del tmp
    script = str(ROOT / 'scripts' / 'ci' / 'pr_body.py')
    result = subprocess.run(
        [sys.executable, script, REPOSITORY, 'template', 'extra'],
        input='', text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    usage = f'usage: {script} repository template\n'
    assert result.stderr == usage, result.stderr


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbody_')


if __name__ == '__main__':
    raise SystemExit(main())
