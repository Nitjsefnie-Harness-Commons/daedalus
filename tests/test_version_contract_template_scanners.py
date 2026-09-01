#!/usr/bin/env python3
"""HTML template text-mode boundary contracts for version regions."""
from pathlib import Path

import _util
from test_version_contract_scanners import (
    _checker,
    _copy_versioned_tree,
    _DASHBOARD,
    _HTML_TEMPLATE_SITES,
    _run_checker,
    _SITE,
    _surviving,
)


_HTML_TEXT_MODE_TAGS = ('script', 'style', 'title', 'textarea')
_FOREIGN_HTML_START_TAGS = tuple(
    ('b big blockquote body br center code dd div dl dt em embed h1 h2 h3 '
     'h4 h5 h6 head hr i img li listing menu meta nobr ol p pre ruby s '
     'small span strong strike sub sup table tt u ul var').split())


def _run_dashboard_markup(root, markup):
    copy_root = Path(root) / 'tree'
    _copy_versioned_tree(copy_root)
    dashboard = copy_root / _DASHBOARD
    text = dashboard.read_text(encoding='utf-8')
    assert text.count('</body>') == 1, text
    dashboard.write_text(text.replace('</body>', markup + '\n</body>'),
                         encoding='utf-8')
    return _run_checker(copy_root)


def test_html_text_modes_ignore_a_false_template_closer(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[0]
    regions = _checker()
    for tag in _HTML_TEXT_MODE_TAGS:
        source = (f'<template><{tag}>const x = "</template>";'
                  f'</{tag}>{site}</template>')
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag


def test_html_text_modes_ignore_a_false_template_opener(tmp):
    del tmp
    regions = _checker()
    for tag in _HTML_TEXT_MODE_TAGS:
        source = (f'<template><{tag}>const x = "<template>";'
                  f'</{tag}></template><div {_SITE}</div>')
        assert _surviving(regions, _DASHBOARD, source, _SITE) == 1, tag


def test_html_text_mode_closer_requires_a_name_boundary(tmp):
    del tmp
    decoy = _HTML_TEMPLATE_SITES[0]
    regions = _checker()
    for tag in _HTML_TEXT_MODE_TAGS:
        source = (f'<template><{tag}></{tag}x><template>{decoy}'
                  f'</{tag}></template><div {_SITE}</div>')
        assert _surviving(regions, _DASHBOARD, source, decoy) == 0, tag
        assert _surviving(regions, _DASHBOARD, source, _SITE) == 1, tag


def test_foreign_template_sites_are_counted(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('svg', 'math'):
        source = f'<{tag}><template>{site}</template></{tag}>'
        assert _surviving(regions, _DASHBOARD, source, site) == 1, tag


def test_html_integration_point_templates_are_inert(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('foreignObject', 'desc', 'title'):
        source = (f'<svg><{tag}><template>{site}</template>'
                  f'</{tag}></svg>')
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag


def test_self_closed_svg_does_not_enter_foreign_content(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<svg/><template>{site}</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_svg_closer_restores_html_context(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<svg></svg><template>{site}</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_nested_svg_closer_keeps_outer_foreign_context(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<svg><svg></svg><template>{site}</template></svg>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 1


def test_check_refuses_an_svg_template_version_site(tmp):
    markup = '<svg><template>' + _HTML_TEMPLATE_SITES[1] + '</template></svg>'
    result = _run_dashboard_markup(tmp, markup)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert 'dashboard status line' in result.stderr, result.stderr


def test_check_refuses_math_inherited_foreignobject_template_site(tmp):
    site = _HTML_TEMPLATE_SITES[1]
    markup = ('<math><svg><foreignObject><template>' + site
              + '</template></foreignObject></svg></math>')
    result = _run_dashboard_markup(tmp, markup)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert 'dashboard status line' in result.stderr, result.stderr
    assert _surviving(_checker(), _DASHBOARD, markup, site) == 1


def test_svg_inherited_foreignobject_template_is_inert(tmp):
    site = _HTML_TEMPLATE_SITES[1]
    markup = ('<svg><math><foreignObject><template>' + site
              + '</template></foreignObject></math></svg>')
    result = _run_dashboard_markup(tmp, markup)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert _surviving(_checker(), _DASHBOARD, markup, site) == 0


def test_annotation_xml_encoding_controls_html_integration(tmp):
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    cases = (('text/html', 0, 0),
             ('APPLICATION/XHTML+XML', 0, 0),
             ('application/x-sql', 1, 1))
    for index, (encoding, surviving, refused) in enumerate(cases):
        markup = (f'<math><annotation-xml encoding="{encoding}">'
                  f'<template>{site}</template></annotation-xml></math>')
        result = _run_dashboard_markup(Path(tmp) / str(index), markup)
        assert bool(result.returncode) == refused, (
            encoding, result.returncode, result.stdout, result.stderr)
        assert _surviving(
            regions, _DASHBOARD, markup, site) == surviving, encoding


def test_math_text_integration_point_templates_are_inert(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('mi', 'mo', 'mn', 'ms', 'mtext'):
        source = f'<math><{tag}><template>{site}</template></{tag}></math>'
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag


def test_foreign_closer_pops_to_matching_ancestor(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<svg><g><math></svg><template>{site}</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_stray_foreign_closer_does_not_create_context(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'</svg><template>{site}</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_math_text_integration_exceptions_stay_foreign(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('mglyph', 'malignmark'):
        source = (f'<math><mi><{tag}><template>{site}</template>'
                  f'</{tag}></mi></math>')
        assert _surviving(regions, _DASHBOARD, source, site) == 1, tag


def test_check_refuses_math_text_exception_template_site(tmp):
    site = _HTML_TEMPLATE_SITES[1]
    markup = ('<math><mi><mglyph><template>' + site
              + '</template></mglyph></mi></math>')
    result = _run_dashboard_markup(tmp, markup)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert 'dashboard status line' in result.stderr, result.stderr


def test_self_closed_math_text_exception_restores_html_routing(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<math><mi><mglyph/><template>{site}</template></mi></math>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_outer_html_text_modes_do_not_poison_foreign_context(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('style', 'title', 'textarea'):
        source = f'<{tag}><svg></{tag}><template>{site}</template>'
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag


def test_check_accepts_outer_html_text_mode_namespace_text(tmp):
    site = _HTML_TEMPLATE_SITES[1]
    markup = ''.join(
        f'<{tag}><svg></{tag}><template>{site}</template>'
        for tag in ('style', 'title', 'textarea'))
    result = _run_dashboard_markup(tmp, markup)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)


def test_outer_html_text_mode_version_markup_is_decoy(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('style', 'title', 'textarea'):
        source = f'<{tag}>{site}</{tag}><div {_SITE}></div>'
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag
        assert _surviving(regions, _DASHBOARD, source, _SITE) == 1, tag


def _assert_raw_text_does_not_poison_context(tag):
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<{tag}><svg></{tag}><template>{site}</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def _assert_raw_text_ignores_false_template_closer(tag):
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    source = (f'<template><{tag}></template></{tag}>{site}'
              f'</template><div {_SITE}</div>')
    assert _surviving(regions, _DASHBOARD, source, site) == 0
    assert _surviving(regions, _DASHBOARD, source, _SITE) == 1


def test_xmp_does_not_poison_foreign_context(tmp):
    del tmp
    _assert_raw_text_does_not_poison_context('xmp')


def test_iframe_does_not_poison_foreign_context(tmp):
    del tmp
    _assert_raw_text_does_not_poison_context('iframe')


def test_noembed_does_not_poison_foreign_context(tmp):
    del tmp
    _assert_raw_text_does_not_poison_context('noembed')


def test_noframes_does_not_poison_foreign_context(tmp):
    del tmp
    _assert_raw_text_does_not_poison_context('noframes')


def test_xmp_ignores_a_false_template_closer(tmp):
    del tmp
    _assert_raw_text_ignores_false_template_closer('xmp')


def test_iframe_ignores_a_false_template_closer(tmp):
    del tmp
    _assert_raw_text_ignores_false_template_closer('iframe')


def test_noembed_ignores_a_false_template_closer(tmp):
    del tmp
    _assert_raw_text_ignores_false_template_closer('noembed')


def test_noframes_ignores_a_false_template_closer(tmp):
    del tmp
    _assert_raw_text_ignores_false_template_closer('noframes')


def test_check_accepts_xmp_namespace_text(tmp):
    site = _HTML_TEMPLATE_SITES[1]
    markup = f'<xmp><svg></xmp><template>{site}</template>'
    result = _run_dashboard_markup(tmp, markup)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)


def test_plaintext_consumes_the_document_remainder(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<plaintext><template>{site}</template><div {_SITE}</div>'
    regions = _checker()
    assert _surviving(regions, _DASHBOARD, source, site) == 0
    assert _surviving(regions, _DASHBOARD, source, _SITE) == 0


def test_noscript_uses_the_scripting_enabled_text_mode(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<noscript><svg></noscript><template>{site}</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_foreign_text_spellings_follow_the_current_namespace(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for namespace in ('svg', 'math'):
        for tag in _HTML_TEXT_MODE_TAGS:
            source = (f'<{namespace}><{tag}><div></div></{tag}>'
                      f'<template>{site}</template></{namespace}>')
            expected = int(namespace == 'svg' and tag == 'title')
            assert _surviving(
                regions, _DASHBOARD, source, site) == expected, (
                    namespace, tag)


def test_all_foreign_html_start_tags_break_out(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in _FOREIGN_HTML_START_TAGS:
        source = (f'<svg><g><{tag}><template>{site}</template>'
                  f'</{tag}></g></svg>')
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag


def test_foreign_font_breakout_requires_a_named_attribute(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for attribute in ('color', 'face', 'size'):
        source = (f'<svg><font {attribute}><template>{site}</template>'
                  '</font></svg>')
        assert _surviving(
            regions, _DASHBOARD, source, site) == 0, attribute
    source = f'<svg><font><template>{site}</template></font></svg>'
    assert _surviving(regions, _DASHBOARD, source, site) == 1


def test_self_closed_foreign_html_start_tag_still_breaks_out(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = f'<svg><g><div/><template>{site}</template></g></svg>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_foreign_br_and_p_end_tags_break_out(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('br', 'p'):
        source = f'<svg><g></{tag}><template>{site}</template>'
        assert _surviving(regions, _DASHBOARD, source, site) == 0, tag


def test_foreign_breakout_applies_in_math_and_stops_at_integration(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    sources = (
        f'<math><mrow><div></div><template>{site}</template></math>',
        ('<svg><foreignObject><svg><g><div></div>'
         f'<template>{site}</template></svg></foreignObject></svg>'),
        ('<math><mi><mglyph><div></div>'
         f'<template>{site}</template></mglyph></mi></math>'),
    )
    for source in sources:
        assert _surviving(regions, _DASHBOARD, source, site) == 0, source


def test_annotation_xml_direct_svg_uses_the_svg_namespace(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    direct = ('<math><annotation-xml><svg><foreignObject><template>' + site
              + '</template></foreignObject></svg></annotation-xml></math>')
    nested = ('<math><annotation-xml><mrow><svg><foreignObject><template>'
              + site + '</template></foreignObject></svg></mrow>'
              '</annotation-xml></math>')
    assert _surviving(regions, _DASHBOARD, direct, site) == 0
    assert _surviving(regions, _DASHBOARD, nested, site) == 1


def test_non_special_foreign_start_tags_remain_foreign(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    regions = _checker()
    for tag in ('section', 'html'):
        source = f'<svg><{tag}><template>{site}</template></{tag}></svg>'
        assert _surviving(regions, _DASHBOARD, source, site) == 1, tag


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_template_scanners_')


if __name__ == '__main__':
    raise SystemExit(main())
