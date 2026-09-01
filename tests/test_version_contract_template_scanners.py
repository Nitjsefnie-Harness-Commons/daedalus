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
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    dashboard = copy_root / _DASHBOARD
    text = dashboard.read_text(encoding='utf-8')
    markup = '<svg><template>' + _HTML_TEMPLATE_SITES[1] + '</template></svg>'
    assert text.count('</body>') == 1, text
    dashboard.write_text(text.replace('</body>', markup + '\n</body>'),
                         encoding='utf-8')
    result = _run_checker(copy_root)
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert 'dashboard status line' in result.stderr, result.stderr


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_template_scanners_')


if __name__ == '__main__':
    raise SystemExit(main())
