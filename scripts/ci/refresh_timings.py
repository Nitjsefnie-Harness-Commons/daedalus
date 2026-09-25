#!/usr/bin/env python3
"""Re-derive `.github/suite-timings.json` from the timed job's artifacts.

The data file is a function of CI's own measurements: each suite's
weight is the time it took per measured head round, as a multiple of
one fixed reference workload (`reference_workload.py`) measured on the
same runner, and the planner packs cells by those weights. This script
is the half of that which owns the measurement; the planner owns the
packing, and the workflow owns the ordering (download the artifacts,
then run this).

WHAT IT READS. One downloaded run is a directory of cell artifacts:

    <runs-root>/<run-id>/<cell>/reference.json
    <runs-root>/<run-id>/<cell>/head-<n>/<suite-stem>.json

and each suite file is what `time_tests.py` writes: `{"tests": {test
name: seconds}}`. A suite's seconds in a run are the mean of its
per-round totals over the rounds that carried it -- the MEASURED head
rounds only. The warm-up round is discarded by the timed job's design
and the base side is a different tree; neither is a head round and
neither is read here. Nothing else under a cell (`base-<n>/`,
`warmup/`, `verdict.json`, `ratio.txt`) is read. Cell NAMES are read
from the directory names and never from a list: the planner generates
them, and a name this file knew would be a name the next packing
renames.

WHICH RUNS. The runs are the most recent that produced a COMPLETE set
of cell artifacts -- the same cell set as the newest run that produced
any -- regardless of whether the run concluded green. A run's overall
conclusion is not evidence about its durations: `timed` lists
`aggregate` in `needs:`, so one red correctness leg (a flaky Windows
`suites` leg reds often enough) skips the whole measuring matrix, and
selecting only green runs would leave the file unrefreshed for exactly
as long as the staleness this file exists to fix goes unnoticed. A run
with a DIFFERENT cell set is a different partition of the tree, so its
numbers are not comparable and it is skipped, and the report says how
far back the search reached and why. A cell directory with no
reference reading is not a skipped run but a refusal naming the cell
and the suites it carried: a run that produced the full set of cells
and lost one unit's reading is a broken measurement, and stepping over
it is the silence the maintainer ruled out. The median is taken over
the selected runs and `runs` records that sample -- a median over one
run is still a median; a file that silently claimed three is not.

THE WRITE. The file is rewritten when a weight moved beyond
`WEIGHT_MARGIN` of the recorded one, a suite appeared in the
measurements, a suite left them, or the recorded target was re-derived
because the margin forbade it -- that last reason depends on the tree,
through the planner, so the decision is not a function of the
measurements alone. Otherwise nothing is written and the reason is
printed, so a scheduled refresh that finds nothing to say is a no-op,
not a commit.

THE BOUNDS. `target_cell_weight` and `max_cells` are not measurements;
they are the file's two policy numbers, coupled to the planner's
`CELL_WEIGHT_MARGIN` by construction and to nothing else, and the
planner only notes a target the margin forbids before exiting 0. So
before every write -- seed or refresh -- the target is verified
against the margin with the weights being written and re-derived
rather than written through when the margin forbids it
(`timings_bounds`, the chokepoint), and the file carries a `seeded`
field naming both bounds, their measured basis and the tree suites the
measurements do not cover, rebuilt from the numbers of that write so
it cannot go stale the way a preserved sentence would.

UNITS. Weights are reference-multiples. A file in seconds is rescaled
into them -- the target with the weights, by the same factor -- so the
cell target keeps the wall-clock load it was derived from; a target
left in seconds beside weights in multiples would be a bound on
nothing. The seed mode (`--seed`) writes the first file from a single
run's RAW seconds, because the reference workload does not exist until
the timed job runs it, and says so in that same field.

  python3 scripts/ci/refresh_timings.py --runs-root runs/ --out FILE
  python3 scripts/ci/refresh_timings.py --runs-root seed/ --out FILE \
      --seed --tree .
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    from plan_timed_matrix import PlanError, SCHEMA_VERSION, read_timings
    from timings_bounds import (
        BoundsError, basis_sentence, derive_target, estimated_count,
        plan_is_balanced, verify_target)
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.plan_timed_matrix import (
        PlanError, SCHEMA_VERSION, read_timings)
    from scripts.ci.timings_bounds import (
        BoundsError, basis_sentence, derive_target, estimated_count,
        plan_is_balanced, verify_target)

# The share a recomputed weight may differ from the recorded one before
# the file is rewritten: runner noise and a suite's own jitter move a
# weight by a few percent, and a file rewritten for that is a commit
# every scheduled run makes.
WEIGHT_MARGIN = 0.25
# Runs the median uses. Three is the smallest count whose median
# ignores one outlier run (two runs and a tie is no majority); more
# would be a better estimate at the cost of one artifact download per
# run, which is 15 cell artifacts each.
SAMPLE_RUNS = 3
# The timed job's own round names. A head round is measured; `base-<n>`
# and `warmup` are not. The cell names, unlike these, are generated.
_ROUND_PREFIX = 'head-'
_REFERENCE_FILE = 'reference.json'


class RefreshError(Exception):
    """A refusal with a reason the caller can act on."""


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RefreshError(f'{name} is not a number: {value!r}')
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise RefreshError(f'{name} is not a positive finite number: '
                           f'{value!r}')
    return number


def _head_rounds(cell):
    """The cell's measured round directories, in name order."""
    return sorted(path for path in cell.iterdir()
                  if path.is_dir() and path.name.startswith(_ROUND_PREFIX))


def _suite_seconds(cell, run_id):
    """Every suite the cell's head rounds carry, and its per-round total.

    A suite missing from one round is averaged over the rounds that
    carried it -- the measured head rounds are what the total is over --
    and a suite in no round is simply not measured, which is the
    planner's estimate path rather than a failure.
    """
    totals = {}
    for round_dir in _head_rounds(cell):
        for path in sorted(round_dir.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as error:
                raise RefreshError(
                    f'run {run_id} cell {cell.name}: cannot read '
                    f'{path.name}: {error}') from None
            if not isinstance(data, dict) or \
                    not isinstance(data.get('tests'), dict):
                raise RefreshError(
                    f'run {run_id} cell {cell.name}: {path.name} carries no '
                    '"tests" map; it is not a time_tests.py summary')
            seconds = 0.0
            for name, value in data['tests'].items():
                try:
                    seconds += _positive(value, f'{path.name} {name}')
                except RefreshError as error:
                    raise RefreshError(
                        f'run {run_id} cell {cell.name}: {error}') from None
            entry = totals.setdefault(f'{path.stem}.py', [])
            entry.append(seconds)
    return {name: sum(values) / len(values)
            for name, values in totals.items()}


def _reference(cell, run_id, suites):
    """The cell's reference seconds, or a refusal naming cell and suites."""
    path = cell / _REFERENCE_FILE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise RefreshError(
            f'run {run_id} cell {cell.name}: no readable '
            f'{_REFERENCE_FILE} ({error}); the weight of the suites it '
            f'carried cannot be counted in reference units: '
            f'{", ".join(sorted(suites)) or "none"}') from None
    if not isinstance(data, dict) or 'seconds' not in data:
        raise RefreshError(
            f'run {run_id} cell {cell.name}: {_REFERENCE_FILE} carries no '
            f'"seconds"; the suites it carried are unmeasured: '
            f'{", ".join(sorted(suites)) or "none"}')
    return _positive(data['seconds'], f'{_REFERENCE_FILE} seconds')


def read_run(run_dir, run_id):
    """One run's per-suite weights in reference-multiples, and readings."""
    cells = {path.name: path for path in sorted(run_dir.iterdir())
             if path.is_dir() and _head_rounds(path)}
    if not cells:
        raise RefreshError(f'run {run_id} carries no cell artifacts')
    weights = {}
    references = {}
    for name, cell in cells.items():
        seconds = _suite_seconds(cell, run_id)
        reading = _reference(cell, run_id, seconds)
        references[name] = reading
        for suite, value in seconds.items():
            weights[suite] = value / reading
    return weights, references


def discover_runs(runs_root):
    """Run directories under the root, newest (highest id) first."""
    if not runs_root.is_dir():
        raise RefreshError(f'no runs root at {runs_root}')
    runs = [(int(path.name), path) for path in runs_root.iterdir()
            if path.is_dir() and path.name.isdigit()]
    return sorted(runs, key=lambda item: -item[0])


def select(runs, wanted):
    """The most recent `wanted` runs with a complete cell set, and a report.

    Completeness is judged against the newest run that produced any cell
    at all: its cell set is the partition these numbers are about. An
    older run with a different set is a different partition; it is
    skipped, and the skip is reported rather than absorbed.
    """
    selected = []
    expected = None
    incomplete = []
    empty = 0
    for run_id, path in runs:
        cells = {entry.name for entry in path.iterdir()
                 if entry.is_dir() and _head_rounds(entry)}
        if not cells:
            empty += 1
            continue
        if expected is None:
            expected = cells
        if cells != expected:
            incomplete.append(run_id)
            continue
        weights, references = read_run(path, run_id)
        selected.append((run_id, weights, references))
        if len(selected) == wanted:
            break
    report = {'reached': len(selected) + len(incomplete) + empty,
              'incomplete': incomplete, 'empty': empty}
    return selected, report


def _median_weights(selected):
    """Every suite's median weight, and the median reference seconds."""
    by_suite = {}
    references = []
    for _run_id, weights, readings in selected:
        for suite, weight in weights.items():
            by_suite.setdefault(suite, []).append(weight)
        references.extend(readings.values())
    medians = {suite: statistics.median(values)
               for suite, values in by_suite.items()}
    return medians, statistics.median(references)


def _unit_scale(old_units, reference):
    """What a number recorded in `old_units` is worth in the new units."""
    if old_units == 'reference-multiples':
        return 1.0
    if old_units == 'seconds':
        return 1.0 / reference
    raise RefreshError(f'cannot convert units {old_units!r} into '
                       'reference-multiples')


def _moved(recorded, computed):
    """Suites whose weight moved beyond the margin, with both numbers.

    A recorded weight of zero -- which the seed's millisecond rounding
    can produce for a sub-millisecond suite -- has no relative move to
    measure, and any new positive weight is infinitely far from it, so
    it counts as moved rather than dividing by it.
    """
    moved = []
    for suite, old in sorted(recorded.items()):
        new = computed.get(suite)
        if new is None:
            continue
        if old <= 0 or abs(new - old) / old > WEIGHT_MARGIN:
            moved.append((suite, old, new))
    return moved


def _reasons(existing, computed):
    """Why the file would be rewritten; empty means leave it alone."""
    reasons = []
    if existing['units'] != 'reference-multiples':
        reasons.append(
            f'units {existing["units"]} -> reference-multiples (target '
            'rescaled by the same factor as the weights)')
    recorded = existing['suite_weights']
    appeared = sorted(set(computed) - set(recorded))
    gone = sorted(set(recorded) - set(computed))
    if appeared:
        reasons.append('appeared: ' + ', '.join(appeared))
    if gone:
        reasons.append('disappeared: ' + ', '.join(gone))
    moved = _moved(recorded, computed)
    if moved:
        reasons.append('moved beyond '
                       f'{WEIGHT_MARGIN:.0%}: ' + ', '.join(
                           f'{suite} {old:.4g} -> {new:.4g}'
                           for suite, old, new in moved))
    return reasons


def _rounded(weight):
    """Four decimals, but never zero: the schema refuses a zero weight."""
    value = round(weight, 4)
    return value if value > 0 else weight


def _written(weights, target, max_cells, run_ids, units):
    return {
        'schema_version': SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': units,
        'suite_weights': {suite: _rounded(weight)
                          for suite, weight in sorted(weights.items())},
        'measured_from': ', '.join(str(run_id) for run_id in run_ids),
        'runs': len(run_ids),
    }


def _write(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def _attach_basis(tree, data, cells):
    """The file's `seeded` field: the basis of both bounds, rebuilt now."""
    data['seeded'] = basis_sentence(
        tree, data, cells, estimated_count(tree, data))
    return data


def refresh(runs_root, out, wanted=SAMPLE_RUNS, tree=None):
    """Recompute the file from the runs; return the message, or refuse.

    The caller owns the exit code; `RefreshError` and `BoundsError` are
    the refusals. The target is verified against the margin BEFORE the
    decision to write, so a forbidden target is re-derived even when no
    weight moved -- the file cannot be left in a state the shipped
    margin forbids, and the re-derivation is itself a reason to write.
    """
    if tree is None:
        tree = _REPO_ROOT
    existing = read_timings(out)
    runs = discover_runs(runs_root)
    selected, report = select(runs, wanted)
    if not selected:
        return (f'no run under {runs_root} produced a complete set of cell '
                f'artifacts ({report["empty"]} with none, '
                f'{len(report["incomplete"])} with a different cell set); '
                f'wrote nothing to {out}')
    medians, reference = _median_weights(selected)
    run_ids = [run_id for run_id, _w, _r in selected]
    where = (f'median over runs {", ".join(str(r) for r in run_ids)} '
             f'(sample {len(run_ids)}, {len(medians)} suites); '
             + _reached_message(report))
    scale = _unit_scale(existing['units'], reference)
    data = _written(medians, existing['target_cell_weight'] * scale,
                    existing['max_cells'], run_ids, 'reference-multiples')
    target, note = verify_target(tree, data, data['max_cells'])
    data['target_cell_weight'] = target
    reasons = _reasons(existing, medians)
    if note:
        reasons.append(note)
    if not reasons:
        return (f'{out} unchanged: no weight moved beyond '
                f'{WEIGHT_MARGIN:.0%} of its recorded value; '
                f'no suite appeared or disappeared; the target holds the '
                f'margin; {where}; wrote nothing')
    _attach_basis(tree, data, len(selected[0][2]))
    _write(out, data)
    return f'wrote {out}: ' + '; '.join(reasons) + f'; {where}'


def _reached_message(report):
    return (f'reached back over {report["reached"]} runs'
            + (f' ({len(report["incomplete"])} incomplete cell sets: '
               + ', '.join(str(run_id) for run_id in report['incomplete'])
               + ')' if report['incomplete'] else '')
            + (f' ({report["empty"]} with no cell artifacts)'
               if report['empty'] else ''))


def seed(runs_root, out, tree):
    """Write the first file from one run's raw seconds; return the message."""
    runs = discover_runs(runs_root)
    for run_id, path in runs:
        cells = {entry.name: entry for entry in sorted(path.iterdir())
                 if entry.is_dir() and _head_rounds(entry)}
        if cells:
            break
    else:
        raise RefreshError(f'no run under {runs_root} produced cell '
                           'artifacts; nothing to seed from')
    seconds = {}
    for cell in cells.values():
        seconds.update(_suite_seconds(cell, run_id))
    if not seconds:
        raise RefreshError(f'run {run_id} carries no suite durations')
    max_cells = len(cells)
    target = derive_target(tree, seconds, max_cells, 'seconds')
    data = _written(seconds, target, max_cells, [run_id], 'seconds')
    if not plan_is_balanced(tree, data):
        raise BoundsError(
            f'run {run_id}: no target within max_cells {max_cells} balances '
            f'its weights at the margin; split the heaviest suite or raise '
            f'max_cells by hand')
    _attach_basis(tree, data, max_cells)
    _write(out, data)
    return (f'seeded {out} from tests run {run_id}: {len(seconds)} suites '
            f'measured, total {sum(seconds.values()):.1f} s, '
            f'target_cell_weight {target:g}, max_cells {max_cells}, '
            f'units seconds')


def _parser():
    parser = argparse.ArgumentParser(
        description='Refresh the suite-timings file from run artifacts.')
    parser.add_argument('--runs-root', required=True,
                        help='directory of downloaded per-run artifact trees')
    parser.add_argument('--out', default=str(
        _REPO_ROOT / '.github' / 'suite-timings.json'),
        help='the data file to read and, on a change, rewrite')
    parser.add_argument('--tree', default=str(_REPO_ROOT),
                        help='checkout whose tests/ names the tree suites')
    parser.add_argument(
        '--runs', type=int, default=SAMPLE_RUNS,
        help='how many complete runs the median uses')
    parser.add_argument(
        '--seed', action='store_true',
        help='seed the first file from one run\'s raw seconds and always '
             'write it; the target and the cell bound are derived from '
             'that run and explained in the file')
    return parser


def main(argv=None):
    """Print what was decided; return 0, or 1 after a named refusal."""
    args = _parser().parse_args(argv)
    out = Path(args.out)
    try:
        if args.seed:
            message = seed(Path(args.runs_root), out, Path(args.tree))
        else:
            message = refresh(Path(args.runs_root), out, args.runs,
                              Path(args.tree))
    except (RefreshError, BoundsError, PlanError) as error:
        print(f'refresh_timings: {error}', file=sys.stderr)
        return 1
    print(message, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
