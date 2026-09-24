"""A shared, pre-staged index, so a scratch need not re-add the tree.

`git ls-files` answers from the index alone, so a copy reaches the same
enumeration with no loose objects present. Every git launch here — the
one template add that replaces the per-scratch staging, and the
per-scratch `git init` — runs unbounded, so each either finishes or Git
reports its own failure, never a wall-clock verdict.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402

_TEMPLATE = None


def _git(root, *command):
    subprocess.run(['git', '-C', str(root), *command],
                   capture_output=True, check=True)


def _build_template():
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace) / 'template'
        root.mkdir()
        copy_test_tree(root)
        (root / 'run_tests.py').write_bytes(
            (ROOT / 'run_tests.py').read_bytes())
        _git(root, 'init', '-q')
        # A split index links to a `.git/sharedindex.<sha>` the copy does
        # not carry, so a scratch would fail `ls-files`; stage one index.
        _git(root, '-c', 'core.splitIndex=false', 'add', '--', 'tests',
             'run_tests.py')
        return (root / '.git' / 'index').read_bytes()


def _template():
    global _TEMPLATE
    if _TEMPLATE is None:  # process-cached; the suite never changes the paths
        _TEMPLATE = _build_template()
    return _TEMPLATE


def install(root):
    """Make `root` a repo whose index enumerates the whole test tree.

    The enumeration is a frozen snapshot of the checkout taken once per
    process and copied into every scratch, so a file written into a
    scratch afterwards is not listed; the old per-scratch add behaved
    the same way, since it never re-staged. Only a fresh add would list
    it.
    """
    _git(root, 'init', '-q')
    (root / '.git' / 'index').write_bytes(_template())
