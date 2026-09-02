#!/usr/bin/env python3
"""Dashboard version sites accept every valid spelling of their opening tag.

HTML lets one element be written several ways: an unquoted attribute value,
uppercase tag and attribute names, further attributes after `class`, and
whitespace before the `>`. A second version element written any of those ways
used to match nothing at all, so the checker reported a tree consistent while
the page rendered two different versions (522).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _run_checker,
)
from test_version_contract_dashboard_duplicates import (  # noqa: E402
    _assert_duplicate_refused,
    _assert_one_dashboard_match,
    _canonical_dashboard_value,
    _insert_before_body,
)


_RAIL = 'dashboard rail footer'
_STATUS = 'dashboard status line'

# Spellings of one element that are equally valid HTML and were equally
# invisible. `uppercase_tag` and `uppercase_attribute` split the uppercase
# case so each scoped `(?i:...)` flag is pinned on its own.
_RAIL_SPELLINGS = {
    'unquoted': '<div class=rail-foot>v9.9.9</div>',
    'uppercase_names': "<DIV CLASS='rail-foot'>v9.9.9</DIV>",
    'uppercase_tag': "<DIV class='rail-foot'>v9.9.9</div>",
    'uppercase_attribute': "<div CLASS='rail-foot'>v9.9.9</div>",
    'trailing_attribute': "<div class='rail-foot' id='x'>v9.9.9</div>",
    'space_before_gt': "<div class='rail-foot' >v9.9.9</div>",
}
_STATUS_SPELLINGS = {
    'unquoted': '<span class=sl-v>9.9.9</span>',
    'uppercase_names': "<SPAN CLASS='sl-v'>9.9.9</SPAN>",
    'uppercase_tag': "<SPAN class='sl-v'>9.9.9</span>",
    'uppercase_attribute': "<span CLASS='sl-v'>9.9.9</span>",
    'space_before_gt': "<span class='sl-v' >9.9.9</span>",
}


def _assert_duplicate_spelling_refused(tmp_path, markup, desc):
    """`markup` planted before `</body>` makes `desc` match twice."""
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    canonical = _canonical_dashboard_value(copy_root, checker, desc)
    _insert_before_body(copy_root, markup)
    _assert_duplicate_refused(_run_checker(copy_root), desc, canonical,
                              '9.9.9')


def _assert_markup_is_not_a_site(tmp_path, markup, desc):
    """`markup` planted before `</body>` leaves `desc` matching once."""
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _insert_before_body(copy_root, markup)
    _assert_one_dashboard_match(copy_root, checker, desc)


def test_unquoted_rail_class_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['unquoted'], _RAIL)


def test_unquoted_status_line_class_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _STATUS_SPELLINGS['unquoted'], _STATUS)


def test_uppercase_names_rail_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['uppercase_names'], _RAIL)


def test_uppercase_names_status_line_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _STATUS_SPELLINGS['uppercase_names'], _STATUS)


def test_uppercase_tag_rail_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['uppercase_tag'], _RAIL)


def test_uppercase_tag_status_line_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _STATUS_SPELLINGS['uppercase_tag'], _STATUS)


def test_uppercase_attribute_rail_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['uppercase_attribute'], _RAIL)


def test_uppercase_attribute_status_line_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _STATUS_SPELLINGS['uppercase_attribute'], _STATUS)


def test_trailing_attribute_rail_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['trailing_attribute'], _RAIL)


def test_space_before_gt_rail_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['space_before_gt'], _RAIL)


def test_space_before_gt_status_line_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _STATUS_SPELLINGS['space_before_gt'], _STATUS)


def test_trailing_region_reads_a_quoted_gt_whole(tmp_path):
    """A `>` inside a later attribute value does not end the tag early."""
    _assert_duplicate_spelling_refused(
        tmp_path, "<div class='rail-foot' title='a>b'>v9.9.9</div>", _RAIL)


def test_status_line_cells_sharing_the_class_are_not_sites(tmp_path):
    """A status cell with a trailing attribute cannot be told from a version.

    The footer renders four `sl-v` cells carrying `data-meta`, and a second
    version span written `<span class='sl-v' id='x'>` has exactly that shape,
    so a status-line pattern admitting one admits all five and refuses the
    tree it exists to pass. The rail class names a single element, so that
    site takes the whole trailing region; this one is pinned to whitespace.
    """
    _assert_markup_is_not_a_site(
        tmp_path, "<span class='sl-v' id='x'>9.9.9</span>", _STATUS)


def test_uppercase_rail_class_value_is_not_a_site(tmp_path):
    """Class names are case-sensitive in standards mode, so the value is."""
    _assert_markup_is_not_a_site(
        tmp_path, "<div class='RAIL-FOOT'>v9.9.9</div>", _RAIL)


def test_uppercase_status_line_class_value_is_not_a_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, "<span class='SL-V'>9.9.9</span>", _STATUS)


def test_unquoted_rail_class_token_suffix_is_not_a_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<div class=rail-footer>v9.9.9</div>', _RAIL)


def test_unquoted_rail_class_token_prefix_is_not_a_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<div class=xrail-foot>v9.9.9</div>', _RAIL)


def test_unquoted_status_line_class_token_suffix_is_not_a_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<span class=sl-value>9.9.9</span>', _STATUS)


def test_unquoted_status_line_class_token_prefix_is_not_a_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<span class=xsl-v>9.9.9</span>', _STATUS)


def test_wrong_tag_name_in_new_spellings_is_not_a_rail_site(tmp_path):
    for name, markup in _RAIL_SPELLINGS.items():
        wrong = markup.replace('div', 'span').replace('DIV', 'SPAN')
        copy_root = Path(tmp_path) / name / 'tree'
        checker = _copy_versioned_tree(copy_root)
        _insert_before_body(copy_root, wrong)
        _assert_one_dashboard_match(copy_root, checker, _RAIL)


def test_wrong_tag_name_in_new_spellings_is_not_a_status_line_site(tmp_path):
    for name, markup in _STATUS_SPELLINGS.items():
        wrong = markup.replace('span', 'div').replace('SPAN', 'DIV')
        copy_root = Path(tmp_path) / name / 'tree'
        checker = _copy_versioned_tree(copy_root)
        _insert_before_body(copy_root, wrong)
        _assert_one_dashboard_match(copy_root, checker, _STATUS)


def test_unterminated_trailing_quote_is_not_a_rail_site(tmp_path):
    """An attribute value that never closes leaves the tag unclosed."""
    _assert_markup_is_not_a_site(
        tmp_path, '<div class=\'rail-foot\' title="a> ordinary>v9.9.9</div>',
        _RAIL)


def test_trailing_attribute_without_whitespace_is_not_a_rail_site(tmp_path):
    """HTML separates attributes by whitespace, so `'id=` is not one."""
    _assert_markup_is_not_a_site(
        tmp_path, "<div class='rail-foot'id='x'>v9.9.9</div>", _RAIL)


def test_unquoted_class_prose_is_not_a_rail_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<p>ordinary class=rail-foot>v9.9.9</p>', _RAIL)


def test_unquoted_class_prose_is_not_a_status_line_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<p>ordinary class=sl-v>9.9.9</span></p>', _STATUS)


def test_new_spelling_decoys_in_script_are_ignored(tmp_path):
    copy_root = Path(tmp_path) / 'tree'
    _copy_versioned_tree(copy_root)
    script = (
        '<script>\n'
        'const a = "<div class=rail-foot>v9.9.9</div>";\n'
        "const b = `<span class=sl-v>8.8.8</span>`;\n"
        "// <div class='rail-foot' id='x'>v7.7.7</div>\n"
        "/* <span class='sl-v' >6.6.6</span> */\n"
        '</script>')
    _insert_before_body(copy_root, script)
    result = _run_checker(copy_root)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)


def test_repeated_class_runs_without_a_close_do_not_flip_the_verdict(
        tmp_path):
    """The shape the trailing region is walked over most: many anchors, many
    `class` candidates, and no `>v` for any of them to reach. This pins the
    verdict on that shape, not the guard's presence: removing the rail
    lookahead leaves this control passing, slowly.
    """
    anchors = 200
    _assert_markup_is_not_a_site(
        tmp_path,
        '<div ' * anchors + "class='rail-foot' " * anchors + 'a' * anchors,
        _RAIL)


def test_canonical_tree_passes_and_set_preserves_dashboard_markup(tmp_path):
    """The widened patterns still read the shipped markup exactly once."""
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    checked = _run_checker(copy_root)
    assert checked.returncode == 0, (checked.returncode, checked.stdout,
                                     checked.stderr)
    canonical = _canonical_dashboard_value(copy_root, checker, _RAIL)
    dashboard = copy_root / 'dashboard' / 'index.html'
    before = dashboard.read_text(encoding='utf-8')
    assert before.count(canonical) == 2, before.count(canonical)
    written = _run_checker(copy_root, '--set', '9.9.9')
    assert written.returncode == 0, (written.returncode, written.stdout,
                                     written.stderr)
    assert dashboard.read_text(encoding='utf-8') == before.replace(
        canonical, '9.9.9')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_dashboard_spellings_')


if __name__ == '__main__':
    raise SystemExit(main())
