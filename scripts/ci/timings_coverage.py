#!/usr/bin/env python3
"""How much of a timings file has to be a real measurement.

The data file is a function of CI's own runs: each suite's weight is
the time it took per measured head round, as a multiple of one fixed
reference workload. A suite the file says nothing about is not measured
at all, and the planner prices it at the median of the suites that
are. That is the right guess for the ordinary case -- a suite added
since the last measurement, a few of them, a few percent of the
tree's weight -- and a very wrong one when the measured suites are not
the tree. Then the median prices a whole population at one sample's
rate and the total the packer sees is wrong by a multiple rather than
by a percent.

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

TWO CONDITIONS, and the second one exists because the first one can be
defeated by the shape of the sample. `MAX_ESTIMATED_WEIGHT_SHARE` is
the share of the plan's total weight that may be an estimate;
`MAX_ESTIMATED_SUITE_SHARE` is the share of its SUITES. They are not
restatements. A file recording the twenty-nine lightest suites of this
tree plus its heaviest is 91% of the plan's suites -- and 31% of its
weight, because the heaviest weight is most of the denominator the
weight share is divided by. That file passes the weight bound and
plans ONE cell for 327 suites, over a total 15.5 times too small: a
heavier error than the 3.15x the guard exists to stop, and reached by
a shape of recorded set, not by a shape of tree. The suite share
cannot be moved by any weight in the file.

They also refuse different files, which is the other half of why both
are here. Ten suites with three recorded at 1.0, 1.0 and 90.0: 7% of
the weight is estimated, 70% of the suites, so only the count refuses.
Nine suites with five recorded at 0.001, 0.001, 5, 5, 5: 57% of the
weight, 44% of the suites, so only the weight refuses. A file can be a
quarter of the tree by count and a fiction by weight, or four fifths
of the tree by count and a fine measurement by weight.

WHY A MAJORITY, twice. Below either bound the plan is mostly a
description of the tree: the numbers that decide the cell count and
the balance are, in the main, numbers some run measured, and a reader
of the summary can tell which they got. Above it they are a function
of one borrowed number applied to a population -- and the count bound
is the one that names the population, because the packer cannot tell
an estimated suite from a measured one and prices both. Half is also
the only value that cannot fire on ordinary drift: reaching it would
take an unmeasured population weighing as much as the measured one, or
half the tree's suites appearing between two measurements, which on a
tree this size means hundreds of new suites rather than the one or two
a week that appear. A tighter bound (a tenth, say) would refuse a file
that has drifted a little and is still worth publishing; a looser one
(0.9, say) would let a file describing a twentieth of the tree through
on the strength of a handful of heavy measured suites. The remedy
both refusals name is the one that fixes it: re-derive the file from a
run that measured the tree, with `scripts/ci/refresh_timings.py`.

The weight share is of WEIGHT and the suite share of COUNT, and they
disagree exactly where the recorded weights are lopsided -- which is
the ordinary shape, since the suites that dominate a matrix are the
ones measured hardest. A tree measured at nine of its ten suites by
count can be measured at 95% of its weight, and that file is a good
one; that is the case the weight share is for, and it is why the count
share is not a substitute for it.

This is a refusal rather than a note because there is nothing a reader
can do with a matrix whose load is mostly invented: unlike a target
the margin forbids, whose numbers are at least the file's own, this
one is a plan of a tree the file has never seen. The margin stays a
note for the same reason it was a note: it is about a number the file
does carry, and the writer is the refresher's chokepoint.

Nothing here imports the planner. Both shares are arithmetic over two
values the caller has already resolved, and keeping it that way is
what lets the chokepoint live in `plan_timed_matrix` -- where the
suite names and the weights come from, and where the refusal's own
exception class is -- without the two modules importing each other.
"""

# The share of a plan's total weight that may be an estimate before the
# plan is refused rather than published; see the module docstring.
MAX_ESTIMATED_WEIGHT_SHARE = 0.5
# The same bound over the plan's suite count, which no recorded weight
# can move; see the module docstring.
MAX_ESTIMATED_SUITE_SHARE = 0.5
_REMEDY = ('re-derive it from a run that measured the tree with '
           '`python3 scripts/ci/refresh_timings.py --runs-root '
           '<downloaded runs> --out .github/suite-timings.json`')


def estimated_share(weights, estimated):
    """The share of the plan's total weight the planner had to invent."""
    if not estimated:
        return 0.0
    return sum(weights[name] for name in estimated) / sum(weights.values())


def estimated_suite_share(weights, estimated):
    """The share of the plan's SUITES the planner had to invent.

    The weight-independent half of the guard. `weights` is keyed by
    every suite the tree holds, so the denominator is the tree and a
    recorded weight -- however heavy -- cannot move this.
    """
    if not estimated:
        return 0.0
    return len(estimated) / len(weights)


def _recorded_clause(weights, estimated):
    return (f'the file records {len(weights) - len(estimated)} of '
            f'the tree\'s {len(weights)} suites')


def _weight_refusal(weights, estimated, share):
    """The share is of weight: the plan's own total is the fiction."""
    unmeasured = sum(weights[name] for name in estimated)
    measured = sum(weights.values()) - unmeasured
    return (
        f'{share:.0%} of this plan\'s weight is estimated, over the '
        f'{MAX_ESTIMATED_WEIGHT_SHARE:.0%} bound: '
        f'{_recorded_clause(weights, estimated)} ({measured:.4g} measured '
        f'against {unmeasured:.4g} estimated at the median recorded '
        f'weight), so the cell count and '
        f'the balance guarantee are computed over a total the file does '
        f'not measure; {_REMEDY}')


def _suite_refusal(weights, estimated, share):
    """The share is of suites: the plan is one borrowed median repeated."""
    return (
        f'{share:.0%} of this plan\'s suites are estimated, over the '
        f'{MAX_ESTIMATED_SUITE_SHARE:.0%} bound: '
        f'{_recorded_clause(weights, estimated)}, so the majority of the '
        f'plan is a single borrowed median rather than a measurement, and '
        f'the weight share cannot see it: the median comes from the '
        f'recorded weights, so one heavy suite among them hides the '
        f'shortfall from the weight share entirely; '
        f'{_REMEDY}')


def coverage_refusal(weights, estimated):
    """The reason this plan must not be published, or None if it may.

    Both bounds, the statistic each one failed, and the command that
    re-derives a file from a run that measured the tree travel with the
    refusal, because "your data file is bad" is not an action.
    """
    if not estimated:
        return None
    share = estimated_share(weights, estimated)
    if share > MAX_ESTIMATED_WEIGHT_SHARE:
        return _weight_refusal(weights, estimated, share)
    suites = estimated_suite_share(weights, estimated)
    if suites > MAX_ESTIMATED_SUITE_SHARE:
        return _suite_refusal(weights, estimated, suites)
    return None
