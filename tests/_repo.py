"""Shared fixtures for the repository-contract suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

These suites read the tree rather than run the bridge, so what they share is
where the tree is, how to walk the part that ships, and its version.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
EXTENSION_ROOT = ROOT / 'extension'


def declared_version():
    """The VERSION constant extension/background.js declares."""
    source = (EXTENSION_ROOT / 'background.js').read_text(encoding='utf-8')
    match = re.search(r"const VERSION = '([^']+)'", source)
    assert match, 'background.js declares no VERSION'
    return match.group(1)


def iter_tree_files(root):
    """Yield every path Git tracks for the release rooted at `root`.

    The root is a parameter rather than this module's own, because the test
    that proves the scanners still catch a violation points them at a
    throwaway repository — and a helper that read one fixed global would
    quietly scan this one instead and pass.
    """
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z'], capture_output=True,
        check=True)
    paths = [path for path in listed.stdout.split(b'\0') if path]
    assert paths, 'Git returned no tracked release paths'
    for path in paths:
        yield root / os.fsdecode(path)
