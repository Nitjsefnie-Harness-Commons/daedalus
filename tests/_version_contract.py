"""The version checker's copied-tree fixtures, shared across its suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/test_version_contract.py so the suites that drive the
checker read one copy of these instead of executing that whole suite to
reach them. Every helper here writes only into the copy it is handed, never
a repository path.
"""
import re
import shutil
import subprocess
from pathlib import Path

import _util
from _repo import ROOT


def _copy_versioned_tree(dest):
    """Copy just the files check_versions.py reads, preserving layout."""
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py',
                         'check_versions_ro')
    rel_paths = {p for p, _, _ in checker.SITES}
    rel_paths.add('scripts/check_versions.py')
    rel_paths.add('scripts/version_regions.py')
    rel_paths.add('scripts/version_html_context.py')
    for rel in rel_paths:
        src = ROOT / rel
        dst = Path(dest) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return checker


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
            capture_output=True, text=True, check=True)
    return copy_root, checker


def _break_one_site(copy_root, replacement='0.0.0-drift', quote='"'):
    """Rewrite the package version in the COPY, never the real file."""
    init_copy = copy_root / 'daedalus_cli' / '__init__.py'
    text = init_copy.read_text(encoding='utf-8')
    new_text, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                          f'__version__ = {quote}{replacement}{quote}', text)
    assert n == 1, text
    init_copy.write_text(new_text, encoding='utf-8')


def _duplicate_the_package_version(copy_root, second_value='0.22.0.2',
                                   quote="'"):
    """Add a second, later `__version__` assignment in the COPY's CLI package.

    A plain, valid assignment in a shape the file does not otherwise use —
    the exact reproduction from #228: nothing about it looks malformed, so a
    checker that reports the first regex match cannot tell the file now binds
    a different value at runtime, and `quote` writes the #319 mirror spelling.
    """
    init_copy = copy_root / 'daedalus_cli' / '__init__.py'
    text = init_copy.read_text(encoding='utf-8')
    assignment = f'\n__version__ = {quote}{second_value}{quote}\n'
    init_copy.write_text(text + assignment, encoding='utf-8')


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
