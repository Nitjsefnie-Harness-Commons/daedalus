"""A shared, pre-staged index, so a scratch need not re-add the tree.

`git ls-files` answers from the index alone, so a scratch reaches the same
enumeration with the index copied in and no loose objects present. Staging
the whole tree per scratch is what timed out on a loaded runner; the one
template add that replaces it runs unbounded, so it either finishes or Git
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
    """Run one git command in `root`; unbounded, so it cannot time out."""
    subprocess.run(['git', '-C', str(root), *command],
                   capture_output=True, check=True)


def _build_template():
    """Stage the whole tree once and return the index it wrote."""
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace) / 'template'
        root.mkdir()
        copy_test_tree(root)
        (root / 'run_tests.py').write_bytes(
            (ROOT / 'run_tests.py').read_bytes())
        _git(root, 'init', '-q')
        _git(root, 'add', '--', 'tests', 'run_tests.py')
        return (root / '.git' / 'index').read_bytes()


def _template():
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = _build_template()
    return _TEMPLATE


def install(root):
    """Make `root` a repo whose index enumerates the whole test tree."""
    _git(root, 'init', '-q')
    (root / '.git' / 'index').write_bytes(_template())
