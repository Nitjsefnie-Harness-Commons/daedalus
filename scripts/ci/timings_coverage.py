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
different prices.

A BIASED recorded set -- the shape a refresh leaves when it writes the
measured set alone, or keeps the lightest of what it measured -- is
starved of the heavy weights, so the median prices a whole population at
one sample's rate. Keeping the `k` LIGHTEST recorded suites of this tree
and estimating the rest, over the 328-suite tree and a true total of
347.14:

    estimated share   plan's total   understated
       8.5%                 199.0        1.74x
      10.4%                 173.7        2.00x
      15.6%                 126.2        2.75x  <- the historical defect
      26.8%                  74.7        4.65x
      50.0%                  38.6        8.98x

A DRIFTED recorded set is a different thing. The write is a union, so a
missed daily refresh loses no weight: the file still records everything
the last run that measured the tree measured, and the unmeasured suites
are the ones that ARRIVED since, which is a random draw from the tree's
distribution rather than a censored sample of it. This tree's recorded
weights are right-skewed the way a measured set is -- mean 1.08 against a
median of 0.22 -- so a suite priced at the median is priced at about a
fifth of what a drawn suite is really worth, and the error grows slowly:

    days missed    estimated share   understated
        1                 5.6%             1.05x
        2                10.6%             1.09x
        5                22.9%             1.22x
        9                34.9%             1.39x

So the two shapes are three orders of magnitude apart in what they cost
at the same share, and the guard CANNOT tell them apart -- it sees only
`k` unmeasured suites and no weights. That is what sets the tiers rather
than either share alone.

The note is a tenth because that is where the BIASED shape's plan is out
by 2x, and 2x is the tolerance the plan's purpose sets: the cell count is
`ceil(total / target)`, so a total out by 2x is a matrix carrying half
the parallelism the tree needs. The shipped file's 2.7% is four times
below it and says nothing, which is the point.

The refusal is a third, and it is DERIVED rather than chosen: with `m`
recorded weights summing `S` at median `e`, at least `floor(m/2)` of them
are at or above `e` and the median itself adds one more, so
`S >= (floor(m/2) + 1) * e` and a weight share can pass its bound only
when `k > floor(m/2) + 1`. That constructible infimum -- a third -- is
where a recorded set stops being a sample of the tree and becomes a
corner of it, and where the two suite bounds stop being redundant: the
weight comparison can only fire above the same line. A drifted file at a
third is 1.39x out, which is a slow matrix; a biased one at a third is
4.65x out, which is a fiction, and the guard has to give the same answer
to both.

WHAT THE FUSE IS NOW, AND WHAT IT COSTS. The tree grew 116 tracked suites
in the six days 2026-09-22 to 2026-09-28 on origin/main -- 212 to 328,
19 a day (`SUITES_PER_DAY`) -- and the refresher is a daily cron at
06:38 UTC, so a missed run is not a hypothetical. At the bound this
module carried before, one missed refresh put the file over a tenth and
REFUSED it, and a refusal here is `Plan the matrix` exiting 1 in
`.github/workflows/tests.yml`: no pull request could create a matrix at
all. Two days of a collapsed run, a rate limit or an Actions outage
would have been a repo-wide CI outage. At a third the fuse is nine
consecutive refreshes that did not land, and in the two-to-nine day band
the note is what runs, on every pull request, naming the share, the days,
and the remedy.

That band is the price of the tier, and it is a real one: a BIASED file
at 10.6% is 2.0x out and is now published with a note instead of
refused. Between the tiers the guard tolerates a biased plan being out by
up to about 4.65x, in exchange for a fuse eight times longer. The
alternative -- refusing the drifted file too -- is not a stricter guard
but a broken one, because the alternative to a wrong number is not a
right number, it is no number. The sharper guard, which would remove the
compromise, can tell the two shapes apart: a biased recorded set is
LEFT-censored, its mean sitting at or below its own median, where a
measured set is right-skewed. That is one comparison the caller already
has the values for, and it is not in this module because a third
condition is a third thing to keep honest; it is the first thing to add
if the band proves too wide.

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
# Tracked suites this tree gains a day, which is how the note converts an
# estimated count into refreshes that did not land. Measured on
# origin/main: 212 tracked suites at 2026-09-22 and 328 at 2026-09-28.
SUITES_PER_DAY = 19
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
        f'{_recorded_clause(weights, estimated)}, so more than a third of '
        f'the plan is one borrowed number rather than a measurement, and '
        f'the weight share cannot see it: the median comes from the '
        f'recorded weights, so one heavy suite among them hides the '
        f'shortfall from the weight share entirely; '
        f'{_REMEDY}')


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
    days = len(estimated) / SUITES_PER_DAY
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
        f'published. {len(estimated)} estimated suites is about {days:.1f} '
        f'days of this tree\'s growth at the measured {SUITES_PER_DAY} '
        f'tracked suites a day, so that many daily refreshes have not '
        f'landed. {_REMEDY}')


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
