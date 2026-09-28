#!/usr/bin/env python3
"""Re-derive `.github/suite-timings.json` from the timed job's artifacts.

The data file is a function of CI's own measurements: each suite's
weight is the time it took per measured head round, as a multiple of
one fixed reference workload (`reference_workload.py`) measured on the
same runner, and the planner packs cells by those weights. This script
is the half of that which owns the measurement; the planner owns the
packing, and the workflow owns the ordering (download the artifacts,
then run this).

WHAT IT READS, AND WHICH RUNS. Both are `scripts/ci/timings_runs.py`,
which owns the artifact layout, the run selection and the two rules
that keep a refresh from narrowing the file: the runs that produced a
COMPLETE cell set, and the collapsed run that produced one cell where
the file bounds the matrix at more than one, which is skipped and
reported rather than taken as the partition. The median is taken over
the selected runs and `runs` records that sample -- a median over one
run is still a median; a file that silently claimed three is not.

THE WRITE, WHICH IS A UNION. The file is rewritten when a weight moved
beyond `WEIGHT_MARGIN` of the recorded one, a suite appeared in the
measurements, or the recorded target was re-derived because the margin
forbade it -- that last reason depends on the tree, through the
planner, so the decision is not a function of the measurements alone.
Otherwise nothing is written and the reason is printed, so a scheduled
refresh that finds nothing to say is a no-op, not a commit.

A suite that LEFT the measurements is not a reason to write, because a
run does not leave a suite by measuring it faster: a run that executed
one cell of a fifteen-cell matrix did not measure the other fourteen,
and writing the measured set alone deletes the weights of every suite
that run did not happen to execute. That is how a refresh left the
shipped file describing 28 of 326 suites and the planner then priced
the other 298 at the median of the heavy tail that survived. So the
recorded weights of suites these runs did not measure are carried into
the write in the file's own units, the new measurement wins wherever
both have one, and the report names what was carried so a reader can
tell which numbers a write measured. A suite deleted from the tree
keeps its recorded weight until some run measures it again, which
costs a named `stale` entry in the planner's summary and nothing else.

THE BOUNDS. `target_cell_weight` and `max_cells` are not measurements;
they are the file's two policy numbers, coupled to the planner's
`CELL_WEIGHT_MARGIN` by construction and to nothing else, and the
planner only notes a target the margin forbids before exiting 0. So
before every write -- seed or refresh -- the target is verified
against the margin with the weights being written and re-derived
rather than written through when the margin forbids it
(`timings_bounds`, the chokepoint), and the file carries a `basis`
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
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    from plan_timed_matrix import (
        BASIS_FIELD, PlanError, SCHEMA_VERSION, read_timings, suite_names)
    from timings_bounds import (
        BoundsError, basis_sentence, derive_target, live_recorded,
        plan_is_balanced, verify_target)
    from timings_runs import (
        RefreshError, cell_dirs, discover_runs, select, suite_seconds)
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.plan_timed_matrix import (
        BASIS_FIELD, PlanError, SCHEMA_VERSION, read_timings, suite_names)
    from scripts.ci.timings_bounds import (
        BoundsError, basis_sentence, derive_target, live_recorded,
        plan_is_balanced, verify_target)
    from scripts.ci.timings_runs import (
        RefreshError, cell_dirs, discover_runs, select, suite_seconds)

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


def median_weights(selected):
    """Every suite's median weight over the sample, and the reference.

    The writing half's own arithmetic, kept beside the write that uses
    it: it is a statistic of a run SAMPLE, which is not a thing the
    reader of a run directory knows anything about.
    """
    by_suite = {}
    references = []
    for _run_id, weights, readings in selected:
        for suite, weight in weights.items():
            by_suite.setdefault(suite, []).append(weight)
        references.extend(readings.values())
    return ({suite: statistics.median(values)
             for suite, values in by_suite.items()},
            statistics.median(references))


def unit_scale(old_units, reference):
    """What a number recorded in `old_units` is worth in the new units."""
    if old_units == 'reference-multiples':
        return 1.0
    if old_units == 'seconds':
        return 1.0 / reference
    raise RefreshError(f'cannot convert units {old_units!r} into '
                       'reference-multiples')


def _reasons(existing, measured):
    """Why the file would be rewritten; empty means leave it alone.

    Membership is judged against what the RUNS measured, not against
    the union: a suite this run did not measure is carried forward, not
    appeared, and one the file records and the runs did not measure is
    neither of the two -- it keeps its recorded weight, so there is no
    reason to write and the message names it separately.
    """
    reasons = []
    if existing['units'] != 'reference-multiples':
        reasons.append(
            f'units {existing["units"]} -> reference-multiples (target '
            'rescaled by the same factor as the weights)')
    recorded = existing['suite_weights']
    appeared = sorted(set(measured) - set(recorded))
    if appeared:
        reasons.append('appeared: ' + ', '.join(appeared))
    moved = _moved(recorded, measured)
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


def _runs_text(run_ids):
    """The ONE rendering of a run list, used by every field that names one.

    The commit subject and the file's `measured_from` are two records
    of the same measurement, so they are rendered from the same list by
    the same join. They did not have to be, and did not: commit
    `eed3ae9e` is titled "ci: refresh suite timings from run
    36318864740" while the file it wrote records `measured_from:
    36310409594`, because the workflow built the subject from `${{
    github.run_id }}` -- the REFRESH workflow's own run -- and the
    refresher recorded the `tests` run it measured. Both were right
    about their own value, and nothing compared them.
    """
    return ', '.join(str(run_id) for run_id in run_ids)


def commit_message(run_ids):
    """The subject the workflow commits a refreshed data file under.

    Written beside the file rather than assembled in the workflow, so
    the two names a reader sees on one commit cannot come from two
    different runs.
    """
    return f'ci: refresh suite timings from run {_runs_text(run_ids)}'


def _written(weights, target, max_cells, run_ids, units):
    return {
        'schema_version': SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': units,
        'suite_weights': {suite: _rounded(weight)
                          for suite, weight in sorted(weights.items())},
        'measured_from': _runs_text(run_ids),
        'runs': len(run_ids),
    }


def _write(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def _attach_basis(tree, data, cells, measured):
    """The file's `basis` field: the basis of both bounds, rebuilt now.

    `measured` is what the RUNS measured, not what the file records.
    The write is a union, so the file records every carried suite too,
    and a clause driven by the file's own weights called the tree fully
    measured by a run that measured three suites of it.
    """
    data[BASIS_FIELD] = basis_sentence(
        tree, data, cells, [name for name in suite_names(tree)
                            if name not in measured])
    return data


def refresh(runs_root, out, wanted=SAMPLE_RUNS, tree=None,
            message_file=None):
    """Recompute the file from the runs; return the message, or refuse.

    A WRITE IS A UNION, never a replacement. A run that executed one
    cell of a fifteen-cell matrix measured a fraction of the tree, and
    writing the measured set alone deletes the weights of every suite
    that run did not happen to execute -- which is how a refresh left
    the shipped file describing 28 of 326 suites, and the planner then
    priced the other 298 at the median of the heavy tail that
    survived. So the recorded weights of suites these runs did not
    measure are carried into the write in the file's own units, the
    new measurement wins wherever both have one, and the report names
    what was carried so a reader can tell which numbers a write
    measured. A suite deleted from the tree keeps its recorded weight
    until some run measures it again, which costs a named `stale`
    entry in the planner's summary and nothing else.

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
    selected, report = select(runs, wanted, existing['max_cells'],
                              live_recorded(tree, existing))
    if not selected:
        return (f'no run under {runs_root} produced a complete set of cell '
                f'artifacts ({report["empty"]} with none, '
                f'{len(report["incomplete"])} with a different cell set'
                + (_degenerate_message(report, existing['max_cells'])
                   if report['degenerate'] else '')
                + f'); wrote nothing to {out}')
    medians, reference = median_weights(selected)
    run_ids = [run_id for run_id, _w, _r in selected]
    if message_file is not None:
        # Written on BOTH outcomes, a changed file and an unchanged
        # one, because the workflow's commit step reads it after its
        # own `git diff --quiet` has already decided there is a commit
        # to make. It is a workspace file the step never stages.
        Path(message_file).write_text(
            commit_message(run_ids) + '\n', encoding='utf-8')
    where = (f'median over runs {_runs_text(run_ids)} '
             f'(sample {len(run_ids)}, {len(medians)} suites); '
             + _reached_message(report))
    scale = unit_scale(existing['units'], reference)
    carried = {suite: weight * scale
               for suite, weight in existing['suite_weights'].items()
               if suite not in medians}
    if carried:
        where += (f'; {len(carried)} recorded weights carried forward '
                  'unmeasured, so the write describes no less of the tree '
                  f'than it did: ' + ', '.join(sorted(carried)))
    data = _written(dict(carried, **medians),
                    existing['target_cell_weight'] * scale,
                    existing['max_cells'], run_ids, 'reference-multiples')
    target, note = verify_target(tree, data, data['max_cells'])
    data['target_cell_weight'] = target
    reasons = _reasons(existing, medians)
    if note:
        reasons.append(note)
    if not reasons:
        return (f'{out} unchanged: no weight moved beyond '
                f'{WEIGHT_MARGIN:.0%} of its recorded value; '
                f'no suite appeared; the target holds the '
                f'margin; {where}; wrote nothing')
    _attach_basis(tree, data, len(selected[0][2]), medians)
    _write(out, data)
    return f'wrote {out}: ' + '; '.join(reasons) + f'; {where}'


def _degenerate_message(report, max_cells):
    return (f', {len(report["degenerate"])} with one cell against a '
            f'max_cells bound of {max_cells}, which is a matrix that '
            'collapsed rather than a partition of this tree: '
            + ', '.join(str(run_id) for run_id in report['degenerate']))


def _reached_message(report):
    return (f'reached back over {report["reached"]} runs'
            + (f' ({len(report["incomplete"])} incomplete cell sets: '
               + ', '.join(str(run_id) for run_id in report['incomplete'])
               + ')' if report['incomplete'] else '')
            + (f' ({len(report["degenerate"])} collapsed to one cell: '
               + ', '.join(str(run_id) for run_id in report['degenerate'])
               + ')' if report['degenerate'] else '')
            + (f' ({report["empty"]} with no cell artifacts)'
               if report['empty'] else ''))


def seed(runs_root, out, tree):
    """Write the first file from one run's raw seconds; return the message."""
    runs = discover_runs(runs_root)
    for run_id, path in runs:
        cells = cell_dirs(path)
        if cells:
            break
    else:
        raise RefreshError(f'no run under {runs_root} produced cell '
                           'artifacts; nothing to seed from')
    seconds = {}
    for cell in cells.values():
        seconds.update(suite_seconds(cell, run_id))
    if not seconds:
        raise RefreshError(f'run {run_id} carries no suite durations')
    max_cells = len(cells)
    target = derive_target(tree, seconds, max_cells)
    data = _written(seconds, target, max_cells, [run_id], 'seconds')
    if not plan_is_balanced(tree, data):
        raise BoundsError(
            f'run {run_id}: no target within max_cells {max_cells} balances '
            f'its weights at the margin; split the heaviest suite or raise '
            f'max_cells by hand')
    _attach_basis(tree, data, max_cells, seconds)
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
        '--message-file', default=None,
        help='write the commit subject the workflow commits the refreshed '
             'file under to this path; it is rendered from the same runs '
             'the file records in `measured_from`, so the two cannot '
             'disagree (a refresh only, since that is the mode the '
             'timed-timings workflow runs)')
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
    # A non-positive sample walks EVERY complete run rather than none,
    # which is a different measurement from the one that was asked for
    # and is a typo rather than an intention.
    if args.runs < 1:
        print(f'refresh_timings: --runs must be at least one, '
              f'not {args.runs}', file=sys.stderr)
        return 1
    try:
        if args.seed:
            message = seed(Path(args.runs_root), out, Path(args.tree))
        else:
            message = refresh(Path(args.runs_root), out, args.runs,
                              Path(args.tree), args.message_file)
    except (RefreshError, BoundsError, PlanError) as error:
        print(f'refresh_timings: {error}', file=sys.stderr)
        return 1
    print(message, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
