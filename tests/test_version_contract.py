#!/usr/bin/env python3
"""One version, spelled the same way everywhere it is written down.

The version appears in the package metadata, the extension manifest, the CLI
and the documentation, and scripts/check_versions.py is what keeps them in
step. These tests drive that checker against trees that agree and trees that
do not, and pin the release gates that refuse a version already published.
"""
import importlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_VERSION_SITE_COUNT_WORDS = {
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'eleven': 11,
    'twelve': 12,
}
_VERSION_SITE_COUNT_COMMENT = re.compile(
    r'^\s*# sends its report to stderr, so a tree whose '
    r'(?P<word>[a-z]+) sites disagree\s*$', re.MULTILINE)


def _version_workflow_site_count(workflow):
    """Read the concrete site count from the version workflow comment."""
    match = _VERSION_SITE_COUNT_COMMENT.search(workflow)
    if match is None:
        raise AssertionError('version workflow site-count comment not found')
    word = match.group('word')
    try:
        return _VERSION_SITE_COUNT_WORDS[word]
    except KeyError as exc:
        raise AssertionError(
            f'unrecognised version-site count word {word!r} in workflow comment') from exc


def _copy_versioned_tree(dest):
    """Copy just the files check_versions.py reads, preserving layout."""
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_ro')
    rel_paths = {p for p, _, _ in checker.SITES}
    rel_paths.add('scripts/check_versions.py')
    for rel in rel_paths:
        src = ROOT / rel
        dst = Path(dest) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return checker


def test_check_versions_passes_on_tree(tmp):
    r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_versions.py')],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'ok:' in r.stdout, r.stdout


def test_check_versions_site_list_covers_package(tmp):
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_sites')
    site_keys = {(p, d) for p, d, _ in checker.SITES}
    # The package __version__ is a version claim about the wire format; it must
    # be one of the checked sites, or the wheel can drift from the extension.
    assert ('daedalus_cli/__init__.py', 'package __version__') in site_keys, site_keys
    assert checker.CANONICAL in site_keys, checker.CANONICAL


def test_check_versions_detects_drift(tmp):
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    # Break exactly one site, in the COPY — never the real files.
    init_copy = copy_root / 'daedalus_cli' / '__init__.py'
    text = init_copy.read_text(encoding='utf-8')
    new_text, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                          '__version__ = "0.0.0-drift"', text)
    assert n == 1, text
    init_copy.write_text(new_text, encoding='utf-8')
    r = subprocess.run([sys.executable, str(copy_root / 'scripts' / 'check_versions.py')],
                       cwd=str(copy_root),
                       env=_util.child_coverage('keep', cwd=copy_root),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert 'FAIL' in r.stderr, r.stderr
    assert 'daedalus_cli/__init__.py' in r.stderr, r.stderr


def test_check_versions_sites_all_present_in_copy(tmp):
    """The drift test copies every file the checker reads — prove the copy is
    complete, so a missing file can never masquerade as a version mismatch."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    r = subprocess.run([sys.executable, str(copy_root / 'scripts' / 'check_versions.py')],
                       cwd=str(copy_root),
                       env=_util.child_coverage('keep', cwd=copy_root),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    expected = len({(p, d) for p, d, _ in checker.SITES})
    m = re.search(r'consistent across (\d+) sites', r.stdout)
    assert m and int(m.group(1)) == expected, r.stdout


def _versioned_git_tree(tmp):
    """A copied versioned tree that is also a git repository with one commit.

    `read_source` reaches the index and a revision through `git show`, so the
    only way to exercise those two branches is against a real repository.
    """
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    for argv in (['git', 'init', '-q'],
                 ['git', 'config', 'user.email', 'tests@example.invalid'],
                 ['git', 'config', 'user.name', 'Tests'],
                 ['git', 'add', '-A'],
                 ['git', 'commit', '-qm', 'versioned tree']):
        subprocess.run(
            argv, cwd=str(copy_root), env=_util.child_coverage('scrub'),
            capture_output=True,
            text=True, timeout=60, check=True)
    return copy_root, checker


def _break_one_site(copy_root, replacement='0.0.0-drift'):
    """Rewrite the package version in the COPY, never the real file."""
    init_copy = copy_root / 'daedalus_cli' / '__init__.py'
    text = init_copy.read_text(encoding='utf-8')
    new_text, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                          f'__version__ = "{replacement}"', text)
    assert n == 1, text
    init_copy.write_text(new_text, encoding='utf-8')


def _duplicate_the_package_version(copy_root, second_value='0.22.0.2'):
    """Add a second, later `__version__` assignment in the COPY's CLI package.

    A plain, valid assignment in a shape the file does not otherwise use —
    the exact reproduction from #228: nothing about it looks malformed, so a
    checker that reports the first regex match cannot tell the file now binds
    a different value at runtime.
    """
    init_copy = copy_root / 'daedalus_cli' / '__init__.py'
    text = init_copy.read_text(encoding='utf-8')
    init_copy.write_text(text + f"\n__version__ = '{second_value}'\n", encoding='utf-8')


def _duplicate_the_page_js_version(copy_root, second_value='9.9.9', quote='"'):
    """Add a second `script: { version: ... }` assignment in the COPY's
    `extension/page.js`, with the value spelled in a given quote style.

    This is the reproduction from #247: the site's own value uses a single
    quote, and a plain object literal spelled with a different quote is just
    as valid JavaScript, so a pattern that only recognizes one quote
    character cannot see the second one at all. `quote` also accepts a
    backtick, the template-literal case review found on top of #247.
    """
    page_copy = copy_root / 'extension' / 'page.js'
    text = page_copy.read_text(encoding='utf-8')
    page_copy.write_text(
        text + '\nconst _dup = { info: { script: { version: '
        f'{quote}{second_value}{quote} }} }} }};\n',
        encoding='utf-8')


def _run_checker(copy_root, *args):
    """Run the copied checker from inside the copy.

    `cwd` is what decides whether the run is measured at all: coverage
    resolves its relative `source` against the process's own directory, so a
    copy driven from the repository root is outside the measured tree and its
    lines are silently absent from the report. The checker itself is
    indifferent — it derives its own root from `__file__`.
    """
    return subprocess.run(
        [sys.executable, str(copy_root / 'scripts' / 'check_versions.py'),
         *args],
        cwd=str(copy_root),
        env=_util.child_coverage('keep', cwd=copy_root),
        capture_output=True, text=True, timeout=60)


def test_check_versions_set_rewrites_every_site(tmp):
    """--set is the whole point of the script: one pass, every site."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    r = _run_checker(copy_root, '--set', '9.9.9')
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    for path, desc, pattern in checker.SITES:
        text = (copy_root / path).read_text(encoding='utf-8')
        m = re.search(pattern, text)
        assert m, (path, desc)
        assert m.group('v') == '9.9.9', (path, desc, m.group('v'))


def test_check_versions_set_writes_one_string_to_every_site(tmp):
    """--set translates nothing: the manifest gets the same string as every
    other site, whatever shape the version has."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    r = _run_checker(copy_root, '--set', '9.9.9.1')
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    for path, desc, pattern in checker.SITES:
        text = (copy_root / path).read_text(encoding='utf-8')
        m = re.search(pattern, text)
        assert m, (path, desc)
        assert m.group('v') == '9.9.9.1', (path, desc, m.group('v'))


def test_a_manifest_spelling_the_version_differently_is_reported(tmp):
    """A manifest holding a different string from the canonical version is
    drift, whatever the shape of the difference.

    The manifest used to be compared against a translation of the canonical
    version rather than against the version itself, so exactly one shape of
    disagreement was absorbed instead of reported — and it was the shape the
    tree carried between every pair of releases.
    """
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    written = _run_checker(copy_root, '--set', '9.9.9.1')
    assert written.returncode == 0, (written.stdout, written.stderr)
    manifest = copy_root / 'extension' / 'manifest.json'
    text = manifest.read_text(encoding='utf-8')
    new_text, n = re.subn(r'"version"\s*:\s*"[^"]+"',
                          '"version": "9.9.9.2"', text)
    assert n == 1, text
    manifest.write_text(new_text, encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert 'extension/manifest.json' in r.stderr, r.stderr


def test_check_versions_set_refuses_a_version_no_site_can_carry(tmp):
    """Chrome accepts only dot-separated integers in a manifest version, so a
    letter-suffixed version cannot be spelled the same way at every site.
    --set is the documented way the tree acquires a version, so it is where
    that shape has to be refused — before anything is written."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    before = {p: (copy_root / p).read_text(encoding='utf-8')
              for p, _, _ in checker.SITES}
    r = _run_checker(copy_root, '--set', '9.9.9a')
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert '9.9.9a' in r.stderr, r.stderr
    for path, text in before.items():
        assert (copy_root / path).read_text(encoding='utf-8') == text, path


def test_check_versions_set_refuses_one_value_spelled_more_than_one_way(tmp):
    """A version has to be the single spelling Chrome reads back, so a
    non-ASCII digit and a leading zero are refused like any other shape no
    site can carry. Both name an integer without being the string an integer
    is written as."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    before = {p: (copy_root / p).read_text(encoding='utf-8')
              for p, _, _ in checker.SITES}
    for version in ('01.2.3', '1.02.3', '\u0661.\u0662.\u0663'):
        r = _run_checker(copy_root, '--set', version)
        assert r.returncode != 0, (version, r.returncode, r.stdout, r.stderr)
        # The constraint is named, and the version itself only where it
        # survives the round trip: a Windows runner's stderr encoding cannot
        # hold a non-ASCII digit, so the child backslash-escapes it and the
        # literal string is absent while the refusal it reports is identical.
        assert 'ASCII digits' in r.stderr, (version, r.stderr)
        # Normalize both sides through the same escaping before comparing, so
        # one assertion covers a UTF-8 stderr (the digits pass through
        # unchanged) and the affected Windows stderr (they arrive already
        # escaped) alike, instead of skipping the check for the non-ASCII
        # case entirely.
        escaped_version = version.encode('ascii', 'backslashreplace').decode('ascii')
        escaped_stderr = r.stderr.encode('ascii', 'backslashreplace').decode('ascii')
        assert escaped_version in escaped_stderr, (version, r.stderr)
        for path, text in before.items():
            assert (copy_root / path).read_text(encoding='utf-8') == text, (
                version, path)


def test_check_versions_set_refuses_a_segment_chrome_cannot_hold(tmp):
    """A manifest version segment stops at 65535, so a version above it is
    refused for the same reason a letter is: no site can carry it."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    before = {p: (copy_root / p).read_text(encoding='utf-8')
              for p, _, _ in checker.SITES}
    r = _run_checker(copy_root, '--set', '9.70000.9')
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert '65535' in r.stderr, r.stderr
    for path, text in before.items():
        assert (copy_root / path).read_text(encoding='utf-8') == text, path


def test_a_version_no_site_can_carry_is_reported(tmp):
    """A tree that acquired such a version some other way is reported rather
    than handed to the gate, which would otherwise pass a tree whose
    extension Chrome refuses to load."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    for path, desc, pattern in checker.SITES:
        target = copy_root / path
        text = target.read_text(encoding='utf-8')
        m = re.search(pattern, text)
        assert m, (path, desc)
        start, end = m.span('v')
        target.write_text(text[:start] + '9.9.9a' + text[end:],
                          encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert '9.9.9a' in r.stderr, r.stderr


def test_check_versions_set_refuses_a_source_it_cannot_rewrite(tmp):
    """--set writes the working tree, so naming another source is a mistake
    the script must refuse rather than half-honour. An empty string still
    names one: the refusal has to test whether --rev was given, not whether
    its value is true, so `--rev ''` is refused before anything is written."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    before = {p: (copy_root / p).read_bytes() for p, _, _ in checker.SITES}
    for source in (['--staged'], ['--rev', 'HEAD'], ['--rev', ''],
                   ['--rev=']):
        r = _run_checker(copy_root, '--set', '9.9.9', *source)
        assert r.returncode != 0, (source, r.returncode, r.stdout, r.stderr)
        assert 'drop --staged/--rev' in r.stderr, (source, r.stderr)
        for path, body in before.items():
            assert (copy_root / path).read_bytes() == body, (source, path)


def test_check_versions_reads_the_index_and_a_revision(tmp):
    """--staged and --rev read through git show, not the working tree, so a
    dirty file must not change what either one reports."""
    copy_root, _checker = _versioned_git_tree(tmp)
    _break_one_site(copy_root)
    committed = _run_checker(copy_root, '--rev', 'HEAD')
    assert committed.returncode == 0, (committed.stdout, committed.stderr)
    staged = _run_checker(copy_root, '--staged')
    assert staged.returncode == 0, (staged.stdout, staged.stderr)
    # The working tree is the one that disagrees.
    worktree = _run_checker(copy_root)
    assert worktree.returncode == 1, (worktree.stdout, worktree.stderr)


def test_check_versions_reports_a_revision_it_cannot_read(tmp):
    """A revision that does not exist is named, not reported as drift."""
    copy_root, _checker = _versioned_git_tree(tmp)
    r = _run_checker(copy_root, '--rev', 'no-such-revision')
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'cannot read' in r.stderr, r.stderr


def test_check_versions_reports_a_site_that_changed_shape(tmp):
    """A site whose line no longer matches is a stale SITES entry, and saying
    so is the difference between fixing the script and hunting a version."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[0]
    target = copy_root / path
    target.write_text('{"nothing": "here"}\n', encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'no version found' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)


def test_check_versions_set_reports_a_site_it_cannot_rewrite(tmp):
    """--set refuses a site whose line no longer matches rather than writing
    some files and abandoning the rest at the old version."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[0]
    (copy_root / path).write_text('{"nothing": "here"}\n', encoding='utf-8')
    r = _run_checker(copy_root, '--set', '9.9.9')
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'no version found' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)


def test_check_versions_refuses_a_second_version_assignment(tmp):
    """A second `__version__` line -- even spelled with the other quote
    character, which the site's old pattern could not see at all -- must not
    let the checker report the tree consistent while Python binds a
    different value at import time (#228)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[-1]
    assert path == 'daedalus_cli/__init__.py', path
    _duplicate_the_package_version(copy_root)
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)
    assert '0.22.0.2' in r.stderr, r.stderr


def test_check_versions_print_refuses_a_second_version_assignment(tmp):
    """--print must not hand out the first of two competing values."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    _duplicate_the_package_version(copy_root)
    r = _run_checker(copy_root, '--print')
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == '', r.stdout
    assert 'matches 2 times' in r.stderr, r.stderr


def test_check_versions_set_refuses_a_second_version_assignment(tmp):
    """--set must not rewrite only the first of two competing assignments
    and leave the second one stale, which would silently reintroduce the
    exact disagreement this checker exists to catch."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    _duplicate_the_package_version(copy_root)
    before = (copy_root / 'daedalus_cli' / '__init__.py').read_text(encoding='utf-8')
    r = _run_checker(copy_root, '--set', '9.9.9')
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    after = (copy_root / 'daedalus_cli' / '__init__.py').read_text(encoding='utf-8')
    assert after == before, 'refused sites must not be partially rewritten'


def test_check_versions_refuses_a_page_js_version_spelled_with_the_other_quote(tmp):
    """A second `script: { version: ... }` in page.js, spelled with a double
    quote where the file's own site uses a single one, still has to be
    caught. Before the fix the site's pattern only recognized the single
    quote, so this duplicate was invisible and the tree came back consistent
    (#247)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[3]
    assert path == 'extension/page.js', path
    _duplicate_the_page_js_version(copy_root)
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)
    assert '9.9.9' in r.stderr, r.stderr


def test_check_versions_refuses_a_page_js_version_in_a_template_literal(tmp):
    """The same duplicate, spelled with a backtick. Review on #247 pointed
    out a template literal is a third valid way to write this string, and
    the widened pattern needs to catch it the same as the other two."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[3]
    assert path == 'extension/page.js', path
    _duplicate_the_page_js_version(copy_root, quote='`')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)


def test_check_versions_refuses_a_page_js_template_literal_spanning_lines(tmp):
    """A backtick value is the one of the three delimiters that may legally
    span multiple lines, and review on #247 pointed out that excluding
    newlines from the value class (to keep the other two delimiters off
    one line each) also hid this exact duplicate."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[3]
    assert path == 'extension/page.js', path
    page_copy = copy_root / 'extension' / 'page.js'
    text = page_copy.read_text(encoding='utf-8')
    page_copy.write_text(
        text + '\nconst _dup = { info: { script: { version: `\n9.9.9` } } };\n',
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)


def test_check_versions_page_js_value_may_contain_a_different_quote_character(tmp):
    """The value class only has to exclude whichever delimiter is actually
    in play, not every delimiter this site recognizes. Review on #247
    found that excluding all three regressed the single-quote case: `main`
    lets a single-quoted value contain a double quote (and vice versa),
    same as the package version site already does, and the widened
    pattern must not narrow that."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, _pattern = checker.SITES[3]
    assert path == 'extension/page.js', path
    page_copy = copy_root / 'extension' / 'page.js'
    text = page_copy.read_text(encoding='utf-8')
    page_copy.write_text(
        text + '\nconst _dup = { info: { script: { version: \'9.9.9"\' } } };\n',
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert desc in r.stderr, (desc, r.stderr)


def test_check_versions_page_js_keys_are_assumed_bare(tmp):
    """A deliberate limit, not an oversight: the page.js pattern matches the
    keys `script` and `version` bare, so a duplicate that also quotes those
    keys is a shape none of page.js's own sites use, and passes through
    unseen. Documented here so a future change to the pattern's key
    handling has a test to update rather than a silent behavior change."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    page_copy = copy_root / 'extension' / 'page.js'
    text = page_copy.read_text(encoding='utf-8')
    page_copy.write_text(
        text + '\nconst _dup = { info: { "script": { "version": "9.9.9" } } };\n',
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


def test_check_versions_print_refuses_a_tree_that_disagrees(tmp):
    """--print feeds another program. Handing it a version from a tree whose
    sites disagree is worse than failing, so stdout stays empty."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    _break_one_site(copy_root)
    r = _run_checker(copy_root, '--print')
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == '', r.stdout
    assert 'FAIL' in r.stderr, r.stderr


def test_check_versions_prints_the_canonical_version_alone(tmp):
    """`--print` is what a script reads, so stdout carries nothing else."""
    del tmp
    r = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'check_versions.py'), '--print'],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py',
                         'check_versions_print')
    expected = next(v for p, d, v in checker.collect()
                    if (p, d) == checker.CANONICAL)
    assert r.stdout.strip() == expected, (r.stdout, expected)
    # The consistency report still runs; it just does not land on stdout.
    assert 'consistent across' in r.stderr, r.stderr


def test_version_workflow_comment_counts_checker_sites(tmp):
    """The workflow's concrete site count stays tied to ``SITES``."""
    del tmp
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py',
                         'check_versions_workflow_count')
    workflow = (ROOT / '.github' / 'workflows' / 'version.yml').read_text(
        encoding='utf-8')
    count = _version_workflow_site_count(workflow)
    expected = len(checker.SITES)
    assert count == expected, (
        f'version workflow says {count} sites, but '
        f'scripts/check_versions.py lists {expected}')


def test_the_version_gate_refuses_an_already_published_version(tmp):
    """Two artifacts must not both call themselves the same released version.

    The version is bumped only when a release is cut, so every commit between
    releases built a wheel naming itself the released version while shipping
    different content — two files called `daedalus-cli 0.19.0` with different
    digests. The gate is one question, asked of the released tag rather than
    of a diff: is this version already claimed?
    """
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'version.yml').read_text(
        encoding='utf-8')
    assert 'scripts/check_versions.py --print' in workflow, workflow
    assert 'releases/tags/v$version' in workflow, workflow
    # `gh api` prints its JSON error body to stdout on a 404 and exits
    # nonzero, so the branch has to be on the exit code — a test of the text
    # would read a "not found" payload as a release that exists.
    assert 'if gh api' in workflow, workflow
    assert 'exit 1' in workflow, workflow
    # The version reaches the script by environment, not by interpolation.
    _, _, body = workflow.partition('run: |')
    assert '${{' not in body, 'an expression is interpolated into the script'


def test_the_tree_does_not_claim_a_version_it_already_released(tmp):
    """The local half of that gate: a released version is spent.

    This asks git rather than the network, so it holds in a checkout with no
    remote: a tag `v<version>` that exists and is not this commit means the
    tree is claiming a version whose content is already published.
    """
    del tmp
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_tag')
    version = next(v for p, d, v in checker.collect() if (p, d) == checker.CANONICAL)
    tag = subprocess.run(
        ['git', '-C', str(ROOT), 'rev-list', '-n', '1', f'v{version}'],
        capture_output=True, text=True, check=False, timeout=30)
    if tag.returncode != 0:
        return  # no such tag: the version is unspent, which is the point
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True, timeout=30)
    assert tag.stdout.strip() == head.stdout.strip(), (
        f'the tree claims version {version}, which tag v{version} already '
        f'published at {tag.stdout.strip()[:12]}; bump every site with '
        f'scripts/check_versions.py --set <released>.1')


def test_manifest_version_matches_package(tmp):
    # check_versions.py covers this, but the manifest is the site Chrome
    # reads, so pin directly that it holds the canonical string verbatim.
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_mv')
    found = checker.collect()
    versions = {(p, d): v for p, d, v in found}
    canonical = versions[checker.CANONICAL]
    assert versions[checker.MANIFEST] == canonical
    pkg = json.loads((ROOT / 'extension' / 'manifest.json').read_text(encoding='utf-8'))
    assert pkg['version'] == canonical


def test_console_scripts_resolve(tmp):
    data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    scripts = data['project']['scripts']
    assert scripts, 'pyproject declares no console scripts'
    sys.path.insert(0, str(ROOT))
    try:
        for name, spec in scripts.items():
            mod_name, _, attr = spec.partition(':')
            assert attr, f'{name}: {spec!r} is not a module:attr spec'
            mod = importlib.import_module(mod_name)
            target = getattr(mod, attr, None)
            assert callable(target), f'{name}: {spec} does not resolve to a callable'
    finally:
        sys.path.remove(str(ROOT))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='versioncontract_')


if __name__ == '__main__':
    raise SystemExit(main())
