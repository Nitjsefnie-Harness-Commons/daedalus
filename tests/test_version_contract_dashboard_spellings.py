#!/usr/bin/env python3
"""Dashboard version sites accept every valid spelling of their opening tag.

HTML lets one element be written several ways: an unquoted attribute value,
uppercase tag and attribute names, further attributes after `class`, and
whitespace before the `>`. A second version element written any of those ways
used to match nothing at all, so the checker reported a tree consistent while
the page rendered two different versions (522). The status line was the one
site still pinned to whitespace after `class`; the four cells that shared its
class moved onto `sl-meta`, so it admits the whole region like the rail
footer (538).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _run_checker,
)
from _version_contract_dashboard_duplicates import (  # noqa: E402
    _assert_duplicate_refused,
    _assert_one_dashboard_match,
    _canonical_dashboard_value,
    _insert_before_body,
)


_RAIL = 'dashboard rail footer'
_STATUS = 'dashboard status line'

# Spellings of one element that are equally valid HTML and were equally
# invisible. `uppercase_tag` and `uppercase_attribute` split the uppercase
# case so each scoped `(?i:...)` flag is pinned on its own. The `unquoted_*`
# rows combine the unquoted value with a further axis, so an intersection
# cannot break silently behind the single-axis row it shares an arm with.
_RAIL_SPELLINGS = {
    'unquoted': '<div class=rail-foot>v9.9.9</div>',
    'uppercase_names': "<DIV CLASS='rail-foot'>v9.9.9</DIV>",
    'uppercase_tag': "<DIV class='rail-foot'>v9.9.9</div>",
    'uppercase_attribute': "<div CLASS='rail-foot'>v9.9.9</div>",
    'trailing_attribute': "<div class='rail-foot' id='x'>v9.9.9</div>",
    'space_before_gt': "<div class='rail-foot' >v9.9.9</div>",
    'unquoted_uppercase_names': '<DIV CLASS=rail-foot>v9.9.9</DIV>',
    'unquoted_trailing_attribute': '<div class=rail-foot id=x>v9.9.9</div>',
    'unquoted_space_before_gt': '<div class=rail-foot >v9.9.9</div>',
    'unquoted_gt_in_earlier_attribute': (
        "<div title='a>b' class=rail-foot>v9.9.9</div>"),
    'quoted_lt_in_trailing_attribute': (
        "<div class='rail-foot' title='a<b'>v9.9.9</div>"),
    'double_quoted_lt_in_trailing_attribute': (
        '<div class="rail-foot" data-x="a<b">v9.9.9</div>'),
}
_STATUS_SPELLINGS = {
    'unquoted': '<span class=sl-v>9.9.9</span>',
    'uppercase_names': "<SPAN CLASS='sl-v'>9.9.9</SPAN>",
    'uppercase_tag': "<SPAN class='sl-v'>9.9.9</span>",
    'uppercase_attribute': "<span CLASS='sl-v'>9.9.9</span>",
    'space_before_gt': "<span class='sl-v' >9.9.9</span>",
    'unquoted_uppercase_names': '<SPAN CLASS=sl-v>9.9.9</SPAN>',
    'unquoted_space_before_gt': '<span class=sl-v >9.9.9</span>',
    'unquoted_gt_in_earlier_attribute': (
        "<span title='a>b' class=sl-v>9.9.9</span>"),
    # The trailing-attribute rows the status site admits now (538): any
    # attribute after `class` is a duplicate, named no way.
    'trailing_attribute': "<span class='sl-v' id='x'>9.9.9</span>",
    'data_attribute': "<span class='sl-v' data-meta='x'>9.9.9</span>",
    'uppercase_trailing_attribute': "<SPAN CLASS='sl-v' ID='X'>9.9.9</SPAN>",
    'unquoted_trailing_attribute': '<span class=sl-v id=x>9.9.9</span>',
    'attribute_before_class': "<span id='x' class='sl-v'>9.9.9</span>",
    'spaced_equals_trailing_attribute': (
        "<span class = 'sl-v' id = 'x'>9.9.9</span>"),
    'quoted_gt_in_trailing_attribute': (
        "<span class='sl-v' title='a>b'>9.9.9</span>"),
    'quoted_lt_in_trailing_attribute': (
        "<span class='sl-v' title='a<b'>9.9.9</span>"),
    'double_quoted_lt_in_trailing_attribute': (
        '<span class="sl-v" data-x="a<b">9.9.9</span>'),
}


def _assert_duplicate_spelling_refused(tmp_path, markup, desc):
    """`markup` planted before `</body>` makes `desc` match twice."""
    try:
        copy_root = Path(tmp_path) / 'tree'
        checker = _copy_versioned_tree(copy_root)
        canonical = _canonical_dashboard_value(copy_root, checker, desc)
        _insert_before_body(copy_root, markup)
        _assert_duplicate_refused(_run_checker(copy_root), desc, canonical,
                                  '9.9.9')
    except AssertionError as exc:
        raise AssertionError(f'{Path(tmp_path).name}: {exc}') from exc


def _assert_markup_is_not_a_site(tmp_path, markup, desc):
    """`markup` planted before `</body>` leaves `desc` matching once."""
    try:
        copy_root = Path(tmp_path) / 'tree'
        checker = _copy_versioned_tree(copy_root)
        _insert_before_body(copy_root, markup)
        _assert_one_dashboard_match(copy_root, checker, desc)
    except AssertionError as exc:
        raise AssertionError(f'{Path(tmp_path).name}: {exc}') from exc


def test_attribute_before_class_status_line_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _STATUS_SPELLINGS['attribute_before_class'], _STATUS)
    for name, markup, desc in (
            ('case-data-attribute-status-line-duplicate',
             "<span class='sl-v' data-meta='x'>9.9.9</span>",
             _STATUS),
            ('case-space-before-gt-rail-duplicate',
             "<div class='rail-foot' >v9.9.9</div>",
             _RAIL),
            ('case-space-before-gt-status-line-duplicate',
             "<span class='sl-v' >9.9.9</span>",
             _STATUS),
            ('case-spaced-equals-trailing-attribute-status-line-duplicate',
             "<span class = 'sl-v' id = 'x'>9.9.9</span>",
             _STATUS),
            ('case-trailing-attribute-rail-duplicate',
             "<div class='rail-foot' id='x'>v9.9.9</div>",
             _RAIL),
            ('case-trailing-attribute-status-line-duplicate',
             "<span class='sl-v' id='x'>9.9.9</span>",
             _STATUS),
            ('case-unquoted-gt-in-earlier-attribute-rail-duplicate',
             "<div title='a>b' class=rail-foot>v9.9.9</div>",
             _RAIL),
            ('case-unquoted-gt-in-earlier-attribute-status-line-duplicate',
             "<span title='a>b' class=sl-v>9.9.9</span>",
             _STATUS),
            ('case-unquoted-rail-duplicate',
             '<div class=rail-foot>v9.9.9</div>',
             _RAIL),
            ('case-unquoted-space-before-gt-rail-duplicate',
             '<div class=rail-foot >v9.9.9</div>',
             _RAIL),
            ('case-unquoted-space-before-gt-status-line-duplicate',
             '<span class=sl-v >9.9.9</span>',
             _STATUS),
            ('case-unquoted-status-line-duplicate',
             '<span class=sl-v>9.9.9</span>',
             _STATUS),
            ('case-unquoted-trailing-attribute-rail-duplicate',
             '<div class=rail-foot id=x>v9.9.9</div>',
             _RAIL),
            ('case-unquoted-trailing-attribute-status-line-duplicate',
             '<span class=sl-v id=x>9.9.9</span>',
             _STATUS),
            ('case-uppercase-trailing-attribute-status-line-duplicate',
             "<SPAN CLASS='sl-v' ID='X'>9.9.9</SPAN>",
             _STATUS),
    ):
        _assert_duplicate_spelling_refused(
            Path(tmp_path) / name, markup, desc)


def test_status_line_trailing_region_reads_a_quoted_lt_whole(tmp_path):
    """A `<` inside a later attribute value does not end the tag early.

    Both quote spellings, so the twin is not itself a single-spelling
    control: a region narrowed at `<` inside one of them fails here
    whichever of the two it is.
    """
    for name in ('quoted_lt_in_trailing_attribute',
                 'double_quoted_lt_in_trailing_attribute'):
        _assert_duplicate_spelling_refused(
            Path(tmp_path) / name, _STATUS_SPELLINGS[name], _STATUS)
    for name, markup, desc in (
            ('case-quoted-gt-in-trailing-attribute-status-line-duplicate',
             "<span class='sl-v' title='a>b'>9.9.9</span>",
             _STATUS),
            ('case-quoted-gt-in-trailing-attribute-rail-duplicate',
             "<div class='rail-foot' title='a>b'>v9.9.9</div>",
             _RAIL),
            ('case-quoted-lt-in-trailing-attribute-rail-duplicate',
             "<div class='rail-foot' title='a<b'>v9.9.9</div>",
             _RAIL),
            ('case-double-quoted-lt-in-trailing-attribute-rail-duplicate',
             '<div class="rail-foot" data-x="a<b">v9.9.9</div>',
             _RAIL),
    ):
        _assert_duplicate_spelling_refused(
            Path(tmp_path) / name, markup, desc)


def test_unquoted_uppercase_names_rail_duplicate_is_refused(tmp_path):
    _assert_duplicate_spelling_refused(
        tmp_path, _RAIL_SPELLINGS['unquoted_uppercase_names'], _RAIL)
    for name, markup, desc in (
            ('case-unquoted-uppercase-names-status-line-duplicate',
             '<SPAN CLASS=sl-v>9.9.9</SPAN>',
             _STATUS),
            ('case-uppercase-attribute-rail-duplicate',
             "<div CLASS='rail-foot'>v9.9.9</div>",
             _RAIL),
            ('case-uppercase-attribute-status-line-duplicate',
             "<span CLASS='sl-v'>9.9.9</span>",
             _STATUS),
            ('case-uppercase-names-rail-duplicate',
             "<DIV CLASS='rail-foot'>v9.9.9</DIV>",
             _RAIL),
            ('case-uppercase-names-status-line-duplicate',
             "<SPAN CLASS='sl-v'>9.9.9</SPAN>",
             _STATUS),
            ('case-uppercase-tag-rail-duplicate',
             "<DIV class='rail-foot'>v9.9.9</div>",
             _RAIL),
            ('case-uppercase-tag-status-line-duplicate',
             "<SPAN class='sl-v'>9.9.9</span>",
             _STATUS),
    ):
        _assert_duplicate_spelling_refused(
            Path(tmp_path) / name, markup, desc)


def test_status_line_shipped_class_names_one_element(tmp_path):
    """The trailing region rests on this class naming one element.

    The four cells that made the region ambiguous moved onto `sl-meta` (538),
    leaving this one to be the class the site admits the whole region for.
    """
    copy_root = Path(tmp_path) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    text = (copy_root / 'dashboard' / 'index.html').read_text(
        encoding='utf-8')
    assert text.count('class="sl-v"') == 1, text
    _assert_one_dashboard_match(copy_root, checker, _STATUS)


def test_shipped_meta_cells_are_not_status_line_sites(tmp_path):
    """The class the four status cells moved to is not a version class."""
    _assert_markup_is_not_a_site(
        tmp_path, "<span class='sl-meta' data-meta='x'>9.9.9</span>", _STATUS)
    for name, markup, desc in (
            ('case-class-token-prefix-status-line-not-a-site',
             "<span class='notsl-v' id='x'>9.9.9</span>",
             _STATUS),
            ('case-class-token-suffix-status-line-not-a-site',
             "<span class='sl-value' id='x'>9.9.9</span>",
             _STATUS),
            ('case-missing-attribute-separator-status-line-not-a-site',
             "<span class='sl-v'id='x'>9.9.9</span>",
             _STATUS),
            ('case-missing-attribute-separator-rail-not-a-site',
             "<div class='rail-foot'id='x'>v9.9.9</div>",
             _RAIL),
            ('case-unquoted-class-prose-status-line-not-a-site',
             '<p>ordinary class=sl-v>9.9.9</span></p>',
             _STATUS),
            ('case-unquoted-class-token-prefix-rail-not-a-site',
             '<div class=xrail-foot>v9.9.9</div>',
             _RAIL),
            ('case-unquoted-class-token-suffix-rail-not-a-site',
             '<div class=rail-footer>v9.9.9</div>',
             _RAIL),
            ('case-unquoted-class-token-prefix-status-line-not-a-site',
             '<span class=xsl-v>9.9.9</span>',
             _STATUS),
            ('case-unquoted-class-token-suffix-status-line-not-a-site',
             '<span class=sl-value>9.9.9</span>',
             _STATUS),
            ('case-unterminated-trailing-quote-rail-not-a-site',
             '<div class=\'rail-foot\' title="a> ordinary>v9.9.9</div>',
             _RAIL),
            ('case-unterminated-trailing-quote-status-line-not-a-site',
             '<span class=\'sl-v\' title="a> ordinary>9.9.9</span>',
             _STATUS),
            ('case-uppercase-class-value-rail-not-a-site',
             "<div class='RAIL-FOOT'>v9.9.9</div>",
             _RAIL),
            ('case-uppercase-class-value-status-line-not-a-site',
             "<span class='SL-V'>9.9.9</span>",
             _STATUS),
            ('case-unquoted-uppercase-class-value-rail-not-a-site',
             '<div CLASS=RAIL-FOOT>v9.9.9</div>',
             _RAIL),
            ('case-unquoted-uppercase-class-value-status-line-not-a-site',
             '<span CLASS=SL-V>9.9.9</span>',
             _STATUS),
    ):
        _assert_markup_is_not_a_site(
            Path(tmp_path) / name, markup, desc)


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


def test_unquoted_class_prose_is_not_a_rail_site(tmp_path):
    _assert_markup_is_not_a_site(
        tmp_path, '<p>ordinary class=rail-foot>v9.9.9</p>', _RAIL)


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
    _assert_markup_is_not_a_site(
        Path(tmp_path) / "status",
        '<span ' * anchors + "class='sl-v' " * anchors
        + 'a' * anchors, _STATUS)


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
