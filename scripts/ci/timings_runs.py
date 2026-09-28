#!/usr/bin/env python3
"""Read a downloaded `timed` run: its cell artifacts, and its weights.

The reading half of the timings refresher, split from the writing
half. One downloaded run is a directory of cell artifacts:

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

WHICH RUNS. `select` takes the most recent runs that produced a
COMPLETE set of cell artifacts -- the same cell set as the newest run
that produced any -- regardless of whether the run concluded green. A
run's overall conclusion is not evidence about its durations: `timed`
lists `aggregate` in `needs:`, so one red correctness leg (a flaky
Windows `suites` leg reds often enough) skips the whole measuring
matrix, and selecting only green runs would leave the file unrefreshed
for exactly as long as the staleness the refresher exists to fix goes
unnoticed. A run with a DIFFERENT cell set is a different partition of
the tree, so its numbers are not comparable and it is skipped, and the
report says how far back the search reached and why. A cell directory
with no reference reading is a REFUSAL naming the cell and the suites
it carried: a run that produced the full set of cells and lost one
unit's reading is a broken measurement, and stepping over it here is
the silence the maintainer ruled out. The workflow's walk is the gate
in front of this one, and it reaches the opposite disposition for that
same run -- it steps over a candidate whose cells lack
`reference.json`, and releases the reference cell set while nothing
has been kept yet, so a run from before the cells measured the
reference workload cannot block the search. The two are not in
conflict, and neither is a fallback for the other: the walk narrows
the candidate list, and this refusal is what a TREE the walk did not
narrow -- a hand-built runs root, an operator's own download -- gets
instead of a silent skip. `tests/test_timed_workflow.py` pins the
walk's half by executing it.

`select` also decides which runs are not evidence at all, which is
where a refresh that would narrow the file is stopped; the reasoning
is in its own docstring, and it is the one place in this module that
looks at a bound the artifacts themselves do not carry.
"""
import json
import math
import statistics
from pathlib import Path

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


def select(runs, wanted, max_cells=1, recorded=0):
    """The most recent `wanted` runs with a complete cell set, and a report.

    Completeness is judged against the newest run that produced any cell
    at all: its cell set is the partition these numbers are about. An
    older run with a different set is a different partition; it is
    skipped, and the skip is reported rather than absorbed.

    A COLLAPSED RUN IS NOT THE PARTITION. A run that produced a single
    cell while the file bounds the matrix at more than one, AND that
    measured fewer suites than the file already records, is a matrix
    that collapsed rather than a partial execution of one -- and taking
    it as the reference is half of the defect that hurt, because its
    one-cell set became `expected` and every older, richer run under the
    root was then filed `incomplete` and stepped over. The runs that
    would have carried the tree were on disk and unread. Such a run is
    skipped and REPORTED, and the runs behind it are still selected.

    Both conditions have to hold, and each rules out the case it looks
    like. A one-cell run that measured every suite the file records has
    lost nothing, and the writer carries the file forward unchanged. A
    file bounded at ONE cell is exempt for the same reason a seed is: a
    seed derives its bound from the run that measured it, so a tree
    small enough to be one cell has a file bounded at one, and a
    one-cell measurement of that tree is an ordinary refresh rather than
    a collapse.
    """
    selected = []
    expected = None
    incomplete = []
    degenerate = []
    empty = 0
    for run_id, path in runs:
        cells = {entry.name for entry in path.iterdir()
                 if entry.is_dir() and _head_rounds(entry)}
        if not cells:
            empty += 1
            continue
        weights = references = None
        if len(cells) < 2 and max_cells > 1:
            weights, references = read_run(path, run_id)
            if len(weights) < recorded:
                degenerate.append(run_id)
                continue
        if expected is None:
            expected = cells
        if cells != expected:
            incomplete.append(run_id)
            continue
        if weights is None:
            weights, references = read_run(path, run_id)
        selected.append((run_id, weights, references))
        if len(selected) == wanted:
            break
    report = {'reached': (len(selected) + len(incomplete)
                          + len(degenerate) + empty),
              'incomplete': incomplete, 'degenerate': degenerate,
              'empty': empty}
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
