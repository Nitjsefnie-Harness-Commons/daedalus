#!/usr/bin/env python3
"""Dashboard version attributes accept either valid HTML quote delimiter."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _run_checker,
)


_SITE_CASES = (
    ('rail-foot', 'dashboard rail footer'),
    ('sl-v', 'dashboard status line'),
)


def _insert_before_body(copy_root, markup):
    dashboard = copy_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    anchor = '</body>'
    assert text.count(anchor) == 1, text
    dashboard.write_text(text.replace(anchor, markup + '\n' + anchor),
                         encoding='utf-8')


def _replace_dashboard_once(copy_root, old, new):
    dashboard = copy_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    assert text.count(old) == 1, (old, text.count(old))
    dashboard.write_text(text.replace(old, new), encoding='utf-8')


def _duplicate_dashboard_version(copy_root, class_name, second_value='9.9.9',
                                 quote="'"):
    if class_name == 'rail-foot':
        duplicate = (f'<div class={quote}rail-foot{quote}>'
                     f'v{second_value}</div>')
    else:
        duplicate = (f'<span class={quote}sl-v{quote}>'
                     f'{second_value}</span>')
    _insert_before_body(copy_root, duplicate)


def _use_single_quotes_for_only_site(copy_root, class_name):
    dashboard = copy_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    if class_name == 'rail-foot':
        old, new = 'class="rail-foot">v', "class='rail-foot'>v"
    else:
        old, new = '<span class="sl-v">', "<span class='sl-v'>"
    rewritten, count = text.replace(old, new), text.count(old)
    assert count == 1, (class_name, count)
    dashboard.write_text(rewritten, encoding='utf-8')


def _dashboard_site(checker, desc):
    entries = [entry for entry in checker.SITES
               if entry[:2] == ('dashboard/index.html', desc)]
    assert len(entries) == 1, (desc, entries)
    return entries[0]


def _canonical_dashboard_value(copy_root, checker, desc):
    path, _site_desc, pattern = _dashboard_site(checker, desc)
    text = (copy_root / path).read_text(encoding='utf-8')
    return re.search(pattern, text).group('v')


def _assert_duplicate_refused(result, desc, canonical, second_value):
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert desc in result.stderr, result.stderr
    assert repr(canonical) in result.stderr, result.stderr
    assert repr(second_value) in result.stderr, result.stderr


def test_check_versions_refuses_single_quoted_rail_footer_duplicate(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    desc = 'dashboard rail footer'
    canonical = _canonical_dashboard_value(copy_root, checker, desc)
    _duplicate_dashboard_version(copy_root, 'rail-foot')
    result = _run_checker(copy_root)
    _assert_duplicate_refused(result, desc, canonical, '9.9.9')


def test_check_versions_refuses_single_quoted_status_line_duplicate(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    desc = 'dashboard status line'
    canonical = _canonical_dashboard_value(copy_root, checker, desc)
    _duplicate_dashboard_version(copy_root, 'sl-v')
    result = _run_checker(copy_root)
    _assert_duplicate_refused(result, desc, canonical, '9.9.9')


def test_check_versions_refuses_mixed_dashboard_delimiters(tmp_path):
    for class_name, desc in _SITE_CASES:
        copy_root = Path(tmp_path) / class_name / 'tree'
        checker = _copy_versioned_tree(copy_root)
        canonical = _canonical_dashboard_value(copy_root, checker, desc)
        _use_single_quotes_for_only_site(copy_root, class_name)
        _duplicate_dashboard_version(copy_root, class_name, quote='"')
        result = _run_checker(copy_root)
        _assert_duplicate_refused(result, desc, canonical, '9.9.9')


def test_check_versions_print_refuses_single_quoted_dashboard_duplicate(
        tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    _duplicate_dashboard_version(copy_root, 'rail-foot')
    result = _run_checker(copy_root, '--print')
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert result.stdout == '', result.stdout
    assert 'matches 2 times' in result.stderr, result.stderr


def test_check_versions_set_refuses_without_partial_dashboard_rewrite(
        tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _duplicate_dashboard_version(copy_root, 'sl-v')
    before = {path: (copy_root / path).read_bytes()
              for path, _desc, _pattern in checker.SITES}
    result = _run_checker(copy_root, '--set', '9.9.9')
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    for path, body in before.items():
        assert (copy_root / path).read_bytes() == body, path


def test_single_quoted_dashboard_site_passes_and_set_preserves_markup(
        tmp_path):
    for class_name, desc in _SITE_CASES:
        copy_root = Path(tmp_path) / class_name / 'tree'
        checker = _copy_versioned_tree(copy_root)
        canonical = _canonical_dashboard_value(copy_root, checker, desc)
        _use_single_quotes_for_only_site(copy_root, class_name)
        checked = _run_checker(copy_root)
        assert checked.returncode == 0, (
            checked.returncode, checked.stdout, checked.stderr)

        dashboard = copy_root / 'dashboard' / 'index.html'
        before = dashboard.read_text(encoding='utf-8')
        assert before.count(canonical) == 2, before
        written = _run_checker(copy_root, '--set', '9.9.9')
        assert written.returncode == 0, (
            written.returncode, written.stdout, written.stderr)
        assert dashboard.read_text(encoding='utf-8') == before.replace(
            canonical, '9.9.9')


def test_script_raw_text_dashboard_version_decoys_are_ignored(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    script = (
        '<script>\n'
        'const s = "<span class=\'sl-v\'>9.9.9</span>";\n'
        'const t = `<span class=\'sl-v\'>8.8.8</span>`;\n'
        "const r = /class='rail-foot'>v7[.]7[.]7/;\n"
        "// <span class='sl-v'>6.6.6</span>\n"
        "/* <div class='rail-foot'>v5.5.5</div> */\n"
        '</script>')
    _insert_before_body(copy_root, script)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_double_escaped_script_end_tag_does_not_expose_decoy(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    script = (
        '<script>\n'
        '<!--<script></script>\n'
        'const decoy = "<span class=\'sl-v\'>9.9.9</span>";\n'
        '//-->\n'
        '</script>')
    _insert_before_body(copy_root, script)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_real_status_line_after_double_escaped_script_is_counted(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    desc = 'dashboard status line'
    canonical = _canonical_dashboard_value(copy_root, checker, desc)
    old = f'<span class="sl-v">{canonical}</span>'
    _replace_dashboard_once(copy_root, old, '')
    markup = (
        '<script>\n'
        '<!--<script></script>\n'
        'const decoy = "<span class=\'sl-v\'>9.9.9</span>";\n'
        f'//--></script><span class=\'sl-v\'>{canonical}</span>')
    _insert_before_body(copy_root, markup)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_unicode_space_does_not_end_script_tag_name(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    script = (
        '<script>\n'
        'const notAnEndTag = "</script\xa0>";\n'
        'const decoy = "<span class=\'sl-v\'>9.9.9</span>";\n'
        '</script>')
    _insert_before_body(copy_root, script)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_classic_script_html_comment_filters_decoy(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    script = (
        '<script>\n'
        '<!-- <span class=\'sl-v\'>9.9.9</span>\n'
        'void 0;\n'
        '</script>')
    _insert_before_body(copy_root, script)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_prefixed_class_attributes_are_not_dashboard_sites(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    markup = (
        "<div x:class='rail-foot'>v9.9.9</div>\n"
        "<span x:class='sl-v'>8.8.8</span>")
    _insert_before_body(copy_root, markup)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_uppercase_script_tag_filters_its_decoy(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    desc = 'dashboard status line'
    canonical = _canonical_dashboard_value(copy_root, checker, desc)
    old = f'<span class="sl-v">{canonical}</span>'
    _replace_dashboard_once(copy_root, old, '')
    markup = (
        '<SCRIPT>\n'
        'const notAnEndTag = "</SCRIPT\xa0>";\n'
        'const decoy = "<span class=\'sl-v\'>9.9.9</span>";\n'
        f'// open</SCRIPT><span class=\'sl-v\'>{canonical}</span>')
    _insert_before_body(copy_root, markup)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_data_class_dashboard_decoys_are_ignored(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    markup = (
        "<div data-class='rail-foot'>v9.9.9</div>\n"
        "<span data-class='sl-v'>8.8.8</span>")
    _insert_before_body(copy_root, markup)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_mismatched_dashboard_delimiters_are_not_sites(tmp_path):
    rail_root = Path(tmp_path) / 'rail-foot' / 'tree'
    _copy_versioned_tree(rail_root)
    _insert_before_body(
        rail_root, "<div class='rail-foot\">v9.9.9</div>")
    rail_result = _run_checker(rail_root)
    assert rail_result.returncode == 0, (
        rail_result.returncode, rail_result.stdout, rail_result.stderr)

    status_root = Path(tmp_path) / 'sl-v' / 'tree'
    _copy_versioned_tree(status_root)
    dashboard = status_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    old, new = '<span class="sl-v">', '<span class="sl-v\'>'
    assert text.count(old) == 1, text
    dashboard.write_text(text.replace(old, new), encoding='utf-8')
    status_result = _run_checker(status_root)
    assert status_result.returncode != 0, (
        status_result.returncode, status_result.stdout, status_result.stderr)
    assert 'no version found for dashboard status line' in status_result.stderr


def test_backticks_are_not_html_attribute_delimiters(tmp_path):
    for class_name, _desc in _SITE_CASES:
        copy_root = Path(tmp_path) / class_name / 'tree'
        _copy_versioned_tree(copy_root)
        _duplicate_dashboard_version(copy_root, class_name, quote='`')
        result = _run_checker(copy_root)
        assert result.returncode == 0, (
            class_name, result.returncode, result.stdout, result.stderr)


def test_status_line_value_stops_at_markup(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    _insert_before_body(
        copy_root, "<span class='sl-v'>9.9.9<em>x</em></span>")
    result = _run_checker(copy_root)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_dashboard_duplicates_')


if __name__ == '__main__':
    raise SystemExit(main())
