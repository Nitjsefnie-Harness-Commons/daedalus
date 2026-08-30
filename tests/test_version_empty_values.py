#!/usr/bin/env python3
"""A version whose value is the empty string still binds, and still competes.

Every site in scripts/check_versions.py quantified its value class
one-or-more, so a zero-length value produced no second match at all: a
duplicate carrying '' came back consistent, --print handed out the first
value, and --set rewrote past it (#324). These tests drive the checker
against trees that spell a version as nothing, and pin the two gates that
refuse them.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _duplicate_the_package_version,
    _duplicate_the_page_js_version,
    _run_checker,
)


def _duplicate_the_status_line_version(copy_root, second_value='9.9.9'):
    """Add a second dashboard status-line version span in the COPY.

    `second_value=''` is the reproduction from #324: a span whose value is
    the empty string, which the site's one-or-more value class turned into
    no second match at all.
    """
    dashboard = copy_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    dashboard.write_text(
        text + f'\n<span class="sl-v">{second_value}</span>\n',
        encoding='utf-8')


def _assert_duplicate_refused(result, desc):
    """The exact-once refusal names the site and both competing values."""
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert desc in result.stderr, (desc, result.stderr)
    assert "''" in result.stderr, result.stderr
    assert 'ok:' not in result.stdout, result.stdout


def _assert_spelling_refused(result):
    """The spelling gate names '' as a version no site can carry."""
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert "''" in result.stderr, result.stderr
    assert 'dot-separated integers' in result.stderr, result.stderr
    assert 'ok:' not in result.stdout, result.stdout


def test_check_versions_refuses_an_empty_page_js_duplicate(tmp):
    """A second `script: { version: ... }` in page.js whose value is the
    empty string is still a second assignment. With the value class
    quantified one-or-more it produced no second match at all, so the
    checker reported the tree consistent (#324)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _path, desc, _pattern = checker.SITES[3]
    assert _path == 'extension/page.js', _path
    _duplicate_the_page_js_version(copy_root, second_value='')
    _assert_duplicate_refused(_run_checker(copy_root), desc)


def test_check_versions_refuses_an_empty_package_version_duplicate(tmp):
    """A second `__version__ = ''` competes for the binding exactly as a
    non-empty duplicate does, so it is refused the same way (#324)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _path, desc, _pattern = checker.SITES[-1]
    assert _path == 'daedalus_cli/__init__.py', _path
    _duplicate_the_package_version(copy_root, second_value='')
    _assert_duplicate_refused(_run_checker(copy_root), desc)


def test_check_versions_refuses_an_empty_status_line_duplicate(tmp):
    """A second `<span class="sl-v"></span>` is a second version claim, not
    no claim at all (#324)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _path, desc, _pattern = checker.SITES[5]
    assert _path == 'dashboard/index.html', _path
    assert desc == 'dashboard status line', desc
    _duplicate_the_status_line_version(copy_root, second_value='')
    _assert_duplicate_refused(_run_checker(copy_root), desc)


def test_check_versions_print_refuses_an_empty_value_duplicate(tmp):
    """--print must not hand out a value while a second, empty assignment
    competes with the first, so stdout stays empty (#324)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _path, _desc, _pattern = checker.SITES[3]
    assert _path == 'extension/page.js', _path
    _duplicate_the_page_js_version(copy_root, second_value='')
    result = _run_checker(copy_root, '--print')
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert result.stdout.strip() == '', result.stdout
    assert 'matches 2 times' in result.stderr, result.stderr


def test_check_versions_set_refuses_an_empty_value_duplicate(tmp):
    """--set must refuse a tree an empty duplicate has made ambiguous, and
    must not rewrite the file holding it (#324)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _path, _desc, _pattern = checker.SITES[3]
    assert _path == 'extension/page.js', _path
    page = copy_root / 'extension' / 'page.js'
    _duplicate_the_page_js_version(copy_root, second_value='')
    before = page.read_text(encoding='utf-8')
    result = _run_checker(copy_root, '--set', '9.9.9')
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert page.read_text(encoding='utf-8') == before, (
        'refused sites must not be partially rewritten')


def test_check_versions_refuses_an_empty_canonical_version(tmp):
    """`const VERSION = ''` on its own is a version no site can carry, so it
    is refused by the spelling gate rather than reported consistent (#324).

    The wording is pinned because it names the gate: only the spelling check
    says "dot-separated integers", and it runs before the comparison, so an
    empty canonical can never be reported as agreement."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    entry = next(e for e in checker.SITES
                 if (e[0], e[1]) == checker.CANONICAL)
    _path, desc, pattern = entry
    target = copy_root / _path
    text = target.read_text(encoding='utf-8')
    match = re.search(pattern, text)
    assert match, (_path, desc)
    start, end = match.span('v')
    target.write_text(text[:start] + text[end:], encoding='utf-8')
    _assert_spelling_refused(_run_checker(copy_root))


def test_check_versions_refuses_a_tree_of_empty_versions(tmp):
    """Every site carrying '' agrees with itself, so the spelling gate is the
    only thing standing between that tree and an `ok:` (#324): the empty
    string is still a version no site can carry."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    for path, desc, pattern in checker.SITES:
        target = copy_root / path
        text = target.read_text(encoding='utf-8')
        match = re.search(pattern, text)
        assert match, (path, desc)
        start, end = match.span('v')
        target.write_text(text[:start] + text[end:], encoding='utf-8')
    _assert_spelling_refused(_run_checker(copy_root))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='versionempty_')


if __name__ == '__main__':
    raise SystemExit(main())
