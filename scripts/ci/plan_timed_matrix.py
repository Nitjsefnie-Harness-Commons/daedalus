#!/usr/bin/env python3
"""Plan the `timed` matrix from a timings file, packing suites by runtime.

The hand-written matrix was suite-name globs per cell plus a catch-all
that grew to 26.4 minutes while the next-longest cell ran 8.5, and alone
set the speed gate's critical path (issue 1073, after issue 536). This
planner replaces those lists with a function of a committed data file:
per-suite weights, a target weight per cell and a bound, packed
longest-first into the lightest cell.

THE DATA FILE, `.github/suite-timings.json`, is its own file with its own
schema, owned here and read only through `read_timings()`, and every
field is required and type-checked, provenance included. A weight is a
suite's runtime as a multiple of a fixed reference workload, so a slower
runner scales every weight together and the packing is unchanged. The
first file is seeded from raw seconds of one run, which `units` says
(`seconds` before the reference workload exists, then
`reference-multiples`); `units` is the only place that fact is recorded,
because a second spelling of it is a second thing to disagree with
itself. `measured_from` names the run the numbers came from and `runs`
says how many, so a weight with no run behind it is a number no refresh
can check. The one optional field, `basis`, is the PROSE the refresher
writes beside the two policy numbers on every write, seed or refresh
(which run, which units, why the target and the cell bound, and which
tree suites the measurements do not cover); its content is rebuilt from
the numbers of each write, and this reader does not type-check it
because the sentence is for a human.

Suites are enumerated from the TRACKED tree, through the timing
instrument's own matcher (`time_tests.selected`, called with no globs,
where it admits every name), so the planner and the instrument run the
same rule today; the call is the seam, not a filter. The set comes from
`git ls-files` rather than a directory listing, for the same reason
`scripts/ci/size_baseline.py` reads the index: a file no commit
contains is not a suite of this repository, and a scratch
`tests/test_*.py` a neighbouring run left in the working tree would
otherwise be allocated a cell and named in the data file's basis
sentence. A missing `git`, or a tree that is not a checkout, is a
refusal with a named reason rather than an empty plan: an empty plan
times nothing.

PACKING. `N = ceil(total / target)`, capped at `max_cells` and at the
number of suites; a suite heavier than the target takes a cell to itself
and is reported as a split candidate. The rest go longest-first (ties by
suite name) into the lightest cell, ties by cell index. Both orders are
total, so the same file and tree always produce the same matrix --
including under a uniform rescale of every weight, which is what
`--scale` simulates.

Four arithmetics the brief does not spell out, each named by the run
summary when it bites. A suite heavier than the target would otherwise
be placed into a shared cell and overweight it, so the heavy ones open
cells of their own; when they outnumber the cells, the least of them
joins the lightest cell, because `max_cells` is the bound on what runs
at once and the matrix never carries more cells than the bound allows --
a heavy suite that gets no cell of its own is still a named split
candidate, and the summary says which ones. A count larger than the
suites would leave a cell holding nothing, which the instrument reads
as "time every suite", so the count is capped at the suite count. And
when the heaviest cell sits more than the margin above the median, the
summary names the smallest target that would satisfy it.

BALANCE GUARANTEE. `CELL_WEIGHT_MARGIN` is the share a cell may sit
above the median cell the issue asks the planner to guarantee. Measured
on this tree with the per-suite head-round medians of run 36070301583
(237 suites, 816.9 s in total, heavy tail led by
`test_watcher_budget.py` at 76.2 s). There are two numbers, and the
margin has to cover the larger. The SHARED cells -- every cell packing
more than one suite -- come out within 0.3% of their own median at
every target from 20 to 1000, so the packer is balanced; that is the
packer's own behaviour. The ALL-cell ratio is `heaviest / median`, and
it is 1.000 above a target of 85 (no suite is over the target, every
cell is shared), 1.03 at 76, 1.34 at 60 and 2.06 at 40, because a
suite heavier than the target is alone in its cell by the rule above
and the median falls as the target does. The margin is `0.35`: the
round number at or above the ratio at 60 on that run (1.34x a 57 s
median). The SHIPPED file's target is 65, not 60, and that is measured
rather than assumed: on run 36054336022's weights -- the seed, 230
recorded suites totalling 804.4 s, heavy tail `test_watcher_budget.py`
at 76.9 s -- the ALL-cell ratio is 1.359 at 60, over the margin,
1.255 at 65 (13 cells, a 61.2 s median) and 1.151 at 70. So the
target is the smallest five-second step the guarantee admits, derived
with the planner itself (`scripts/ci/timings_bounds.derive_target`) and
verified against the margin before every write by
`scripts/ci/refresh_timings.py`, the one owner of the data file: this
module NAMES a target the margin forbids, in the summary and in the
exit-0 matrix it still prints, and the refresher refuses to write
one. Below a target the margin admits the guarantee does not hold, and
the summary says so, naming the smallest target that would
(heaviest / 1.35); the alternative is a lower median, which is what
splitting `test_watcher_budget.py` -- a split candidate at the seeded
target -- would give. The guard's other job is catching a packer that
stops packing, and the shortest-first order reaches 2.1x on the same
weights, well past it. Re-derive the two numbers with `python3
scripts/ci/plan_timed_matrix.py --summary` against the data file at
each target.
"""
import argparse
import json
import math
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    from time_tests import selected
except ImportError:  # pragma: no cover - the script-directory import path
    from scripts.ci.time_tests import selected

# The data file's schema, as one authority for the writer to import.
SCHEMA_VERSION = 2
REQUIRED = ('schema_version', 'target_cell_weight', 'max_cells', 'units',
            'suite_weights', 'measured_from', 'runs')
PROVENANCE_FIELDS = ('measured_from', 'runs')
UNITS = ('seconds', 'reference-multiples')
# The optional basis field every write carries; see the module docstring.
BASIS_FIELD = 'basis'
# A suite the file records nothing about is estimated at the median of
# the recorded weights, or at this when the file records none.
DEFAULT_ESTIMATE = 1.0
CELL_WEIGHT_MARGIN = 0.35
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
        """The matrix entry.

        Every cell carries a suite and the matrix is exactly these
        entries, so nothing here can be empty: an empty cell times
        nothing and an empty `--only` times the WHOLE tree, and the
        packer's openers are the reason neither shape exists.
        """
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
            f'cells {len(self.cells)} (max_cells bound {self.max_cells}, '
            f'target {self.target:g}); total {self.total:g}; median cell '
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
    """Load and validate the timings data file, or refuse with a reason."""
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
    for name in sorted(set(data) - set(REQUIRED) - {BASIS_FIELD}):
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
    if not isinstance(data['measured_from'], str) \
            or not data['measured_from'].strip():
        raise PlanError('measured_from must be a non-empty string naming '
                        'the run the numbers came from')
    if isinstance(data['runs'], bool) or not isinstance(data['runs'], int) \
            or data['runs'] < 1:
        raise PlanError('runs must be an integer of at least one')
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
    """The tree's TRACKED suite files, by the instrument's own matcher.

    Read from git's index (`git ls-files`), not from a directory listing:
    a file no commit contains is not a suite of this repository, and a
    scratch `tests/test_*.py` left in the working tree by another run
    must not be planned into a cell or named in the data file's basis.
    `scripts/ci/size_baseline.py` reads the same way. A missing `git`,
    or a tree that is not a checkout, is a refusal with a named reason
    rather than an empty plan, because an empty plan times nothing.
    """
    try:
        listed = subprocess.run(
            ['git', '-C', str(tree), 'ls-files', '-z', '--', 'tests/'],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise PlanError(
            f'cannot list tracked suites under {tree}: {error}; the planner '
            'needs a git checkout') from None
    if listed.returncode != 0:
        detail = listed.stderr.decode('utf-8', 'replace').strip()
        raise PlanError(
            f'cannot list tracked suites under {tree}: git ls-files failed '
            f'({detail or "no detail"}); the planner needs a git checkout')
    names = []
    for raw in listed.stdout.split(b'\0'):
        if not raw:
            continue
        parts = raw.decode('utf-8', 'surrogateescape').split('/')
        if len(parts) != 2 or parts[0] != 'tests':
            continue
        name = parts[1]
        if (name.startswith('test_') and name.endswith('.py')
                and (tree / 'tests' / name).is_file()
                and selected(name, None, ())):
            names.append(name)
    names.sort()
    if not names:
        raise PlanError(f'no suites found under {tree}')
    return names


def _resolve(recorded, names, scale):
    """Every tree suite's weight, and which names were estimated or stale.

    A weight for a suite the tree no longer holds is stale -- a deleted
    suite -- and is reported and dropped, so it can never consume a cell.
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


def _open_cells(weights, order, target, max_cells, notes):
    """The cell count, and which suites open cells of their own.

    A suite heavier than the target opens a cell unless the bound leaves
    none to spare, in which case the least of them joins the lightest
    cell: a matrix with more cells than the bound is not the bound.
    """
    total = sum(weights[name] for name in order)
    derived = max(1, math.ceil(total / target))
    count = max(1, min(max_cells, derived, len(order)))
    if derived > max_cells:
        notes.append(
            f'cell count clamped to max_cells {max_cells}: the weights would '
            f'need {derived} cells for a target of {target:g}')
    if len(order) < derived:
        notes.append(
            f'cell count reduced to {len(order)}: the weights asked for more '
            'cells than there are suites to fill them with')
    heavy = [name for name in order if weights[name] > target]
    given_up = []
    if len(heavy) > max(0, count - 1):
        given_up = heavy[max(0, count - 1):]
        notes.append(
            f'{len(heavy)} suites are heavier than the target and only '
            f'{count} cells exist; ' + ', '.join(given_up)
            + ' join the other cells as split candidates without cells of '
            'their own, because max_cells is the bound on what runs at '
            'once')
    return count, heavy, given_up


def _pack(weights, names, target, max_cells):
    notes = []
    order = sorted(names, key=lambda name: (-weights[name], name))
    count, heavy, given_up = _open_cells(
        weights, order, target, max_cells, notes)
    alone = [name for name in heavy if name not in given_up]
    rest = [name for name in order if name not in heavy]
    # The heavy suites that kept a cell hold it alone; the rest open the
    # shared cells, which is what is left of the count. A bound of one
    # with every suite heavy leaves no cell to place into, so the first
    # cell opens on the heaviest suite rather than the placement choosing
    # among none.
    shared = min(max(0, count - len(alone)), len(rest))
    openers = alone + rest[:shared]
    if not openers:
        openers = [order[0]]
    cells = [[name] for name in openers]
    loads = [weights[name] for name in openers]
    for name in rest[shared:] + given_up:
        if name in openers:
            continue
        index = min(range(len(cells)), key=lambda i: (loads[i], i))
        cells[index].append(name)
        loads[index] += weights[name]
    return ([Cell(_cell_name(index), cell, loads[index])
             for index, cell in enumerate(cells)], heavy, notes)


def _cell_name(index):
    return f'{_CELL_PREFIX}{index + 1:02d}'


def plan(tree, timings, scale=1.0):
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
    loads = [cell.weight for cell in cells]
    median = statistics.median(loads) if loads else 0.0
    if loads and max(loads) > median * (1 + CELL_WEIGHT_MARGIN):
        floor = max(loads) / (1 + CELL_WEIGHT_MARGIN)
        notes.append(
            f'heaviest cell is {max(loads) / median:.3f}x the median, over '
            f'the {CELL_WEIGHT_MARGIN:g} margin: the target of {target:g} is '
            f'below the {floor:.4g} this cell set would need, or the '
            'heaviest suite needs splitting first')
    return Plan(cells=cells, total=sum(weights.values()), target=target,
                max_cells=int(timings['max_cells']), estimated=estimated,
                split_candidates=list(heavy), stale=stale, notes=notes,
                matrix=[cell.include() for cell in cells])


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
        # No trailing newline: the value is written straight into a
        # `$GITHUB_OUTPUT` line as `matrix=$json`, and a newline there
        # would end the line before the value does.
        sys.stdout.write(payload)
    if args.summary:
        print(decision.summary(), file=sys.stderr)
    if args.summary_file:
        with open(args.summary_file, 'a', encoding='utf-8') as handle:
            handle.write(decision.summary())
    return 0


if __name__ == '__main__':
    sys.exit(main())
