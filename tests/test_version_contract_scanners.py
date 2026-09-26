#!/usr/bin/env python3
"""The version checker's decoy-region scanner contracts (#316).

HTML text-mode cases live in test_version_contract_template_scanners.py.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _run_checker,
)
from _version_contract_scanners import (  # noqa: E402
    _append_to,
    _checker,
    _covered_kind,
    _DASHBOARD,
    _EXECUTABLE,
    _HTML_CASES,
    _HTML_CLOSE_STARTS,
    _HTML_LINE_TERMINATORS,
    _HTML_TEMPLATE_SITES,
    _insert_before_rail_foot,
    _JS,
    _JS_CASES,
    _MALFORMED,
    _region_texts,
    _refused,
    _SCRIPT_END_CASES,
    _SITE,
    _SPACED_DUP,
    _surviving,
)


def test_javascript_regions_classify_adversarial_cases(tmp):
    """Every JavaScript case keeps its decoy filtered and its executable
    binding counted.

    A regex literal is its own region kind — a version pattern inside one is
    a pattern, not a site — but it still has to end where JavaScript ends
    it, and a template substitution is scanned as the code it is.
    """
    del tmp
    for label, source, filtered, kind, code in _JS_CASES:
        context = (label, source)
        assert _surviving(_checker(), _JS, source, code) == 1, context
        if filtered is None:
            continue
        assert _surviving(_checker(), _JS, source, filtered) == 0, context
        assert _covered_kind(
            source, _checker().regions_for(_JS, source),
            filtered) == kind, context


def test_a_division_slash_opens_no_region(tmp):
    """Division leaves the code around it unfiltered."""
    del tmp
    source = 'const d = a / b / c;\n' + _EXECUTABLE
    assert _region_texts(_JS, source) == [("'9.9.9'", 'string')]


def test_a_substitution_spans_to_its_own_closing_brace(tmp):
    """A substitution does not end at a nested object's `}`: the code behind
    a nested brace stays code, and the template text past its own `}` is
    string again."""
    del tmp
    source = ('const t = `${ {a: 1} && { script : { version : \'9.9.9\' } }'
              ' }`;\n')
    assert _region_texts(_JS, source) == [
        ('`', 'string'),
        ("'9.9.9'", 'string'),
        ('`', 'string'),
    ]


def test_json_regions_honour_escaped_quotes(tmp):
    """An escaped quote is content in a JSON string, not its end."""
    del tmp
    source = '{"a": "x \\"y\\" z", "version": "1.2.3"}'
    assert _region_texts('extension/manifest.json', source) == [
        ('"a"', 'string'),
        ('"x \\"y\\" z"', 'string'),
        ('"version"', 'string'),
        ('"1.2.3"', 'string'),
    ]
    assert _surviving(_checker(), 'extension/manifest.json', source,
                      '"version": "1.2.3"') == 1


def test_python_regions_filter_comments_and_strings(tmp):
    """Python decoys are filtered by token, not by quote hunting."""
    del tmp
    source = ("# __version__ = '9.9.9'\n"
              '_s = \'\'\'__version__ = "8.8.8"\'\'\'\n'
              '__version__ = "1.2.3"\n')
    path = 'daedalus_cli/__init__.py'
    regions = _checker()
    for needle, kind in (("__version__ = '9.9.9'", 'comment'),
                         ('__version__ = "8.8.8"', 'string')):
        assert _surviving(regions, path, source, needle) == 0, needle
        assert _covered_kind(
            source, regions.regions_for(path, source), needle) == kind, needle
    assert _surviving(regions, path, source, '__version__ = "1.2.3"') == 1


def test_python_regions_align_across_a_form_feed(tmp):
    """The row map splits where the tokenizer reads rows — on the newline —
    so a form feed inside a line cannot shift the regions after it."""
    del tmp
    source = 'x = 1\x0c2\ny = "decoy"\nz = "1.2.3"\n'
    regions = _checker().regions_for('daedalus_cli/__init__.py', source)
    assert regions == [(12, 19, 'string'), (24, 31, 'string')], regions
    assert _surviving(_checker(), 'daedalus_cli/__init__.py', source,
                      'decoy') == 0


def test_a_python_source_that_cannot_be_tokenized_is_refused(tmp):
    """The Python scanner refuses what it cannot tokenize instead of handing
    back no regions and reading the text inside a malformed string as the
    binding it never was."""
    del tmp
    for label, source in _MALFORMED:
        message = _refused(
            lambda source=source: _checker()._python_regions(source, 'x.py'))
        assert 'x.py' in message, (label, message)
        assert 'tokenize' in message, (label, message)


def _malformed_tree(tmp, label, fragment):
    """Copy the versioned tree and break its Python site beyond tokenizing.

    The copy sits in a directory whose last component is `tree`, which is
    what the coverage path mapping requires of a child that keeps the
    collector.
    """
    copy_root = Path(tmp) / label / 'tree'
    _copy_versioned_tree(copy_root)
    _append_to(copy_root, 'daedalus_cli/__init__.py', '\n' + fragment + '\n')
    return copy_root


def test_check_refuses_a_source_it_cannot_tokenize(tmp):
    """check refuses a tree whose Python site it cannot tokenize."""
    for label, fragment in _MALFORMED:
        copy_root = _malformed_tree(tmp, label, fragment)
        r = _run_checker(copy_root)
        assert r.returncode != 0, (label, r.returncode, r.stdout, r.stderr)
        assert 'tokenize' in r.stderr, (label, r.stderr)
        assert 'daedalus_cli/__init__.py' in r.stderr, (label, r.stderr)


def test_print_refuses_a_source_it_cannot_tokenize(tmp):
    """--print hands out nothing for a tree it cannot read."""
    for label, fragment in _MALFORMED:
        copy_root = _malformed_tree(tmp, label, fragment)
        r = _run_checker(copy_root, '--print')
        assert r.returncode != 0, (label, r.returncode, r.stdout, r.stderr)
        assert r.stdout.strip() == '', (label, r.stdout)
        assert 'tokenize' in r.stderr, (label, r.stderr)


def test_set_refuses_a_source_it_cannot_tokenize_and_writes_nothing(tmp):
    """--set is a rewrite mode: a tree it cannot read is refused before a
    single site is rewritten."""
    for label, fragment in _MALFORMED:
        copy_root = Path(tmp) / label / 'tree'
        checker = _copy_versioned_tree(copy_root)
        before = {path: (copy_root / path).read_bytes()
                  for path, _, _ in checker.SITES}
        target = 'daedalus_cli/__init__.py'
        appended = before[target] + ('\n' + fragment + '\n').encode('utf-8')
        (copy_root / target).write_bytes(appended)
        r = _run_checker(copy_root, '--set', '9.9.9')
        assert r.returncode != 0, (label, r.returncode, r.stdout, r.stderr)
        assert 'tokenize' in r.stderr, (label, r.stderr)
        for path, _, _ in checker.SITES:
            expected = appended if path == target else before[path]
            assert (copy_root / path).read_bytes() == expected, (label, path)


def test_html_regions_classify_adversarial_cases(tmp):
    """Every HTML case leaves the real site counted and its own markup in
    the regions it belongs to."""
    del tmp
    for label, markup, expected in _HTML_CASES:
        source = markup + '<div ' + _SITE + '</div>'
        assert _surviving(_checker(), _DASHBOARD, source, _SITE) == 1, label
        assert _region_texts(_DASHBOARD, source) == expected, label


def test_html_template_version_sites_are_inert(tmp):
    del tmp
    regions = _checker()
    for site in _HTML_TEMPLATE_SITES:
        source = '<template>' + site + '</template>'
        spans = regions.regions_for(_DASHBOARD, source)
        assert _surviving(regions, _DASHBOARD, source, site) == 0, site
        assert _covered_kind(source, spans, site) == 'inert', site
        assert [kind for _start, _end, kind in spans] == ['inert'], site


def test_html_scanning_resumes_after_a_closed_template(tmp):
    del tmp
    decoy = _HTML_TEMPLATE_SITES[0]
    source = '<template>' + decoy + '</template><div ' + _SITE + '</div>'
    regions = _checker()
    assert _surviving(regions, _DASHBOARD, source, decoy) == 0
    assert _surviving(regions, _DASHBOARD, source, _SITE) == 1


def test_nested_html_template_content_stays_inert(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[0]
    source = '<template><template></template>' + site + '</template>'
    assert _surviving(_checker(), _DASHBOARD, source, site) == 0


def test_html_template_closer_in_a_quoted_attribute_is_inert(tmp):
    del tmp
    site = _HTML_TEMPLATE_SITES[1]
    source = ('<template><div title="</template>">' + site
              + '</div></template><div ' + _SITE + '</div>')
    regions = _checker()
    assert _surviving(regions, _DASHBOARD, source, site) == 0
    assert _surviving(regions, _DASHBOARD, source, _SITE) == 1


def test_html_template_comment_tags_do_not_change_depth(tmp):
    del tmp
    decoy = _HTML_TEMPLATE_SITES[0]
    regions = _checker()
    comments = ('<!-- x > <template> </template> -->',
                '<!-- <template> x > </template> -->')
    for comment in comments:
        source = ('<template>' + comment + decoy
                  + '</template><div ' + _SITE + '</div>')
        assert _surviving(regions, _DASHBOARD, source, decoy) == 0, comment
        assert _surviving(regions, _DASHBOARD, source, _SITE) == 1, comment


def test_midline_html_close_marker_stays_javascript(tmp):
    """A mid-line `-->` is an operator sequence, not an HTML comment."""
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    code = '0; const text'
    regions = _checker()
    expressions = (('adjacent', '1-->0'), ('after whitespace', '1 --> 0'))
    for label, expression in expressions:
        source = (
            f'<script>\nconst r = {expression}; '
            f'const text = "{decoy}";\n</script>')
        spans = regions.regions_for(_DASHBOARD, source)
        assert _surviving(
            regions, _DASHBOARD, source, code) == 1, label
        assert _surviving(
            regions, _DASHBOARD, source, decoy) == 0, label
        assert _covered_kind(source, spans, decoy) == 'string', label


def test_html_close_marker_mode_is_off_for_javascript_files(tmp):
    """A `.js` file keeps the scanner's pre-HTML comment grammar."""
    del tmp
    source = '--> const live = ' + _SPACED_DUP + ';\n'
    assert _surviving(_checker(), _JS, source, _SPACED_DUP) == 1


def test_html_close_comments_begin_at_every_javascript_line_start(tmp):
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    live = 'const live = 1;'
    regions = _checker()
    for label, prefix in _HTML_CLOSE_STARTS:
        source = f'<script>{prefix}--> {decoy}\n{live}</script>'
        assert _surviving(regions, _DASHBOARD, source, decoy) == 0, label
        assert _surviving(regions, _DASHBOARD, source, live) == 1, label


def test_html_comments_end_at_every_javascript_line_terminator(tmp):
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    live = 'const live = 1;'
    regions = _checker()
    for label, terminator in _HTML_LINE_TERMINATORS:
        source = f'<script>\n--> {decoy}{terminator}{live}</script>'
        assert _surviving(regions, _DASHBOARD, source, decoy) == 0, label
        assert _surviving(regions, _DASHBOARD, source, live) == 1, label


def test_html_comment_uses_the_nearest_line_terminator(tmp):
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    live = 'const live = 1;'
    regions = _checker()
    orders = (('CR before LF', '\r', '\n'),
              ('LF before CR', '\n', '\r'))
    for label, nearer, farther in orders:
        source = (
            f'<script>\n--> {decoy}{nearer}{live}{farther}</script>')
        assert _surviving(
            regions, _DASHBOARD, source, decoy) == 0, label
        assert _surviving(
            regions, _DASHBOARD, source, live) == 1, label


def test_slash_comments_in_html_end_at_unicode_line_terminators(tmp):
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    live = 'const live = 1;'
    regions = _checker()
    terminators = (('line separator', '\u2028'),
                   ('paragraph separator', '\u2029'))
    for label, terminator in terminators:
        source = f'<script>\n// {decoy}{terminator}{live}</script>'
        assert _surviving(
            regions, _DASHBOARD, source, decoy) == 0, label
        assert _surviving(
            regions, _DASHBOARD, source, live) == 1, label


def test_script_data_state_limbs_reach_the_first_end_tag(tmp):
    """Each state-machine limb leaves the first end tag as the closer."""
    del tmp
    regions = _checker()
    for label, source in _SCRIPT_END_CASES:
        expected = source.index('</script')
        assert regions._html_script_end(source, 0) == expected, label


def test_check_survives_an_apostrophe_in_html_text(tmp):
    """An apostrophe in ordinary text is not a quote delimiter (#316)."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    _insert_before_rail_foot(copy_root, "<p>it's ordinary text</p>\n")
    r = _run_checker(copy_root)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'ok:' in r.stdout, r.stdout


def test_check_survives_the_html_adversarial_cases(tmp):
    """No HTML case takes the real dashboard site out of the count."""
    for index, (label, markup, _expected) in enumerate(_HTML_CASES):
        copy_root = Path(tmp) / f'case-{index}' / 'tree'
        _copy_versioned_tree(copy_root)
        _insert_before_rail_foot(copy_root, markup)
        r = _run_checker(copy_root)
        assert r.returncode == 0, (label, r.returncode, r.stdout, r.stderr)
        assert 'ok:' in r.stdout, (label, r.stdout)


def test_check_ignores_version_sites_in_html_template_content(tmp):
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    dashboard = copy_root / _DASHBOARD
    text = dashboard.read_text(encoding='utf-8')
    markup = ''.join('<template>' + site + '</template>\n'
                     for site in _HTML_TEMPLATE_SITES)
    assert text.count('</body>') == 1, text
    dashboard.write_text(text.replace('</body>', markup + '</body>'),
                         encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'ok:' in r.stdout, r.stdout


def test_check_counts_a_duplicate_after_a_regex_literal(tmp):
    """A quote inside a regex literal must not swallow the executable
    binding after it (#316)."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    page = copy_root / 'extension' / 'page.js'
    page.write_text(
        page.read_text(encoding='utf-8') + "\nconst _probe = /'/;\n"
        + "const _dup = {script: {version: '9.9.9'}};\n",
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert '9.9.9' in r.stderr, r.stderr


def test_check_counts_a_duplicate_in_a_template_substitution(tmp):
    """A template substitution is executable code, so a binding inside one
    counts (#316)."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    page = copy_root / 'extension' / 'page.js'
    page.write_text(
        page.read_text(encoding='utf-8')
        + '\nconst _dup = `${({script: {version: \'9.9.9\'}})'
        + '.script.version}`;\n',
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr


def test_check_counts_a_duplicate_in_a_brace_nested_substitution(tmp):
    """A binding behind a nested brace inside a substitution is executable
    code too: the substitution ends at its own closing brace."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    page = copy_root / 'extension' / 'page.js'
    page.write_text(
        page.read_text(encoding='utf-8')
        + '\nconst _probe = `${ {a: 1} && { script : { version : '
        + '\'9.9.9\' } } }`;\n',
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr


def test_check_survives_a_json_escaped_quote(tmp):
    """Escaped quotes in a manifest string are content, not delimiters."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    _add_json_decoy(copy_root)
    r = _run_checker(copy_root)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'ok:' in r.stdout, r.stdout


def _add_json_decoy(copy_root):
    """Spell an escaped quote into the copied manifest's description."""
    manifest = copy_root / 'extension' / 'manifest.json'
    text = manifest.read_text(encoding='utf-8')
    new_text, count = re.subn(
        r'"description": "[^"]*"',
        '"description": "handles \\"version\\" too"', text)
    assert count == 1, text
    manifest.write_text(new_text, encoding='utf-8')


def test_set_rewrites_every_site_beside_decoys(tmp):
    """--set still finds the one real site in a tree full of decoys, and the
    tree it leaves behind passes."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    page = copy_root / 'extension' / 'page.js'
    page.write_text(
        page.read_text(encoding='utf-8')
        + '\n// script: { version: \'8.8.8\' }\n'
        + '/* script: { version: \'7.7.7\' } */\n'
        + "const _s = 'script: { version: \\'6.6.6\\' }';\n"
        + "const _t = `script: { version: '5.5.5' }`;\n"
        + "const _r = /script: { version: '4.4.4' }/;\n",
        encoding='utf-8')
    _insert_before_rail_foot(
        copy_root,
        "<p>it's ordinary text</p>\n"
        '<!-- <div class="rail-foot">v9.9.9</div> -->\n')
    _add_json_decoy(copy_root)
    target = copy_root / 'daedalus_cli' / '__init__.py'
    target.write_text(
        target.read_text(encoding='utf-8')
        + "\n# __version__ = '9.9.9'\n"
        + '_decoy = "__version__ = \'8.8.8\'"\n',
        encoding='utf-8')
    r = _run_checker(copy_root, '--set', '9.9.9')
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    for path, desc, pattern in checker.SITES:
        text = (copy_root / path).read_text(encoding='utf-8')
        match = re.search(pattern, text)
        assert match and match.group('v') == '9.9.9', (path, desc)
    r = _run_checker(copy_root)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'ok:' in r.stdout, r.stdout


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_scanners_')


if __name__ == '__main__':
    raise SystemExit(main())
