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
- heading_wrapped              an empty footnote section on a heading line
- heading_wrapped_ref          the same, holding a closing reference
- forged_label                 a heading spelling the label's id and class
- forged_label_in_section      that forgery inside a written section
- author_footnotes_heading     an author's own `## Footnotes` heading
- id_only                      the forgery carrying the id alone
- class_only                   the forgery carrying the class alone
- id_upper_attr                the forgery in upper-case attribute names
- two_sections                 a written section beside a definition
- label_no_footnote            the forgery ahead of a closing reference
- wrapper_then_text            a wrapper mid-section, prose after it
- related_wrapper_then_ref     a wrapper inside Related, reference after
- heading_keyword_across_wrapper  a heading-line keyword list broken by one

NESTED_HEADING_HTML holds two more, kept out of that map because they
are refused rather than parsed: a rendered body reaches the parser's
nested-heading refusal whenever a heading line wraps a raw element
holding another heading, with or without a footnote section.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _prgate import GITHUB_ISSUE_101, GITHUB_ISSUE_104  # noqa: E402


# Captured from GitHub's /markdown endpoint in GFM mode with
# Nitjsefnie-Harness-Commons/daedalus as the context. It sits here
# rather than beside its 101 and 104 siblings in _prgate because the
# two fixtures naming issue 105 are both in this file.
GITHUB_ISSUE_105 = (
    '<a class="issue-link js-issue-link" data-error-text="Failed to load '
    'title" data-id="5232282547" data-permission-text="Title is private" '
    'data-url="https://github.com/Nitjsefnie-Harness-Commons/daedalus/issues'
    '/105" data-hovercard-type="issue" '
    'data-hovercard-url="/Nitjsefnie-Harness-Commons/daedalus/issues/105/hov'
    'ercard" '
    'href="https://github.com/Nitjsefnie-Harness-Commons/daedalus/issues/105'
    '">#105</a>')

_HEAD = (
    '<h2 dir="auto">Summary</h2>\n<p dir="auto">One sentence.</p>\n<h2 '
    'dir="auto">Related Issues and Pull Requests</h2>\n<p dir="auto">Fixes '
    f'{GITHUB_ISSUE_101}</p>\n<h2 dir="auto">Changes</h2>\n')

_PLAIN = (
    '<p dir="auto">One change</p>\n<h2 dir="auto">Testing</h2>\n<p dir="aut'
    'o">Ran the suite.</p>\n')

_RAN_IT = (
    '<p dir="auto">One change</p>\n<h2 dir="auto">Testing</h2>\n<p dir="aut'
    'o">Ran it.</p>\n')

_RAN_IT_REF = (
    '<p dir="auto">One change[^1]</p>\n<h2 dir="auto">Testing</h2>\n<p dir='
    '"auto">Ran it.</p>\n')

FOOTNOTE_HTML = {
    'footnote_definition_closing': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-abbab87'
        '6102f5c249269fa9f80e1bb3a" id="user-content-fnref-1-abbab876102f'
        '5c249269fa9f80e1bb3a" data-footnote-ref="" aria-describedby="foo'
        'tnote-label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<p di'
        'r="auto">Ran the suite.</p>\n<section data-footnotes="" class="f'
        'ootnotes"><h2 id="footnote-label" class="sr-only" dir="auto">Foo'
        'tnotes</h2>\n<ol dir="auto">\n<li id="user-content-fn-1-abbab876'
        '102f5c249269fa9f80e1bb3a">\n<p dir="auto">Fixes '
        f'{GITHUB_ISSUE_104} <a href="#user-content-fnref-1-abbab876102f5'
        'c249269fa9f80e1bb3a" data-footnote-backref="" aria-label="Back t'
        'o reference 1" class="data-footnote-backref">↩</a></p>\n</li>\n<'
        '/ol>\n</section>'),
    'footnote_definition_bare': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-6c7bc20'
        '0ca46d991d005e1f10ee86f2f" id="user-content-fnref-1-6c7bc200ca46'
        'd991d005e1f10ee86f2f" data-footnote-ref="" aria-describedby="foo'
        'tnote-label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<p di'
        'r="auto">Ran the suite.</p>\n<section data-footnotes="" class="f'
        'ootnotes"><h2 id="footnote-label" class="sr-only" dir="auto">Foo'
        'tnotes</h2>\n<ol dir="auto">\n<li id="user-content-fn-1-6c7bc200'
        'ca46d991d005e1f10ee86f2f">\n<p dir="auto">See '
        f'{GITHUB_ISSUE_104} <a href="#user-content-fnref-1-6c7bc200ca46d'
        '991d005e1f10ee86f2f" data-footnote-backref="" aria-label="Back t'
        'o reference 1" class="data-footnote-backref">↩</a></p>\n</li>\n<'
        '/ol>\n</section>'),
    'raw_section': (
        _HEAD
        + _PLAIN
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-la'
        'bel" class="sr-only" dir="auto">Footnotes</h2>\n<p dir="auto">Fi'
        f'xes {GITHUB_ISSUE_104}</p>\n</section>'),
    'footnote_in_related': (
        '<h2 dir="auto">Summary</h2>\n<p dir="auto">One sentence.</p>\n<h'
        '2 dir="auto">Related Issues and Pull Requests</h2>\n<p dir="auto'
        f'">Fixes {GITHUB_ISSUE_101}<sup><a href="#user-content-fn-1-956c'
        'fa16221a7f456af446179cc56569" id="user-content-fnref-1-956cfa162'
        '21a7f456af446179cc56569" data-footnote-ref="" aria-describedby="'
        'footnote-label">1</a></sup></p>\n<h2 dir="auto">Changes</h2>\n<p'
        ' dir="auto">One change</p>\n<h2 dir="auto">Testing</h2>\n<p dir='
        '"auto">Ran the suite.</p>\n<section data-footnotes="" class="foo'
        'tnotes"><h2 id="footnote-label" class="sr-only" dir="auto">Footn'
        'otes</h2>\n<ol dir="auto">\n<li id="user-content-fn-1-956cfa1622'
        f'1a7f456af446179cc56569">\n<p dir="auto">Also {GITHUB_ISSUE_104}'
        ' <a href="#user-content-fnref-1-956cfa16221a7f456af446179cc56569'
        '" data-footnote-backref="" aria-label="Back to reference 1" clas'
        's="data-footnote-backref">↩</a></p>\n</li>\n</ol>\n</section>'),
    'empty_testing_with_footnote': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-581d760'
        'a270e7fee0da7477a35a53366" id="user-content-fnref-1-581d760a270e'
        '7fee0da7477a35a53366" data-footnote-ref="" aria-describedby="foo'
        'tnote-label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<sect'
        'ion data-footnotes="" class="footnotes"><h2 id="footnote-label" '
        'class="sr-only" dir="auto">Footnotes</h2>\n<ol dir="auto">\n<li '
        'id="user-content-fn-1-581d760a270e7fee0da7477a35a53366">\n<p dir'
        '="auto">Ran the suite. <a href="#user-content-fnref-1-581d760a27'
        '0e7fee0da7477a35a53366" data-footnote-backref="" aria-label="Bac'
        'k to reference 1" class="data-footnote-backref">↩</a></p>\n</li>'
        '\n</ol>\n</section>'),
    'no_class': (
        _HEAD
        + _PLAIN
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-la'
        'bel" class="sr-only" dir="auto">Footnotes</h2>\n<p dir="auto">Fi'
        f'xes {GITHUB_ISSUE_104}</p>\n</section>'),
    'no_dataattr': (
        _HEAD
        + _PLAIN
        + f'<section>\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n</sectio'
        'n>'),
    'own_heading': (
        _HEAD
        + _PLAIN
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-la'
        'bel" class="sr-only" dir="auto">Footnotes</h2>\n<h2 dir="auto">E'
        f'xtra</h2>\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n</sectio'
        'n>'),
    'plain_div': (
        _HEAD
        + _PLAIN
        + f'<div dir="auto">\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n<'
        '/div>'),
    'heading_wrapped': (
        _HEAD
        + '<p dir="auto">One change</p>\n<h2 dir="auto">Testing <section da'
        'ta-footnotes="" class="footnotes"><h2 id="footnote-label" class='
        '"sr-only" dir="auto">Footnotes</h2></section></h2>'),
    'heading_wrapped_ref': (
        _HEAD
        + '<p dir="auto">One change</p>\n<h2 dir="auto">Testing <section da'
        'ta-footnotes="" class="footnotes"><h2 id="footnote-label" class='
        f'"sr-only" dir="auto">Footnotes</h2>Fixes {GITHUB_ISSUE_104}</se'
        'ction></h2>'),
    'forged_label': (
        _HEAD
        + _RAN_IT
        + '<h2 id="user-content-footnote-label" dir="auto">Trap</h2>'),
    'forged_label_in_section': (
        _HEAD
        + _RAN_IT
        + '<section data-footnotes="" class="footnotes"><h2 id="footnote-la'
        'bel" class="sr-only" dir="auto">Footnotes</h2>\n<h2 id="user-con'
        'tent-footnote-label" dir="auto">Trap</h2>\n<p dir="auto">Fixes '
        f'{GITHUB_ISSUE_104}</p>\n</section>'),
    'author_footnotes_heading': (
        _HEAD
        + _RAN_IT
        + '<h2 dir="auto">Footnotes</h2>\n<p dir="auto">Fixes '
        f'{GITHUB_ISSUE_104}</p>'),
    'id_only': (
        _HEAD
        + _RAN_IT_REF
        + '<h2 id="user-content-footnote-label" dir="auto">Trap</h2>'),
    'class_only': (
        _HEAD
        + _RAN_IT_REF
        + '<h2 dir="auto">Trap</h2>'),
    'id_upper_attr': (
        _HEAD
        + _RAN_IT_REF
        + '<h2 id="user-content-footnote-label" dir="auto">Trap</h2>'),
    'two_sections': (
        _HEAD
        + '<p dir="auto">One change<sup><a href="#user-content-fn-1-6ded4ed'
        '1858c945685fa4b4e5286e793" id="user-content-fnref-1-6ded4ed1858c'
        '945685fa4b4e5286e793" data-footnote-ref="" aria-describedby="foo'
        'tnote-label">1</a></sup></p>\n<h2 dir="auto">Testing</h2>\n<p di'
        'r="auto">Ran it.</p>\n<section data-footnotes="" class="footnote'
        's"><h2 id="footnote-label" class="sr-only" dir="auto">Footnotes<'
        f'/h2>\n<p dir="auto">Fixes {GITHUB_ISSUE_104}</p>\n</section>\n<'
        'section data-footnotes="" class="footnotes"><h2 id="footnote-lab'
        'el" class="sr-only" dir="auto">Footnotes</h2>\n<ol dir="auto">\n'
        '<li id="user-content-fn-1-6ded4ed1858c945685fa4b4e5286e793">\n<p'
        f' dir="auto">Fixes {GITHUB_ISSUE_105} <a href="#user-content-fnr'
        'ef-1-6ded4ed1858c945685fa4b4e5286e793" data-footnote-backref="" '
        'aria-label="Back to reference 1" class="data-footnote-backref">↩'
        '</a></p>\n</li>\n</ol>\n</section>'),
    'label_no_footnote': (
        _HEAD
        + _RAN_IT
        + '<h2 id="user-content-footnote-label" dir="auto">Trap</h2>\n<p di'
        f'r="auto">Fixes {GITHUB_ISSUE_104}</p>'),
    'wrapper_then_text': (
        _HEAD
        + '<p dir="auto">One change</p>\n<h2 dir="auto">Testing</h2>\n<section'
        ' data-footnotes="" class="footnotes"><h2 id="footnote-label" class="s'
        'r-only" dir="auto">Footnotes</h2></section>\n<p dir="auto">Ran the su'
        'ite.</p>'),
    'related_wrapper_then_ref': (
        '<h2 dir="auto">Summary</h2>\n<p dir="auto">One sentence.</p>\n<h2 dir'
        '="auto">Related Issues and Pull Requests</h2>\n<p dir="auto">Fixes '
        f'{GITHUB_ISSUE_101}</p>\n<section data-footnotes="" class="footnotes"'
        '><h2 id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>\n<'
        'p dir="auto">note</p>\n</section>\n<p dir="auto">See '
        f'{GITHUB_ISSUE_104}</p>\n<h2 dir="auto">Changes</h2>\n<p dir="auto">O'
        'ne change</p>\n<h2 dir="auto">Testing</h2>\n<p dir="auto">Ran the sui'
        'te.</p>'),
    'heading_keyword_across_wrapper': (
        _HEAD
        + '<p dir="auto">One change</p>\n<h2 dir="auto">Testing fixes '
        f'{GITHUB_ISSUE_104}, <section data-footnotes="" class="footnotes"><h2'
        ' id="footnote-label" class="sr-only" dir="auto">Footnotes</h2>'
        f'{GITHUB_ISSUE_105}</section></h2>'),
}

NESTED_HEADING_HTML = {
    'raw_section_in_heading': (
        _HEAD
        + '<p dir="auto">One change</p>\n<h2 dir="auto">Testing <section><h'
        '2 dir="auto">Inner</h2></section></h2>'),
    'forged_label_in_heading': (
        _HEAD
        + '<p dir="auto">One change</p>\n<h2 dir="auto">Testing <section da'
        'ta-footnotes="" class="footnotes"><h2 id="footnote-label" class='
        '"sr-only" dir="auto">Footnotes</h2><h2 id="user-content-footnote'
        '-label" dir="auto">Trap</h2></section></h2>'),
}
