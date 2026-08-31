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
    # tests/_util.py loads the fixture's parent-watch module at import, so a
    # fabricated tree carrying the test tree cannot import without it.
    bridge = root / 'daedalus_bridge'
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / 'parent_watch.py').write_bytes(
        (ROOT / 'daedalus_bridge' / 'parent_watch.py').read_bytes())
