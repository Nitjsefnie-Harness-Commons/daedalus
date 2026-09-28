#!/usr/bin/env python3
"""How much of a timings file's weight has to be a real measurement.

The data file is a function of CI's own runs: each suite's weight is
the time it took per measured head round, as a multiple of one fixed
reference workload. A suite the file says nothing about is not measured
at all, and the planner prices it at the median of the suites that
are. That is the right guess for the ordinary case -- a suite added
since the last measurement, a few of them, a few percent of the
tree's weight -- and a very wrong one when the measured suites are not
the tree. The measured set skews heavy, so its median prices the whole
light population at the heavy tail's rate and the total the packer
sees is wrong by a multiple rather than by a percent.

That is exactly what happened here, and nothing went red. A refresh
measured 28 of the tree's 326 suites and wrote the file as the measured
set alone, so the other 298 became estimates priced at the median of
those 28. The surviving weights totalled 25.8 reference-multiples
where the tree really holds 345.2: the packer saw 109.5, derived five
cells where the weights want fourteen, and reported the result as
balanced at `heaviest/median 1.000`, because a total that is wrong by
3.15x is balanced with itself. The planner's own `CELL_WEIGHT_MARGIN`
is computed over that same invented total, so it could not be the
thing that objected; the matrix published, the speed gate measured a
five-cell critical path, and the file was internally consistent and
wrong.

`MAX_ESTIMATED_WEIGHT_SHARE` is the bound, and `coverage_refusal` is
the sentence the planner turns into a `PlanError` before it plans
anything.

Nothing here imports the planner. The share is arithmetic over two
values the caller has already resolved, and keeping it that way is
what lets the chokepoint live in `plan_timed_matrix` -- where the
suite names and the weights come from, and where the refusal's own
exception class is -- without the two modules importing each other.

THE BOUND IS A MAJORITY, and a majority is where the argument turns.
Below it the plan is mostly a description of the tree: the numbers
that decide the cell count and the balance are, in the main, numbers
some run measured. Above it they are a function of one borrowed
median, and a reader of the summary cannot tell which they got -- the
`estimated:` clause names the suites, and the line under it reports a
confident ratio over the same fiction. Half is also the only value
that cannot fire on ordinary drift. Reaching it would take an
unmeasured population weighing as much as the measured one, which on a
tree this size means hundreds of new suites rather than the one or two
a week that appear between two measurements. A tighter bound (a tenth,
say) would refuse a file that has drifted a little and is still worth
publishing; a looser one (0.9, say) would let a file describing a
twentieth of the tree through on the strength of a handful of heavy
measured suites. The remedy the refusal names is the one that fixes
it: re-derive the file from a run that measured the tree, with
`scripts/ci/refresh_timings.py`.

The share is of WEIGHT, not of suite count, and the two disagree
wherever the recorded weights are lopsided -- the normal shape, since
the suites that dominate a matrix are the ones measured hardest. A
tree measured at nine of its ten suites by count can be measured at
95% of its weight, and that file is a good one.

This is a refusal rather than a note because there is nothing a reader
can do with a matrix whose load is mostly invented: unlike a target
the margin forbids, whose numbers are at least the file's own, this
one is a plan of a tree the file has never seen. The margin stays a
note for the same reason it was a note: it is about a number the file
does carry, and the writer is the refresher's chokepoint.
"""

# The share of a plan's total weight that may be an estimate before the
# plan is refused rather than published; see the module docstring.
MAX_ESTIMATED_WEIGHT_SHARE = 0.5


def estimated_share(weights, estimated):
    """The share of the plan's total weight the planner had to invent."""
    if not estimated:
        return 0.0
    total = sum(weights.values())
    if total <= 0:
        return 1.0
    return sum(weights[name] for name in estimated) / total


def coverage_refusal(weights, estimated):
    """The reason this plan must not be published, or None if it may.

    The share, the measured and estimated weights it came from, and the
    command that re-derives a file from a run that measured the tree
    all travel with the refusal, because "your data file is bad" is not
    an action and the three numbers between here and a fixed file are
    the whole diagnosis.
    """
    share = estimated_share(weights, estimated)
    if share <= MAX_ESTIMATED_WEIGHT_SHARE:
        return None
    unmeasured = sum(weights[name] for name in estimated)
    measured = sum(weights.values()) - unmeasured
    return (
        f'{share:.0%} of this plan\'s weight is estimated, over the '
        f'{MAX_ESTIMATED_WEIGHT_SHARE:.0%} bound: the file records '
        f'{len(weights) - len(estimated)} of the tree\'s {len(weights)} '
        f'suites ({measured:.4g} measured against {unmeasured:.4g} '
        f'estimated at the median recorded weight), so the cell count and '
        f'the balance guarantee are computed over a total the file does '
        f'not measure; re-derive it from a run that measured the tree '
        f'with `python3 scripts/ci/refresh_timings.py --runs-root '
        f'<downloaded runs> --out .github/suite-timings.json`')
