"""The version checker's decoy-region fixtures and cases, shared across suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/test_version_contract_scanners.py so
test_version_contract_template_scanners.py reads one copy of these instead
of executing that whole suite to reach them. The contracts these cases pin
live with the suites that drive the checker; only the fixtures moved.
"""
import re

import _util
from _repo import ROOT


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


def _append_to(copy_root, rel, fragment):
    """Append `fragment` to one copied site file, returning its old bytes."""
    target = copy_root / rel
    before = target.read_bytes()
    target.write_text(before.decode('utf-8') + fragment, encoding='utf-8')
    return before


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


_HTML_TEMPLATE_SITES = (
    '<div class="rail-foot">v9.9.9</div>',
    '<span class="sl-v">9.9.9</span>',
)


_HTML_CLOSE_STARTS = (
    ('script text start', ''),
    ('LF', 'const before = 0;\n'),
    ('CR', 'const before = 0;\r'),
    ('CRLF', 'const before = 0;\r\n'),
    ('line separator', 'const before = 0;\u2028'),
    ('paragraph separator', 'const before = 0;\u2029'),
    ('zero-width no-break space', 'const before = 0;\n\ufeff'),
)


_HTML_LINE_TERMINATORS = (
    ('LF', '\n'),
    ('CR', '\r'),
    ('line separator', '\u2028'),
    ('paragraph separator', '\u2029'),
)


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


def _insert_before_rail_foot(copy_root, markup):
    """Insert `markup` ahead of the real rail-footer site in the copy."""
    dashboard = copy_root / _DASHBOARD
    text = dashboard.read_text(encoding='utf-8')
    anchor = '<div class="rail-foot">'
    assert anchor in text, text
    dashboard.write_text(text.replace(anchor, markup + anchor, 1),
                         encoding='utf-8')
