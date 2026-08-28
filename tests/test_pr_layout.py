#!/usr/bin/env python3
"""Pull-request template layout validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import PR_BODY, TEMPLATE, _layout_body  # noqa: E402


def test_layout_accepts_required_sections_without_optional_footer(tmp):
    del tmp
    body = _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))
    assert PR_BODY.layout_errors(body, TEMPLATE) == []


def test_layout_reports_each_missing_or_empty_section(tmp):
    del tmp
    body = _layout_body(
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Changes', ''),
        ('Testing', 'Ran the suite.'),
        ('Breaking Changes', ''))
    errors = PR_BODY.layout_errors(body, TEMPLATE)
    assert 'Required section "Summary" is missing.' in errors
    assert 'Section "Changes" is empty.' in errors
    assert 'Section "Breaking Changes" is empty.' in errors


def test_layout_rejects_markers_that_render_as_empty(tmp):
    del tmp
    for content in ('-', '1.', '>'):
        body = _layout_body(
            ('Summary', 'One sentence.'),
            ('Related Issues and Pull Requests', 'Fixes #91'),
            ('Changes', content),
            ('Testing', 'Ran the suite.'))
        assert 'Section "Changes" is empty.' in PR_BODY.layout_errors(
            body, TEMPLATE)


def test_layout_reports_unknown_duplicate_and_out_of_order_sections(tmp):
    del tmp
    body = _layout_body(
        ('Summary', 'First.'),
        ('Changes', '- Too early'),
        ('Notes', 'Unknown.'),
        ('Summary', 'Again.'),
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Testing', 'Ran the suite.'))
    errors = PR_BODY.layout_errors(body, TEMPLATE)
    assert 'Section `Notes` is not defined by the template.' in errors
    assert 'Section "Summary" appears more than once.' in errors
    assert 'Section "Related Issues and Pull Requests" is out of order.' \
        in errors


def test_layout_names_retained_template_instructions_separately(tmp):
    del tmp
    errors = PR_BODY.layout_errors(TEMPLATE, TEMPLATE)
    assert 'Remove the template instruction comments.' in errors


def test_layout_resolves_code_before_template_comments(tmp):
    del tmp
    instruction = (
        '<!-- required: bullet list of concrete changes — files, modules, '
        'behavior. -->')
    changes = (
        f'- Changed `{instruction}` to prose.',
        f'    {instruction}\n- A visible change',
    )
    for content in changes:
        body = _layout_body(
            ('Summary', 'One sentence.'),
            ('Related Issues and Pull Requests', 'Fixes #91'),
            ('Changes', content),
            ('Testing', 'Ran the suite.'))
        assert PR_BODY.layout_errors(body, TEMPLATE) == []


def test_layout_ignores_headings_inside_raw_html_blocks(tmp):
    del tmp
    body = _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Changes', '<pre>\n## literal heading\n</pre>\n- A change'),
        ('Testing', 'Ran the suite.'))
    assert PR_BODY.layout_errors(body, TEMPLATE) == []


def test_layout_quotes_unknown_names_as_safe_inline_code(tmp):
    del tmp
    name = 'Notes ``for #255``'
    body = _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Changes', '- A change'),
        ('Testing', 'Ran the suite.'),
        (name, 'Unknown.'))
    quoted = '```Notes ``for #255`````'
    assert f'Section {quoted} is not defined by the template.' \
        in PR_BODY.layout_errors(body, TEMPLATE)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prlayout_')


if __name__ == '__main__':
    raise SystemExit(main())
