"""The dashboard version site's markup fixtures, shared across its suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/test_version_contract_dashboard_duplicates.py so
test_version_contract_dashboard_spellings.py reads one copy of these
instead of executing that whole suite to reach them. Every helper here
writes only into the copy it is handed, never a repository path.
"""
import re

from _version_contract import _run_checker


def _insert_before_body(copy_root, markup):
    dashboard = copy_root / 'dashboard' / 'index.html'
    text = dashboard.read_text(encoding='utf-8')
    anchor = '</body>'
    assert text.count(anchor) == 1, text
    dashboard.write_text(text.replace(anchor, markup + '\n' + anchor),
                         encoding='utf-8')


def _dashboard_site(checker, desc):
    entries = [entry for entry in checker.SITES
               if entry[:2] == ('dashboard/index.html', desc)]
    assert len(entries) == 1, (desc, entries)
    return entries[0]


def _canonical_dashboard_value(copy_root, checker, desc):
    path, _site_desc, pattern = _dashboard_site(checker, desc)
    text = (copy_root / path).read_text(encoding='utf-8')
    return re.search(pattern, text).group('v')


def _assert_duplicate_refused(result, desc, canonical, second_value):
    assert result.returncode != 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'matches 2 times' in result.stderr, result.stderr
    assert desc in result.stderr, result.stderr
    assert repr(canonical) in result.stderr, result.stderr
    assert repr(second_value) in result.stderr, result.stderr


def _assert_one_dashboard_match(copy_root, checker, desc):
    """Markup the matcher must ignore leaves the real site counted once."""
    result = _run_checker(copy_root)
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    path, _site_desc, pattern = _dashboard_site(checker, desc)
    dashboard = (copy_root / path).read_text(encoding='utf-8')
    assert len(list(re.finditer(pattern, dashboard))) == 1, desc
