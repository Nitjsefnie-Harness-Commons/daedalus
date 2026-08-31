#!/usr/bin/env python3
"""The region scanners behind the version checker's decoy filter (#316).

A version pattern inside a comment, a string or a regex literal is not a
binding, and an executable binding must not be swallowed by one. These tests
drive every host scanner over the adversarial cases review reproduced, then
pin the reproduced routes end to end through check, --print and --set.

test_version_contract.py holds the checker's other contracts; these were
split out so neither file crosses its size ceiling.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from test_version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _run_checker,
)


def _checker():
    return _util.load(ROOT / 'scripts' / 'version_regions.py',
                      'version_regions_scanners')


def _surviving(regions, path, text, needle):
    """How many `needle` occurrences survive the checker's decoy filter.

    Routed through the module's own `is_decoy`, so the assertion says what
    the checker counts rather than what its region list happens to hold.
    """
    spans = regions.regions_for(path, text)
    pattern = re.compile(re.escape(needle))
    return sum(1 for found in pattern.finditer(text)
               if not regions.is_decoy(found, spans, False))


def _covered_kind(text, regions, needle):
    """The kind of the region that swallows `needle`'s first occurrence."""
    start = text.index(needle)
    for begin, end, kind in regions:
        if begin < start < end:
            return kind
    raise AssertionError(f'{needle!r} lies inside no region at all')


def _region_texts(path, source):
    """(region text, kind) for every region one source scans into."""
    return [(source[begin:end], kind) for begin, end, kind
            in _checker().regions_for(path, source)]


# The executable duplicate every JavaScript case carries. A scanner that
# swallows code after its construct turns the duplicate into silence, which
# reads as a pass; a second counted match is what says the swallow happened.
_EXECUTABLE = "const x = {script: {version: '9.9.9'}};"
_DECOY = "script: { version: '8.8.8' }"
_JS = 'extension/page.js'

# A duplicate spelled on the construct's own line, with spaced colons so the
# needle counts the case's own duplicate and never the _EXECUTABLE line's:
# a mis-scan that swallows only to the line's end hides this one while the
# next line still reads as code.
_SPACED_DUP = "script : { version : '9.9.9' }"
_BEHIND = 'const dup = ' + _SPACED_DUP + ';\n' + _EXECUTABLE

# (label, source, needle that must be filtered, its region kind, needle that
# must survive as code). A case with no filtered needle pins only that its
# executable part stays code. Each filtered needle begins strictly inside
# its region: a match starting at a region's first character is code by the
# filter's own rule, which is what keeps a quoted key or a `<tag` a site.
_JS_CASES = (
    ('line comment',
     '// ' + _DECOY + '\n' + _EXECUTABLE,
     _DECOY, 'comment', _EXECUTABLE),
    ('block comment',
     '/* ' + _DECOY + ' */\n' + _EXECUTABLE,
     _DECOY, 'comment', _EXECUTABLE),
    ('escaped quote in a single-quoted string',
     "const s = 'script: { version: \\'8.8.8\\' }';\n" + _EXECUTABLE,
     "version: \\'8.8.8\\'", 'string', _EXECUTABLE),
    ('escaped quote in a double-quoted string',
     'const s = "script: { version: \\"8.8.8\\" }";\n' + _EXECUTABLE,
     'version: \\"8.8.8\\"', 'string', _EXECUTABLE),
    ('escaped quote before the string closes',
     "const s = 'it\\'s'; " + _BEHIND,
     "it\\'s'", 'string', _SPACED_DUP),
    ('raw template decoy',
     'const t = `script: { version: \'8.8.8\' }`;\n' + _EXECUTABLE,
     _DECOY, 'string', _EXECUTABLE),
    ('template decoy spanning lines',
     'const t = `script: {\n version: \'8.8.8\' }`;\n' + _EXECUTABLE,
     "'8.8.8'", 'string', _EXECUTABLE),
    ('regex literal holding a quote',
     "const r = /'/;\n" + _EXECUTABLE,
     "'/;", 'regex', _EXECUTABLE),
    ('regex literal holding a comment opener',
     'const re = /[/*]+/g;\n' + _EXECUTABLE,
     '[/*]+/g', 'regex', _EXECUTABLE),
    ('regex class slash with code on the same line',
     'const re = /[/*]+/g; ' + _BEHIND,
     '[/*]+/g', 'regex', _SPACED_DUP),
    ('regex carrying an escaped slash, code on the same line',
     "const re = /'\\/'/; " + _BEHIND,
     "'\\/'", 'regex', _SPACED_DUP),
    ('regex literal carrying a version pattern',
     'const re = /script: { version: \'8.8.8\' }/;\n' + _EXECUTABLE,
     _DECOY, 'regex', _EXECUTABLE),
    ('division, not a regex',
     'const d = a / b / c;\n' + _EXECUTABLE,
     None, None, _EXECUTABLE),
    ('division after a string literal',
     "const s = 'x' / 2; " + _BEHIND,
     None, None, _SPACED_DUP),
    ('regex after a keyword',
     "function f() { return /'/; } " + _BEHIND,
     None, None, _SPACED_DUP),
    ('regex after a block opener',
     "if (x) { /'/; } " + _BEHIND,
     None, None, _SPACED_DUP),
    ('binding inside a template substitution',
     'const t = `${({script: {version: \'9.9.9\'}}).script.version}`;',
     None, None, "script: {version: '9.9.9'}"),
    ('binding behind a nested brace in a substitution',
     'const t = `${ {a: 1} && { ' + _SPACED_DUP + ' } } ' + _DECOY + '`;\n'
     + _EXECUTABLE,
     _DECOY, 'string', _SPACED_DUP),
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


# (label, source) pairs no interpreter can tokenize. Each used to leave the
# scanner with no regions at all, so text inside the malformed string was
# read as the binding it never was.
_MALFORMED = (
    ('an-unterminated-triple-quoted-string', "x = '''never closed\n"),
    ('a-bad-indentation', 'def f():\n    pass\n   pass\n'),
)


def _refused(scan):
    """What SystemExit the checker raises for a source it cannot read."""
    try:
        scan()
    except SystemExit as exc:
        return str(exc)
    raise AssertionError('the malformed source was accepted, not refused')


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


def _append_to(copy_root, rel, fragment):
    """Append `fragment` to one copied site file, returning its old bytes."""
    target = copy_root / rel
    before = target.read_bytes()
    target.write_text(before.decode('utf-8') + fragment, encoding='utf-8')
    return before


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


# (label, markup to insert ahead of the real dashboard site, the regions the
# markup must scan into). A version pattern in ordinary text is not a site,
# and HTML quoting is HTML's: no backslash escapes anything, and a quote
# delimits only inside a tag.
_DASHBOARD = 'dashboard/index.html'
_SITE = 'class="rail-foot">v1.2.3'
_HTML_CASES = (
    ('an-apostrophe-in-ordinary-text',
     "<p>it's ordinary text</p>\n",
     [('"rail-foot"', 'string')]),
    ('a-backslash-and-an-apostrophe-in-text',
     '<p>a backslash \\ and it\'s fine</p>\n',
     [('"rail-foot"', 'string')]),
    ('a-comment-decoy',
     '<!-- <div class="rail-foot">v9.9.9</div> -->\n',
     [('<!-- <div class="rail-foot">v9.9.9</div> -->', 'comment'),
      ('"rail-foot"', 'string')]),
    ('both-attribute-quotes',
     '<div data-a=\'1\' data-b="2"></div>\n',
     [("'1'", 'string'), ('"2"', 'string'), ('"rail-foot"', 'string')]),
    ('a-backslash-inside-an-attribute-value',
     '<div title=\'a\\\' data-x="b">t</div>\n',
     [("'a\\'", 'string'), ('"b"', 'string'), ('"rail-foot"', 'string')]),
)


def test_html_regions_classify_adversarial_cases(tmp):
    """Every HTML case leaves the real site counted and its own markup in
    the regions it belongs to."""
    del tmp
    for label, markup, expected in _HTML_CASES:
        source = markup + '<div ' + _SITE + '</div>'
        assert _surviving(_checker(), _DASHBOARD, source, _SITE) == 1, label
        assert _region_texts(_DASHBOARD, source) == expected, label


def test_midline_html_close_marker_stays_javascript(tmp):
    """A mid-line `-->` is an operator sequence, not an HTML comment."""
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    code = '0; const text'
    source = f'<script>\nconst r = 1-->0; const text = "{decoy}";\n</script>'
    regions = _checker()
    spans = regions.regions_for(_DASHBOARD, source)
    assert _surviving(regions, _DASHBOARD, source, code) == 1
    assert _surviving(regions, _DASHBOARD, source, decoy) == 0
    assert _covered_kind(source, spans, decoy) == 'string'


def test_html_close_marker_mode_is_off_for_javascript_files(tmp):
    """A `.js` file keeps the scanner's pre-HTML comment grammar."""
    del tmp
    source = '--> const live = ' + _SPACED_DUP + ';\n'
    assert _surviving(_checker(), _JS, source, _SPACED_DUP) == 1


_HTML_CLOSE_STARTS = (
    ('script text start', ''),
    ('LF', 'const before = 0;\n'),
    ('CR', 'const before = 0;\r'),
    ('CRLF', 'const before = 0;\r\n'),
    ('line separator', 'const before = 0;\u2028'),
    ('paragraph separator', 'const before = 0;\u2029'),
    ('zero-width no-break space', 'const before = 0;\n\ufeff'),
)


def test_html_close_comments_begin_at_every_javascript_line_start(tmp):
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    live = 'const live = 1;'
    regions = _checker()
    for label, prefix in _HTML_CLOSE_STARTS:
        source = f'<script>{prefix}--> {decoy}\n{live}</script>'
        assert _surviving(regions, _DASHBOARD, source, decoy) == 0, label
        assert _surviving(regions, _DASHBOARD, source, live) == 1, label


_HTML_LINE_TERMINATORS = (
    ('LF', '\n'),
    ('CR', '\r'),
    ('line separator', '\u2028'),
    ('paragraph separator', '\u2029'),
)


def test_html_comments_end_at_every_javascript_line_terminator(tmp):
    del tmp
    decoy = "<span class='sl-v'>9.9.9</span>"
    live = 'const live = 1;'
    regions = _checker()
    for label, terminator in _HTML_LINE_TERMINATORS:
        source = f'<script>\n--> {decoy}{terminator}{live}</script>'
        assert _surviving(regions, _DASHBOARD, source, decoy) == 0, label
        assert _surviving(regions, _DASHBOARD, source, live) == 1, label


_SCRIPT_END_CASES = (
    ('escaped entry and dash-dash exit',
     '<!--><script></script>tail</script>'),
    ('escaped dash reset and exit',
     '<!-- a - b --><script></script>tail</script>'),
    ('double-escaped dash-dash exit',
     '<!--<script>--></script>tail</script>'),
    ('form-feed end-tag delimiter',
     'code</script\f>tail</script>'),
)


def test_script_data_state_limbs_reach_the_first_end_tag(tmp):
    """Each state-machine limb leaves the first end tag as the closer."""
    del tmp
    regions = _checker()
    for label, source in _SCRIPT_END_CASES:
        expected = source.index('</script')
        assert regions._html_script_end(source, 0) == expected, label


def _insert_before_rail_foot(copy_root, markup):
    """Insert `markup` ahead of the real rail-footer site in the copy."""
    dashboard = copy_root / _DASHBOARD
    text = dashboard.read_text(encoding='utf-8')
    anchor = '<div class="rail-foot">'
    assert anchor in text, text
    dashboard.write_text(text.replace(anchor, markup + anchor, 1),
                         encoding='utf-8')


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
