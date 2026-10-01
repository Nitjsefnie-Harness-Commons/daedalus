#!/usr/bin/env python3
"""The three ways a call-site Node deadline stops being a hang detector.

Eight sites in `tests/` deliberately keep their bound at their own call
site rather than reaching `tests/_noderun.py`'s shared hang detector,
because routing them through it puts every module they call inside that
launcher's audited path. The cost of staying out is a rule nothing else
enforces: a deadline that is a number TYPED at the call site is read by
nothing else in this tree.

Each control below answers one way that goes wrong, and each fabrication is
accepted by the other two — which is the reason this is three controls and
not one:

- the ALGEBRA (deadline retyped rather than `round()`ed) — a bare `10` at
  one of these call sites was read by nothing;
- the VALUE (`SITE_HANG_MULTIPLE` rebound in the module that composes with
  it) — the same algebra, spelled correctly, composing a 208-day bound;
- the TABLE (samples fabricated too small to compose a reachable bound) —
  the algebra rule reads everything DOWNSTREAM of the table, so a table
  nobody measured satisfies it as long as someone did the arithmetic.

The rules they drive live in `tests/_composed_bounds.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _composed_bounds as bounds  # noqa: E402
import _util  # noqa: E402
from _node_launch_routing import SITE_HANG_MULTIPLE  # noqa: E402


def test_a_call_site_bound_is_derived_and_not_written(tmp):
    """The eight composed bounds, refused if any is a retyped number.

    The negative half is planted because a rule that cannot tell a composed
    figure from a typed one is the false green these controls exist to
    remove, and the plants are compared against the shared multiple by
    VALUE so a rebound name cannot satisfy the spelling.
    """
    del tmp
    assert not bounds.retyped_bound_sites(), (
        'a call-site bound that is a retyped number rather than a figure '
        f'composed from its recorded table: {bounds.retyped_bound_sites()}')
    composed = {'GM_CHILD_SAMPLES_S': '(1.0, 2.0, 3.0)',
                'GM_CHILD_SLOWEST_S': 'max(*GM_CHILD_SAMPLES_S)',
                'GM_CHILD_DEADLINE_S': 'round(GM_CHILD_SLOWEST_S * '
                                       'SITE_HANG_MULTIPLE)'}
    assert bounds.is_composed(composed, 'GM_CHILD', SITE_HANG_MULTIPLE)
    for broken, why in (
            ({**composed, 'GM_CHILD_DEADLINE_S': '90'},
             'the deadline is a retyped number'),
            ({**composed, 'GM_CHILD_SLOWEST_S': '3.0'},
             'the slowest sample is not taken from the table'),
            ({key: value for key, value in composed.items()
             if not key.endswith('_SAMPLES_S')},
             'the recorded table is gone')):
        assert not bounds.is_composed(
            broken, 'GM_CHILD', SITE_HANG_MULTIPLE), why


def test_a_module_cannot_rebind_the_shared_hang_multiple(tmp):
    """The multiple is compared by VALUE, in the module that composes with it.

    A deadline spelled `round(X * SITE_HANG_MULTIPLE)` proves the name is
    used, and the name is a module-scope binding a real module can shadow.
    One line appended to the real `tests/_gm_harness.py` — the same table,
    the same algebra, the same spelling, and a `GM_CHILD_DEADLINE_S` of
    18,015,000s — left this suite and `tests/test_repo_layout.py` green,
    because the thin-table control catches a fabricated table and a
    fabricated multiple is not a table.

    The plant is a real module's own bytes with one line appended, read back
    from disk, so what the control reads is what the rule would read. Every
    other composed module is copied unplanted beside it — the population
    is eight SITES over five files, and the assertion is that this ONE site
    is refused and not that the control refuses everything.
    """
    root = bounds.composed_population(Path(tmp) / 'planted', '_gm_harness.py',
                                      'SITE_HANG_MULTIPLE = 10 ** 6')
    plant = (root / '_gm_harness.py').read_text(encoding='utf-8')
    assert plant.rstrip().endswith('SITE_HANG_MULTIPLE = 10 ** 6'), (
        'the plant did not reach the real module')
    # The plant makes `_gm_harness.py:GM_CHILD` the ONLY deviation, so the
    # equality is over the whole population rather than a membership: a
    # retyped deadline planted beside it would show up here too, which is
    # why the message names the population and not just the rebind.
    assert bounds.retyped_bound_sites(root) == ['_gm_harness.py:GM_CHILD'], (
        'the planted population is not exactly the one expected \u2014 the '
        'planted rebind is the cause to check first, but any other site '
        'that stopped composing its bound reads the same way: '
        f'{bounds.retyped_bound_sites(root)}')
    assert bounds.shared_multiple('_gm_harness.py') == SITE_HANG_MULTIPLE, (
        'the healthy reading of the real module is not the shared multiple')
    # `10 ** 6` does not fold, and a value this cannot read is refused
    # rather than believed — which is the same verdict by a second route.
    assert bounds.shared_multiple('_gm_harness.py', root) is None, (
        'the planted multiple resolved where the deadline is composed')
    assert not bounds.is_composed(
        bounds.site_constants('_gm_harness.py', root), 'GM_CHILD', 10 ** 6), (
        'a folded fabricated multiple was certified')


def test_a_composed_deadline_is_not_below_what_a_child_could_cost(tmp):
    """A table small enough to compose a zero is not a measurement.

    The composition rule reads the algebra DOWNSTREAM of the table, so a
    table nobody measured satisfies it as long as someone did the
    arithmetic: a fabricated `(0.01, 0.02, 0.03)` composes a ZERO-second
    bound. What no control can check is whether a table is true, and no site
    here runs often enough for a slow bound to show up as a failure — so
    the one property a fabricated table cannot keep is the cheap one it has
    to cross: `round(max(table) * 5)` cannot be under a second, because that
    needs every recorded sample of a process launch under 200ms.
    """
    del tmp
    floor = 1 / SITE_HANG_MULTIPLE
    thin = []
    for module_name, stem in bounds.COMPOSED_BOUND_SITES:
        constants = bounds.site_constants(module_name)
        figures = [value for value in constants
                   if value.startswith(stem) and value.endswith('_SAMPLES_S')]
        for table in figures:
            slowest = max(bounds.table_values(constants[table]), default=0.0)
            if slowest < floor:
                thin.append(f'{module_name}:{table} slowest={slowest}')
    assert not thin, (
        'a recorded table whose slowest sample is below '
        f'{round(floor, 3)}s, which composes a deadline no child could '
        f'reach: {thin}')


def test_the_composed_site_table_is_what_the_tree_carries(tmp):
    """The eight-site population is measured from the tree, not asserted.

    A table that is right today is right by a hand-written edit nobody has
    to justify: deleting a row drops a site out of the population and every
    other control here stops looking at it, silently. So the population is
    read off the tree instead — every module-level `*_DEADLINE_S` outside
    the shared launcher, and every `*_SAMPLES_S` with a `*_SLOWEST_S` that
    reads it — and the table has to equal what that reading finds.

    Three ways this goes, all of them real: a site drops out of the table
    and stops being checked; a module composes a bound at its own call site
    and nothing looks at it; and a sample table appears that no deadline was
    built out of.
    """
    del tmp
    sites, tables, unconsumed = bounds.derived_population()
    listed = set(bounds.COMPOSED_BOUND_SITES)
    assert sites == listed, (
        'the composed-bound sites the tree carries are not the ones the '
        'table lists, so a site is checked by nothing or a listed site no '
        f'longer exists: {sorted(sites ^ listed)}')
    assert len(tables) >= len(sites), (
        f'{len(tables)} recorded tables for {len(sites)} composed sites, '
        'so at least one deadline is built out of no measurement')
    assert not unconsumed, (
        'a recorded sample table no `max()` in its own module reads, so no '
        f'deadline was composed from it: {unconsumed}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
