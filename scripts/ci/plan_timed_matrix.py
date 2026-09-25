#!/usr/bin/env python3
"""Plan the `timed` matrix from a timings file, packing suites by runtime.

The hand-written matrix was a list of suite-name globs per cell plus a
catch-all, and the catch-all grew to 26.4 minutes while the next-longest cell
ran 8.5 -- it alone set the speed gate's critical path, twice (issue 1073,
after issue 536). This planner replaces those lists with a function of a
committed data file: per-suite weights, a target weight per cell, and a
bound, packed longest-first into the lightest cell.

THE TIMINGS FILE IS ITS OWN FILE with its own schema, owned here and read
only through `read_timings()`. A refresher writes what this module reads, so
the field names live in SCHEMA_VERSION, REQUIRED, PROVENANCE_FIELDS, UNITS
and SEED_REASON here, not restated by a writer or a test.
`.github/ci-thresholds.json` was not usable for this: it rejects unknown
top-level keys, and its test pins that.

THE UNIT. A weight is a suite's runtime as a multiple of a fixed reference
workload, so a slower runner scales every weight together and the packing is
unchanged. The first file is seeded from raw seconds of one run, which the
`units` field says (`seconds` before the reference workload exists, then
`reference-multiples`), because relative weights within one run are what the
planner needs either way.

Suites are enumerated by the timing instrument's own rule
(`time_tests.selected` over `tests/test_*.py`), so a cell can never claim a
suite the instrument would not time, and every suite the instrument would
time lands in exactly one cell.

PACKING. `N = ceil(total / target)`, clamped to `[1, max_cells]`; a suite
heavier than the target takes a cell to itself and is reported as a split
candidate. The rest go longest-first (ties by suite name) into the lightest
cell, ties by cell index. Both orders are total, so the same file and tree
always produce the same matrix -- including under a uniform rescale of every
weight, which is what `--scale` simulates.

Two arithmetics the brief does not spell out, both of which the run
summary names when they bite. A suite heavier than the target would
otherwise be placed into a shared cell and overweight it, so the heavy
ones open cells of their own; when they outnumber the cells, the
lightest of them joins the shared cells and the summary says how many
shared. And a count larger than the suites would leave a cell holding
nothing, which the instrument reads as "time every suite" -- so the
count is capped at the suite count, and the matrix never carries an
empty cell.

BALANCE GUARANTEE. `CELL_WEIGHT_MARGIN` is the share a cell may sit
above the median cell the issue asks the planner to guarantee. Measured
on this tree with the weights of run 36070301583 (237 suites, 817.7 s
in total): at a target of 80 the 11 cells come out within 2.8% of the
median, and over every target from 40 to 1000 the packer never left
the heaviest cell more than 2.2% above the median. The one case that
breaks it is a suite heavier than the target, which takes a cell to
itself by the rule above: `test_watcher_budget.py` at 76.2 s sits
1.34x a 57 s median at a target of 60. The margin is `0.35`, the round
number at or above that, because the heavy-suite rule can cost up to
one suite per cell -- 1.0 / median, which is 1.78 at today's weights
and only falls as the target rises. A margin below 0.35 would make
the guarantee a false alarm on today's tree; the guard's other job is
catching a packer that stops packing, and the shortest-first order
reaches 2.1x on the same weights, well past it. Re-derive with
`python3 scripts/ci/plan_timed_matrix.py --summary` against the file.

The default file is `.github/suite-timings.json`, the `.github/` sibling of
`ci-thresholds.json`, which is where this repository keeps the data its CI
reads. Task 2's refresher writes it; the planner's own suite reads a temp
file and never the real one, so it is absent until that lands.
"""
import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    from time_tests import selected
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.time_tests import selected

# The data file's schema, as one authority. SCHEMA_VERSION rises when the
# meaning of an existing field changes; REQUIRED, UNITS, PROVENANCE_FIELDS
# and SEED_REASON are what a writer must supply and what this module
# refuses to invent an interpretation for.
SCHEMA_VERSION = 1
REQUIRED = ('schema_version', 'target_cell_weight', 'max_cells', 'units',
            'suite_weights')
PROVENANCE_FIELDS = ('measured_from', 'reference_normalized', 'runs')
# The seed is raw seconds from one run and says so; once the reference
# workload exists the refresher replaces it with multiples of it.
UNITS = ('seconds', 'reference-multiples')
SEED_REASON = 'seeded'
# Estimate for a suite the file records nothing about: the median of the
# recorded weights, or this when the file records none. A number needs a
# basis, and the median of the tree's own distribution is that basis.
DEFAULT_ESTIMATE = 1.0
# The share a cell may sit above the median cell. The docstring carries
# the measurement the number rests on.
CELL_WEIGHT_MARGIN = 0.35
# Cell names appear in check-run names, so they stay short and match
# `[a-z0-9][a-z0-9-]*`.
_CELL_PREFIX = 'cell-'


class PlanError(Exception):
    """A refusal with a reason the caller can act on."""


@dataclass
class Cell:
    """One matrix include entry: its name, its suites, and its weight."""

    name: str
    suites: list
    weight: float

    def include(self):
        """The matrix entry, or None when the cell holds no suite.

        An empty cell runs a matrix job that times nothing, and an empty
        `--only` selection means the instrument times the WHOLE tree
        again -- the one shape where a cell silently measures something
        other than its slice. A cell that would be empty is never emitted
        as a matrix entry; the planner drops it after reporting it.
        """
        if not self.suites:
            return None
        return {'group': self.name, 'suites': ' '.join(self.suites)}


@dataclass
class Plan:
    """What one planning run decided: the cells, the matrix, the notes."""

    cells: list
    total: float
    target: float
    max_cells: int
    estimated: list
    split_candidates: list
    stale: list
    notes: list
    matrix: list

    def summary(self):
        """Markdown for `$GITHUB_STEP_SUMMARY`: the numbers and the names."""
        loads = sorted(cell.weight for cell in self.cells)
        median = statistics.median(loads) if loads else 0.0
        ratio = max(loads) / median if median > 0 else 0.0
        lines = [
            '## Timed matrix plan', '',
            f'cells {len(self.cells)} (max {self.max_cells}, target '
            f'{self.target:g}); total {self.total:g}; median cell '
            f'{median:g}; heaviest/median {ratio:.3f} against a margin of '
            f'{CELL_WEIGHT_MARGIN:g}; suites '
            f'{sum(len(c.suites) for c in self.cells)}',
            '', '| cell | suites | planned weight |', '| --- | --- | --- |',
        ]
        lines.extend(f'| {cell.name} | {len(cell.suites)} | {cell.weight:g} |'
                     for cell in self.cells)
        for label, names in (('split candidate', self.split_candidates),
                             ('estimated', self.estimated),
                             ('stale', self.stale)):
            if names:
                lines.extend(['', f'{label}: ' + ', '.join(names)])
        for note in self.notes:
            lines.extend(['', f'note: {note}'])
        return '\n'.join(lines) + '\n'


def read_timings(path):
    """Load and validate the timings data file, or refuse with a reason.

    Unknown top-level fields are refused, the way `ci-thresholds.json`
    treats its own keys: the schema is the module's, and a field nothing
    reads is a field nothing can hold honest.
    """
    if not path.exists():
        raise PlanError(
            f'no timings data at {path}; seed it from a recent successful '
            '`tests` run on main (see scripts/ci/refresh_timings.py)')
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as error:
        raise PlanError(f'cannot read {path}: {error}') from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PlanError(f'invalid timings JSON at {path}: {error}') from None
    if not isinstance(data, dict):
        raise PlanError(f'timings data at {path} must be an object')
    for name in REQUIRED:
        if name not in data:
            raise PlanError(f'missing field: {name} (in {path})')
    for name in sorted(set(data) - set(REQUIRED) - set(PROVENANCE_FIELDS)
                       - {SEED_REASON}):
        raise PlanError(f'unknown field: {name} (in {path})')
    if data['schema_version'] != SCHEMA_VERSION:
        raise PlanError(f'schema_version {data["schema_version"]!r} is not '
                        f'{SCHEMA_VERSION} (in {path})')
    if data['units'] not in UNITS:
        raise PlanError(f'units {data["units"]!r} must be one of {UNITS} '
                        f'(in {path})')
    target = _number(data['target_cell_weight'], 'target_cell_weight')
    if target <= 0:
        raise PlanError('target_cell_weight must be above zero')
    if isinstance(data['max_cells'], bool) or not isinstance(
            data['max_cells'], int) or data['max_cells'] < 1:
        raise PlanError('max_cells must be an integer of at least one')
    weights = data['suite_weights']
    if not isinstance(weights, dict):
        raise PlanError('suite_weights must be an object mapping suite name '
                        'to weight')
    for name, weight in weights.items():
        if not isinstance(name, str) or not (name.startswith('test_')
                                             and name.endswith('.py')):
            raise PlanError(f'suite_weights key {name!r} is not a suite file '
                            'name')
        if _number(weight, f'suite_weights[{name}]') <= 0:
            raise PlanError(f'suite_weights[{name}] must be above zero')
    return data


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError(f'{name} must be a JSON number')
    number = float(value)
    if not math.isfinite(number):
        raise PlanError(f'{name} must be finite')
    return number


def suite_names(tree):
    """The tree's suite files, by the timing instrument's own rule."""
    found = sorted((tree / 'tests').glob('test_*.py'))
    names = [suite.name for suite in found if selected(suite.name, None, ())]
    if not names:
        raise PlanError(f'no suites found under {tree}')
    return names


def _resolve(recorded, names, scale):
    """Every tree suite's weight, and which names were estimated or stale.

    A suite the file does not record is estimated at the median of the
    recorded weights (the stated constant when the file records none) and
    reported; a weight for a suite the tree no longer holds is stale -- a
    deleted suite -- and is reported and dropped, so it can never consume
    a cell.
    """
    recorded = {name: _number(weight, name) * scale
                for name, weight in recorded.items()}
    known = [recorded[name] for name in names if name in recorded]
    estimate = statistics.median(known) if known else DEFAULT_ESTIMATE
    weights = {}
    estimated = []
    for name in names:
        if name in recorded:
            weights[name] = recorded[name]
        else:
            weights[name] = estimate
            estimated.append(name)
    stale = sorted(set(recorded) - set(names))
    return weights, estimated, stale


def _pack(weights, names, target, max_cells):
    """Longest-first into the lightest cell; the heavy take cells alone.

    Both orderings are total (weight, then suite name; planned weight,
    then cell index), so the same file and tree always produce the same
    matrix. A uniform rescale of every weight and the target leaves both
    orders unchanged, which is the property a slower or faster runner
    depends on.
    """
    total = sum(weights[name] for name in names)
    notes = []
    derived = max(1, math.ceil(total / target))
    count = min(max_cells, derived, max(1, len(names)))
    if derived > max_cells:
        notes.append(
            f'cell count clamped to max_cells {max_cells}: the weights would '
            f'need {derived} cells for a target of {target:g}')
    order = sorted(names, key=lambda name: (-weights[name], name))
    heavy = [name for name in order if weights[name] > target]
    if len(heavy) > count:
        # More suites are heavier than the target than the whole tree
        # can occupy; the least of them joins the shared cells, where
        # the heaviest one placed happens to be, and the summary names
        # it as a split candidate like every other.
        notes.append(
            f'{len(heavy)} suites are heavier than the target and only '
            f'{count} cells exist; the lightest of them shares a cell')
    shared = max(1, count - len(heavy))
    if shared > len(names) - len(heavy):
        # More cells than suites: the arithmetic would make a cell that
        # times nothing, which is a cell the instrument refuses.
        shared = len(names) - len(heavy)
        count = len(heavy) + shared
        notes.append(
            f'cell count reduced to {count}: the weights asked for more '
            'cells than there are suites to fill them with')
    rest = [name for name in order if name not in heavy]
    if shared > len(rest):
        # More cells than suites (every suite is a split candidate): each
        # cell takes the heaviest suite left.
        heavy = heavy[:max(0, shared)]
        rest = [name for name in order if name not in heavy]
    cells = [[name] for name in heavy] + [[name] for name in rest[:shared]]
    loads = ([weights[name] for name in heavy]
             + [weights[name] for name in rest[:shared]])
    for name in rest[shared:]:
        index = min(range(shared), key=lambda i: (loads[len(heavy) + i], i))
        slot = len(heavy) + index
        cells[slot].append(name)
        loads[slot] += weights[name]
    return ([Cell(_cell_name(index), cell, loads[index])
             for index, cell in enumerate(cells)], heavy, notes)


def _cell_name(index):
    return f'{_CELL_PREFIX}{index + 1:02d}'


def plan(tree, timings, scale=1.0):
    """Pack the tree's suites into cells by weight. Never returns None."""
    names = suite_names(tree)
    weights, estimated, stale = _resolve(
        timings['suite_weights'], names, scale)
    target = _number(
        timings['target_cell_weight'], 'target_cell_weight') * scale
    cells, heavy, notes = _pack(
        weights, names, target, int(timings['max_cells']))
    if stale:
        notes.append(
            'stale weights dropped (their suites are gone from the tree): '
            + ', '.join(stale))
    filled = [cell for cell in cells if cell.suites]
    if len(filled) != len(cells):
        notes.append(
            f'dropped {len(cells) - len(filled)} empty cells: a cell with no '
            'suite times the whole tree or nothing')
    return Plan(cells=cells, total=sum(weights.values()), target=target,
                max_cells=int(timings['max_cells']), estimated=estimated,
                split_candidates=list(heavy), stale=stale, notes=notes,
                matrix=[cell.include() for cell in filled])


def _parser():
    parser = argparse.ArgumentParser(
        description='Plan the timed matrix by runtime.')
    parser.add_argument('--tree', default=str(_REPO_ROOT),
                        help='checkout whose tests/ is planned')
    parser.add_argument(
        '--timings', default=str(_REPO_ROOT / '.github'
                                 / 'suite-timings.json'),
        help='timings data file')
    parser.add_argument(
        '--out', default=None,
        help='write the matrix JSON to this file instead of stdout')
    parser.add_argument(
        '--summary', action='store_true',
        help='print the step summary to stderr as well')
    parser.add_argument(
        '--summary-file', default=None,
        help='append the step summary to this Markdown file')
    parser.add_argument(
        '--scale', type=float, default=1.0,
        help='multiply every recorded weight AND the target by this factor, '
             'as a runner-speed simulation')
    return parser


_LAST = None


def last_plan():
    """The last Plan made in this process, for a caller that wants it."""
    return _LAST


def main(argv=None):
    """Print the matrix JSON; return 0, or 1 after a named refusal."""
    global _LAST  # pylint: disable=global-statement
    args = _parser().parse_args(argv)
    try:
        decision = plan(Path(args.tree), read_timings(Path(args.timings)),
                        args.scale)
    except PlanError as error:
        print(f'plan_timed_matrix: {error}', file=sys.stderr)
        return 1
    _LAST = decision
    payload = json.dumps(decision.matrix)
    if args.out:
        Path(args.out).write_text(payload, encoding='utf-8')
    else:
        print(payload)
    if args.summary:
        print(decision.summary(), file=sys.stderr)
    if args.summary_file:
        with open(args.summary_file, 'a', encoding='utf-8') as handle:
            handle.write(decision.summary())
    return 0


if __name__ == '__main__':
    sys.exit(main())
