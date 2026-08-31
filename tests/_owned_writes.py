"""The one primitive through which a control copies the test tree.

Not a suite itself — run_tests.py only loads `test_*.py`.

A control that plants a defect in a real module works on a copy, and
the copy is the one write the checker in tests/_control_writes.py
cannot prove from a control's own text: each file's destination comes
from a directory listing, which no syntactic path kind can follow. So
the loop lives here behind a runtime proof — the root must lie outside
the checkout — and the checker proves the root at every call site.
"""
from pathlib import Path

from _repo import ROOT


def copy_test_tree(root):
    """Copy every tests/*.py below `root`, which must lie outside ROOT."""
    root = Path(root).resolve()
    if root == ROOT or root.is_relative_to(ROOT):
        raise ValueError(
            f'copy_test_tree root lies inside the checkout: {root}')
    destination = root / 'tests'
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT / 'tests').glob('*.py')):
        (destination / source.name).write_bytes(source.read_bytes())
