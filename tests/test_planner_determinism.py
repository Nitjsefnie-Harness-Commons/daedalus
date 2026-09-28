#!/usr/bin/env python3
"""The planner's own two contracts: the packing is a FUNCTION, and a
missing data file is a named refusal.

Both lived in `test_timed_planner.py` until commit `a8a8b693` deleted
them, silently: that commit grew two fixtures the coverage bound had
made unpublishable, its message says "The assertions are unchanged", and
it is silent about these. `test_the_packing_is_deterministic` pinned the
docstring's load-bearing "Both orders are total, so the same file and
tree always produce the same matrix" -- which is what lets one pull
request's matrix be compared with the next one's without a job going
flaky on a dictionary's iteration order. `test_a_missing_timings_file_is_
a_named_refusal` is the first reader of the remedy string every refusal
in this guard carries, and its reader is a checkout that has no data
file yet: the state between a clone and the first seed. While it was
gone that string had no reference anywhere in `tests/` or `scripts/`.

They are their own file because the planner suite reached the 700-line
ceiling these modules are held to, and a ceiling is not raised for a
control. The fixtures are copies of that suite's, deliberately: a shared
helper module one call site away is the same rule written twice, and two
files' worth of `_data` and `_tree` is cheaper than one helper both of
them have to agree about.
"""
import contextlib
import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index  # noqa: E402


def _planner():
    return _util.load(
        ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
        'plan_timed_matrix')


def _tree(tmp, suites):
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True, exist_ok=True)
    for name in suites:
        (tree / 'tests' / name).write_text('pass\n', encoding='utf-8')
    # The planner enumerates the TRACKED tree, so a fixture tree is a
    # git checkout with these files in its index; `git ls-files` reads
    # the index, so no commit is made or needed.
    git_index(tree, 'init', '-q')
    git_index(tree, 'add', '--', 'tests/')
    return tree


def _data(weights, target=10.0, max_cells=30, **fields):
    data = {
        'schema_version': _planner().SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': 'seconds',
        'measured_from': 'tests run 1',
        'runs': 1,
        'suite_weights': weights,
    }
    data.update(fields)
    return data


def _write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


def _run(planner, args, expect=0):
    """Run main() with stdout captured; return (exit code, stdout)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = planner.main(args)
    assert code == expect, (code, buffer.getvalue())
    return buffer.getvalue()


def _plan(tmp, suites, data, *flags, expect=0):
    """Run the CLI over a temp tree and file; return the Plan it made."""
    tree = _tree(tmp, suites)
    path = _write(Path(tmp) / 'timings.json', data)
    planner = _planner()
    out = _run(planner, ['--tree', str(tree), '--timings', str(path),
                         *flags], expect)
    return planner.last_plan(), out


def _captured_stderr(planner, args):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        planner.main(args)
    return buffer.getvalue()




def test_the_packing_is_deterministic(tmp):
    """The same file twice, and the file's own order permuted: one matrix.

    The load-bearing sentence in `plan_timed_matrix`'s docstring is
    "Both orders are total, so the same file and tree always produce
    the same matrix -- including under a uniform rescale of every
    weight", and it is what lets a pull request's matrix be compared
    against the next one's without anyone's job going flaky on a
    dictionary's iteration order. Two things have to hold for it, and
    the first is that the plan is a FUNCTION of the data rather than of
    the run: the same file planned twice must be the same matrix. The
    second is that JSON object order is not part of the data, so a
    file whose `suite_weights` are written in another order must plan
    to the same matrix.

    Both were here, and both were deleted by commit `a8a8b693` growing
    two fixtures the suite bound made unpublishable. That commit's
    message says "The assertions are unchanged" and is silent about
    them; the two it grew are in this same file, and nothing else was.
    A mutation that broke the total order would have gone through six
    review rounds with this control gone and the other reviewer's
    re-run against HEAD the only thing that found it.
    """
    suites = [f'test_{chr(ord("a") + i)}.py' for i in range(8)]
    weights = {name: 1.0 + (index % 3) for index, name in enumerate(suites)}
    first, _out = _plan(tmp, suites, _data(weights))
    second, _out = _plan(tmp, suites, _data(weights))
    assert first.matrix == second.matrix
    shuffled = list(weights.items())
    random.Random(7).shuffle(shuffled)
    permuted, _out = _plan(tmp, suites, _data(dict(shuffled)))
    assert permuted.matrix == first.matrix, permuted.matrix
    # And the two ties the docstring names, in the same direction: the
    # packer breaks equal weights by suite name and equal cells by cell
    # index, so a file of eight equal weights is the shape where both
    # tie-breaks are the only thing deciding the matrix at all.
    flat = {name: 2.0 for name in suites}
    one, _out = _plan(tmp, suites, _data(flat))
    twice, _out = _plan(tmp, suites, _data(dict(reversed(list(
        flat.items())))))
    assert one.matrix == twice.matrix, one.matrix


def test_a_missing_timings_file_is_a_named_refusal(tmp):
    """No data file yet is a refusal with a remedy, not a traceback.

    The remedy string `refresh_timings.py --runs-root ...` travels with
    every refusal this module's guard produces, and its FIRST reader is
    a repository that has no data file yet -- which is the state
    between a checkout and the first seed. Nothing else in the tree
    exercises that path, so a reader that raised `FileNotFoundError`
    instead of naming the file and the command to write it would be
    green everywhere else, and the first person to meet it would meet
    a traceback.

    Also deleted by `a8a8b693`, silently, for the same reason as the
    control above. Its remedy string has zero references anywhere in
    `tests/` or `scripts/` while it was gone.
    """
    tree = _tree(tmp, ['test_a.py'])
    planner = _planner()
    stderr = _captured_stderr(planner, [
        '--tree', str(tree), '--timings', str(Path(tmp) / 'absent.json')])
    assert stderr.startswith('plan_timed_matrix:'), stderr
    assert 'no timings data at' in stderr, stderr
    assert 'refresh_timings.py' in stderr, stderr
    _run(planner, [
        '--tree', str(tree), '--timings', str(Path(tmp) / 'absent.json')], 1)


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='determinism_')


if __name__ == '__main__':
    raise SystemExit(main())
