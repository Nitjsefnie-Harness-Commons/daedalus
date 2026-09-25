#!/usr/bin/env python3
"""The timings file's two bounds, and the sentence that explains them.

`target_cell_weight` and `max_cells` are not measurements: they are the
file's two policy numbers, and they are coupled by construction to
`CELL_WEIGHT_MARGIN` in `plan_timed_matrix.py` and to nothing else. The
planner only NOTES a target the margin forbids -- it still prints a full
matrix and exits 0 -- and `read_timings` type-checks the number without
knowing what it means. So the coupling needs an owner, and the owner is
this module: `verify_target` is the chokepoint the refresher calls
before every write, and `basis_sentence` is the text the file carries
so a reader of the file alone knows what the two numbers are and on
what measurement.

THE TARGET IS DERIVED, NOT CHOSEN. `derive_target` returns the smallest
step at which the planner's own balance guarantee holds on the weights
being written AND the derived cell count fits `max_cells`. More cells
is a shorter critical path and the count falls as the target rises, so
the smallest admissible step packs the most cells that fit the bound.
`verify_target` applies it to a target the file already carries: one
the margin admits is kept untouched (a refresh that changes nothing
changes nothing), one the margin forbids is re-derived and the reason
travels with the write, and a distribution on which no target within
the bound balances is a refusal rather than a number nobody can act
on. The step is five in whatever unit the file carries -- seconds
before the reference workload exists, reference-multiples after, where
five is a coarser physical step because one multiple is a whole
reference workload.

Every write carries a basis, seed or refresh, rebuilt from the numbers
of that write rather than preserved from an older one: a sentence that
describes a run, a target and a cell count the file no longer has is
the same defect as a stale number, one level up.
"""
import math
import statistics

try:
    from plan_timed_matrix import (
        CELL_WEIGHT_MARGIN, SCHEMA_VERSION, suite_names)
    from plan_timed_matrix import plan as plan_matrix
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.plan_timed_matrix import (
        CELL_WEIGHT_MARGIN, SCHEMA_VERSION, suite_names)
    from scripts.ci.plan_timed_matrix import plan as plan_matrix

# The step between candidate targets, in the file's own units.
TARGET_STEP = 5


class BoundsError(Exception):
    """A refusal: no target within the bound balances on these weights."""


def plan_is_balanced(tree, data):
    """Whether the planner's own balance guarantee holds for this plan."""
    loads = [cell.weight for cell in plan_matrix(tree, data).cells]
    if not loads:
        return False
    return max(loads) <= statistics.median(loads) * (1 + CELL_WEIGHT_MARGIN)


def _candidate(weights, target, max_cells, units):
    return {'schema_version': SCHEMA_VERSION,
            'target_cell_weight': float(target),
            'max_cells': max_cells, 'units': units,
            'suite_weights': weights, 'measured_from': 'derive', 'runs': 1}


def derive_target(tree, weights, max_cells, units):
    """The smallest target the balance guarantee allows, in TARGET_STEPs.

    At a target the margin forbids, a heavy suite sits alone in its
    cell while the median cell stays small, and the ratio crosses the
    margin; raising the target lets more suites share cells and the
    median rises under the heavy one. `units` is the file's own unit,
    carried into the probe so the candidate never asserts one the
    weights are not in.
    """
    total = sum(weights.values())
    if total <= 0 or max_cells < 1:
        return float(TARGET_STEP)
    for target in range(TARGET_STEP, int(total) + TARGET_STEP, TARGET_STEP):
        if math.ceil(total / target) > max_cells:
            continue
        if plan_is_balanced(
                tree, _candidate(weights, target, max_cells, units)):
            return float(target)
    return float(math.ceil(total / max_cells / TARGET_STEP) * TARGET_STEP)


def verify_target(tree, data, max_cells):
    """`(target, note)`: the recorded target, or the one the margin admits.

    `note` is empty when the recorded target stood. A forbidden target
    is re-derived rather than written through, so no write can put the
    file into a state the shipped margin forbids; a distribution on
    which nothing within the bound balances is a `BoundsError`.
    """
    if plan_is_balanced(tree, data):
        return data['target_cell_weight'], ''
    recorded = data['target_cell_weight']
    target = derive_target(
        tree, data['suite_weights'], max_cells, data['units'])
    if not plan_is_balanced(tree, dict(data,
                                       target_cell_weight=target)):
        raise BoundsError(
            f'no target within max_cells {max_cells} balances these weights '
            f'at the margin {CELL_WEIGHT_MARGIN:g} (the recorded target '
            f'{recorded:g} does not, and the smallest admissible step '
            f'{target:g} does not either); split the heaviest suite or '
            f'raise max_cells by hand')
    loads = [cell.weight for cell in plan_matrix(tree, data).cells]
    note = (f'target re-derived: {recorded:g} leaves the heaviest cell at '
            f'{max(loads) / statistics.median(loads):.3f}x the median, over '
            f'the {CELL_WEIGHT_MARGIN:g} margin; {target:g} is the smallest '
            f'step that holds')
    return target, note


def estimated_count(tree, data):
    """Tree suites the file records nothing about (the planner estimates)."""
    recorded = data['suite_weights']
    return [name for name in suite_names(tree) if name not in recorded]


def _plural(count, word):
    """`1 cell`, `2 cells` -- the sentence is read, not just parsed."""
    return f'{count} {word}' + ('' if count == 1 else 's')


def basis_sentence(tree, data, cells, estimated):
    """The file's `seeded` field: both bounds, their basis, and the rest.

    `cells` is the number of cells the measured run(s) ran -- the
    concurrency today's matrix is measured against -- and `estimated`
    the tree suites the file records nothing about. Rebuilt from the
    numbers of this write on every write, seed or refresh.
    """
    decision = plan_matrix(tree, data)
    loads = [cell.weight for cell in decision.cells]
    weights = data['suite_weights']
    total = sum(weights.values())
    target = data['target_cell_weight']
    heaviest = max(loads) if loads else 0.0
    median_cell = statistics.median(loads) if loads else 0.0
    if data['units'] == 'seconds':
        unit = 's'
        per_suite = (
            'Each suite is the mean of its measured head-round totals in '
            'that run, in raw seconds; the warm-up round is discarded by '
            'the timed job\'s design and the base side is another tree, so '
            'neither is counted.')
        source = (
            f'raw seconds from one run (tests run {data["measured_from"]}), '
            'not reference-normalized: the reference workload does not run '
            'in the timed job until the planner lands beside it, so this '
            'run took no reading to normalize by, and the first scheduled '
            'refresh replaces every number here with reference-normalized '
            'medians over main runs, which is why `units` is the one place '
            'that fact is recorded. A pull request\'s run, not main\'s, '
            'on purpose: main\'s newest fully-measured run was 97 commits '
            'stale and covered fewer suites, and the first scheduled '
            'refresh re-derives everything from main')
    else:
        unit = 'reference multiples'
        per_suite = (
            'Each suite is the mean of its measured head-round totals in '
            'that run, divided by its own cell\'s reference seconds; the '
            'warm-up round is discarded by the timed job\'s design and the '
            'base side is another tree, so neither is counted.')
        source = (
            f'reference-normalized medians over {data["runs"]} run(s) '
            f'({data["measured_from"]}), each suite a multiple of the '
            'reference workload its own cell measured')
    parts = [
        source + '.',
        per_suite,
        (f'target_cell_weight {target:g}: the {len(weights)} recorded '
         f'weights total {total:.4g} {unit}, which is '
         f'{_plural(len(decision.cells), "cell")} at that target; it is the '
         f'smallest {TARGET_STEP}-unit step at which the planner\'s balance '
         f'guarantee holds (heaviest cell {heaviest:.4g} against a '
         f'{median_cell:.4g} median, margin {CELL_WEIGHT_MARGIN:g}) and the '
         f'count fits the bound, so the target is measured rather than '
         f'chosen.'),
        (f'max_cells {data["max_cells"]}: the bound on the cells the planner '
         f'derives; the measured run ran {_plural(cells, "cell")}, the '
         f'concurrency the repository runs today, and when the derived '
         f'count reaches the bound the planner clamps and names the target '
         f'the margin would need.'),
    ]
    if estimated:
        parts.append(
            f'{len(estimated)} of the tree\'s {len(suite_names(tree))} '
            f'suites are not measured by these runs and are estimated at '
            f'the median of the recorded weights: '
            f'{", ".join(estimated)}')
    else:
        parts.append('every suite in the tree is measured by these runs')
    parts.append(
        'Re-derive with `python3 scripts/ci/refresh_timings.py --runs-root '
        '<downloaded runs> --out .github/suite-timings.json` (--seed for '
        'the first file from one run).')
    return ' '.join(parts)
