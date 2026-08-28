#!/usr/bin/env python3
"""Pull-request body reference parsing and its CLI."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import PR_BODY, ROOT, SECTION, _layout_body  # noqa: E402


def test_parser_accepts_a_reference_in_the_real_template(_tmp):
    template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
        encoding='utf-8')
    body = template.replace('\nFixes #\n', '\nFixes #314\n', 1)
    assert body != template, 'the template no longer carries its Fixes marker'
    assert PR_BODY.referenced_issues(body) == [314]


def test_parser_returns_nothing_for_empty_or_missing_sections(_tmp):
    assert PR_BODY.referenced_issues(None) == []
    assert PR_BODY.referenced_issues('') == []
    assert PR_BODY.referenced_issues('## Summary\nFixes #31\n') == []
    assert PR_BODY.referenced_issues('Fixes #31\n') == []
    assert PR_BODY.referenced_issues(SECTION + '\n## Changes\n') == []


def test_parser_normalizes_windows_and_lone_carriage_returns(_tmp):
    crlf = '# Summary\r\ntext\r\n' + SECTION.replace('\n', '\r\n')
    assert PR_BODY.referenced_issues(crlf + 'Fixes #32\r\n') == [32]
    assert PR_BODY.referenced_issues(
        '# Summary\rtext\r' + SECTION.replace('\n', '\r') + '#33\r') == [33]


def test_parser_removes_complete_and_unterminated_html_comments(_tmp):
    body = (SECTION + '<!-- hidden across\nFixes #40\n-->\n'
            'Fixes #41\n')
    assert PR_BODY.referenced_issues(body) == [41]
    body = SECTION + 'Fixes #42\n<!-- #43\nFixes #44\n'
    assert PR_BODY.referenced_issues(body) == [42]
    hidden_section = '<!--\n' + SECTION + 'Fixes #45\n-->'
    assert PR_BODY.referenced_issues(hidden_section) == []


def test_parser_accepts_only_the_first_exact_atx_section_heading(_tmp):
    body = ('   ### rELATED iSSUES AND pULL rEQUESTS   ###\n'
            'Fixes #50\n# Changes\nFixes #51\n'
            + SECTION + 'Fixes #52\n')
    assert PR_BODY.referenced_issues(body) == [50]
    assert PR_BODY.referenced_issues(
        '    ' + SECTION + 'Fixes #53\n') == []
    assert PR_BODY.referenced_issues(
        '## Related Issues and Pull Requests later\nFixes #54\n') == []


def test_parser_stops_at_the_next_atx_heading(_tmp):
    body = SECTION + 'Fixes #60\n###### Testing\nFixes #61\n'
    assert PR_BODY.referenced_issues(body) == [60]


def test_parser_stops_at_a_setext_heading(_tmp):
    body = SECTION + 'Fixes #62\nSummary\n-------\nFixes #63\n'
    assert PR_BODY.referenced_issues(body) == [62]


def test_parser_does_not_treat_list_or_quote_as_setext_heading(tmp):
    del tmp
    bodies = [
        SECTION + '- Fixes #65\n---\n',
        SECTION + '> Fixes #66\n---\n',
    ]
    for number, body in zip((65, 66), bodies):
        assert PR_BODY.referenced_issues(body) == [number]


def test_parser_accepts_a_setext_section_heading(tmp):
    del tmp
    body = ('Related Issues and Pull Requests\n'
            '--------------------------------\n')
    assert PR_BODY.referenced_issues(body + 'Fixes #67\n') == [67]


def test_parser_ignores_atx_headings_inside_fenced_code(tmp):
    del tmp
    body = SECTION + '```markdown\n## Changes\n```\nFixes #64\n'
    assert PR_BODY.referenced_issues(body) == [64]


def test_parser_keeps_comment_markers_inside_fenced_code_literal(tmp):
    del tmp
    body = ('## Summary\n```html\n<!-- a template comment\n```\n'
            + SECTION + 'Fixes #68\n')
    assert PR_BODY.referenced_issues(body) == [68]
    body = ('## Summary\n<!-- a hidden fence\n```\n-->\n'
            + SECTION + 'Fixes #69\n')
    assert PR_BODY.referenced_issues(body) == [69]


def test_parser_resolves_code_and_raw_html_before_structure(tmp):
    del tmp
    body = (SECTION + 'The token `<!--` is literal; Fixes #83\n'
            '## Changes\n- A change\n')
    assert PR_BODY.referenced_issues(body) == [83]
    body = (SECTION + '<pre>\nFixes #84\n## literal heading\n</pre>\n'
            'Fixes #85\n')
    assert PR_BODY.referenced_issues(body) == [85]


def test_parser_removes_fenced_and_inline_code(tmp):
    del tmp
    body = (SECTION + '```text\nFixes #70\n```\nFixes #71\n'
            '~~~\nFixes #72\n')
    assert PR_BODY.referenced_issues(body) == [71]
    body = SECTION + 'Use `Fixes #73` or ``code ` #74``. Fixes #75\n'
    assert PR_BODY.referenced_issues(body) == [75]
    body = SECTION + 'A stray ` in prose does not hide Fixes #76\n'
    assert PR_BODY.referenced_issues(body) == [76]


def test_parser_ignores_indented_code_blocks(tmp):
    del tmp
    body = SECTION + '    Fixes #77\nFixes #78\n'
    assert PR_BODY.referenced_issues(body) == [78]


def test_parser_ignores_backslash_escaped_references(tmp):
    del tmp
    body = SECTION + r'Literal \#79, real #80.'
    assert PR_BODY.referenced_issues(body) == [80]


def test_parser_accepts_emphasized_or_colon_section_headings(tmp):
    del tmp
    headings = (
        '## **Related Issues and Pull Requests**:\n',
        '__Related Issues and Pull Requests__:\n'
        '=========================================\n',
    )
    for number, heading in zip((81, 82), headings):
        assert PR_BODY.referenced_issues(
            heading + f'Fixes #{number}\n') == [number]


def test_parser_filters_and_deduplicates_references_in_order(tmp):
    del tmp
    body = (SECTION + '#3 #1 #3 #0 abc#12 ##12 x_#13 '
            '(#42) and -#14\n')
    assert PR_BODY.referenced_issues(body) == [3, 1, 42, 14]


def test_parser_ignores_html_numeric_entities(tmp):
    del tmp
    body = SECTION + '&#8212; is an em dash. Fixes #15\n'
    assert PR_BODY.referenced_issues(body) == [15]


def test_parser_ignores_link_destinations_and_html_attributes(tmp):
    del tmp
    hidden = (
        '[documentation](#84)',
        '![diagram](#84)',
        '<a href="#84">documentation</a>',
    )
    for content in hidden:
        assert PR_BODY.referenced_issues(SECTION + content) == []
    assert PR_BODY.referenced_issues(
        SECTION + '[Fixes #85](https://example.com)') == [85]


def test_cli_ignores_reference_numbers_too_wide_for_github(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py')],
        input=SECTION + 'Fixes #' + ('9' * 5000), text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '', repr(result.stdout)


def test_cli_prints_one_issue_number_per_line(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py')],
        input=SECTION + 'Fixes #81 and #82 and #81\n', text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '81\n82\n', repr(result.stdout)
    body = _layout_body(
        ('Summary', 'A summary.'),
        ('Related Issues and Pull Requests', 'Fixes #81'),
        ('Changes', '- A change'),
        ('Testing', 'Ran tests.'))
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         str(ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md')],
        input=body, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'issue:81\n', repr(result.stdout)


def test_cli_rejects_extra_arguments(tmp):
    del tmp
    script = str(ROOT / 'scripts' / 'ci' / 'pr_body.py')
    result = subprocess.run(
        [sys.executable, script, 'template', 'extra'], input='', text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == '', repr(result.stdout)
    assert result.stderr == f'usage: {script} [template]\n', result.stderr


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbody_')


if __name__ == '__main__':
    raise SystemExit(main())
