#!/usr/bin/env python3
"""Pull-request issue references and the workflow that enforces claims."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


PR_CLAIM = _util.load(ROOT / 'scripts' / 'ci' / 'pr_claim.py')
SECTION = '## Related Issues and Pull Requests\n'


def test_parser_accepts_a_reference_in_the_real_template(tmp):
    del tmp
    template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
        encoding='utf-8')
    body = template.replace('\nFixes #\n', '\nFixes #314\n', 1)
    assert body != template, 'the template no longer carries its Fixes marker'
    assert PR_CLAIM.referenced_issues(body) == [314]


def test_parser_returns_nothing_for_empty_or_missing_sections(tmp):
    del tmp
    assert PR_CLAIM.referenced_issues(None) == []
    assert PR_CLAIM.referenced_issues('') == []
    assert PR_CLAIM.referenced_issues('## Summary\nFixes #31\n') == []
    assert PR_CLAIM.referenced_issues('Fixes #31\n') == []
    assert PR_CLAIM.referenced_issues(SECTION + '\n## Changes\n') == []


def test_parser_normalizes_windows_and_lone_carriage_returns(tmp):
    del tmp
    crlf = '# Summary\r\ntext\r\n' + SECTION.replace('\n', '\r\n')
    assert PR_CLAIM.referenced_issues(crlf + 'Fixes #32\r\n') == [32]
    assert PR_CLAIM.referenced_issues(
        '# Summary\rtext\r' + SECTION.replace('\n', '\r') + '#33\r') == [33]


def test_parser_removes_complete_and_unterminated_html_comments(tmp):
    del tmp
    body = (SECTION + '<!-- hidden across\nFixes #40\n-->\n'
            'Fixes #41\n')
    assert PR_CLAIM.referenced_issues(body) == [41]
    body = SECTION + 'Fixes #42\n<!-- #43\nFixes #44\n'
    assert PR_CLAIM.referenced_issues(body) == [42]
    hidden_section = '<!--\n' + SECTION + 'Fixes #45\n-->'
    assert PR_CLAIM.referenced_issues(hidden_section) == []


def test_parser_accepts_only_the_first_exact_atx_section_heading(tmp):
    del tmp
    body = ('   ### rELATED iSSUES AND pULL rEQUESTS   ###\n'
            'Fixes #50\n# Changes\nFixes #51\n'
            + SECTION + 'Fixes #52\n')
    assert PR_CLAIM.referenced_issues(body) == [50]
    assert PR_CLAIM.referenced_issues(
        '    ' + SECTION + 'Fixes #53\n') == []
    assert PR_CLAIM.referenced_issues(
        '## Related Issues and Pull Requests later\nFixes #54\n') == []


def test_parser_stops_at_the_next_atx_heading(tmp):
    del tmp
    body = SECTION + 'Fixes #60\n###### Testing\nFixes #61\n'
    assert PR_CLAIM.referenced_issues(body) == [60]


def test_parser_removes_fenced_and_inline_code(tmp):
    del tmp
    body = (SECTION + '```text\nFixes #70\n```\nFixes #71\n'
            '~~~\nFixes #72\n')
    assert PR_CLAIM.referenced_issues(body) == [71]
    body = SECTION + 'Use `Fixes #73` or ``code ` #74``. Fixes #75\n'
    assert PR_CLAIM.referenced_issues(body) == [75]


def test_parser_filters_and_deduplicates_references_in_order(tmp):
    del tmp
    body = (SECTION + '#3 #1 #3 #0 abc#12 ##12 x_#13 '
            '(#42) and -#14\n')
    assert PR_CLAIM.referenced_issues(body) == [3, 1, 42, 14]


def test_cli_prints_one_issue_number_per_line(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_claim.py')],
        input=SECTION + 'Fixes #81 and #82 and #81\n', text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '81\n82\n', repr(result.stdout)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prclaim_')


if __name__ == '__main__':
    raise SystemExit(main())
