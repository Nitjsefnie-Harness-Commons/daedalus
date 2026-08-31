"""Copy the test tree; the root must lie outside the checkout."""
from pathlib import Path

from _repo import ROOT


def copy_test_tree(root):
    root = Path(root).resolve()
    if root == ROOT or root.is_relative_to(ROOT):
        raise ValueError(
            f'copy_test_tree root lies inside the checkout: {root}')
    destination = root / 'tests'
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT / 'tests').glob('*.py')):
        (destination / source.name).write_bytes(source.read_bytes())
