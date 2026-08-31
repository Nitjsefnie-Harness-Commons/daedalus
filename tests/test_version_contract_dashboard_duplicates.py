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
    ('rail-foot', 4, 'dashboard rail footer'),
    ('sl-v', 5, 'dashboard status line'),
)


def _duplicate_dashboard_version(copy_root, class_name, second_value='9.9.9',
                                 quote="'"):
    dashboard = copy_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    if class_name == 'rail-foot':
        duplicate = (f'\n<div class={quote}rail-foot{quote}>'
                     f'v{second_value}</div>\n')
    else:
        duplicate = (f'\n<span class={quote}sl-v{quote}>'
                     f'{second_value}</span>\n')
    dashboard.write_text(text + duplicate, encoding='utf-8')


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


def _canonical_dashboard_value(copy_root, checker, site_index):
    path, _desc, pattern = checker.SITES[site_index]
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
    canonical = _canonical_dashboard_value(copy_root, checker, 4)
    _duplicate_dashboard_version(copy_root, 'rail-foot')
    result = _run_checker(copy_root)
    _assert_duplicate_refused(
        result, 'dashboard rail footer', canonical, '9.9.9')


def test_check_versions_refuses_single_quoted_status_line_duplicate(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    canonical = _canonical_dashboard_value(copy_root, checker, 5)
    _duplicate_dashboard_version(copy_root, 'sl-v')
    result = _run_checker(copy_root)
    _assert_duplicate_refused(
        result, 'dashboard status line', canonical, '9.9.9')


def test_check_versions_refuses_mixed_dashboard_delimiters(tmp_path):
    for class_name, site_index, desc in _SITE_CASES:
        copy_root = Path(tmp_path) / class_name / 'tree'
        checker = _copy_versioned_tree(copy_root)
        canonical = _canonical_dashboard_value(
            copy_root, checker, site_index)
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
    for class_name, site_index, _desc in _SITE_CASES:
        copy_root = Path(tmp_path) / class_name / 'tree'
        checker = _copy_versioned_tree(copy_root)
        canonical = _canonical_dashboard_value(
            copy_root, checker, site_index)
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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_dashboard_duplicates_')


if __name__ == '__main__':
    raise SystemExit(main())
