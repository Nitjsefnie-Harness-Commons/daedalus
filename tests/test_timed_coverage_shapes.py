#!/usr/bin/env python3
"""The THIRD condition, and the two measurements the module quotes.

`scripts/ci/timings_coverage.py` refuses on three things, and the other
two are shares of a count. This file holds the third -- the recorded
set's own skew, which is the only one of the three that can tell a
DRIFTED file from a BIASED one -- plus the two numbers the module's own
prose quotes: how fast this tree grows, and what the two share bounds
compare with. It is a separate file because `test_timed_coverage_bounds`
reached the 700-line ceiling these modules are held to, which is the
same reason the guard's controls sit apart from the run-selection ones
in the first place, and a ceiling is not raised for prose.

The fixtures are the shipped file's own weights over the tree that file
DESCRIBES -- its recorded weights plus the names its coverage clause
lists -- so every ratio here is a claim about this file's measured
distribution and not about how many suites the repository has this
week. The tree is built the way `tests/_timed_basis.py` builds it for
the basis compare, and for the reason that helper states: the file
describes its HEAD, and the `suites` job checks out
`refs/pull/N/merge`.

Nothing here restates the module's arithmetic. The skew, the shares and
the refusal are read back out of `timings_coverage` itself, so a control
cannot pass by agreeing with a copy of the rule.
"""
import contextlib
import datetime
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _repo  # noqa: E402
from _repo import ROOT  # noqa: E402
from _timed_basis import (  # noqa: E402
    fixture_tree, unmeasured_names)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _coverage():
    return _util.load(ROOT / 'scripts' / 'ci' / 'timings_coverage.py',
                      'timings_coverage')


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


def _lightest(data, names, keep):
    """The file's own `keep` lightest recorded weights, as a data file.

    The BIASED shape: a refresh that writes the measured set alone, or
    keeps the lightest of what it measured. The suites it dropped are
    the ones the planner then prices at the surviving median, and the
    median of the lightest `keep` is the floor of the tree's
    distribution rather than a sample of it.
    """
    recorded = {name: data['suite_weights'][name] for name in names
                if name in data['suite_weights']}
    light = sorted(recorded, key=lambda name: recorded[name])[:keep]
    return {name: recorded[name] for name in light}


def _truncation_boundary(planner, coverage, data, names):
    """The fewest kept weights still at or under the suite bound: the gap.

    The share bound compares `k` against a third of the tree, so the
    biased files it lets through are exactly the heavily truncated ones.
    This returns the SMALLEST kept count that is still inside the bound
    -- the most truncated file the bound was going to publish, which is
    the worst plan it can produce and therefore the place any further
    condition has to be tested. The count walks with the tree, so a
    fixture written as the reviewer's 222 would have been a fixture
    written as a fraction of a set that changes on every refresh.
    """
    for keep in range(1, len(names)):
        weights, estimated, _stale = planner.resolve(
            _lightest(data, names, keep), names, 1.0)
        if coverage.estimated_suite_share(weights, estimated) <= (
                coverage.MAX_ESTIMATED_SUITE_SHARE):
            return keep
    raise AssertionError('no kept count is inside the suite bound')


def test_the_growth_rate_is_measured_against_the_tree_not_asserted(tmp):
    """`SUITES_PER_DAY` re-derived from this repository's own history.

    The note turns an estimated count into "so that many daily refreshes
    have not landed", and that is the number an operator reads to
    decide how urgent a re-run is. It used to be checked the way the
    module supplies it: the control computed `days` from
    `SUITES_PER_DAY` and asserted the summary contained the string the
    module had just built with `SUITES_PER_DAY`. Any value of the
    constant passed, which is a control reading back a fixture.

    So the constant is measured here instead. Both endpoints are
    re-derived from `HEAD`'s own history -- the tracked `tests/test_*.
    py` count at the newest commit and at the commit nearest each end
    of the module's stated window -- and the module's range has to
    contain what the tree actually did. A rate that has moved reddens
    this, and re-measuring the constant is the fix; that is a true
    statement about the repository rather than a fixture defect, and it
    is the same trade every other operating-point control here makes.

    The window is read out of `SUITES_PER_DAY_BASIS` rather than
    written here, so the two cannot disagree, and the history is walked
    on `HEAD` rather than on a named ref: a `refs/pull/N/merge`
    checkout has no `origin/main`, and this control runs in the `suites`
    job.

    The note names the WINDOWS and no figure. It used to name the
    suite count at the head of each window and the rate each one
    implies, and every one of those was stale within a day of being
    measured while nothing read them, so nothing went red. Reading the
    windows out of it is what keeps the number in the note load-bearing
    at all: the control walks every window the note names, and a note
    naming one it ignores would be a measurement nothing checked -- it
    named three and the control walked two.
    """
    coverage = _coverage()
    fewest, most = coverage.SUITES_PER_DAY
    assert fewest < most, coverage.SUITES_PER_DAY
    assert coverage.SUITES_PER_DAY_BASIS, 'the rate has no stated window'
    now_suites, history = _suites_by_day(ROOT)
    if history is None:
        _util.skip(
            'this checkout does not carry enough history to re-derive the '
            'growth rate; the constant is stated with its window and the '
            'summary quotes its endpoints, which the two note controls '
            'above read back')
    measured = _rates(history, now_suites, _stated_windows())
    assert measured, 'the stated window is not in this history'
    for days, rate in measured:
        assert fewest <= rate <= most, (
            f'{days} days back this tree gained {rate:.1f} suites a day, '
            f'outside the {coverage.SUITES_PER_DAY} range '
            f'{coverage.SUITES_PER_DAY_BASIS} states; re-measure it')


def _suites_by_day(repo):
    """`(suites at HEAD, {commit date: suites})` over the tracked history.

    The planner's own enumeration rule, at every commit: a suite is a
    tracked `tests/test_*.py`, which is what `time_tests.selected`
    admits with no globs. `(None, None)` where the history is too short,
    which is the measured fact the caller skips on.
    """
    log = _repo.git_output(
        repo, 'log', '--format=%cI %H', '-n', '2000').splitlines()
    history = {}
    for line in log:
        date, sha = line.split()
        count = _repo.git_output(
            repo, 'ls-tree', '-r', '--name-only', sha, '--', 'tests/')
        history[date[:10]] = sum(
            1 for path in count.split()
            if path.startswith('tests/test_') and path.endswith('.py'))
    if len(history) < 2:
        return None, None
    return history[max(history)], history


def _rates(history, now_suites, windows):
    """`(days back, suites a day)` at each window the module's basis names."""
    newest = max(history)
    today = datetime.date.fromisoformat(newest)
    rates = []
    for days in windows:
        want = (today - datetime.timedelta(days=days)).isoformat()
        older = [date for date in history if date <= want]
        if not older:
            continue
        span = (today - datetime.date.fromisoformat(max(older))).days
        if span < 1:
            continue
        rates.append((span, (now_suites - history[max(older)]) / span))
    return rates


# The windows the module's own note names, in the two spellings a prose
# list of them can take. A suite count and a rate are true of one
# afternoon and false of the next, so the note quotes neither; the
# window is the part that does not move, and it is the part the control
# walks, so a note naming a window the control ignores names a
# measurement nothing checked.
_WINDOWS = r'over windows of ([\d, and]+?) days back'


def _stated_windows():
    """The window lengths `SUITES_PER_DAY_BASIS` names, in days."""
    basis = _coverage().SUITES_PER_DAY_BASIS
    match = re.search(_WINDOWS, basis)
    assert match, f'the rate names no window to measure: {basis}'
    return [int(days) for days in re.findall(r'\d+', match.group(1))]


def test_a_truncated_recorded_set_is_refused_under_the_share_bound(tmp):
    """The file the bound was about to let through, and it is not one.

    The suite share is a COUNT, and a count is blind to WHICH suites
    are missing. So a refresh that keeps the lightest weights of what it
    measured, or writes one collapsed cell's worth of a matrix and
    calls that the tree, produces a file whose estimated share is a
    third and whose recorded set is the BOTTOM of the distribution the
    planner is about to price a third of the tree from. Measured on
    the shipped file's own weights that plan came to 42.6 against a
    true 356.4 -- 8.4x out, in two cells where the same weights pack
    fifteen -- behind a step summary reading `heaviest/median 1.000
    against a margin of 0.35`, because a total that is 8.4x invented
    is balanced with itself and the margin is computed over it.

    The count is DERIVED, not the reviewer's 222: `_truncation_boundary`
    walks down to the largest kept count whose estimated share is still
    at or under a third, which is the exact file the bound was about to
    publish and therefore the one a further condition has to hold. The
    assertions are that the share is inside the bound -- so the share
    cannot be what stops it -- that the CLI refuses, and that it
    refuses on the recorded set's skew and names it.
    """
    planner = _planner()
    coverage = _coverage()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    tree = _file_tree(tmp, data, 'truncated')
    names = planner.suite_names(tree)
    keep = _truncation_boundary(planner, coverage, data, names)
    kept = _lightest(data, names, keep)
    weights, estimated, _stale = planner.resolve(kept, names, 1.0)
    share = coverage.estimated_suite_share(weights, estimated)
    skew = coverage.recorded_skew(weights, estimated)
    # Neither share can be what refuses it: this file is inside both.
    assert share <= coverage.MAX_ESTIMATED_SUITE_SHARE, share
    assert coverage.estimated_share(weights, estimated) <= (
        coverage.MAX_ESTIMATED_WEIGHT_SHARE), weights
    assert skew < coverage.MIN_RECORDED_SKEW, skew
    code, err, summary, published = _verdict(
        tree, tmp, dict(data, suite_weights=kept), name='truncated.json')
    assert code == 1, (keep, share, skew, summary)
    assert 'not a sample of the tree' in err, err
    assert f'{skew:.2f}' in err, err
    assert 'refresh_timings.py' in err, err
    assert summary == '' and published == '', (summary, published)


def test_the_two_shapes_are_told_apart_by_the_recorded_set_alone(tmp):
    """Same coverage, same shares, opposite verdicts: only the skew moves.

    The whole claim the third condition rests on is that a drifted
    file's recorded set is REPRESENTATIVE and a biased one is not, and
    that the recorded values alone can tell them. So this drives both
    shapes at the SAME estimated share, from the same file and the same
    tree, and asserts the two verdicts differ while the two shares do
    not.

    It also pins what the drift fuse costs, which is nothing: the
    recorded set of a drifted file is the whole measured population, so
    its skew is one number at every arrival count from one day to
    nineteen, and no number of missed refreshes reaches the floor. That
    is the property the tier's price rests on, and a floor derived any
    other way -- one reading the estimated count, say -- would spend it.
    """
    planner = _planner()
    coverage = _coverage()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    tree = _file_tree(tmp, data, 'two-shapes')
    names = planner.suite_names(tree)
    recorded = {name: data['suite_weights'][name] for name in names
                if name in data['suite_weights']}
    # The same number of estimated suites in both shapes, so the share
    # is identical and nothing but the recorded set can decide. The
    # file's own tree already prices some of its suites at the median,
    # so the arrivals are what has to be ADDED to that base rather than
    # the whole count: the file once described exactly the 328 it
    # recorded, and naming the 12 it does not is what moved this.
    keep = _truncation_boundary(planner, coverage, data, names)
    base = sum(1 for name in names if name not in data['suite_weights'])
    dropped = len(names) - keep
    arrivals = [f'test_arrived{index:04d}.py'
                for index in range(dropped - base)]
    drifted = fixture_tree(Path(tmp) / 'drifted',
                           sorted(set(names) | set(arrivals)))
    for kept, tree_under, refused in (
            (_lightest(data, names, keep), tree, True),
            (recorded, drifted, False)):
        under = set(planner.suite_names(tree_under))
        weights, estimated, _stale = planner.resolve(kept, under, 1.0)
        share = coverage.estimated_suite_share(weights, estimated)
        assert len(estimated) == dropped, (keep, len(estimated))
        assert share <= coverage.MAX_ESTIMATED_SUITE_SHARE, (keep, share)
        assert coverage.estimated_share(weights, estimated) <= (
            coverage.MAX_ESTIMATED_WEIGHT_SHARE), (keep, weights)
        reason = coverage.coverage_refusal(weights, estimated)
        assert (reason is not None) is refused, (keep, reason)
        if refused:
            assert 'not a sample of the tree' in reason, reason
    # The drift fuse: the recorded set does not change, so the ratio
    # does not, at any arrival count this tree can reach.
    skew = coverage.recorded_skew(recorded, [])
    assert skew > coverage.MIN_RECORDED_SKEW, skew
    for days in (1, 2, 5, 9, 19):
        arrivals = [f'test_arrived{index:04d}.py'
                    for index in range(days * coverage.SUITES_PER_DAY[0])]
        drifted = fixture_tree(Path(tmp) / f'drift-{days}',
                               sorted(set(names) | set(arrivals)))
        weights, estimated, _stale = planner.resolve(
            recorded, set(planner.suite_names(drifted)), 1.0)
        assert coverage.recorded_skew(weights, estimated) == skew, days


def test_the_weight_bound_operators_are_themselves_pinned(tmp):
    """Both new operators: one is load-bearing, and one cannot be reached.

    The VALUES of `MAX_ESTIMATED_WEIGHT_SHARE` and
    `MIN_RECORDED_SKEW` are pinned elsewhere; the operators that read
    them were not. Both are now, and they come out differently, which is
    the finding.

    THE SKEW FLOOR'S OPERATOR MATTERS. A recorded set of two 1.0s and a
    `2 * MIN_RECORDED_SKEW` sits exactly on the floor, and the module
    publishes it -- at a bound the plan is published, above it the plan
    is refused, which is the same convention the two share bounds use
    and the reason the operator is `<` and not `<=`. Swapping the two
    characters reddens this.

    THE WEIGHT BOUND'S OPERATOR CANNOT BE REACHED, so it is not a
    style question. For a weight share of exactly a half the recorded
    total would have to be exactly the estimated total, `S = k * e`;
    but the suite bound caps `k` at `floor(m/2)` and the median gives
    `S >= (floor(m/2) + 1) * e` for `m` recorded weights, so
    `S > k * e` always and the share is strictly under a half. 200,000
    random recorded sets with the suite share held at a third or under
    peaked at 0.4202. This asserts the algebra rather than a sample of
    it, and then drives the extreme: a recorded set with every weight
    equal (the smallest median-to-total ratio there is) at the largest
    `k` the suite bound admits, which is the shape that comes closest.
    """
    coverage = _coverage()
    floor = coverage.MIN_RECORDED_SKEW
    for heavy, refused in ((2 * floor, False),
                           (2 * floor - 0.001, True),
                           (2 * floor + 0.001, False)):
        recorded = {'test_00.py': 1.0, 'test_01.py': 1.0,
                    'test_02.py': heavy}
        # One estimated of four is a quarter of the suites and a seventh
        # of the weight, so neither share arm can fire and the skew floor
        # is the only condition this case reaches.
        estimated = ['test_03.py']
        weights = dict(recorded, **{'test_03.py': 1.0})
        skew = coverage.recorded_skew(weights, estimated)
        assert _close(skew, (2 + heavy) / 3), (heavy, skew)
        reason = coverage.coverage_refusal(weights, estimated)
        assert (reason is not None) is refused, (heavy, skew, reason)
        if refused:
            assert 'not a sample of the tree' in reason, reason

    # The weight bound's own edge, approached as closely as it can be.
    # For `m` recorded weights at median `e` summing `S`, the share at
    # the largest `k` the suite bound admits is `k e / (S + k e)`, and
    # the median gives `S >= (floor(m/2) + 1) e`, so the share is at
    # most `m / (3m + 2)` for even `m` -- under a half at every size,
    # and rising towards it. Driven with the recorded weights all equal,
    # which is the shape that PUTS that median bound, so the share is
    # exactly the third the suite share already refused past.
    for recorded_count in (2, 4, 6, 12, 60, 600):
        estimated_count = recorded_count // 2
        names = [f'test_{index:05d}.py'
                 for index in range(recorded_count + estimated_count)]
        weights = {name: 1.0 for name in names}
        estimated = names[recorded_count:]
        share = coverage.estimated_share(weights, estimated)
        suites = coverage.estimated_suite_share(weights, estimated)
        assert suites <= coverage.MAX_ESTIMATED_SUITE_SHARE, suites
        assert _close(share, 1 / 3), (recorded_count, share)
        assert share < coverage.MAX_ESTIMATED_WEIGHT_SHARE, (
            recorded_count, share)
        assert recorded_count / (3 * recorded_count + 2) < (
            coverage.MAX_ESTIMATED_WEIGHT_SHARE), recorded_count
        # Uniform recorded weights cannot clear the skew floor, so what
        # refuses them is the THIRD condition and never the weight arm --
        # which is the whole point: the arm whose operator is unpinned
        # has no input of its own to be given.
        reason = coverage.coverage_refusal(weights, estimated)
        assert reason is not None and 'not a sample of the tree' in reason, (
            recorded_count, share, reason)
        assert 'weight is estimated' not in reason, reason


def _close(got, want):
    """A 1e-9 relative comparison, so a float sum is not an equality test."""
    return abs(got - want) <= 1e-9 * max(1.0, abs(want))


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='shapes_')


if __name__ == '__main__':
    raise SystemExit(main())
