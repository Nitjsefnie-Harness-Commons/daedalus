#!/usr/bin/env python3
"""The timings file's two bounds, and the prose that explains them.

WHAT THIS MODULE OWNS. Two things, both the file's reader-facing
contract rather than the planner's packing. The first is the coupling
between `target_cell_weight` and `max_cells` and the planner's
`CELL_WEIGHT_MARGIN`: those two numbers are not measurements, the
planner only NOTES a target the margin forbids -- it still prints a full
matrix and exits 0 -- and `read_timings` type-checks the number without
knowing what it means, so `verify_target` is the chokepoint the refresher
calls before every write. The second is the file's `basis` field: the
whole paragraph a reader of the data file alone reads, rebuilt from the
numbers of that write rather than preserved, because a sentence
describing a run, a target and a cell count the file no longer has is
the same defect as a stale number one level up.

THE TARGET IS DERIVED, NOT CHOSEN. `derive_target` returns the smallest
step at which the planner's balance guarantee holds on the weights being
written AND the derived cell count fits `max_cells`. More cells is a
shorter critical path and the count falls as the target rises, so the
smallest admissible step packs the most cells that fit the bound.
`verify_target` applies it to a target the file already carries: one the
margin admits is kept untouched, one the margin forbids is re-derived and
the reason travels with the write, and a distribution on which no target
within the bound balances is a refusal rather than a number nobody can
act on. The step is five in whatever unit the file carries -- seconds
before the reference workload exists, reference-multiples after, where
five is a coarser physical step because one multiple is a whole
reference workload. The margin's own measured basis is in
`plan_timed_matrix`'s docstring, beside the constant it explains.
"""
import math
import statistics

try:
    from plan_timed_matrix import (
        CELL_WEIGHT_MARGIN, resolve, suite_names)
    from plan_timed_matrix import plan as plan_matrix
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.plan_timed_matrix import (
        CELL_WEIGHT_MARGIN, resolve, suite_names)
    from scripts.ci.plan_timed_matrix import plan as plan_matrix

# The step between candidate targets, in the file's own units.
TARGET_STEP = 5
# The most candidate targets `derive_target` will ask the planner about.
# `read_timings` accepts any positive finite weight, so the count of
# steps a total spans is a function of the total's MAGNITUDE and not of
# the tree: one suite recorded at 1e300 puts 2e299 candidates between
# the planner and a return, each a full `plan()` with its own
# `git ls-files`. 256 steps is 1280 multiples, against 356.4 for the
# shipped file -- a real tree of this size is under half the bound --
# and past it the weights are not runtimes, which is a refusal with a
# reason rather than a number to spend a cron job's 30 minutes on.
MAX_TARGET_PROBES = 256


class BoundsError(Exception):
    """A refusal: no target within the bound balances on these weights."""


def plan_is_balanced(tree, data):
    """Whether the planner's own balance guarantee holds for this plan."""
    loads = [cell.weight for cell in plan_matrix(tree, data).cells]
    if not loads:
        return False
    return max(loads) <= statistics.median(loads) * (1 + CELL_WEIGHT_MARGIN)


def _candidate(weights, target, max_cells):
    """The probe `plan()` is asked about: the three fields it reads.

    Not a data file -- `read_timings` never sees this dict, so the
    fields it omits cannot reach a write from here.
    """
    return {'target_cell_weight': float(target),
            'max_cells': max_cells,
            'suite_weights': weights}


def tree_weights(tree, recorded):
    """The recorded weights the TREE still holds, as the planner sees them.

    One resolver, so a weight for a suite the tree has since deleted
    cannot read as a live one outside the planner. The union carries
    such a weight forward forever -- no run can measure a deleted suite
    -- and a hundred dead weights beside six live ones moved the derived
    target by an order of magnitude and packed a 120-multiple live tree
    into one cell.
    """
    return resolve(recorded, suite_names(tree), 1.0)[0]


def live_recorded(tree, data):
    """How many of the file's recorded weights the TREE still holds.

    Not the resolved weights: this counts what the file MEASURED, and
    the collapse rule is judged against a suite a run could have
    measured. `resolve` would price an unmeasured tree suite at the
    recorded median, which answers a different question.
    """
    return len(set(data['suite_weights']) & set(suite_names(tree)))


def derive_target(tree, recorded, max_cells):
    """The smallest target the balance guarantee allows, in TARGET_STEPs.

    At a target the margin forbids, a heavy suite sits alone in its cell
    while the median cell stays small and the ratio crosses the margin;
    raising the target lets more suites share cells and the median rises
    under the heavy one. Scoped to the tree before anything is summed,
    because a weight for a deleted suite is never placed.

    The candidates run from one step to the whole total, so their
    COUNT is the total's magnitude and not the tree's size, and every
    one of them is a `plan()` with its own `git ls-files`. A total past
    `MAX_TARGET_PROBES` steps is a `BoundsError`: it is a file whose
    weights are not runtimes, which is a hand-edit or a bad merge
    rather than a measurement, and the alternative is a refresh that
    spends the job's whole timeout deciding nothing.
    """
    weights = tree_weights(tree, recorded)
    total = sum(weights.values())
    if total <= 0 or max_cells < 1:
        return float(TARGET_STEP)
    if total / TARGET_STEP > MAX_TARGET_PROBES:
        raise BoundsError(
            f'these weights total {total:.4g} over the tree, which is '
            f'{total / TARGET_STEP:.3g} candidate targets of {TARGET_STEP:g} '
            f'against the {MAX_TARGET_PROBES} this derivation will ask the '
            f'planner about: a suite recorded at a magnitude like that is '
            f'not a runtime. Re-derive the file with '
            f'`python3 scripts/ci/refresh_timings.py --runs-root '
            f'<downloaded runs> --out .github/suite-timings.json`, or raise '
            f'TARGET_STEP if the step is genuinely coarser than the data')
    for target in range(TARGET_STEP, int(total) + TARGET_STEP, TARGET_STEP):
        if math.ceil(total / target) > max_cells:
            continue
        if plan_is_balanced(tree, _candidate(weights, target, max_cells)):
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
    target = derive_target(tree, data['suite_weights'], max_cells)
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


def _coverage_clause(recorded, names, unmeasured):
    """What a run did not measure, split by where the weight came from.

    Two kinds, and the difference is the price: a suite this file
    already records keeps its own recorded weight through the union
    write, while a suite it records nothing about is priced by the
    planner at the median of the recorded ones. Calling both of them
    estimates made the clause read beside a weight clause that adds
    their recorded values -- "estimated at the median" beside a number
    that is not the median.

    The names are listed in the tree's own order, which is sorted, and
    not in the order of a set: this clause is compared byte for byte
    against a committed file by two suites, so a set's iteration order
    would make the sentence -- and the file written from it -- a
    different string in every process.
    """
    carried = [name for name in unmeasured if name in recorded]
    unknown = [name for name in unmeasured if name not in recorded]
    spans = []
    if carried:
        spans.append(f'{len(carried)} carried at the weight this file '
                     'already recorded')
    if unknown:
        spans.append(f'{len(unknown)} estimated at the median of the '
                     'recorded weights')
    return (f'{len(unmeasured)} of the tree\'s {len(names)} suites are not '
            f'measured by these runs, ' + ' and '.join(spans) + ': '
            + ', '.join(name for name in names if name in unmeasured))


def basis_sentence(tree, data, cells, estimated):
    """The file's `basis` field: both bounds, their basis, and the rest.

    `cells` is the number of cells the measured run(s) ran, and
    `estimated` the TREE suites those runs did not measure. Rebuilt
    from the numbers of this write on every write, seed or refresh.

    `estimated` is about the RUNS, not about the file. The write is a
    union, so a suite the runs did not measure is in the file anyway,
    and a clause driven by the file's own weights counted the tree as
    fully measured by a run that measured three suites of it. The
    caller has the measured set and the file does not. Nor is it one
    set: `_coverage_clause` splits it by whether the file records that
    suite, because the planner prices the halves differently.

    `cells` is not the concurrency either, and the sentence calls it one
    only when the two agree: a run that produced two cells of a
    fourteen-cell matrix measured two cells, and the repository runs
    fourteen.

    The unmeasured set is a UNION, and the second term is the floor the
    file carries on its own: the write is a union, so a suite the tree
    holds and the file records nothing about was not measured by the
    runs that produced this file, whatever the caller reports. Taking
    the caller's report alone let a caller holding a tree it could see
    and naming no unmeasured suite have the sentence certify the whole
    of it -- which is how a regeneration that read the list back out of
    the prose it was regenerating wrote "every suite in the tree is
    measured" over a file that records twelve of its 340. On a caller
    holding the run's real set the floor is a subset of it and the
    union moves nothing.
    """
    decision = plan_matrix(tree, data)
    loads = [cell.weight for cell in decision.cells]
    names = suite_names(tree)
    planned_weights = resolve(data['suite_weights'], names, 1.0)[0]
    unmeasured = set(estimated) | set(estimated_count(tree, data))
    measured_weights = {name: weight for name, weight
                        in planned_weights.items()
                        if name not in unmeasured}
    total = sum(measured_weights.values())
    planned = sum(planned_weights.values())
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
    if unmeasured:
        weight_clause = (
            f'the {len(measured_weights)} recorded weights total '
            f'{total:.4g} {unit} and the {_plural(len(unmeasured), "suite")} '
            f'these runs did not measure add {planned - total:.4g}, '
            f'{planned:.4g} in all')
    else:
        weight_clause = (f'the {len(measured_weights)} recorded weights '
                         f'total {total:.4g} {unit}')
    if cells == len(decision.cells):
        concurrency = (f'the measured run ran {_plural(cells, "cell")}, '
                       'the concurrency the repository runs today')
    else:
        concurrency = (
            f'the measured run ran {_plural(cells, "cell")}, while this file '
            f'derives {_plural(len(decision.cells), "cell")}, so that run is '
            'not the matrix this file plans')
    parts = [
        source + '.',
        per_suite,
        (f'target_cell_weight {target:g}: {weight_clause}, which is '
         f'{_plural(len(decision.cells), "cell")} at that target; it is the '
         f'smallest {TARGET_STEP}-unit step at which the planner\'s balance '
         f'guarantee holds (heaviest cell {heaviest:.4g} against a '
         f'{median_cell:.4g} median, margin {CELL_WEIGHT_MARGIN:g}) and the '
         f'count fits the bound, so the target is measured rather than '
         f'chosen.'),
        (f'max_cells {data["max_cells"]}: the bound on the cells the planner '
         f'derives; {concurrency}, and when the derived '
         f'count reaches the bound the planner clamps and names the target '
         f'the margin would need.'),
    ]
    if unmeasured:
        parts.append(_coverage_clause(
            data['suite_weights'], names, unmeasured))
    else:
        parts.append('every suite in the tree is measured by these runs')
    parts.append(
        'Re-derive with `python3 scripts/ci/refresh_timings.py --runs-root '
        '<downloaded runs> --out .github/suite-timings.json` (--seed for '
        'the first file from one run).')
    return ' '.join(parts)
