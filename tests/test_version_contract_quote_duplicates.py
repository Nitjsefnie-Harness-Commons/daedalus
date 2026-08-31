#!/usr/bin/env python3
"""Duplicate version spellings the checker must refuse (#319, #320).

A second assignment whose value carries the other quote character, or runs
across a continuation, is still what Python binds at import time, so the
three mirror tests refuse every spelling `_DUPLICATE_SPELLINGS` holds, the
page.js duplicate-spelling coverage includes optional whitespace before
colons, and a separate test pins that the widened value class still admits a
foreign quote inside a value that is not a duplicate at all.
test_version_contract.py holds the checker's other contracts; these tests
were moved here when that file met its 700-line ceiling.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_version_contract import (  # noqa: E402
    _break_one_site,
    _copy_versioned_tree,
    _duplicate_the_package_version,
    _run_checker,
)


# Duplicate `__version__` spellings the three mirror tests must all refuse:
# a value carrying the other quote character, or a continuation spelling.
_DUPLICATE_SPELLINGS = (('9.9.9"', "'"), ("9.9.9'", '"'), ('9.9.\\\n9', "'"))


def test_check_versions_refuses_the_other_quote_package_duplicate(tmp):
    """A duplicate `__version__` whose value carries a quote other than
    its own delimiter, or across a continuation, is refused (#319): the
    canonical needle is derived, and a CRLF continuation matches the class."""
    for second_value, quote in _DUPLICATE_SPELLINGS:
        copy_root = Path(tmp) / 'tree'
        checker = _copy_versioned_tree(copy_root)
        path, desc, pattern = checker.SITES[-1]
        assert path == 'daedalus_cli/__init__.py', path
        pristine = (copy_root / path).read_text(encoding='utf-8')
        canonical = re.search(pattern, pristine).group('v')
        _duplicate_the_package_version(copy_root, second_value, quote)
        r = _run_checker(copy_root)
        assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
        assert 'matches 2 times' in r.stderr, r.stderr
        assert desc in r.stderr, r.stderr
        assert canonical in r.stderr, r.stderr
        assert repr(second_value) in r.stderr, r.stderr
        # Matched by the class itself, not by a read normalizing CRLF away.
        assert re.fullmatch(pattern, "__version__ = '9.9.\\\r\n9'")


def test_check_versions_print_refuses_the_other_quote_package_duplicate(tmp):
    """--print must not hand out the first of two competing values (#319)."""
    for second_value, quote in _DUPLICATE_SPELLINGS:
        copy_root = Path(tmp) / 'tree'
        _copy_versioned_tree(copy_root)
        _duplicate_the_package_version(copy_root, second_value, quote)
        r = _run_checker(copy_root, '--print')
        assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.strip() == '', r.stdout
        assert 'matches 2 times' in r.stderr, r.stderr


def test_check_versions_set_refuses_the_other_quote_package_duplicate(tmp):
    """--set must not rewrite every site to an invisible duplicate (#319)."""
    for second_value, quote in _DUPLICATE_SPELLINGS:
        copy_root = Path(tmp) / 'tree'
        _copy_versioned_tree(copy_root)
        _duplicate_the_package_version(copy_root, second_value, quote)
        init_copy = copy_root / 'daedalus_cli' / '__init__.py'
        before = init_copy.read_text(encoding='utf-8')
        r = _run_checker(copy_root, '--set', '9.9.9')
        assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
        assert 'matches 2 times' in r.stderr, r.stderr
        after = init_copy.read_text(encoding='utf-8')
        assert after == before, 'refused sites must not be partially rewritten'


def test_check_versions_package_value_may_contain_a_different_quote(tmp):
    """The value class only has to refuse whichever delimiter `q` captured, so
    a single-quoted value may still carry a double quote the way page.js's
    already does, and the widened pattern must still see it (#319)."""
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    _break_one_site(copy_root, replacement='9.9.9"', quote="'")
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'version strings disagree' in r.stderr, r.stderr
    assert '9.9.9"' in r.stderr, r.stderr


def test_check_versions_refuses_a_page_js_version_with_spaced_colons(tmp):
    """A duplicate with whitespace before both colons is valid JavaScript and
    must be counted as a competing page.js binding (#320)."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    path, desc, pattern = checker.SITES[3]
    assert path == 'extension/page.js', path
    page_copy = copy_root / 'extension' / 'page.js'
    text = page_copy.read_text(encoding='utf-8')
    canonical = re.search(pattern, text).group('v')
    page_copy.write_text(
        text + '\nconst _dup = { info: { script : { version : '
        '\'9.9.9\' } } };\n',
        encoding='utf-8')
    r = _run_checker(copy_root)
    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert 'matches 2 times' in r.stderr, r.stderr
    assert 'extension/page.js' in r.stderr, r.stderr
    assert desc in r.stderr, r.stderr
    assert canonical in r.stderr, r.stderr
    assert '9.9.9' in r.stderr, r.stderr


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_quote_duplicates_')


if __name__ == '__main__':
    raise SystemExit(main())
