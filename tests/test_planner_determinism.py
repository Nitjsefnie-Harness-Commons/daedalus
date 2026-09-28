#!/usr/bin/env python3
"""The planner's own two contracts: the packing is a FUNCTION, and a
missing data file is a named refusal.

Both lived in `test_timed_planner.py` until commit `a8a8b693` deleted
them, silently: that commit grew two fixtures the coverage bound had
made unpublishable, its message says "The assertions are unchanged", and
it is silent about these.

`test_the_packing_is_deterministic` pins the docstring's load-bearing
"Both orders are total, so the same file and tree always produce the
same matrix" -- which is what lets one pull request's matrix be compared
with the next one's without a job going flaky on a dictionary's
iteration order. `test_a_missing_timings_file_is_a_named_refusal` is the
first reader of the remedy string every refusal in this guard carries,
and its reader is a checkout that has no data file yet: the state
between a clone and the first seed. While it was gone that string had no
reference anywhere in `tests/` or `scripts/`.

They are their own file because the planner suite reached the 700-line
ceiling these modules are held to, and a ceiling is not raised for a
control. They carry NO fixtures, which is what lets them live apart: the
planner suite's `_tree` and `_data` are what a copy of its controls would
have to duplicate, and `pylint`'s duplicate-code finding is what says a
copy is the wrong answer. So both drive the SHIPPED file over the REAL
tree instead -- a stronger claim than a twenty-suite fixture made, since
the property is about the file the repository actually publishes, and
the file's own dict order is the order that would have to be wrong.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def test_the_packing_is_deterministic(tmp):
    """The same file twice, and the file's own order permuted: one matrix.

    The load-bearing sentence in `plan_timed_matrix`'s docstring is
    "Both orders are total, so the same file and tree always produce
    the same matrix -- including under a uniform rescale of every
    weight", and it is what lets a pull request's matrix be compared
    against the next one's without anyone's job going flaky on a
    dictionary's iteration order. Two things have to hold. The first is
    that the plan is a FUNCTION of the data rather than of the run: the
    same file planned twice is the same matrix. The second is that JSON
    object order is not part of the data, so a file whose
    `suite_weights` are written in another order must plan to the same
    matrix.

    Driven over the SHIPPED file and the REAL tree rather than a
    fixture, for the reason the module docstring gives. The permutation
    is a reversal of the file's own order, which is the one a rewriter
    or a merge conflict would produce; a random shuffle would be a
    second fixture whose order nothing else shares.
    """
    planner = _planner()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    first = planner.plan(ROOT, data).matrix
    second = planner.plan(ROOT, data).matrix
    assert first == second
    assert first, 'the shipped file planned no suites at all'
    permuted = dict(data, suite_weights=dict(
        reversed(list(data['suite_weights'].items()))))
    assert planner.plan(ROOT, permuted).matrix == first, (
        'the same weights in another order planned a different matrix')
    # And the tie-breaks the docstring names, in the direction it names:
    # equal weights break by suite name, so a rescale that collapses
    # every weight onto one value is the shape where the tie-break is
    # the only thing deciding the matrix at all, and a total order has
    # to answer it the same way every time.
    flat = dict(data, suite_weights={
        name: 1.0 for name in data['suite_weights']})
    assert planner.plan(ROOT, flat).matrix == planner.plan(ROOT, flat).matrix


def test_a_missing_timings_file_is_a_named_refusal(tmp):
    """No data file yet is a refusal with a remedy, not a traceback.

    The remedy string `refresh_timings.py --runs-root ...` travels with
    every refusal this module's guard produces, and its FIRST reader is
    a repository that has no data file yet -- which is the state
    between a checkout and the first seed. Nothing else in the tree
    exercises that path, so a reader that raised `FileNotFoundError`
    instead of naming the file and the command to write it would be
    green everywhere else, and the first person to meet it would meet a
    traceback.

    The tree is this repository, so nothing about the refusal depends on
    a fixture: the command answers from the data file, which is not
    there, before it asks the tree anything.
    """
    planner = _planner()
    absent = Path(tmp) / 'no-such-timings-file.json'
    assert not absent.exists(), absent
    err = io.StringIO()
    out = io.StringIO()
    argv = ['--tree', str(ROOT), '--timings', str(absent)]
    saved_out, sys.stdout = sys.stdout, out
    try:
        with contextlib.redirect_stderr(err):
            code = planner.main(argv)
    finally:
        sys.stdout = saved_out
    said = err.getvalue()
    assert code == 1, (code, said, out.getvalue())
    assert said.startswith('plan_timed_matrix:'), said
    assert 'no timings data at' in said, said
    assert 'refresh_timings.py' in said, said
    assert 'Traceback' not in said, said
    assert out.getvalue() == '', out.getvalue()


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='determinism_')


if __name__ == '__main__':
    raise SystemExit(main())
