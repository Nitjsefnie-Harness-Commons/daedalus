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
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_path import (  # noqa: E402
    _module_constants as module_constants)
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


# The eight sites that keep a bound at their own call site, as (module,
# stem). The stem names three constants: the recorded table(s)
# `<stem>*_SAMPLES_S`, the `<stem>_SLOWEST_S` taken from them, and the
# `<stem>_DEADLINE_S`. This is the OTHER direction of the rule above — the
# sites that do NOT reach the shared detector still have to stop a wedged
# child, and the census cannot be what holds them honest.
COMPOSED_BOUND_SITES = (
    ('_realbrowser.py', 'NODE_PROBE'),
    ('_realbrowser.py', 'MINIMAL_SPAWN'),
    ('_gm_harness.py', 'GM_CHILD'),
    ('test_real_browser_classification.py', 'CONTROL_CHILD'),
    ('test_real_browser_environment.py', 'REPO_PROBE'),
    ('test_real_browser_environment.py', 'WORKER_PROBE'),
    ('test_real_browser_harness.py', 'WORKER_CHECK'),
    ('test_real_browser_harness.py', 'CDP_HARNESS'),
)


def _is_composed(constants, stem):
    """Whether a stem's deadline is `round()`ed from a table and the multiple.

    Composed means all three: a recorded table of samples, a `max()` taken
    over it, and a deadline rounded from that by the shared multiple. A
    number written at the call site is none of them.
    """
    tables = [name for name in constants
              if name.startswith(stem) and name.endswith('_SAMPLES_S')]
    if not tables:
        return False
    taken = ast.parse('X = ' + constants[f'{stem}_SLOWEST_S']).body[0].value
    if not (isinstance(taken, ast.Call)
            and ast.unparse(taken.func) == 'max'):
        return False
    # `*TABLE` unparses with its star, so a substring test reads both
    # the one-table `max(TABLE)` and the two-table `max(*A, *B)`.
    sources = [ast.unparse(argument) for argument in taken.args]
    if not any(any(table in source for source in sources)
               for table in tables):
        return False
    return constants[f'{stem}_DEADLINE_S'] == (
        f'round({stem}_SLOWEST_S * SITE_HANG_MULTIPLE)')


def test_a_call_site_bound_is_derived_and_not_written(tmp):
    """The eight composed bounds, refused if any is a retyped number.

    A site that keeps its bound at its own call site is deliberately OUTSIDE
    `tests/_launch_census.py`'s audited path — that is what holds the path at
    57 modules rather than 76 — and the cost of that is this: the census's
    rule that a bound's figure must be COMPOSED rather than written reaches
    only path modules, so a bare `10` retyped at one of these call sites was
    read by nothing in the tree. Every control in the repository stayed
    green against that mutation, which is the observation this answers.

    The negative half is planted because a rule that cannot tell a composed
    figure from a typed one is the false green this branch exists to remove.
    """
    del tmp
    typed = []
    for module_name, stem in COMPOSED_BOUND_SITES:
        source = Path(__file__).resolve().parent / module_name
        tree = ast.parse(source.read_text(encoding='utf-8'))
        constants = {name: ast.unparse(value) for name, value
                     in module_constants(tree).items()}
        if not _is_composed(constants, stem):
            typed.append(f'{module_name}:{stem}')
    assert not typed, (
        'a call-site bound that is a retyped number rather than a figure '
        f'composed from its recorded table: {typed}')
    composed = {'GM_CHILD_SAMPLES_S': '(1.0, 2.0, 3.0)',
                'GM_CHILD_SLOWEST_S': 'max(*GM_CHILD_SAMPLES_S)',
                'GM_CHILD_DEADLINE_S': 'round(GM_CHILD_SLOWEST_S * '
                                       'SITE_HANG_MULTIPLE)'}
    assert _is_composed(composed, 'GM_CHILD')
    for broken, why in (
            ({**composed, 'GM_CHILD_DEADLINE_S': '90'},
             'the deadline is a retyped number'),
            ({**composed, 'GM_CHILD_SLOWEST_S': '3.0'},
             'the slowest sample is not taken from the table'),
            ({key: value for key, value in composed.items()
             if not key.endswith('_SAMPLES_S')},
             'the recorded table is gone')):
        assert not _is_composed(broken, 'GM_CHILD'), why


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
