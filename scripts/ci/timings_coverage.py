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

THE SUITE SHARE, IN TWO TIERS. How much of the plan's CONTENT is
invented, which no recorded weight can move: the packer cannot tell an
estimated suite from a measured one and prices both at the same borrowed
number. `NOTE_ESTIMATED_SUITE_SHARE` (a tenth) NAMES it in the step
summary and publishes; `MAX_ESTIMATED_SUITE_SHARE` (a third) refuses.

THE TIER IS A CONSEQUENCE, NOT A COMPROMISE. An unmeasured suite is
priced at the recorded MEDIAN, so what a share costs depends on HOW the
file came to be missing those suites, and there are two ways with very
different prices. Both tables below are measured over ONE TREE -- the
file's OWN suite set, its 328 recorded weights plus every name its own
coverage clause lists, never the live tree -- so a number here is a
statement about this file's measured distribution and not about how
many suites the repository has this week. The shipped weights total
356.4 reference multiples, with a recorded mean of 1.087 against a
median of 0.2118: a ratio of 5.13, and that ratio is the third
condition's whole subject.

A BIASED recorded set is the shape a refresh leaves when it writes the
measured set alone, or keeps the lightest of what it measured. It is
starved of the heavy weights, so the median prices a whole population at
one sample's rate. CONSTRUCTION A: keep the `k` LIGHTEST of the 328
recorded weights, drop the rest, let the planner estimate every suite the
file no longer records.

      k kept   share   weight share   skew   plan total   understated
        320     2.4%        0.6%      4.10       271.6        1.31x
        300     8.5%        3.2%      2.87       155.2        2.30x
        280    14.6%        6.2%      2.59       103.3        3.45x
        260    20.7%       10.0%      2.34        73.6        4.84x
        240    26.8%       14.4%      2.18        54.6        6.53x
        222    32.3%       20.4%      1.87        42.6        8.36x
        200    39.0%       28.6%      1.60        32.6       10.93x
        164    50.0%       46.0%      1.18        22.9       15.57x

A DRIFTED recorded set is a different thing. The write is a union, so a
missed daily refresh loses no weight: the file still records everything
the last run that measured the tree measured, and the unmeasured suites
are the ones that ARRIVED since, which is a random draw from the tree's
distribution rather than a censored sample of it. CONSTRUCTION B: keep
all 328 recorded weights, add `k` suites the tree has gained, and price
each arrival at what a draw from the recorded distribution is really
worth -- the recorded MEAN of 1.087, against the 0.2118 the planner
lends it.

      k arrivals   share   plan total   honest total   understated
           24      6.8%         361.5          382.5        1.06x
           40     10.9%         364.8          399.8        1.10x
           72     18.0%         371.6          434.6        1.17x
          164     33.3%         391.1          534.6        1.37x

So at the same third of the tree, one shape is 1.37x out and the other
8.36x, and that gap is what sets the tiers rather than either share
alone. It also sets the third condition, because the two tables differ in
a fourth column neither share reads: a drifted set keeps the whole
measured population, so its skew is 5.13 at every arrival count, and a
truncated set's skew collapses towards 1 as the tail is cut away. The
guard could not see that from `k` alone, which is why the band below
existed; `MIN_RECORDED_SKEW` closes it.

The note is a tenth because that is roughly where the BIASED shape's plan
is out by 2x (construction A reaches 2.30x at 8.5%), and 2x is the
tolerance the plan's purpose sets: the cell count is
`ceil(total / target)`, so a total out by 2x is a matrix carrying half
the parallelism the tree needs. The shipped file's own coverage is 4 of
332 -- 1.2% -- an eighth of the bound, and says nothing, which is the
point.

The refusal is a third, and it is DERIVED rather than chosen: with `m`
recorded weights summing `S` at median `e`, at least `floor(m/2)` of them
are at or above `e` and the median itself adds one more, so
`S >= (floor(m/2) + 1) * e` and a weight share can pass its bound only
when `k > floor(m/2) + 1`. That constructible infimum -- a third -- is
where a recorded set stops being a sample of the tree and becomes a
corner of it, and where the two suite bounds stop being redundant: the
weight comparison can only fire above the same line, and in fact can
never reach its own bound, since at `k = m/2` the largest `k` the suite
bound admits, the share is at most `m / (3m + 2)` -- a third. A drifted
file at a third is 1.37x out, which is a slow matrix; a biased one at a
third is 8.36x out, which is a fiction.

WHAT THE FUSE IS NOW, AND WHAT IT COSTS. The refresher is a daily cron at
06:38 UTC, so a missed run is not a hypothetical, and this tree grows at
`SUITES_PER_DAY` a day -- a measured RANGE, because the rate is not a
constant (see the constant). Against a 328-suite recorded set the note
reaches at 37 arrivals and the refusal at 165, which is 1.2 to 4.6 days
and 5.5 to 20.6 days at the measured rate. At the bound this module
carried before, one missed refresh put the file over a tenth and REFUSED
it, and a refusal here is `Plan the matrix` exiting 1 in
`.github/workflows/tests.yml`: no pull request could create a matrix at
all. Two days of a collapsed run, a rate limit or an Actions outage
would have been a repo-wide CI outage.

That band is the price of the tier, and it is a real one. A BIASED file
at 10.9% estimated is between 1.10x out (construction B) and 3.45x
(construction A) and is published with a note instead of refused. The
band is the part no share can close and `MIN_RECORDED_SKEW` only partly
does: between a skew of 2.0 and 2.34 a biased plan is 2.3x to 4.8x out
and is still published, because its recorded set still carries enough
tail to look like a sample. That is a priced acceptance and the
constant carries the numbers, the trade and the reason it is not closed
-- which is a number about this tree, not about a distribution a
nightly refresh re-measures. The alternative -- refusing the drifted
file too -- is not a stricter guard but a broken one, because the
alternative to a wrong number is not a right number, it is no number.

A refusal rather than a note above it because there is nothing a reader
can do with a matrix whose load is mostly invented: unlike a target the
margin forbids, whose numbers are at least the file's own, this is a plan
of a tree the file has never seen. The margin stays a note for the same
reason -- it is about a number the file does carry, and the writer is
the refresher's chokepoint.

Nothing here imports the planner. Every share is arithmetic over two
values the caller has already resolved, which is what lets the
chokepoint live in `plan_timed_matrix`, where the suite names and the
weights come from and where the refusal's exception class is.
"""
import statistics

# The share of a plan's total weight that may be an estimate; its own
# domain is a recorded set whose mean sits far below its median, which
# a measured set does not, so it names a refusal rather than gating one.
MAX_ESTIMATED_WEIGHT_SHARE = 0.5
# The share of the plan's SUITES that STOPS the plan, and the live
# guard: no recorded weight can move it. A third, and not a tenth,
# because a refusal here is `Plan the matrix` exiting 1 in
# `.github/workflows/tests.yml` on every pull request, and the two-day
# fuse a tenth gave it is the whole of the module's argument.
MAX_ESTIMATED_SUITE_SHARE = 1 / 3
# The share at which the plan is PUBLISHED and the number is quoted in
# the step summary, the way `CELL_WEIGHT_MARGIN` quotes a target the
# margin forbids. Below it the file describes its tree and the summary
# says so by omission.
NOTE_ESTIMATED_SUITE_SHARE = 0.10
# The ratio of a recorded set's own mean to its own median below which
# the set is not a SAMPLE of a tree but the bottom of one, and the
# median it lends the unmeasured suites is a price from the floor of
# the distribution. See `recorded_skew` and the docstring's cost table.
#
# 2.0, AND THE NUMBER IS A DECISION, NOT A GAP. The construction: over
# the live tree (334 suites, 328 recorded, true total 357.6), keep the
# `k` LIGHTEST recorded weights, drop the rest, and let the planner
# estimate every suite the file no longer records.
#
#   k-rec  est-share  skew   plan total   understated    2.0    2.2
#     328      1.8%   5.13       357.6        1.00x       pub    pub
#     300     10.2%   2.87       156.3        2.29x       pub    pub
#     260     22.2%   2.34        74.2        4.82x       pub    pub
#     240     28.1%   2.18        55.1        6.49x       pub   REF
#     222     33.5%   1.87        43.1        8.29x       REF    REF
#     200     40.1%   1.60        33.0       10.82x       REF    REF
#
# 2.0 DOES ITS JOB: the file that motivated this condition -- the 222
# lightest, 8.29x out, published behind a summary reading
# `heaviest/median 1.000` -- sits at 1.87 and is refused, and so is
# everything more truncated. The residual band is 2.0 to 2.34, which is
# 2.3x to 4.8x out, and it is a PRICED ACCEPTANCE rather than an
# oversight: a plan in that band is lopsided, and every suite in it
# still runs, so a lopsided matrix and no matrix are not comparable
# outcomes. Ruled by the maintainer.
#
# 2.2 WOULD BUY the 240-lightest file at 6.49x, and IS NOT TAKEN, for a
# reason that is NOT the drift path. It is tempting to read 2.2 as
# costing the fuse -- as though arrivals priced at the recorded median
# dilute the recorded set and walk its skew down to the floor. They do
# not: `recorded_skew` is over the resolved weights MINUS the invented
# ones, so a drifted file's recorded set is unchanged and its skew is
# 5.13 at one day, at nineteen, and at every arrival count between.
# Measured, and `test_the_two_shapes_are_told_apart_by_the_recorded_set_
# alone` asserts the same float at five arrival counts. Under the other
# definition -- the ratio over ALL resolved weights, arrivals included --
# the skew does fall, from 4.90 at a day to 2.97 at nineteen, and it
# still has not reached 2.2 at a third of the tree. So no reading makes
# 2.2 cost five days of a missed nightly, and the real reason is the
# one below.
#
# THE REAL REASON IS THAT 2.2 IS NOT A NUMBER ABOUT THIS TREE. 2.0 sits
# 0.13 clear of the shape it catches (1.87) and 0.18 below the one it
# does not (2.18), and both of those are properties of a distribution
# that a nightly refresh re-measures. 2.2 would sit 0.02 above a shape
# that moves, so a file whose only change was last night's data could
# flip between publishing and being refused, and the flip would be a
# true statement about the data wearing the costume of a guard
# tightening. 2.0 is a round number about equally clear of both, and
# raising it is a decision to be made against a fresh measurement, not
# a number to nudge.
MIN_RECORDED_SKEW = 2.0
# Tracked suites this tree gains a day, which is how the note converts an
# estimated count into refreshes that did not land. A RANGE and not a
# point, because the rate is not a constant: on this branch's history at
# 2026-09-28 the tree held 321 tracked suites, against 166 sixteen days
# earlier, 190 seven days earlier and 215 four days earlier -- 9.7, 18.7
# and 26.5 a day over those three windows. A single figure quoted to one
# decimal place is a precision the measurement does not have, and it is
# read by an operator deciding how urgently to re-run a refresh.
# `SUITES_PER_DAY_BASIS` names the windows so the next reader
# re-measures rather than re-argues, and
# `test_the_growth_rate_is_measured_against_the_tree_not_asserted`
# re-derives both ends from the tree's own history.
SUITES_PER_DAY = (8, 30)
SUITES_PER_DAY_BASIS = (
    'this branch at 2026-09-28: 321 tracked tests/ suites, against 166 at '
    '2026-09-12 (16 days, 9.7 a day), 190 at 2026-09-21 (7 days, 18.7) and '
    '215 at 2026-09-24 (4 days, 26.5)')
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


def recorded_skew(weights, estimated):
    """The recorded set's own mean over its own median: 0.0 if unknown.

    The third condition's statistic, and the one that tells the two
    shapes apart. A measured set of runtimes is right-skewed -- one
    suite at 76 s beside three hundred at 0.2 s is what a tree of tests
    looks like -- so its mean runs several times its median. A
    BIASED recorded set is the BOTTOM of that distribution with the
    heavy tail cut off, and cutting the tail off is exactly what
    collapses the mean back down onto the median. The ratio is
    therefore the size of the tail the file still carries, read off
    the file itself.

    It does not move with the arrival count, which is what leaves the
    drift fuse alone: a drifted file's recorded set is the whole
    measured population at one, nineteen or ninety days, so its skew
    is the same number every time and a missed refresh cannot reach
    this condition at any coverage.

    The recorded values are the resolved weights minus the ones the
    planner had to invent, because those are the only entries the
    median was taken over. A CARRIED suite -- one the runs did not
    measure but the file already recorded -- is in the file's own
    numbers and is counted here, which is right: the union is what the
    file is, and the tail it carries is the tail it lends.
    """
    recorded = [value for name, value in weights.items()
                if name not in set(estimated)]
    if not recorded:
        return 0.0
    return statistics.fmean(recorded) / statistics.median(recorded)


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
        f'{_recorded_clause(weights, estimated)}, so more than a third of '
        f'the plan is one borrowed number rather than a measurement, and '
        f'the weight share cannot see it: the median comes from the '
        f'recorded weights, so one heavy suite among them hides the '
        f'shortfall from the weight share entirely; '
        f'{_REMEDY}')


def growth_days(count):
    """`(fewest, most)` days of growth `count` suites could have taken.

    The two ends of `SUITES_PER_DAY`, so the note quotes a range whose
    endpoints are the module's own numbers rather than a point estimate
    derived from a figure the measurement does not support. A control
    reads the endpoints back out of the summary, which is what keeps the
    operator-facing sentence falsifiable: a rate that has moved reddens
    it, and the constant is re-measured.
    """
    return count / SUITES_PER_DAY[1], count / SUITES_PER_DAY[0]


def _censored_refusal(weights, estimated, share, skew):
    """The recorded set is the bottom of the tree, not a sample of it."""
    return (
        f'{share:.0%} of this plan\'s suites are estimated and the file\'s '
        f'recorded weights are not a sample of the tree they price: their '
        f'mean is only {skew:.2f} their own median, against '
        f'{MIN_RECORDED_SKEW:g} for a measured set, so the heavy tail that '
        f'makes a suite expensive has been cut off the top of what the file '
        f'holds and every suite it says nothing about is priced from the '
        f'floor of the distribution -- {_recorded_clause(weights, estimated)}'
        f'. Neither share can see this: the suite share counts the missing '
        f'suites and the weight share divides by a total this set has '
        f'already understated, and a drifted file -- whose recorded set is '
        f'the whole measured population, so its skew does not move with the '
        f'arrivals -- is not this shape; {_REMEDY}')


def coverage_note(weights, estimated):
    """The share a reader must be told about, or None if there is nothing.

    Below `NOTE_ESTIMATED_SUITE_SHARE` the file describes its tree and
    the summary says so by omission. Above it, and below the refusal,
    the plan is PUBLISHED and the share is named: every suite in the
    tree is in some cell, the packer still orders the measured ones
    longest-first, and the only consequence is a matrix carrying less
    parallelism than the tree wants. A refusal is reserved for a file
    that is no longer a description of its tree, because a guard whose
    own failure stops every pull request from creating a matrix is a
    worse failure than a lopsided one.
    """
    if not estimated:
        return None
    share = estimated_suite_share(weights, estimated)
    if share < NOTE_ESTIMATED_SUITE_SHARE:
        return None
    fewest, most = growth_days(len(estimated))
    return (
        f'{share:.1%} of this plan\'s suites are estimated, at or above '
        f'the {NOTE_ESTIMATED_SUITE_SHARE:.0%} at which a tree of this size '
        f'quotes it: {_recorded_clause(weights, estimated)}, and '
        f'{estimated_share(weights, estimated):.1%} of the plan\'s weight '
        f'is the median recorded weight rather than a measurement, so the '
        f'cell count and the balance guarantee are computed over a total '
        f'this file does not measure. Every suite in the tree is still in '
        f'a cell and the packer still orders the measured ones '
        f'longest-first: the matrix is lopsided, not wrong, and it is '
        f'published. {len(estimated)} estimated suites is between '
        f'{fewest:.1f} and {most:.1f} days of this tree\'s growth, which '
        f'measures {SUITES_PER_DAY[0]} to {SUITES_PER_DAY[1]} tracked '
        f'suites a day ({SUITES_PER_DAY_BASIS}), so somewhere between that '
        f'many and that many daily refreshes have not landed. '
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
    skew = recorded_skew(weights, estimated)
    if skew < MIN_RECORDED_SKEW:
        return _censored_refusal(weights, estimated, suites, skew)
    return None
