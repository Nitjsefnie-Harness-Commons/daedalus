#!/usr/bin/env python3
"""The rule the launch-routing walk enforces over the real tree.

The walk itself, and the tables that close its population, live in
`tests/_node_launch_routing.py` — a shared helper, because the plant-based
controls in `tests/test_node_launch_routing_shapes.py` need the same
decisions and a sibling SUITE import is a seam this repository refuses
(`tests/test_suite_import_boundaries.py`).

What is here is the one control that runs the walk over every tracked
module and asks the question the branch exists to keep true: is a Node
child whose cost is a fixed unit of work launched through the shared hang
detector, and does every launch the walk cannot classify appear in a table
that says why.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _node_launch_routing import _routing_sweep  # noqa: E402


def test_every_fixed_work_node_child_goes_through_the_shared_detector(tmp):
    """The rule, read off the tree rather than off a list of sites.

    One findings list and one assert over all four classes, so a run reports
    every class it found rather than the first. An early assert here is how
    a plant in a later class hides behind one caught by an earlier one.
    """
    del tmp
    unrouted, unbounded, unclassified, unused = _routing_sweep()
    findings = []
    if unrouted:
        findings.append(
            'a Node child whose cost is a fixed unit of work is launched '
            'outside the shared hang detector:\n  '
            + '\n  '.join(unrouted))
    if unbounded:
        findings.append(
            'a classifying module left its child with no bound of its own:\n  '
            + '\n  '.join(unbounded))
    # Fail-closed: an executable the walk cannot resolve is a site it has
    # not discharged, not a site it has decided is not node.
    if unclassified:
        findings.append(
            'a launch whose executable this walk cannot resolve, so it '
            'cannot prove the child is not node. Name it in '
            'UNRESOLVED_LAUNCHES with the reason, or teach the resolver '
            'the shape:\n  ' + '\n  '.join(unclassified))
    # And the table cannot outlive what it excused, or it becomes a set of
    # permissions rather than a record of what could not be classified.
    if unused:
        findings.append(
            'a row that no longer matches any launch, and the table it is '
            'in:\n  '
            + '\n  '.join(f'{table}: {row}' for table, row in unused))
    assert not findings, '\n'.join(findings)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
