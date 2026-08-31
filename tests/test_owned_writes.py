#!/usr/bin/env python3
"""The copy primitive proves at runtime what no control text can.

tests/_control_writes.py judges spelling, and the refusal this suite
pins has to be spelled to be exercised, so this suite is deliberately
not one the writer checker scans.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402


def test_copy_test_tree_copies_every_test_module_below_the_root(tmp):
    """Every tests/*.py lands under root/tests, byte for byte."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    sources = sorted((ROOT / 'tests').glob('*.py'))
    assert sources, 'the checkout has no test modules'
    for source in sources:
        copied = root / 'tests' / source.name
        assert copied.is_file(), copied
        assert copied.read_bytes() == source.read_bytes(), copied
    assert (len(list((root / 'tests').glob('*.py')))
            == len(sources)), 'the copy holds files the checkout does not'


def test_copy_test_tree_refuses_a_root_inside_the_checkout(tmp):
    """The checkout, a directory below it, and a link to it are refused."""
    link = Path(tmp) / 'checkout-link'
    link.symlink_to(ROOT, target_is_directory=True)
    for root in (ROOT, ROOT / '.probe', link):
        try:
            copy_test_tree(root)
        except ValueError as error:
            assert 'inside the checkout' in str(error), error
        else:
            raise AssertionError(f'copy_test_tree accepted {root}')
    assert not (ROOT / '.probe').exists(), 'the refusal came after a write'


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
