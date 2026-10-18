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
from _version_contract import (  # noqa: E402
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


def test_check_versions_refuses_an_empty_header_version_duplicate(tmp):
    # Append the header last so its \s+ cannot consume a following line.
    for index, expected_path, expected_desc, duplicate in (
            (1, 'extension/background.js', '@version header', None),
            (3, 'extension/page.js', None, _duplicate_the_page_js_version),
            (-1, 'daedalus_cli/__init__.py', None,
             _duplicate_the_package_version),
            (5, 'dashboard/index.html', 'dashboard status line',
             _duplicate_the_status_line_version)):
        copy_root = Path(tmp) / 'tree'
        checker = _copy_versioned_tree(copy_root)
        _path, desc, _pattern = checker.SITES[index]
        assert _path == expected_path, _path
        if expected_desc is not None:
            assert desc == expected_desc, desc
        if duplicate is None:
            header = copy_root / 'extension' / 'background.js'
            header.write_text(
                header.read_text(encoding='utf-8') + '// @version \n',
                encoding='utf-8')
        else:
            duplicate(copy_root, second_value='')
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
    must not rewrite the file holding it (#324). Both argparse spellings of
    the flag take the same refusal: the separated token and `--set=<value>`."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    _path, _desc, _pattern = checker.SITES[3]
    assert _path == 'extension/page.js', _path
    page = copy_root / 'extension' / 'page.js'
    _duplicate_the_page_js_version(copy_root, second_value='')
    before = page.read_text(encoding='utf-8')
    for argv in (['--set', '9.9.9'], ['--set=9.9.9']):
        result = _run_checker(copy_root, *argv)
        assert result.returncode != 0, (argv, result.returncode,
                                        result.stdout, result.stderr)
        assert 'matches 2 times' in result.stderr, (argv, result.stderr)
        assert page.read_text(encoding='utf-8') == before, (
            argv, 'refused sites must not be partially rewritten')


def test_check_versions_refuses_a_tree_of_empty_versions(tmp):
    """Agreement alone cannot make an invalid version carryable."""
    for scope, version in (('canonical', ''), ('all', ''), ('all', '9.9.9a')):
        copy_root = Path(tmp) / 'tree'
        checker = _copy_versioned_tree(copy_root)
        entries = [e for e in checker.SITES
                   if scope == 'all' or (e[0], e[1]) == checker.CANONICAL]
        for path, desc, pattern in entries:
            target = copy_root / path
            text = target.read_text(encoding='utf-8')
            match = re.search(pattern, text)
            assert match, (path, desc)
            start, end = match.span('v')
            target.write_text(text[:start] + version + text[end:],
                              encoding='utf-8')
        if version == '':
            _assert_spelling_refused(_run_checker(copy_root))
        else:
            r = _run_checker(copy_root)
            assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
            assert '9.9.9a' in r.stderr, r.stderr


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='versionempty_')


if __name__ == '__main__':
    raise SystemExit(main())
