#!/usr/bin/env python3
"""The bounds that decide how much of a plan may be an estimate.

`scripts/ci/timings_coverage.py` is the chokepoint `plan_timed_matrix`
calls before it publishes a matrix, and every test here drives that
chokepoint -- `coverage_refusal` for the statistic, the planner's own
CLI for the verdict -- rather than restating the arithmetic. The
shipped file's own weights over the real tree are the fixtures, because
the bounds are claims about a distribution this repository has and a
shape invented here would be a claim about nothing.

What each one earns its place on:

- a total of zero or less is not a load, whatever the file records;
- the SUITE share is the live guard, in TWO tiers -- a note at a tenth,
  which names the share and publishes, and a refusal above a third,
  which does not publish at all. The gap between them is the drift the
  daily refresh leaves behind, and it is the whole of the design;
- the WEIGHT share names a different quantity, and can only fire on a
  recorded set whose mean sits far below its own median, which a
  measured set does not. It decides which refusal a reader gets.

The two controls that drive the planner CLI with its own fixture block
stay in `test_timed_planner.py`, beside the fixtures they use: a copy of
that block here was `pylint`'s duplicate-code finding, and a shared
helper module for one call site would be the same rule twice.

The file lives apart from `test_timed_coverage.py` because that one
reaches the 700-line ceiling every tests module in this repository is
held to, and the guard's controls did not fit beside the run-selection
ones.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _timed_basis import fixture_tree, unmeasured_names  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _coverage():
    return _util.load(ROOT / 'scripts' / 'ci' / 'timings_coverage.py',
                      'timings_coverage')


def _planner_verdict(tmp, data, *flags, name='timings.json'):
    """The planner's own CLI over a data file: (exit code, stderr, output)."""
    path = Path(tmp) / name
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    out = Path(tmp) / 'matrix.json'
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = _planner().main(['--tree', str(ROOT), '--timings', str(path),
                                '--out', str(out), *flags])
    published = out.read_text(encoding='utf-8') if out.exists() else ''
    out.unlink(missing_ok=True)
    return code, err.getvalue(), published


def _shipped_weights():
    """The shipped file's recorded weights, over the suites it names."""
    planner = _planner()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = set(planner.suite_names(ROOT))
    return data, {name: weight
                  for name, weight in data['suite_weights'].items()
                  if name in names}


def _file_tree(tmp, data, name):
    """A fixture tree carrying the shipped file's OWN suite set.

    Every ratio in the two thinned-file controls below is a function of
    how much of the TREE the file records, and a live tree outruns the
    file. Measured over the shipped 328 recorded weights with arrivals
    added to the tree, the half-recorded control's plan goes from two
    cells to three and its understatement from 9.7x to 7.0x within 80
    arrivals, and the light-tailed control's from one cell to two by
    300. Those are fixtures failing on the tree's growth rather than on
    the guard they exist for.

    So these two plan the tree the file DESCRIBES -- its recorded
    weights plus every name its own coverage clause lists -- which is
    the construction `tests/_timed_basis.py` states for the basis
    compare, and the file against the LIVE tree keeps its own control
    in `test_timed_coverage.py`.
    """
    _count, _total, listed, _carried, _estimated = unmeasured_names(
        data['basis'])
    return fixture_tree(Path(tmp) / name,
                        sorted(set(data['suite_weights']) | set(listed)))


def _arrivals(tmp, recorded, gain, name):
    """The file above, and `gain` unrefreshed arrivals after it.

    A missed daily refresh does not lose a weight: the union write is
    unchanged, so the file still records everything the last run that
    measured the tree measured, and every suite the tree has GAINED
    since is the one the planner prices at the recorded median.

    `gain` is a COUNT, and a count is the one shape a drift fixture
    does not drift in: the two tier boundaries below are derived from
    the size of the recorded set this build ships, and the control
    `test_both_drift_boundaries_hold_at_every_recorded_set_size` holds
    that derivation for a spread of tree sizes. A fixture written as
    "two days" or as "half the tree" is a fixture written as a fraction
    of a set that gains about nineteen suites a day, and it fails on
    the tree rather than on the guard the day the fraction moves.
    """
    suites = sorted(recorded) + [f'test_arrived{index:03d}.py'
                                 for index in range(gain)]
    return fixture_tree(Path(tmp) / name, suites), gain


def _note_boundary(recorded_count):
    """The fewest arrivals that put a tree of that size AT the note bound.

    The note fires at a tenth and not above it, so the boundary is the
    smallest `k` with `k / (n + k) >= 1/10`, which in integers is
    `9k >= n`. Derived from the tree rather than written down: at 19
    arrivals a day, "two days of drift" stops reaching a tenth once the
    recorded set passes 342, and the fixture would have failed on that
    day with nothing about the guard having changed.
    """
    return -(-recorded_count // 9)


def _refusal_boundary(recorded_count):
    """The fewest arrivals a tree of that size is REFUSED over: `2k > n`.

    Same derivation against `MAX_ESTIMATED_SUITE_SHARE`: the refusal
    compares `k / (n + k)` against a third with `>`, so a tree of `n`
    publishes at exactly `n // 2` estimated and is refused at one more.
    """
    return recorded_count // 2 + 1


def _verdict(tree, tmp, data, *flags, name='timings.json'):
    """The planner's CLI over a fixture tree: (code, stderr, summary)."""
    path = Path(tmp) / name
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    out = Path(tmp) / 'matrix.json'
    summary = Path(tmp) / 'summary.md'
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = _planner().main(['--tree', str(tree), '--timings', str(path),
                                '--out', str(out), '--summary',
                                '--summary-file', str(summary), *flags])
    text = summary.read_text(encoding='utf-8') if summary.exists() else ''
    summary.unlink(missing_ok=True)
    published = out.read_text(encoding='utf-8') if out.exists() else ''
    out.unlink(missing_ok=True)
    return code, err.getvalue(), text, published


def test_the_fewest_arrivals_that_reach_the_note_publish_with_it_named(tmp):
    """A tenth of the tree estimated: the plan runs, and the share is named.

    THE FUSE. The refresher is a daily cron, and this tree grew 116
    tracked suites in the six days 2026-09-22..28 on origin/main -- 212
    to 328, 19 a day -- so every suite added since the last run that
    measured the tree is priced at the recorded median until the next
    one lands. Two days of that is a tenth of a tree the size this
    file recorded, and the plan it makes is published with the share
    quoted.

    At the bound this branch carried before this wave that file was
    REFUSED, and a refusal here is not a slow matrix. It is `Plan the
    matrix` exiting 1 in `.github/workflows/tests.yml` on every pull
    request, so one missed nightly -- a rate limit, a cancelled
    workflow, an Actions outage, a weekend -- became a repo-wide CI
    outage inside forty-eight hours, with the bound firing as the first
    and only news of it.

    The two failure modes are not comparable. A stale-but-mostly-
    measured file plans a LOPQSIDED matrix: every suite is in a cell,
    the packer still orders the measured ones longest-first, and the
    cost is a matrix carrying less parallelism than the tree wants. No
    number is published instead. So the share is named in the summary
    -- the same surface the `CELL_WEIGHT_MARGIN` note already uses -- and
    the matrix is planned.

    WHAT IS DERIVED, AND WHY IT IS NOT A SNAPSHOT. The arrivals are
    `_note_boundary` over the recorded set this build ships -- the
    fewest that reach a tenth, which at the module's measured growth
    rate is a couple of days -- and one fewer is asserted SILENT, so
    the note's own edge is pinned from both sides. Every figure the
    summary prints is compared to a value derived here, never to a
    count this tree happened to have last week: a suite added to
    `tests/` moves the recorded set, and a fixture that named 38 of
    357 fails on the tree instead of on the guard.
    """
    data, recorded = _shipped_weights()
    coverage = _coverage()
    recorded_count = len(recorded)
    loud = _note_boundary(recorded_count)
    for gain, quoted in ((loud - 1, False), (loud, True)):
        tree, _gain = _arrivals(tmp, recorded, gain, f'note-{gain}')
        share = gain / (recorded_count + gain)
        code, err, summary, published = _verdict(
            tree, tmp, data, name=f'note-{gain}.json')
        assert code == 0, (gain, share, err)
        assert 'cell-01' in published, published
        if not quoted:
            assert 'of this plan\'s suites are estimated' not in summary, (
                gain, share, summary)
            continue
        assert coverage.NOTE_ESTIMATED_SUITE_SHARE <= share, share
        assert share < coverage.MAX_ESTIMATED_SUITE_SHARE, share
        assert f'{share:.1%} of this plan' in summary, summary
        assert (f'the file records {recorded_count} of the tree\'s '
                f'{recorded_count + gain} suites') in summary, summary
        assert 'Every suite in the tree is still in a cell' in summary, summary
        days = gain / coverage.SUITES_PER_DAY
        assert f'{days:.1f} days' in summary, summary
        assert 'refresh_timings.py' in summary, summary


def test_the_shipped_files_own_coverage_quotes_no_note(tmp):
    """The operating point stays quiet: 9 of 328 is 2.7%, under a tenth."""
    data, _recorded = _shipped_weights()
    _code, _err, summary, _out = _verdict(ROOT, tmp, data, name='shipped.json')
    assert 'of this plan\'s suites are estimated' not in summary, summary


def test_the_refusal_waits_for_a_file_that_is_not_a_description_of_its_tree(
        tmp):
    """Half the tree estimated publishes; one suite more does not.

    The bound is not "where the number becomes wrong" -- a tenth of the
    tree estimated is already 2x out. It is "where the file stops
    describing the tree", and the module derives that line rather than
    choosing it: with `m` recorded weights summing `S` at median `e`,
    at least `floor(m/2)` of them are at or above `e` and the median
    itself adds one more, so `S >= (floor(m/2) + 1) * e` and a share can
    pass a weight bound only when `k > floor(m/2) + 1`. That constructible
    infimum is a third of the tree, and it is where a recorded set
    stops being a sample of the tree and becomes a corner of it. No
    recorded weight can move the suite share to reach it.

    THE BOUNDARY IS MEASURED, NOT SCHEDULED. The two cases are
    `_refusal_boundary(n)` and one suite short of it, where `n` is the
    recorded set this build ships: half of it, and half of it plus one,
    which at the module's measured growth rate is the better part of a
    week of daily refreshes that did not land. The refusal is a share,
    so it MOVES as the recorded set grows -- which is the safe
    direction, since a longer fuse is the module's argument and not a
    defect in it -- and a fixture that named "eight days, then nine"
    would have read that drift as a broken guard. What the cases hold is
    the comparison itself: exactly a third of the tree publishes, with
    the note naming it, and one suite more is refused by name.
    """
    data, recorded = _shipped_weights()
    coverage = _coverage()
    recorded_count = len(recorded)
    for gain, refused in ((recorded_count // 2, False),
                          (_refusal_boundary(recorded_count), True)):
        tree, _gain = _arrivals(tmp, recorded, gain, f'third-{gain}')
        share = gain / (recorded_count + gain)
        code, err, summary, published = _verdict(
            tree, tmp, data, name=f'third-{gain}.json')
        if refused:
            assert code == 1, (gain, share, summary)
            assert share > coverage.MAX_ESTIMATED_SUITE_SHARE, share
            assert 'suites are estimated' in err, err
            assert 'refresh_timings.py' in err, err
            assert summary == '', summary
            assert published == '', published
        else:
            assert code == 0, (gain, share, err)
            assert share <= coverage.MAX_ESTIMATED_SUITE_SHARE, share
            assert 'cell-01' in published, published
            assert f'{share:.1%} of this plan' in summary, summary
            days = gain / coverage.SUITES_PER_DAY
            assert f'{days:.1f} days' in summary, summary


def test_both_drift_boundaries_hold_at_every_recorded_set_size(tmp):
    """Both fixtures' arithmetic, at seven tree sizes instead of one.

    The two controls above derive their arrivals from the recorded set
    this build happens to ship, which moves as the tree grows. This one
    holds the DERIVATION to sizes this build does not have -- nine,
    twenty, a hundred, a hundred and one off the note's own multiple,
    the shipped set, and two sizes well past it -- so the property being
    relied on is "the boundary is where the module's comparison is",
    and not "328 is a number these two cases were written for".

    The share comes from the module's own `estimated_suite_share` over
    a plain weights map, because the arithmetic under test is the
    guard's and restating it here would test the restatement.
    """
    coverage = _coverage()
    for recorded_count in (9, 20, 100, 324, 328, 512, 1000):
        loud = _note_boundary(recorded_count)
        quiet = _share_of(coverage, recorded_count, loud - 1)
        assert quiet < coverage.NOTE_ESTIMATED_SUITE_SHARE, (
            recorded_count, loud, quiet)
        loud_share = _share_of(coverage, recorded_count, loud)
        assert loud_share >= coverage.NOTE_ESTIMATED_SUITE_SHARE, (
            recorded_count, loud, loud_share)
        refused = _refusal_boundary(recorded_count)
        assert _share_of(coverage, recorded_count, refused - 1) <= (
            coverage.MAX_ESTIMATED_SUITE_SHARE), recorded_count
        assert _share_of(coverage, recorded_count, refused) > (
            coverage.MAX_ESTIMATED_SUITE_SHARE), recorded_count


def _share_of(coverage, recorded_count, gain):
    """`estimated_suite_share` over a tree of `n` with `gain` estimated."""
    weights = {f'test_{index:05d}.py': 1.0
               for index in range(recorded_count + gain)}
    estimated = list(weights)[recorded_count:]
    return coverage.estimated_suite_share(weights, estimated)


def test_a_scale_that_leaves_no_positive_load_is_a_named_refusal(tmp):
    """`--scale 0` and `--scale -1` are refusals, in both directions.

    `read_timings` refuses a non-positive recorded WEIGHT, and `--scale`
    multiplies every weight by a float afterwards, so a factor of zero
    or below reaches the guard with a schema-valid file behind it. At
    zero the share divided by a zero total and the CLI died on a
    traceback, which is not the refusal with a reason this module
    promises; at minus one the plan's total came out negative, the
    shares came out positive, and a MATRIX PUBLISHED -- one cell per
    suite in reverse weight order, from numbers that are not runtimes.
    The one that used to refuse it by name was the degenerate-total
    branch, removed as unreachable on a premise that did not cover the
    scale path.
    """
    data = _planner().read_timings(ROOT / '.github' / 'suite-timings.json')
    for scale in ('0', '-1'):
        code, err, published = _planner_verdict(
            tmp, data, '--scale', scale, name=f'scaled-{scale}.json')
        assert code == 1, (scale, published)
        assert 'not a positive load' in err, (scale, err)
        assert 'Traceback' not in err, (scale, err)
        assert published == '', (scale, published)


def _thinned_to_the_lightest(data, names, keep=30):
    """The file's own weights, kept at their lightest plus its heaviest.

    The shape that flatters the weight share: `keep - 1` weights at the
    bottom of the tree's distribution and the single heaviest one on
    top. The median the planner lends the unmeasured suites is the
    median of that set, which is tiny, and the heaviest weight sits in
    the denominator the share is divided by.
    """
    recorded = {name: data['suite_weights'][name] for name in names
                if name in data['suite_weights']}
    light = sorted(recorded, key=lambda name: recorded[name])[:keep - 1]
    heavy = max(recorded, key=lambda name: recorded[name])
    return {name: recorded[name] for name in list(light) + [heavy]}


def test_a_file_recording_half_the_tree_is_refused_not_published(tmp):
    """Half the tree recorded: a plan an order out, and still refused.

    The shape a refresh leaves when it keeps the lightest recorded
    suites and the one heavy one, which is the shape that flatters the
    weight share -- the heavy weight is most of the denominator it is
    divided by. On the shipped file's own weights this leaves a little
    under three tenths of the plan's weight estimated (under a half, so
    the weight bound cannot see it) and half of its SUITES, a plan
    whose cells are a fraction of the ones the same weights pack
    honestly, and a total short of the tree's real load by more than
    eight times. A file sitting between the two bounds is exactly what
    a coverage guard has to be worth something about.

    The share is a HALF, so the count is derived from the tree rather
    than written down. A hand-written `keep=164` looks stable and is
    not: the tree grows and the count does not, so the estimated share
    climbs -- 49.85% at 327 suites, exactly a half at 328, 50.15% at 329
    -- and any upper cap written by hand is a number the tree's size
    crosses again. Deriving the count makes the share `ceil(n/2)/n`,
    which sits at a half and falls back toward it as the tree grows, so
    the band below is a property of "half" that no tree size can cross.

    The tree is the file's OWN suite set for the same reason one step
    further out (`_file_tree`): the cell count and the shortfall are
    ratios of the tree to the file, so a live tree growing past the
    file shrinks both -- measured, the plan reaches three cells and a
    7x shortfall within 80 arrivals -- and what is left is a claim
    about the file's measured distribution, which is what the bounds
    are claims about. The cell count is compared to the plan the same
    weights pack honestly rather than written down, so it is the
    RATIO that is pinned. Driven through the planner's own CLI, so the
    verdict is the one the workflow would get.
    """
    planner = _planner()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    tree = _file_tree(tmp, data, 'half')
    names = planner.suite_names(tree)
    keep = len(names) // 2
    kept = _thinned_to_the_lightest(data, names, keep=keep)
    coverage = _coverage()
    weights, estimated, _stale = planner.resolve(kept, names, 1.0)
    truth = sum(planner.resolve(
        data['suite_weights'], names, 1.0)[0].values())
    assert len(kept) == keep and len(estimated) == len(names) - keep
    weight_share = coverage.estimated_share(weights, estimated)
    assert weight_share < 0.5, weight_share
    share = coverage.estimated_suite_share(weights, estimated)
    assert 0.5 <= share < 0.55, share
    assert share > coverage.MAX_ESTIMATED_SUITE_SHARE, share
    plan = planner.plan(tree, dict(data, suite_weights=kept))
    honest = planner.plan(tree, data)
    assert len(plan.cells) * 5 <= len(honest.cells), (
        len(plan.cells), len(honest.cells))
    assert truth / sum(weights.values()) > 8, (truth, sum(weights.values()))
    code, err, published = _planner_verdict(
        tmp, dict(data, suite_weights=kept), name='half.json')
    assert code == 1, 'the planner published a matrix on a fiction'
    assert 'suites are estimated' in err, err
    assert 'refresh_timings.py' in err, err
    assert published == '', published


def test_a_light_tailed_recorded_set_is_not_a_share_the_guard_believes(tmp):
    """The weight share, on a real file, is a third of a 16x error.

    The guard's own arithmetic, on the shipped file's own weights over
    the tree that file describes: twenty-nine lightest suites plus the
    heaviest. The estimate is their median, the denominator is their
    sum, and the heaviest weight is most of it -- so the share reads a
    little over a third and passes a 50% bound, while the plan it
    produces is a fraction of the cells the same weights pack honestly
    and totals a fifteenth of the tree's real load. A heavy recorded
    suite flatters the very statistic meant to catch it, and a second
    condition that no recorded weight can move is the only thing that
    closes that.

    Same tree as the control above, for the same reason: the cell count
    and the shortfall here are ratios of the file to the tree, and both
    decay as a live tree outgrows the file this shape is measured
    against (`_file_tree`).
    """
    planner = _planner()
    coverage = _util.load(ROOT / 'scripts' / 'ci' / 'timings_coverage.py',
                          'timings_coverage')
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    tree = _file_tree(tmp, data, 'light-tailed')
    names = planner.suite_names(tree)
    kept = _thinned_to_the_lightest(data, names)
    weights, estimated, _stale = planner.resolve(kept, names, 1.0)
    assert len(kept) == 30 and len(estimated) == len(names) - 30
    truth = sum(planner.resolve(
        data['suite_weights'], names, 1.0)[0].values())
    assert coverage.estimated_share(weights, estimated) < 0.5
    plan = planner.plan(tree, dict(data, suite_weights=kept))
    honest = planner.plan(tree, data)
    assert len(plan.cells) * 5 <= len(honest.cells), (
        len(plan.cells), len(honest.cells))
    assert truth / sum(weights.values()) > 15, (truth, sum(weights.values()))
    refusal = coverage.coverage_refusal(weights, estimated)
    assert refusal is not None, 'the planner published a matrix on a fiction'
    assert 'refresh_timings.py' in refusal, refusal


def _skewed_shares(tmp, recorded, count, name):
    """`(weight_share, suite_share, refusal)` over a tree of `count` suites."""
    suites = [f'test_{index:02d}.py' for index in range(count)]
    tree = fixture_tree(Path(tmp) / name, suites)
    planner = _planner()
    weights, estimated, _stale = planner.resolve(
        recorded, planner.suite_names(tree), 1.0)
    coverage = _coverage()
    return (coverage.estimated_share(weights, estimated),
            coverage.estimated_suite_share(weights, estimated),
            coverage.coverage_refusal(weights, estimated))


def test_the_two_shares_are_different_quantities_and_one_is_nested(tmp):
    """What each bound decides, and the fact that decides for the weight one.

    Ten suites with three recorded at 1.0, 1.0 and 90.0: seven of the
    plan's SUITES are estimated and 7% of its weight, so only the count
    bound can see it. Nine suites with five recorded at 0.001, 0.001, 5,
    5, 5: 57% of the weight and 44% of the suites, so only the weight
    bound refuses it.

    That second case is the whole of the weight bound's domain, and it
    is narrow. The estimate is the median of the recorded set, so with
    `m` recorded weights at median `e` and total `S`, at least
    `floor(m/2)` of them are at or above `e` and the median itself adds
    one more: `S >= (floor(m/2) + 1) * e`. A weight share over its bound
    needs `k * e > S`, hence `k > floor(m/2) + 1` -- more than a third
    of the tree estimated. Below that the weight bound cannot fire at
    any coverage, and the suite refusal now sits ON that same line, so
    the two bounds are no longer nested with room between them: above a
    third the weight comparison chooses which refusal a reader gets,
    and below it the suite bound is the only one that can fire at all.
    That is why the refusal is a third and not a tenth -- the module
    docstring's derivation is the reason, and this is the arithmetic it
    is derived from. Both bounds stay, and this pins the shape that is
    left to the weight one.
    """
    coverage = _coverage()
    heavy_tail, _s, refused = _skewed_shares(
        tmp, {'test_00.py': 1.0, 'test_01.py': 1.0, 'test_09.py': 90.0}, 10,
        'heavy')
    assert 'suites are estimated' in refused, refused
    light_tail, weight_bound, refused = _skewed_shares(
        tmp, {'test_00.py': 0.001, 'test_01.py': 0.001, 'test_02.py': 5.0,
              'test_03.py': 5.0, 'test_04.py': 5.0}, 9, 'light')
    assert 'weight is estimated' in refused, refused
    assert light_tail > coverage.MAX_ESTIMATED_WEIGHT_SHARE, light_tail
    # The nesting, on the fixture that reaches the weight bound: an
    # estimated third of the tree is already past the count bound, so
    # neither bound here is a restatement and the weight one is not a
    # second chance at the same file.
    assert weight_bound > 1 / 3, weight_bound
    assert weight_bound > coverage.MAX_ESTIMATED_SUITE_SHARE, weight_bound
    assert heavy_tail < coverage.MAX_ESTIMATED_WEIGHT_SHARE, heavy_tail


def test_a_fixture_parked_at_each_tier_moves_with_it(tmp):
    """Both comparisons are pinned to a couple of points, not twenty.

    The other fixtures sit at 70% and 50%, so any bound between them
    and a half reads the same and a suite bound several points either
    side of the real one would still be green. These two are 1.7 and 1.6
    points from the refusal: twenty suites with seven unrecorded is 35%
    and is refused, twenty with six is 30% and is published. The refusal
    has to move by more than a twentieth of the share to change either
    answer.

    The note is pinned the same way, and a little more tightly because
    it is the one that has to be loud: ten suites with one unrecorded is
    10.0% and quotes the share, twenty with one is 5.0% and does not.
    Each verdict names the statistic that decided it.
    """
    coverage = _coverage()
    unit = [f'test_{index:02d}.py' for index in range(20)]
    above = {name: 1.0 for name in unit[:13]}
    weight_share, suite_share, refused = _skewed_shares(
        tmp, above, 20, 'above')
    assert 0.3 < suite_share < 0.4, suite_share
    assert weight_share < coverage.MAX_ESTIMATED_WEIGHT_SHARE, weight_share
    assert refused and 'suites are estimated' in refused, refused
    below = {name: 1.0 for name in unit[:14]}
    weight_share, suite_share, published = _skewed_shares(
        tmp, below, 20, 'below')
    assert 0.25 < suite_share < 0.35, suite_share
    assert weight_share < coverage.MAX_ESTIMATED_WEIGHT_SHARE, weight_share
    assert published is None, published
    assert coverage.MAX_ESTIMATED_SUITE_SHARE == 1 / 3, (
        coverage.MAX_ESTIMATED_SUITE_SHARE)
    assert coverage.NOTE_ESTIMATED_SUITE_SHARE == 0.1, (
        coverage.NOTE_ESTIMATED_SUITE_SHARE)
    # The note's own edge, which is what makes it loud rather than
    # decorative: exactly a tenth of the tree is quoted, half of that is
    # not, and a file that measures the whole tree says nothing at all.
    twenty = [f'test_{index:02d}.py' for index in range(20)]
    loud = _fixture_shares(
        tmp, {name: 1.0 for name in twenty[:18]}, 20, 'loud')
    assert loud[1] == 0.1, loud[1]
    assert coverage.coverage_note(loud[0], loud[2]) is not None
    quiet = _fixture_shares(
        tmp, {name: 1.0 for name in twenty[:19]}, 20, 'quiet')
    assert quiet[1] == 0.05, quiet[1]
    assert coverage.coverage_note(quiet[0], quiet[2]) is None
    whole = _fixture_shares(
        tmp, {name: 1.0 for name in twenty}, 20, 'whole')
    assert coverage.coverage_note(whole[0], whole[2]) is None


def _fixture_shares(tmp, recorded, count, name):
    """`(weights, suite_share, estimated)` over a fixture tree."""
    suites = [f'test_{index:02d}.py' for index in range(count)]
    tree = fixture_tree(Path(tmp) / name, suites)
    planner = _planner()
    weights, estimated, _stale = planner.resolve(
        recorded, planner.suite_names(tree), 1.0)
    share = _coverage().estimated_suite_share(weights, estimated)
    return weights, share, estimated


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='bounds_')


if __name__ == '__main__':
    raise SystemExit(main())
