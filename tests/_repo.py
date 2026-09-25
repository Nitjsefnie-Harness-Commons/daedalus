"""Shared fixtures for the repository-contract suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

These suites read the tree rather than run the bridge, so what they share is
where the tree is and how to walk the part of it that ships.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
EXTENSION_ROOT = ROOT / 'extension'


def iter_tree_files(root):
    """Yield every path Git tracks for the release rooted at `root`.

    The root is a parameter rather than this module's own, because the test
    that proves the scanners still catch a violation points them at a
    throwaway repository — and a helper that read one fixed global would
    quietly scan this one instead and pass.
    """
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z'], capture_output=True,
        check=True, timeout=30)
    paths = [path for path in listed.stdout.split(b'\0') if path]
    assert paths, 'Git returned no tracked release paths'
    for path in paths:
        yield root / os.fsdecode(path)


def git_index(root, *args):
    """Run a git command against `root`'s index, raising on failure.

    A fixture the CI planner reads has to be a git checkout, because the
    planner enumerates the TRACKED tree through `git ls-files`; `init`
    plus `add` populates the index, which is what `ls-files` reads, so
    no commit is made or needed.
    """
    subprocess.run(['git', '-C', str(root), *args], check=True,
                   capture_output=True, timeout=30)
