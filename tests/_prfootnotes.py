#!/usr/bin/env python3
"""GitHub renderings of pull-request bodies carrying a footnote section.

Captured from GitHub's /markdown endpoint in GFM mode with
Nitjsefnie-Harness-Commons/daedalus as the context. They live here
rather than beside their assertions because the section suite and the
closing suite both read them, and neither has room for the set.

Each key names what its Markdown source did:

- footnote_definition_closing  a `[^1]: Fixes #104` definition
- footnote_definition_bare     the same definition without a keyword
- raw_section                  an author-written footnote section
- footnote_in_related          a footnote reference in Related Issues
- empty_testing_with_footnote  a definition as Testing's only content
- no_class                     `<section data-footnotes>` alone
- no_dataattr                  `<section class="footnotes">` alone
- own_heading                  a heading inside a written footnote section
- plain_div                    the same content in a `<div>`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _prgate import GITHUB_ISSUE_101, GITHUB_ISSUE_104  # noqa: E402


_HEAD = (
    '<h2 dir="auto">Summary</h2>\n<p dir="auto">One sentence.</p>\n<h2 '
    'dir="auto">Related Issues and Pull Requests</h2>\n<p dir="auto">Fixes '
    f'{GITHUB_ISSUE_101}</p>\n<h2 dir="auto">Changes</h2>\n')

_PLAIN = (
    '<p dir="auto">One change</p>\n<h2 dir="auto">Testing</h2>\n<p dir="aut'
    'o">Ran the suite.</p>\n')

FOOTNOTE_HTML = {
    'footnote_definition_closing': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-abbab8761'
        '02f5c249269fa9f80e1bb3a" id="user-content-fnref-1-abbab876102f5c24'
        '9269fa9f80e1bb3a" data-footnote-ref="" aria-describedby="footnote-'
        'label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<p dir="auto"'
        '>Ran the suite.</p>\n<section data-footnotes="" class="footnotes">'
        '<h2 id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>'
        '\n<ol dir="auto">\n<li id="user-content-fn-1-abbab876102f5c249269f'
        f'a9f80e1bb3a">\n<p dir="auto">Fixes {GITHUB_ISSUE_104} <a href="#us'
        'er-content-fnref-1-abbab876102f5c249269fa9f80e1bb3a" data-footnote'
        '-backref="" aria-label="Back to reference 1" class="data-footnote-'
        'backref">↩</a></p>\n</li>\n</ol>\n</section>'),
    'footnote_definition_bare': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-6c7bc200c'
        'a46d991d005e1f10ee86f2f" id="user-content-fnref-1-6c7bc200ca46d991'
        'd005e1f10ee86f2f" data-footnote-ref="" aria-describedby="footnote-'
        'label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<p dir="auto"'
        '>Ran the suite.</p>\n<section data-footnotes="" class="footnotes">'
        '<h2 id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>'
        '\n<ol dir="auto">\n<li id="user-content-fn-1-6c7bc200ca46d991d005e'
        f'1f10ee86f2f">\n<p dir="auto">See {GITHUB_ISSUE_104} <a href="#user'
        '-content-fnref-1-6c7bc200ca46d991d005e1f10ee86f2f" data-footnote-b'
        'ackref="" aria-label="Back to reference 1" class="data-footnote-ba'
        'ckref">↩</a></p>\n</li>\n</ol>\n</section>'),
    'raw_section': (
        _HEAD
        + _PLAIN
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-labe'
        'l" class="sr-only" dir="auto">Footnotes</h2>\n<p dir="auto">Fixes '
        f'{GITHUB_ISSUE_104}</p>\n</section>'),
    'footnote_in_related': (
        '<h2 dir="auto">Summary</h2>\n<p dir="auto">One sentence.</p>\n<h2 '
        'dir="auto">Related Issues and Pull Requests</h2>\n<p dir="auto">Fi'
        f'xes {GITHUB_ISSUE_101}<sup><a href="#user-content-fn-1-956cfa16221'
        'a7f456af446179cc56569" id="user-content-fnref-1-956cfa16221a7f456a'
        'f446179cc56569" data-footnote-ref="" aria-describedby="footnote-la'
        'bel">1</a></sup></p>\n<h2 dir="auto">Changes</h2>\n<p dir="auto">O'
        'ne change</p>\n<h2 dir="auto">Testing</h2>\n<p dir="auto">Ran the '
        'suite.</p>\n<section data-footnotes="" class="footnotes"><h2 '
        'id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>\n'
        '<ol dir="auto">\n<li id="user-content-fn-1-956cfa16221a7f456af4461'
        f'79cc56569">\n<p dir="auto">Also {GITHUB_ISSUE_104} <a href="#user-'
        'content-fnref-1-956cfa16221a7f456af446179cc56569" data-footnote-ba'
        'ckref="" aria-label="Back to reference 1" class="data-footnote-bac'
        'kref">↩</a></p>\n</li>\n</ol>\n</section>'),
    'empty_testing_with_footnote': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-581d760a2'
        '70e7fee0da7477a35a53366" id="user-content-fnref-1-581d760a270e7fee'
        '0da7477a35a53366" data-footnote-ref="" aria-describedby="footnote-'
        'label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<section '
        'data-footnotes="" class="footnotes"><h2 id="footnote-label" '
        'class="sr-only" dir="auto">Footnotes</h2>\n<ol dir="auto">\n<li '
        'id="user-content-fn-1-581d760a270e7fee0da7477a35a53366">\n<p '
        'dir="auto">Ran the suite. <a href="#user-content-fnref-1-581d760a2'
        '70e7fee0da7477a35a53366" data-footnote-backref="" aria-label="Back'
        ' to reference 1" class="data-footnote-backref">↩</a></p>\n</li>\n'
        '</ol>\n</section>'),
    'no_class': (
        _HEAD
        + _PLAIN
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-labe'
        'l" class="sr-only" dir="auto">Footnotes</h2>\n<p dir="auto">Fixes '
        f'{GITHUB_ISSUE_104}</p>\n</section>'),
    'no_dataattr': (
        _HEAD
        + _PLAIN
        + f'<section>\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n'
        '</section>'),
    'own_heading': (
        _HEAD
        + _PLAIN
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-labe'
        'l" class="sr-only" dir="auto">Footnotes</h2>\n<h2 dir="auto">Extra'
        f'</h2>\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n</section>'),
    'plain_div': (
        _HEAD
        + _PLAIN
        + f'<div dir="auto">\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n'
        '</div>'),
}
