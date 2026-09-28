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
- the SUITE share is the live guard -- no recorded weight can move it,
  and the module's own series is what puts its bound at a tenth;
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
from _timed_basis import fixture_tree  # noqa: E402

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
    """164 recorded of 327: both bounds at a half, and a plan 9x out.

    The shape a refresh leaves when it keeps the lightest recorded
    suites and the one heavy one, which is the shape that flatters the
    weight share -- the heavy weight is most of the denominator it is
    divided by. Here 28.1% of the plan's weight is estimated (under a
    half) and 49.85% of its SUITES, which is under a half and over a
    tenth, the packer derives two cells where the tree's weights want
    fourteen, and the plan's total is 38.6 reference multiples where
    the tree holds 346.9. A file sitting between the two bounds is
    exactly what a coverage guard has to be worth something about, and
    it is committed as the data file and driven through the planner's
    own CLI, so the verdict is the one the workflow would get.
    """
    planner = _planner()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = planner.suite_names(ROOT)
    kept = _thinned_to_the_lightest(data, names, keep=164)
    coverage = _coverage()
    weights, estimated, _stale = planner.resolve(kept, names, 1.0)
    truth = sum(planner.resolve(
        data['suite_weights'], names, 1.0)[0].values())
    assert len(kept) == 164 and len(estimated) == len(names) - 164
    assert coverage.estimated_share(weights, estimated) < 0.5
    assert 0.45 < coverage.estimated_suite_share(weights, estimated) < 0.5
    plan = planner.plan(ROOT, dict(data, suite_weights=kept))
    assert len(plan.cells) == 2, [cell.suites for cell in plan.cells]
    assert truth / sum(weights.values()) > 8, (truth, sum(weights.values()))
    code, err, published = _planner_verdict(
        tmp, dict(data, suite_weights=kept), name='half.json')
    assert code == 1, 'the planner published a matrix on a fiction'
    assert 'suites are estimated' in err, err
    assert 'refresh_timings.py' in err, err
    assert published == '', published


def test_a_light_tailed_recorded_set_is_not_a_share_the_guard_believes(tmp):
    """The weight share, on a real file, is 31.1% of a 15.5x error.

    The guard's own arithmetic, on the shipped file's own weights over
    the real tree: twenty-nine lightest suites plus the heaviest. The
    estimate is their median, the denominator is their sum, and the
    heaviest weight is most of it -- so the share reads 31% and passes
    a 50% bound, while the plan it produces is ONE cell for 327 suites
    and totals 22.3 reference multiples where the tree holds 345.2.
    A heavy recorded suite flatters the very statistic meant to catch
    it, and a second condition that no recorded weight can move is the
    only thing that closes that.
    """
    planner = _planner()
    coverage = _util.load(ROOT / 'scripts' / 'ci' / 'timings_coverage.py',
                          'timings_coverage')
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = planner.suite_names(ROOT)
    kept = _thinned_to_the_lightest(data, names)
    weights, estimated, _stale = planner.resolve(kept, names, 1.0)
    assert len(kept) == 30 and len(estimated) == len(names) - 30
    truth = sum(planner.resolve(
        data['suite_weights'], names, 1.0)[0].values())
    assert coverage.estimated_share(weights, estimated) < 0.5
    plan = planner.plan(ROOT, dict(data, suite_weights=kept))
    assert len(plan.cells) == 1, [cell.suites for cell in plan.cells]
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
    any coverage, and above it the count bound already has, so with the
    count bound at a tenth the weight comparison chooses which refusal a
    reader gets and never whether to give one. Both bounds stay, and
    this pins the shape that is left to the weight one.
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


def test_a_fixture_parked_at_the_count_bound_moves_with_it(tmp):
    """The count comparison is pinned to a couple of points, not twenty.

    The other fixtures sit at 70% and 44%, so any bound between them
    and a half reads the same and a suite bound two points either side
    of the real one would still be green. These two are 2.5 and 2.3
    points from it: eight suites with one unrecorded is 12.5% and is
    refused, thirteen with one is 7.7% and is published. The bound has
    to move by more than a twentieth of the share to change either
    answer, and each verdict names the statistic that decided it.
    """
    coverage = _coverage()
    unit = [f'test_{index:02d}.py' for index in range(12)]
    above = {name: 1.0 for name in unit[:7]}
    weight_share, suite_share, refused = _skewed_shares(
        tmp, above, 8, 'above')
    assert 0.1 < suite_share < 0.15, suite_share
    assert weight_share < coverage.MAX_ESTIMATED_WEIGHT_SHARE, weight_share
    assert refused and 'suites are estimated' in refused, refused
    below = {name: 1.0 for name in unit[:12]}
    weight_share, suite_share, published = _skewed_shares(
        tmp, below, 13, 'below')
    assert 0.05 < suite_share < 0.1, suite_share
    assert weight_share < coverage.MAX_ESTIMATED_WEIGHT_SHARE, weight_share
    assert published is None, published
    assert coverage.MAX_ESTIMATED_SUITE_SHARE == 0.1, (
        coverage.MAX_ESTIMATED_SUITE_SHARE)
