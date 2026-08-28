#!/usr/bin/env python3
"""Rendered pull-request template layout validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    GITHUB_HTML, PR_BODY, TEMPLATE, _html_body, _issue_html, _text_html,
    _valid_html,
)


RELATED = 'Related Issues and Pull Requests'


def test_layout_accepts_required_sections_without_optional_footer(tmp):
    del tmp
    assert PR_BODY.layout_errors(_valid_html(), TEMPLATE) == []


def test_layout_reports_each_missing_or_empty_section(tmp):
    del tmp
    rendered = _html_body(
        (RELATED, f'Fixes {_issue_html(91)}'),
        ('Changes', ''),
        ('Testing', _text_html('Ran the suite.')),
        ('Breaking Changes', ''))
    errors = PR_BODY.layout_errors(rendered, TEMPLATE)
    assert 'Required section "Summary" is missing.' in errors
    assert 'Section "Changes" is empty.' in errors
    assert 'Section "Breaking Changes" is empty.' in errors


def test_layout_rejects_constructs_that_render_as_empty(tmp):
    del tmp
    for name in (
            'empty_list', 'empty_ordered', 'empty_quote',
            'link_definition'):
        rendered = _valid_html(changes=GITHUB_HTML[name])
        assert 'Section "Changes" is empty.' in PR_BODY.layout_errors(
            rendered, TEMPLATE), name


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
        assert PR_BODY.layout_errors(rendered, TEMPLATE) == [], image


def test_layout_treats_invisible_text_as_empty(tmp):
    del tmp
    invisible = (
        '\u200b', '\ufe0f', '\u034f', '\u115f', '\u1160', '\u3164',
        '\uffa0', '\u2800')
    for character in invisible:
        rendered = _valid_html(
            changes=f'<p dir="auto">{character}</p>')
        assert 'Section "Changes" is empty.' in PR_BODY.layout_errors(
            rendered, TEMPLATE), f'U+{ord(character):04X}'


def test_layout_treats_empty_or_invisible_images_as_empty(tmp):
    del tmp
    for image in (
            GITHUB_HTML['empty_image'],
            GITHUB_HTML['zero_size_image']):
        rendered = _valid_html(changes=image)
        assert 'Section "Changes" is empty.' in PR_BODY.layout_errors(
            rendered, TEMPLATE), image


def test_layout_rejects_a_template_heading_without_a_rule(tmp):
    del tmp
    template = '## Summary\n\n## Changes\n<!-- required: explain -->\n'
    rendered = _html_body(
        ('Summary', _text_html('Summary.')),
        ('Changes', _text_html('Change.')))
    try:
        PR_BODY.layout_errors(rendered, template)
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
        (RELATED, f'Fixes {_issue_html(91)}'),
        ('Testing', _text_html('Ran the suite.')))
    errors = PR_BODY.layout_errors(rendered, TEMPLATE)
    assert 'Section `Notes` is not defined by the template.' in errors
    assert 'Section "Summary" appears more than once.' in errors
    assert f'Section "{RELATED}" is out of order.' in errors


def test_layout_counts_rendered_code_as_content(tmp):
    del tmp
    rendered = _valid_html(changes=GITHUB_HTML['inline_code'])
    assert PR_BODY.layout_errors(rendered, TEMPLATE) == []


def test_layout_ignores_heading_shaped_text_in_raw_html(tmp):
    del tmp
    rendered = _valid_html(changes=GITHUB_HTML['kbd_block'])
    assert PR_BODY.layout_errors(rendered, TEMPLATE) == []


def test_unknown_section_reasons_escape_adversarial_names(tmp):
    del tmp
    cases = (
        ('Notes for #255', '`Notes for #255`'),
        ('Notes ` for #255', '``Notes ` for #255``'),
        ('``` #255', '```` ``` #255 ````'),
        ('#255 ```', '```` #255 ``` ````'),
        ('`', '`` ` ``'),
        ('`#255`', '`` `#255` ``'),
        ('#255', '`#255`'),
    )
    failures = []
    for name, quoted in cases:
        rendered = _html_body(
            ('Summary', _text_html('One sentence.')),
            (RELATED, f'Fixes {_issue_html(91)}'),
            ('Changes', _text_html('A change.')),
            ('Testing', _text_html('Ran the suite.')),
            (name, _text_html('Unknown.')))
        reason = f'Section {quoted} is not defined by the template.'
        errors = PR_BODY.layout_errors(rendered, TEMPLATE)
        if reason not in errors:
            failures.append((name, reason, errors))
    assert failures == [], failures


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prlayout_')


if __name__ == '__main__':
    raise SystemExit(main())
