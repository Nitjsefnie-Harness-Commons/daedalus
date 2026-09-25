#!/usr/bin/env python3
"""The published timed matrix is a SHAPE, checked against its consumer.

A whole CI run was green — twelve `suites` platforms, every coverage leg,
actionlint, the linters — while the `timed` matrix created ZERO cells, and
the only red was the required `speed` verdict over an empty table. The
planner published a bare JSON ARRAY, and the plan step's read-back asserted
`isinstance(parsed, list)`, confirming the wrong shape. GitHub's
`strategy.matrix` is an OBJECT whose keys are dimensions or the special
`include`/`exclude` lists; a bare array carries none of them and expands to
no instances.

Two reviews missed it because they read the planner's output as DATA and the
workflow's wiring as TEXT, and never asked what `strategy.matrix` accepts.
So this suite runs the planner to the value it actually publishes and feeds
it to a model of the runner's expansion: a cell per planned cell. The
payload is validated against its CONSUMER, not against the language that
parses it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _timed_matrix import matrix_cells, published_matrix  # noqa: E402


def test_the_published_matrix_is_the_shape_strategy_matrix_expands(tmp):
    """The published value expands to one cell per planned cell — or none.

    This is the property at the boundary that failed. The planner's stdout
    is what `strategy.matrix` evaluates, and a bare ARRAY of the same cells
    is JSON the old read-back accepted, yet it expands to zero instances:
    the `timed` job is never created. So this runs the planner, feeds its
    published value to a model of the expansion, and requires a cell per
    planned cell, each carrying a `group` and a `suites`. The model empties
    a bare array and an empty `include` here too, so the control is shown to
    see the defect it exists for rather than passing vacuously.
    """
    published, plan = published_matrix(tmp)
    assert isinstance(published, dict), published
    # Only `include`: the cells are a correlated pair (one `group` and the
    # exact `suites` packed into it), so a dimension key would multiply and
    # cross-pair them.
    assert set(published) == {'include'}, published
    cells = matrix_cells(published)
    assert cells, f'the published matrix expands to no cells: {published!r}'
    assert len(cells) == len(plan.matrix), (len(cells), len(plan.matrix))
    for cell in cells:
        assert set(cell) == {'group', 'suites'}, cell
        assert cell['group'] and isinstance(cell['suites'], str), cell
    # The model empties the ORIGINAL defect and the silent-empty variants,
    # so it is not a rubber stamp that accepts anything.
    assert matrix_cells(plan.matrix) == [], (
        'a bare array of cells must expand to nothing for this control to '
        'bite')
    assert matrix_cells({'include': []}) == []


def test_a_cell_that_cannot_name_a_check_run_is_not_publishable(tmp):
    """A `group` GitHub cannot put in a cell's check-run name is refused.

    `timed` truncates a cell's display name to the `group`, so a name with a
    space, an uppercase letter, or a leading `-` is a cell the runner and the
    verdict job cannot agree on. The plan step's read-back refuses the same
    names; this holds the published value to that rule so a planner that grew
    one is caught here rather than in a check-run title.
    """
    _published, plan = published_matrix(tmp)
    for entry in plan.matrix:
        group = entry['group']
        assert group.isascii(), group
        assert group[0] in 'abcdefghijklmnopqrstuvwxyz0123456789', group
        assert all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-'
                   for c in group), group
    # A planted name the runner would mangle is rejected by the same rule.
    for bad in ('Cell 01', '-cell-01', 'cell_01', 'cell/01'):
        assert not (bad.isascii()
                    and bad[0] in 'abcdefghijklmnopqrstuvwxyz0123456789'
                    and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-'
                            for c in bad)), bad


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedshape_')


if __name__ == '__main__':
    raise SystemExit(main())
