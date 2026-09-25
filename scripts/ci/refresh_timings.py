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

THE WRITE. The file is rewritten only when a weight moved beyond
`WEIGHT_MARGIN` of the recorded one, a suite appeared in the
measurements, or a suite left them. Otherwise nothing is written and
the reason is printed, so a scheduled refresh that finds nothing to
say is a no-op, not a commit.

UNITS. Weights are reference-multiples. A file in seconds is rescaled
into them -- the target with the weights, by the same factor -- so the
cell target keeps the wall-clock load it was derived from; a target
left in seconds beside weights in multiples would be a bound on
nothing. The seed mode (`--seed`) writes the first file from a single
run's RAW seconds, because the reference workload does not exist until
the timed job runs it, and records that fact in the file's `seeded`
field with the measured basis for the target and the cell bound.

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
    from plan_timed_matrix import (
        CELL_WEIGHT_MARGIN, SCHEMA_VERSION, PlanError, read_timings,
        suite_names)
    from plan_timed_matrix import plan as plan_matrix
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.plan_timed_matrix import (
        CELL_WEIGHT_MARGIN, SCHEMA_VERSION, PlanError, read_timings,
        suite_names)
    from scripts.ci.plan_timed_matrix import plan as plan_matrix

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
_TARGET_STEP = 5
_SEED_NOTE = ('raw seconds, from one run, not reference-normalized: the '
              'reference workload does not run in the timed job until the '
              'planner lands beside it, so this run took no reading to '
              'normalize by. The first scheduled refresh replaces every '
              'number here with reference-normalized medians over main '
              'runs, which is why `units` is the one place this fact is '
              'recorded.')


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
    """Suites whose weight moved beyond the margin, with both numbers."""
    moved = []
    for suite, old in sorted(recorded.items()):
        new = computed.get(suite)
        if new is None:
            continue
        if abs(new - old) / old > WEIGHT_MARGIN:
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


def _written(weights, target, max_cells, run_ids):
    return {
        'schema_version': SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': 'reference-multiples',
        'suite_weights': {suite: round(weight, 4)
                          for suite, weight in sorted(weights.items())},
        'measured_from': ', '.join(str(run_id) for run_id in run_ids),
        'runs': len(run_ids),
    }


def _write(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def refresh(runs_root, out, wanted=SAMPLE_RUNS, tree=None):
    """Recompute the file from the runs; return the message, or refuse.

    The caller owns the exit code; `RefreshError` is the refusal.
    """
    existing = read_timings(out)
    runs = discover_runs(runs_root)
    selected, report = select(runs, wanted)
    if not selected:
        return (f'no run under {runs_root} produced a complete set of cell '
                f'artifacts ({report["empty"]} with none, '
                f'{len(report["incomplete"])} with a different cell set); '
                f'wrote nothing to {out}')
    medians, reference = _median_weights(selected)
    reasons = _reasons(existing, medians)
    run_ids = [run_id for run_id, _w, _r in selected]
    where = (f'median over runs {", ".join(str(r) for r in run_ids)} '
             f'(sample {len(run_ids)}, {len(medians)} suites); '
             + _reached_message(report))
    if not reasons:
        return (f'{out} unchanged: no weight moved beyond '
                f'{WEIGHT_MARGIN:.0%} of its recorded value; '
                f'no suite appeared or disappeared; {where}; wrote nothing')
    scale = _unit_scale(existing['units'], reference)
    data = _written(medians, existing['target_cell_weight'] * scale,
                    existing['max_cells'], run_ids)
    _write(out, data)
    estimated = _estimated(tree, data) if tree is not None else []
    return (f'wrote {out}: ' + '; '.join(reasons) + f'; {where}'
            + (f'; {len(estimated)} tree suites estimated'
               if estimated else ''))


def _reached_message(report):
    return (f'reached back over {report["reached"]} runs'
            + (f' ({len(report["incomplete"])} incomplete cell sets: '
               + ', '.join(str(run_id) for run_id in report['incomplete'])
               + ')' if report['incomplete'] else '')
            + (f' ({report["empty"]} with no cell artifacts)'
               if report['empty'] else ''))


def _estimated(tree, data):
    """Tree suites the file records nothing about (the planner estimates)."""
    recorded = data['suite_weights']
    return [name for name in suite_names(tree) if name not in recorded]


def _balanced(decision):
    """Whether the planner's own balance guarantee holds for this plan."""
    loads = [cell.weight for cell in decision.cells]
    if not loads:
        return False
    return max(loads) <= statistics.median(loads) * (1 + CELL_WEIGHT_MARGIN)


def derive_target(tree, weights, max_cells):
    """The smallest target the balance guarantee allows, in 5-second steps.

    More cells is a shorter critical path, and the cell count falls as
    the target rises, so the smallest target that still balances packs
    the most cells that fit the bound. The guarantee is the planner's
    own `CELL_WEIGHT_MARGIN`, checked with the planner itself: at a
    lower target a heavy suite sits alone in its cell while the median
    cell stays small, and the ratio crosses the margin.
    """
    total = sum(weights.values())
    if total <= 0 or max_cells < 1:
        return float(_TARGET_STEP)
    for target in range(_TARGET_STEP, int(total) + _TARGET_STEP,
                        _TARGET_STEP):
        if math.ceil(total / target) > max_cells:
            continue
        data = {'schema_version': SCHEMA_VERSION,
                'target_cell_weight': float(target),
                'max_cells': max_cells, 'units': 'seconds',
                'suite_weights': weights, 'measured_from': 'seed',
                'runs': 1}
        if _balanced(plan_matrix(tree, data)):
            return float(target)
    return float(math.ceil(total / max_cells / _TARGET_STEP)
                 * _TARGET_STEP)


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
    target = derive_target(tree, seconds, max_cells)
    total = sum(seconds.values())
    decision = plan_matrix(tree, {
        'schema_version': SCHEMA_VERSION,
        'target_cell_weight': target, 'max_cells': max_cells,
        'units': 'seconds', 'suite_weights': seconds,
        'measured_from': str(run_id), 'runs': 1})
    loads = [cell.weight for cell in decision.cells]
    data = {
        'schema_version': SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': 'seconds',
        'suite_weights': {suite: round(value, 3)
                          for suite, value in sorted(seconds.items())},
        'measured_from': str(run_id),
        'runs': 1,
        'seeded': (
            f'{_SEED_NOTE} Each suite is the mean of its measured head-round '
            f'totals in tests run {run_id}; the warm-up round is discarded '
            f'and the base side is another tree, so neither is counted. '
            f'target_cell_weight {target:g}: this run measured {total:.1f} s '
            f'across {len(seconds)} suites, which is '
            f'{math.ceil(total / target)} cells at that target; it is the '
            f'smallest {_TARGET_STEP}-second step at which the planner\'s '
            f'balance guarantee holds (heaviest cell {max(loads):.1f} s '
            f'against a {statistics.median(loads):.1f} s median, margin '
            f'{CELL_WEIGHT_MARGIN:g}) and the count fits the bound. '
            f'max_cells {max_cells}: the {max_cells} cells this run measured '
            f'are the concurrency the repository runs today, so the bound is '
            f'that number and the planner clamps and names the target if the '
            f'count ever reaches it. A run on a pull request rather than '
            f'main, because main\'s newest fully measured run was 97 commits '
            f'stale and covered fewer suites: strictly fresher data for a '
            f'one-time seed, and the first scheduled refresh re-derives '
            f'everything from main.'),
    }
    _write(out, data)
    return (f'seeded {out} from tests run {run_id}: {len(seconds)} suites '
            f'measured, total {total:.1f} s, target_cell_weight {target:g}, '
            f'max_cells {max_cells}, units seconds')


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
    except (RefreshError, PlanError) as error:
        print(f'refresh_timings: {error}', file=sys.stderr)
        return 1
    print(message, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
