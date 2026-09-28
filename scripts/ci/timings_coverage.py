#!/usr/bin/env python3
"""How much of a timings file has to be a real measurement.

A suite the file says nothing about is not measured at all, and the
planner prices it at the median of the suites that are. That is the
right guess for a suite added since the last measurement and a very
wrong one when the measured suites are not the tree: the median then
prices a whole population at one sample's rate, and the total the packer
sees is wrong by a multiple rather than by a percent.

That is what happened here and nothing went red. A refresh measured 28
of the tree's 326 suites and wrote the file as the measured set alone;
the survivors totalled 25.8 reference multiples where the tree holds
346.9. The packer saw 109.5, derived five cells where the weights want
fourteen, and reported `heaviest/median 1.000` -- a total that is wrong
by 3.15x is balanced with itself, and the margin is computed over that
same invented total. The matrix published and the file was internally
consistent and wrong.

THREE CONDITIONS, each answering a question the others cannot.

A TOTAL THAT IS NOT POSITIVE (`_degenerate_refusal`). `read_timings`
refuses a non-positive recorded weight and `--scale` multiplies every
weight by a float afterwards, so a factor of zero or below reaches here
behind a schema-valid file. There is no share to divide at zero, and at
a negative factor the packer orders a negative weight as the lightest
and publishes numbers that are not runtimes.

THE WEIGHT SHARE (`MAX_ESTIMATED_WEIGHT_SHARE`). How much of the number
the packer packs is invented. Its domain is narrow, and the derivation
is short. With `m` recorded weights summing `S` at median `e`, at least
`floor(m/2)` of them are at or above `e` and the median itself adds one
more, so `S >= (floor(m/2) + 1) * e`. The share passes its bound only
when `k * e > S`, hence `k > floor(m/2) + 1`: MORE THAN A THIRD of the
tree estimated. This file's own recorded set is the other shape -- mean
1.08 against a median of 0.22, right-skewed as a measured set is -- so
it cannot reach the bound at any coverage. The weight comparison
therefore decides WHICH refusal a reader gets, never whether to give
one.

THE SUITE SHARE (`MAX_ESTIMATED_SUITE_SHARE`). How much of the plan's
CONTENT is invented, which no recorded weight can move: the packer
cannot tell an estimated suite from a measured one and prices both at
the same borrowed number.

THE BOUND IS MEASURED, NOT CHOSEN. Keeping the `k` LIGHTEST recorded
suites of this tree and estimating the rest -- the shape that flatters
the weight share, since the one heavy recorded weight is most of the
denominator it is divided by -- gives, over the 327-suite tree and a
true total of 346.93:

    estimated share   plan's total   understated
      2.4% (shipped)      346.9        1.00x
       9.5%                 170.4        2.04x
      10.1%                 162.5        2.13x
      15.3%                 113.4        3.06x  <- the historical defect
      49.9%                  38.6        9.0x

A tenth is where the plan's own load is out by a little over 2x, and 2x
is the tolerance the plan's purpose sets: the cell count is
`ceil(total / target)`, so a total out by 2x is a matrix carrying half
the parallelism the tree needs, and a speed gate measuring that
critical path cannot see it from inside the plan it measures. The
historical 3.15x sits at 15.3%, above the bound with a third of its
width to spare; the shipped file's 2.4% is 1.00x, four times below it;
and at half the plan is out by 9x. Drift is what could justify a looser
bound, and it is bounded: the union write never raises the estimated
count, so reaching a tenth takes twenty-five new suites arriving between
two measurements of a 327-suite tree. The first scheduled refreshes
produce that series, and it is what would move the number.

A refusal rather than a note because there is nothing a reader can do
with a matrix whose load is mostly invented: unlike a target the margin
forbids, whose numbers are at least the file's own, this is a plan of a
tree the file has never seen. The margin stays a note for the same
reason -- it is about a number the file does carry, and the writer is
the refresher's chokepoint.

Nothing here imports the planner. Every share is arithmetic over two
values the caller has already resolved, which is what lets the
chokepoint live in `plan_timed_matrix`, where the suite names and the
weights come from and where the refusal's exception class is.
"""
# The share of a plan's total weight that may be an estimate; its own
# domain is a recorded set whose mean sits far below its median, which
# a measured set does not, so it names a refusal rather than gating one.
MAX_ESTIMATED_WEIGHT_SHARE = 0.5
# The share of the plan's SUITES that may be an estimate, and the live
# guard: no recorded weight can move it, and the module docstring's
# measured series is what puts it at a tenth.
MAX_ESTIMATED_SUITE_SHARE = 0.1
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


def _degenerate_refusal(total):
    return (
        f'the resolved weights total {total:g}, which is not a positive '
        'load, so there is no plan here to publish: the packer orders a '
        'weight of zero or less as the lightest and every cell would '
        'carry nothing. `--scale` multiplies every recorded weight after '
        'the schema has checked it, so a factor of zero or below reaches '
        'here behind a valid file; plan without it, or with a positive '
        'factor')


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
        f'{_recorded_clause(weights, estimated)}, so a tenth of the plan '
        f'or more is one borrowed number rather than a measurement, and '
        f'the weight share cannot see it: the median comes from the '
        f'recorded weights, so one heavy suite among them hides the '
        f'shortfall from the weight share entirely; '
        f'{_REMEDY}')


def coverage_refusal(weights, estimated):
    """The reason this plan must not be published, or None if it may.

    Each bound, the statistic each one failed, and the command that
    re-derives a file from a run that measured the tree travel with the
    refusal, because "your data file is bad" is not an action. The
    non-positive total is checked first and without a share: there is
    nothing to divide, and the rest of the module would describe a
    total that is not a load.
    """
    if sum(weights.values()) <= 0:
        return _degenerate_refusal(sum(weights.values()))
    if not estimated:
        return None
    share = estimated_share(weights, estimated)
    if share > MAX_ESTIMATED_WEIGHT_SHARE:
        return _weight_refusal(weights, estimated, share)
    suites = estimated_suite_share(weights, estimated)
    if suites > MAX_ESTIMATED_SUITE_SHARE:
        return _suite_refusal(weights, estimated, suites)
    return None
