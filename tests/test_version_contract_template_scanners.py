#!/usr/bin/env python3
"""HTML template text-mode boundary contracts for version regions."""
import _util
from test_version_contract_scanners import (
    _checker,
    _DASHBOARD,
    _HTML_TEMPLATE_SITES,
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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_template_scanners_')


if __name__ == '__main__':
    raise SystemExit(main())
