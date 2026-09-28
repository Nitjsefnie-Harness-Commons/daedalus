#!/usr/bin/env python3
"""Which downloaded runs count as evidence for a refresh.

A refresh reads the runs under a root, picks a sample, and writes the
data file from it. Two rules decide whether a run is in that sample,
and both live in `scripts/ci/timings_runs.py`: a run is judged against
the cell set of the newest run that produced any, and a run that
produced ONE cell where the file bounds the matrix at more than one is
a collapsed matrix rather than a partition, so it is skipped and
reported instead of becoming the reference every other run is then
filed `incomplete` against.

That is not a hypothetical shape. It is how the shipped data file came
to describe 28 of the tree's 326 suites: the one-cell run became the
partition, the runs that would have carried the tree were under the
root and were stepped over, and the write took the measured set alone.
`test_timed_refresh.py` covers the other half -- that a write is a
union -- and `test_timed_planner.py` covers the guard on the other
side of the file, which refuses a plan whose weight is mostly
estimated.

One more thing lives here because its subject is what a refresh
RECORDS rather than what it packs. The guard on the other side of the
file, `scripts/ci/timings_coverage.py`, compares two shares and the
weight one alone cannot hold it: a file recording the twenty-nine
LIGHTEST suites of the tree plus its heaviest estimates ninety-one per
cent of the plan's suites and, because that one heavy weight inflates
the denominator the weight share is divided by, only thirty-one per
cent of its weight. That is under the weight bound, the planner
published one cell for 327 suites, and the plan's total was 22.3 where
the tree really holds 345.2 -- a 15.5x understatement, worse than the
3.15x the guard exists to stop. Its bound, its note and the fixtures
that pin them are in `test_timed_coverage_bounds.py`.

The commit seam is not here. Executing it rather than grepping it --
because a line planted in the commit step left every substring an
earlier control asserted in place -- needs the workflow's two `run:`
blocks, an ubuntu-only step replayed on whatever machine runs the
suite, and a control that pins the scope of a skip; that is
`test_commit_step_seam.py`, which carries all of it.

The artifacts are fixtures under a temp tree: no API, no `gh`, no
network. Each test builds a run the way the timed job leaves one --
`<run>/<cell>/head-N/<suite>.json`, one JSON per suite, plus the cell's
reference reading -- and drives `refresh_timings.main()` over it. The
one that is not about a run at all reads the real shipped file.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _timed_basis import (  # noqa: E402
    fixture_tree, unmeasured_names, write_run as _write_run)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _seed(tmp, weights, max_cells=15, name='suite-timings.json'):
    """The data file a refresh starts from: the file's own defaults."""
    path = Path(tmp) / name
    seed = {'schema_version': _planner().SCHEMA_VERSION,
            'target_cell_weight': 10.0, 'max_cells': max_cells,
            'units': 'reference-multiples', 'measured_from': 'tests run 1',
            'runs': 1, 'suite_weights': weights}
    path.write_text(json.dumps(seed, indent=2) + '\n', encoding='utf-8')
    return path


def _drive(tmp, root, weights, runs=3, max_cells=15, tree=None, **flags):
    """Refresh `weights` from `root` and report what the command did.

    One shape because every test here is the same three steps with
    different numbers: seed a data file, run the refresher over a runs
    root, read the file back. `flags` are the refresher's own options by
    name, so a test that needs `--message-file` says so and one that
    needs nothing says nothing. Returns the file's TEXT, the exit code
    and stderr -- a test that wants the weights parses the text, and one
    that wants to prove the file is untouched compares it verbatim.

    The tree is a fixture holding exactly the file's recorded suites,
    because the rules here are judged against what the TREE holds: the
    collapse is counted against the recorded suites the tree still has,
    and against the real repository's 327 suites every fixture name
    would be a deleted suite and the rule could never fire.
    """
    path = _seed(tmp, weights, max_cells)
    tree = tree if tree is not None else fixture_tree(tmp, sorted(weights))
    argv = ['--runs-root', str(root), '--out', str(path),
            '--runs', str(runs), '--tree', str(tree)]
    argv += [item for name in sorted(flags)
             for item in (f'--{name.replace("_", "-")}', str(flags[name]))]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                          'refresh_timings').main(argv)
    return path.read_text(encoding='utf-8'), code, err.getvalue()


def test_a_one_cell_run_is_not_the_partition_when_an_older_measured_more(
        tmp):
    """A collapsed matrix must not make every richer run `incomplete`.

    `select` takes the reference cell set from the newest run that
    produced any cell, so a one-cell run is "complete" against its own
    one-cell set and every older, richer run is filed as incomplete and
    skipped. That is what turned a single bad run into a 28-suite file:
    the runs that would have carried the tree were under the root and
    were stepped over. The newest run here measures one cell and one of
    the two suites the file records; the two behind it measured two
    cells and both suites, and the sample must come from those.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 140, {'cell-01': {'test_a.py': 4.0}})
    for run_id in (139, 138):
        _write_run(root, run_id, {'cell-01': {'test_a.py': 8.0},
                                  'cell-02': {'test_b.py': 2.0}})
    text, _code, err = _drive(tmp, root, {'test_a.py': 2.0, 'test_b.py': 1.0})
    written = json.loads(text)
    assert written['measured_from'] == '139, 138', written
    assert written['suite_weights'] == {'test_a.py': 4.0,
                                        'test_b.py': 1.0}, written
    assert '140' in err and 'one cell' in err, err


def test_a_suite_seconds_is_the_mean_of_its_rounds_not_the_first(tmp):
    """Two head rounds at different values give their mean, not one of them.

    A suite's seconds in a run are "the mean of its per-round totals over
    the rounds that carried it", and that mean is what makes a weight
    robust to one noisy head round: a runner that happened to be busy
    for the first round moves the mean, and a runner that was busy for
    the SECOND round moves it the same amount, while either one alone
    would have decided the weight.

    The repository's only two-head-round fixture gave both rounds the
    same number, so `mean(values)` and `values[0]` were the same
    function on every fixture in the suite set: replacing one with the
    other left all seven of these suites green. The first round here is
    a third of the second, and the file the refresher writes carries
    the mean, divided by the cell's own reference reading.
    """
    runs = _util.load(ROOT / 'scripts' / 'ci' / 'timings_runs.py',
                      'timings_runs')
    root = Path(tmp) / 'runs'
    # Ten at 10.0 and 30.0, so the mean is 20.0 and either round alone
    # is 10.0 or 30.0. The other suite carries 4.0 in both rounds, so a
    # parser that read one round for everything would give 2.0 here and
    # 5.0 for the noisy one.
    _write_run(root, 600,
               {'cell-01': {'test_a.py': 10.0}, 'cell-02': {'test_b.py': 4.0}},
               reference=2.0, rounds={'test_a.py': (10.0, 30.0)})
    cell = root / '600' / 'cell-01'
    assert runs.suite_seconds(cell, 600) == {'test_a.py': 20.0}, (
        runs.suite_seconds(cell, 600))
    assert runs.suite_seconds(root / '600' / 'cell-02', 600) == {
        'test_b.py': 4.0}
    # The same number through the writer, which is where the aggregation
    # becomes the committed weight the whole data file is built on.
    text, code, err = _drive(tmp, root, {'test_a.py': 5.0, 'test_b.py': 2.0},
                             runs=1)
    assert code == 0, err
    assert json.loads(text)['suite_weights'] == {'test_a.py': 10.0,
                                                 'test_b.py': 2.0}, text


def test_a_cell_with_no_measured_round_is_not_a_cell_of_the_run(tmp):
    """A cell that died before its first round is not half a run.

    `cell_dirs` is the filter that keeps a timed job's leftovers out of
    a measurement. Drop it and a run holding one good cell beside a
    `cell-02/` that only ever received a `verdict.json` -- which is
    exactly what a cell that lost its runner before writing a single
    suite summary leaves behind -- reports BOTH, and `read_run` then
    raises for the whole run: one dead cell out of fifteen takes the
    fourteen good ones with it. `select` propagates the raise,
    `refresh()` does not catch it, and `main()` returns 1, so the daily
    cron writes nothing for a run that measured the tree perfectly.

    The shipped code is right; nothing held the filter. Both halves are
    asserted here because either alone is a weaker claim: the set
    excluding the leftover, and a read of the run beside it succeeding
    and carrying the good cell's suites.
    """
    runs = _util.load(ROOT / 'scripts' / 'ci' / 'timings_runs.py',
                      'timings_runs')
    root = Path(tmp) / 'runs'
    _write_run(root, 610, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 2.0}})
    leftover = root / '610' / 'cell-02'
    leftover.mkdir(parents=True)
    (leftover / 'verdict.json').write_text(
        '{"name": "cell-02"}', encoding='utf-8')
    (leftover / 'ratio.txt').write_text('n/a\n', encoding='utf-8')
    assert sorted(runs.cell_dirs(root / '610')) == ['cell-01']
    weights, references = runs.read_run(root / '610', 610)
    assert weights == {'test_a.py': 2.0, 'test_b.py': 1.0}, weights
    assert sorted(references) == ['cell-01'], references
    # And the leftover is not a partition either, so `select` takes the
    # good cell's run as a one-cell run rather than filing it incomplete
    # against a two-cell set that never existed.
    selected, report = runs.select([(610, root / '610')], 1, 15, 0)
    assert [run_id for run_id, _w, _r in selected] == [610], selected
    assert report['incomplete'] == [], report


def test_a_total_that_needs_too_many_probes_is_a_refusal_not_a_walk(tmp):
    """A weight at 1e300 is a refusal, and the shipped total still derives.

    `derive_target` walks candidate targets from one step to the whole
    total, so the number of candidates is the total's MAGNITUDE and not
    the tree's size -- and every one of them is a `plan()` with its own
    `git ls-files`. `read_timings` accepts any positive finite weight,
    so a hand-edited or badly-merged data file with one suite recorded
    at 1e300 puts 2e299 candidates between the refresher and a return.
    The `timed` job's 30-minute timeout bounds that to a refresh that
    writes nothing, in the job that refreshes the file on a cron.

    Two halves, and the second is the one that would be lost. The
    refusal is not a scope: a total inside the bound still derives, at
    a magnitude well past the shipped file's own, so a file that is
    merely LARGE is not what this turns away -- a file whose weights
    are not runtimes is.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    live = [f'test_{index:02d}.py' for index in range(20)]
    tree = fixture_tree(tmp, live)
    # Inside the bound: 19 light suites and one heavy, the shape every
    # other target control here uses, at a magnitude the bound admits.
    # The bound is on the TOTAL's step count, so the heavy suite takes
    # what is left of the allowance after the nineteen ones.
    allowance = bounds.MAX_TARGET_PROBES * bounds.TARGET_STEP - 19.0
    inside = {name: 1.0 for name in live[:19]}
    inside[live[19]] = allowance
    assert sum(inside.values()) / bounds.TARGET_STEP == (
        bounds.MAX_TARGET_PROBES), sum(inside.values())
    target = bounds.derive_target(tree, inside, 15)
    assert target % bounds.TARGET_STEP == 0 and target > 0, target
    # Outside it: one suite recorded at a magnitude no runtime has.
    absurd = {name: 1.0 for name in live[:19]}
    absurd[live[19]] = 1e300
    try:
        bounds.derive_target(tree, absurd, 15)
    except bounds.BoundsError as error:
        said = str(error)
        assert 'candidate targets' in said, said
        assert str(bounds.MAX_TARGET_PROBES) in said, said
        assert 'not a runtime' in said, said
        assert 'refresh_timings.py' in said, said
    else:
        raise AssertionError('derive_target returned a target for 1e300')


def test_a_recorded_zero_weight_cannot_reach_the_move_divisor(tmp):
    """The divisor is positive because the SCHEMA says so, not by luck.

    `_moved` divides by the recorded weight, so a zero there is a
    `ZeroDivisionError` in the middle of a refresh. It used to carry an
    arm for one, on the claim that the seed's four-decimal rounding can
    produce a sub-millisecond suite at zero -- and `_rounded` is the
    answer to exactly that, on the write side. So the arm was dead code
    guarding a case the writer already prevents, and the control that
    drove it was pinning the dead arm rather than the invariant.

    This pins the invariant at the end that carries it: the schema
    refuses a non-positive recorded weight, so `recorded` cannot hold
    one, and the writer never produces one to put there.
    """
    planner = _planner()
    file = Path(tmp) / 'zero.json'
    for weight in (0, 0.0, -1.5):
        file.write_text(json.dumps({
            'schema_version': planner.SCHEMA_VERSION,
            'target_cell_weight': 10.0, 'max_cells': 15,
            'units': 'reference-multiples', 'measured_from': 'tests run 1',
            'runs': 1, 'suite_weights': {'test_tiny.py': weight}}),
            encoding='utf-8')
        try:
            planner.read_timings(file)
        except planner.PlanError as error:
            assert 'must be above zero' in str(error), (weight, error)
        else:
            raise AssertionError(f'a weight of {weight!r} was accepted')
    # And the writer's own half: a weight that rounds to zero is written
    # at its own value rather than at the rounded one.
    refresh = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                         'refresh_timings')
    # pylint: disable=protected-access
    assert refresh._rounded(0.00001) == 0.00001, refresh._rounded(0.00001)
    assert refresh._rounded(1.23456) == 1.2346, refresh._rounded(1.23456)


def test_a_deleted_suites_weight_does_not_move_the_derived_target(tmp):
    """The target is derived from what the TREE holds, not the file.

    The union carries a weight forward for every suite the runs did not
    measure, including one the tree has since deleted -- no run can ever
    measure a deleted suite, so the entry is permanent. The planner
    drops such a weight and names it `stale`, and the three consumers
    that are not the planner have to as well: `derive_target` summed
    the raw dict, so two dead weights of 900 beside six live ones of
    20.0 put the derived target at 130, and a 130 target packs a
    120-multiple live tree into ONE cell where its own weights ask for
    six. The target is the file's other policy number, and this is the
    chokepoint every write passes through.
    """
    planner = _planner()
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    live = [f'test_{index:02d}.py' for index in range(6)]
    tree = fixture_tree(tmp, live)
    weights = {name: 20.0 for name in live}
    weights['test_gone_a.py'] = 900.0
    weights['test_gone_b.py'] = 900.0
    data = json.loads(_seed(tmp, weights).read_text(encoding='utf-8'))
    target, _note = bounds.verify_target(tree, data, 15)
    plan = planner.plan(tree, dict(data, target_cell_weight=target))
    assert len(plan.cells) == 6, [cell.suites for cell in plan.cells]
    assert plan.stale == ['test_gone_a.py', 'test_gone_b.py'], plan.stale


def test_a_deleted_suites_weight_is_not_a_recorded_suite_for_the_collapse(
        tmp):
    """The collapse rule counts what the TREE holds, not the file.

    The third consumer: the collapsed-run rule is judged on how many
    suites the file already records, and it was handed the raw count, so
    fifty weights for deleted suites stood in for fifty suites the
    planner will never pack. A file with six live and fifty dead
    weights, refreshed by a one-cell run that measured all six live
    suites, was filed `degenerate` -- and the refresh then found
    nothing to sample, so the file could only recover by hand.
    """
    live = [f'test_{index:02d}.py' for index in range(6)]
    tree = fixture_tree(tmp, live)
    weights = {name: 1.0 for name in live}
    for index in range(50):
        weights[f'test_gone_{index:02d}.py'] = 1.0
    root = Path(tmp) / 'runs'
    _write_run(root, 300, {'cell-01': {name: 4.0 for name in live}},
               reference=2.0)
    text, _code, err = _drive(tmp, root, weights, runs=1, tree=tree)
    assert 'collapsed' not in err, err
    assert 'carried forward' in err, err
    assert json.loads(text)['measured_from'] == '300', text


def test_a_refresh_with_only_a_collapsed_matrix_to_choose_from_is_refused(
        tmp):
    """Nothing usable under the root: a named refusal, and no write.

    The healthy limb of the control above removes the richer runs, and
    the single one-cell run left is not a partition of a tree this file
    bounds at fifteen cells. The file must come back byte-identical
    rather than narrowed to whatever that run happened to measure.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 150, {'cell-01': {'test_a.py': 4.0}})
    weights = {'test_a.py': 2.0, 'test_b.py': 1.0}
    text, _code, err = _drive(tmp, root, weights)
    assert 'wrote nothing' in err, err
    assert '150' in err and 'one cell' in err, err
    assert 'max_cells bound of 15' in err, err
    assert json.loads(text)['suite_weights'] == weights, text


def test_a_one_cell_run_that_measured_everything_is_not_a_collapse(tmp):
    """The refusal needs BOTH conditions, and this is the one that is not.

    A single cell against a bound of fifteen is on its own the shape a
    small tree leaves, not evidence of anything: this file records both
    suites, the run measured both of them, and the refresh is an
    ordinary measurement that leaves the file alone. A guard firing
    here would stop the refresher on every hand-built runs root, and the
    two conditions exist so that it does not.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 160, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 2.0}})
    weights = {'test_a.py': 2.0, 'test_b.py': 1.0}
    text, _code, err = _drive(tmp, root, weights)
    assert 'wrote nothing' in err, err
    assert 'one cell' not in err, err
    assert json.loads(text)['suite_weights'] == weights, text


def test_a_file_bounded_at_one_cell_takes_a_one_cell_measurement(tmp):
    """A seed derives its bound from the run that measured it.

    A tree small enough to be one cell has a file bounded at one, so a
    one-cell run of that tree is exactly the partition the bound says
    to expect, and refusing it would stop the refresher on every small
    tree.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 170, {'cell-01': {'test_a.py': 4.0}})
    _text, _code, err = _drive(tmp, root, {'test_a.py': 2.0,
                                           'test_b.py': 1.0}, max_cells=1)
    assert 'carried forward' in err, err
    assert 'one cell' not in err, err


def test_a_one_cell_candidate_that_lost_its_reference_is_stepped_over(tmp):
    """Counting a collapsed run's suites must not read its references.

    The collapsed-run rule needs a candidate's suite count before it
    knows whether that candidate is the partition, and it got the count
    by reading the whole run. A cell that lost its `reference.json` is
    a REFUSAL in that path, so a run that would have been filed
    `incomplete` and stepped over -- which is what the base does with
    it, and what the two dispositions agree is right -- instead
    aborted the entire refresh, including the good two-cell run behind
    it. Same input, same root, two different answers from the two
    versions of this code.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 200, {'cell-01': {'test_a.py': 4.0},
                           'cell-02': {'test_b.py': 4.0}})
    _write_run(root, 199, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0}},
               reference=None)
    text, code, err = _drive(tmp, root, {'test_a.py': 2.0, 'test_b.py': 1.0})
    assert code == 0, err
    assert json.loads(text)['measured_from'] == '200', text
    assert '199' in err and 'incomplete' in err, err


def test_a_multi_cell_run_that_measured_less_is_carried_not_refused(tmp):
    """Limb one of the collapsed rule, on its own: two cells is a matrix.

    The rule is a conjunction of three, and the two that read the FILE
    have controls of their own. The one that reads the RUN -- a single
    cell against a multi-cell bound -- did not, and a mutation that
    dropped it left every other control green. Here the newest run has
    TWO cells and measured one of the two suites the file records: a
    partial matrix, which the file carries forward by the union, and
    not the collapse the rule exists to stop.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 210, {'cell-01': {'test_a.py': 4.0},
                           'cell-02': {'test_c.py': 2.0}})
    text, _code, err = _drive(tmp, root, {'test_a.py': 2.0, 'test_b.py': 1.0,
                                          'test_d.py': 3.0}, runs=1)
    assert 'carried forward' in err, err
    assert 'one cell' not in err, err
    assert json.loads(text)['suite_weights'] == {
        'test_a.py': 2.0, 'test_b.py': 1.0, 'test_c.py': 1.0,
        'test_d.py': 3.0}, text


def test_a_partial_run_does_not_claim_to_have_measured_the_tree(tmp):
    """The `basis` is about the RUNS, not about the file the union wrote.

    The write is a union, so a suite the runs did not measure is in
    the file anyway -- and the coverage clause was driven by the file's
    own weights, so it stopped counting those suites as estimated. A
    two-cell run measuring three of five recorded suites then wrote a
    file whose own prose read "every suite in the tree is measured by
    these runs". The committed file is honest today and a generator
    that agrees with the file it wrote cannot see it: both are wrong
    together. This one is the runs' side, and the cell-count clause
    beside it is the same defect in the other sentence.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 220, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0},
                           'cell-02': {'test_c.py': 4.0}})
    weights = {'test_a.py': 1.0, 'test_b.py': 1.0, 'test_c.py': 1.0,
               'test_d.py': 1.0, 'test_e.py': 1.0}
    text, _code, err = _drive(tmp, root, weights, runs=1)
    basis = json.loads(text)['basis']
    assert 'wrote' in err, err
    assert 'every suite in the tree is measured' not in basis, basis
    assert "2 of the tree's 5 suites are not measured" in basis, basis
    assert 'test_d.py, test_e.py' in basis, basis
    # The other sentence: two measured cells are not the concurrency
    # the repository runs, whatever the file went on to derive.
    assert 'the concurrency the repository runs today' not in basis, basis
    assert 'the measured run ran 2 cells,' in basis, basis


def test_a_seed_that_measured_a_corner_of_the_tree_is_refused(tmp):
    """The one writer with no previous file gets the bound before its write.

    A refresh is a union, so it can only widen what the file already
    described and the planner's own guard is enough. A seed has
    nothing to widen: it writes one run's measured set and nothing
    else, and `select`'s collapse rule exempts a file bounded at one
    cell because a seed derives that bound from the run that measured
    it. So a run that executed one cell of a fifteen-cell matrix seeds
    a file describing a corner of the tree, and that file is exactly
    the one the coverage guard exists to refuse -- only the refusal
    arrives at plan time, in a different job, naming a different
    remedy. A writer refuses before it writes; the refresher already
    does that for the target (`verify_target`), and this is the same
    chokepoint for the coverage bound.

    The fixture is a two-cell run over a twenty-suite tree that
    measured four: 16 of 20 suites estimated, 80% of the plan, a
    single borrowed median repeated, against a tenth.
    """
    refresh = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                         'refresh_timings')
    live = [f'test_{index:02d}.py' for index in range(20)]
    tree = fixture_tree(tmp, live)
    root = Path(tmp) / 'runs'
    _write_run(root, 240, {'cell-01': {name: 4.0 for name in live[:2]},
                           'cell-02': {name: 4.0 for name in live[2:4]}})
    out = _seed(tmp, {}, max_cells=2)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = refresh.main(['--runs-root', str(root), '--out', str(out),
                             '--seed', '--tree', str(tree)])
    said = err.getvalue()
    assert code == 1, said
    # The coverage guard's own two sentences, whichever bound it stops
    # on: no other refusal in this module prints either of them.
    assert 'the file records 4 of the tree' in said, said
    assert 're-derive it from a run that measured the tree' in said, said
    assert json.loads(out.read_text(encoding='utf-8'))['suite_weights'] == {}


def test_the_coverage_clause_separates_a_carried_weight_from_an_estimate(
        tmp):
    """Two kinds of unmeasured suite, priced two ways, named two ways.

    The write is a union, so a suite the runs did not measure is in the
    file anyway when the file already recorded it, and the planner
    prices it at ITS OWN recorded weight. A suite the file records
    nothing about is priced at the median of the recorded ones. The
    clause beside the weight clause called both of them "estimated at
    the median of the recorded weights" -- false of the carried half,
    and false in a committed artifact a human reads to decide whether
    to trust the file, where the weight clause adds their real values.

    Four of the six recorded suites here are measured by the run and the
    tree holds a seventh the file has never seen, so three suites are
    unmeasured: two the file records and one it does not. That is the
    only shape that tells the two halves apart, and it is the ordinary
    one -- the union carries everything the file had, so an unmeasured
    suite is estimated exactly when it arrived after the last write.
    """
    live = [f'test_{index:02d}.py' for index in range(6)]
    root = Path(tmp) / 'runs'
    # Two cells, so the collapsed-run rule has nothing to say about it.
    _write_run(root, 230, {'cell-01': {name: 4.0 for name in live[:2]},
                           'cell-02': {name: 4.0 for name in live[2:4]}})
    weights = {name: 1.0 for name in live}
    text, code, err = _drive(tmp, root, weights, runs=1,
                             tree=fixture_tree(tmp, live + ['test_06.py']))
    assert code == 0 and 'wrote' in err, err
    basis = json.loads(text)['basis']
    assert (
        "3 of the tree's 7 suites are not measured by these runs, 2 carried "
        "at the weight this file already recorded and 1 estimated at the "
        "median of the recorded weights: test_04.py, "
        "test_05.py, test_06.py") in basis, basis
    # The tripwire's reader splits the same clause, so the counts the
    # control checks are the ones the prose states rather than the ones
    # a reader would infer.
    count, total, listed, carried, estimated = unmeasured_names(basis)
    assert (count, total, carried, estimated) == (3, 7, 2, 1), basis
    assert listed == ['test_04.py', 'test_05.py', 'test_06.py'], listed


def test_the_shipped_file_describes_the_tree_it_plans(tmp):
    """The other half of the tripwire, and the half that was missing.

    `test_timed_refresh.py` checks that the shipped file's own numbers
    hold the balance margin they name. That file satisfied it while
    recording 28 of the tree's 326 suites, because a total that is
    wrong by 3.15x is balanced with itself: the check reads the file
    against ITSELF and cannot see that it describes a corner of the
    tree.

    So this reads the file against the TREE. Every suite the file
    claims -- a recorded weight or a name in its own coverage clause --
    is a suite the tree holds, and the coverage guard accepts the plan
    those weights make. Both fail on the file that shipped: 28
    recorded, 288 estimated, and names it never heard of.

    A tree suite that arrived AFTER the write is deliberately outside
    it: the clause is about the runs, and the file is recomputed rather
    than parsed. Asserting the other way would fail the day this
    branch's own new suite was added, which is the state the branch is
    driving toward.
    """
    planner = _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                         'plan_timed_matrix')
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = set(planner.suite_names(ROOT))
    _count, _total, listed, _carried, _estimated = unmeasured_names(
        data['basis'])
    unknown = sorted((set(data['suite_weights']) | set(listed)) - names)
    assert not unknown, unknown
    # The guard is the same chokepoint the planner's CLI calls, so this
    # is a refusal on the shipped file rather than a restatement of the
    # arithmetic: a 28-weight file raises here. Its own anti-vacuity
    # control is `test_a_file_whose_weight_is_mostly_estimated_is_a_
    # named_refusal` in the planner suite, which drives this same
    # chokepoint on a narrowed file -- a control that `assert listed`
    # never was, since it failed the day the file covered the tree.
    planner.verify_measured(ROOT, data)


def test_the_commit_message_names_the_runs_the_file_records(tmp):
    """The subject and `measured_from` are rendered from ONE value.

    They did not have to be. Commit `eed3ae9e` is titled "ci: refresh
    suite timings from run 36318864740" and the file it wrote records
    `measured_from: 36310409594`, because the workflow built its
    subject from `${{ github.run_id }}` -- the REFRESH workflow's own
    run -- while the refresher recorded the `tests` run it measured.
    Two different runs, spelled as if they were one, and nothing
    compared them: the refresher is what writes `measured_from`, and
    the workflow is what writes the subject, and each was right about
    its own value.

    So the refresher now renders the subject too, from the same run
    list and through the same join as the field, and writes it where
    the workflow can read it. The assertion is on the two strings
    agreeing, over a THREE-run sample, because a one-run sample cannot
    tell a shared rendering from a coincidence.
    """
    root = Path(tmp) / 'runs'
    for run_id, seconds in ((30, 4.0), (29, 6.0), (28, 8.0)):
        _write_run(root, run_id, {'cell-01': {'test_a.py': seconds}})
    message = Path(tmp) / 'subject.txt'
    text, _code, err = _drive(tmp, root, {'test_a.py': 2.0},
                              message_file=message)
    written = json.loads(text)
    assert written['measured_from'] == '30, 29, 28', written
    subject = message.read_text(encoding='utf-8')
    refresher = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                           'refresh_timings')
    assert subject.strip() == refresher.commit_message([30, 29, 28]), subject
    # The property, not the spelling: every run the file records is
    # named in the subject, and nothing else is.
    for run_id in written['measured_from'].split(', '):
        assert run_id in subject, (run_id, subject)
    assert '36318864740' not in subject, subject
    assert 'wrote' in err, err


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedcover_')


if __name__ == '__main__':
    raise SystemExit(main())
