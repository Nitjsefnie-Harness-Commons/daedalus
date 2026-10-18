#!/usr/bin/env python3
"""Duplicate version spellings the checker must refuse (#319, #320).

A second assignment whose value carries the other quote character, or runs
across a continuation, is still what Python binds at import time, so the
three mirror tests refuse every spelling `_DUPLICATE_SPELLINGS` holds,
and a separate test pins that the widened value class still admits a
foreign quote inside a value that is not a duplicate at all.
test_version_contract.py holds the checker's other contracts; these tests
were moved here when that file met its 700-line ceiling.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _version_contract import (  # noqa: E402
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


def test_check_versions_ignores_page_js_comment_and_string_decoys(tmp):
    for index, expected_path, decoys in (
            (3, 'extension/page.js', (
                '\n'
                "// script: { version: '9.9.9' }\n"
                '// script: { version: "8.8.8" }\n'
                'const _single = \'script: { version: "7.7.7" }\';\n'
                'const _double = "script: { version: \'6.6.6\' }";\n'
            )),
            (-1, 'daedalus_cli/__init__.py', (
                '\n'
                "# __version__ = '9.9.9'\n"
                '# __version__ = "8.8.8"\n'
                '_single = \'__version__ = "7.7.7"\'\n'
                '_double = "__version__ = \'6.6.6\'"\n'
            ))):
        copy_root = Path(tmp) / 'tree'
        checker = _copy_versioned_tree(copy_root)
        path, desc, pattern = checker.SITES[index]
        assert path == expected_path, path
        target = copy_root / path
        text = target.read_text(encoding='utf-8')
        canonical = re.search(pattern, text).group('v')
        target.write_text(text + decoys, encoding='utf-8')
        r = _run_checker(copy_root)
        expected = (f'ok: version {canonical} consistent across '
                    f'{len(checker.SITES)} sites in '
                    f'{len({p for p, _, _ in checker.SITES})} files\n')
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout == expected, r.stdout
        assert desc not in r.stderr, r.stderr


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_quote_duplicates_')


if __name__ == '__main__':
    raise SystemExit(main())
