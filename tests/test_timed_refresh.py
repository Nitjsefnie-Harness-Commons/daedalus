#!/usr/bin/env python3
"""Refresh the suite-timings file: what it writes, and what it refuses.

The artifacts are fixtures under a temp tree: no API, no `gh`, no
network. Each test builds a run the way the timed job leaves one --
`<run>/<cell>/head-N/<suite>.json`, one JSON per suite, plus the cell's
reference reading -- and drives `refresh_timings.main()` over it.
"""
import contextlib
import io
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index  # noqa: E402
from _timed_basis import (  # noqa: E402
    assert_the_generator_wrote_the_basis, verify_recorded_count)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _refresh():
    return _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                      'refresh_timings')


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _workload():
    return _util.load(ROOT / 'scripts' / 'ci' / 'reference_workload.py',
                      'reference_workload')


def _tree(tmp, suites):
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True, exist_ok=True)
    for name in suites:
        (tree / 'tests' / name).write_text('pass\n', encoding='utf-8')
    # The planner and the basis both enumerate the TRACKED tree, so a
    # fixture tree is a git checkout with these files in its index;
    # `git ls-files` reads the index, so no commit is made or needed.
    git_index(tree, 'init', '-q')
    git_index(tree, 'add', '--', 'tests/')
    return tree


def _suite_file(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'tests': {'test_a': seconds},
                                'outcomes': {}}), encoding='utf-8')


def _write_run(root, run_id, cells,
               reference: float | None = 2.0, decoys=()):
    """One run's artifact tree; `cells` maps a cell name to suite seconds.

    Each suite gets the given seconds in `head-1` and `head-2`, the two
    measured rounds. `reference=None` writes no reference reading at
    all. `decoys` names extra round directories (`base-1`, `warmup`)
    that carry the same suites at ten times the seconds, so a parser
    that counted them would not agree with one that did not.
    """
    run = Path(root) / str(run_id)
    for cell, suites in cells.items():
        for suite, seconds in suites.items():
            # time_tests.py names each file after the suite's STEM, so
            # the file is `test_a.json` for `test_a.py`.
            name = f'{Path(suite).stem}.json'
            for round_name in ('head-1', 'head-2'):
                _suite_file(run / cell / round_name / name, seconds)
        for decoy in decoys:
            for suite, seconds in suites.items():
                name = f'{Path(suite).stem}.json'
                _suite_file(run / cell / decoy / name, seconds * 10)
        if reference is not None:
            (run / cell).mkdir(parents=True, exist_ok=True)
            (run / cell / 'reference.json').write_text(
                json.dumps({'seconds': reference, 'iterations': 16}),
                encoding='utf-8')
    return run


def _data(weights, units='reference-multiples', target=10.0, max_cells=15,
          **fields):
    data = {
        'schema_version': _planner().SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': units,
        'measured_from': 'tests run 1',
        'runs': 1,
        'suite_weights': weights,
    }
    data.update(fields)
    return data


def _file(tmp, data, name='suite-timings.json'):
    path = Path(tmp) / name
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return path


def _run(refresh, args, expect=0):
    """Run main() with both streams captured; return (code, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = refresh.main(args)
    assert code == expect, (code, out.getvalue(), err.getvalue())
    return out.getvalue(), err.getvalue()


def _refresh_args(tmp, root, out, runs=3, seed=False, tree=None):
    args = ['--runs-root', str(root), '--out', str(out)]
    if tree is not None:
        args += ['--tree', str(tree)]
    if runs is not None:
        args += ['--runs', str(runs)]
    if seed:
        args += ['--seed']
    return args


def test_one_outlier_run_among_several_does_not_change_the_file(tmp):
    """The maintainer's requirement: the median, not the mean.

    The window is the three most recent complete runs, and the
    OUTLIER IS INSIDE IT: runs 40 and 38 measure the suite at 2.0
    reference-multiples and run 39, the middle one, measures it ten
    times that. The median of [2.0, 20.0, 2.0] is 2.0 and the file
    must be left exactly as it was; a mean would follow the outlier
    past the margin and write. The err names the window, so a reader
    can see the outlier was inside it.
    """
    refresh = _refresh()
    for run_id, seconds in ((40, 4.0), (39, 40.0), (38, 4.0)):
        cells = {'cell-01': {'test_slow.py': seconds,
                             'test_quick.py': seconds / 4.0},
                 'cell-02': {'test_other.py': 1.0}}
        _write_run(Path(tmp) / 'runs', run_id, cells)
    out = _file(tmp, _data({'test_slow.py': 2.0, 'test_quick.py': 0.5,
                            'test_other.py': 0.5}))
    before = out.read_text(encoding='utf-8')
    _out, err = _run(refresh, _refresh_args(tmp, Path(tmp) / 'runs', out))
    assert 'runs 40, 39, 38' in err, err
    assert 'wrote nothing' in err, err
    assert out.read_text(encoding='utf-8') == before


def test_a_weight_beyond_the_margin_is_written_and_one_inside_is_not(tmp):
    """Both limbs, varying the magnitude against the MARGIN constant."""
    refresh = _refresh()
    margin = refresh.WEIGHT_MARGIN
    inside, beyond = 1.0 + margin / 2, 1.0 + margin * 1.5
    for factor, written in ((inside, False), (beyond, True)):
        case = Path(tmp) / f'case{int(factor * 100)}'
        root = case / 'runs'
        _write_run(root, 50, {'cell-01': {'test_a.py': 4.0 * factor}})
        out = _file(case, _data({'test_a.py': 2.0}), name='timings.json')
        _out, err = _run(refresh, _refresh_args(case, root, out))
        if written:
            assert 'wrote' in err and 'wrote nothing' not in err, err
            moved = json.loads(out.read_text(encoding='utf-8'))
            assert moved['suite_weights']['test_a.py'] == 2.0 * factor
        else:
            assert 'wrote nothing' in err, err
            assert json.loads(
                out.read_text(encoding='utf-8'))['suite_weights'] == {
                    'test_a.py': 2.0}


def test_the_parser_reads_cell_names_from_the_artifacts(tmp):
    """Cell names the repository has never used, read the same."""
    refresh = _refresh()
    cells = {'group-alpha-7f3': {'test_a.py': 2.0},
             'zz last cell': {'test_b.py': 3.0},
             '9': {'test_c.py': 1.0}}
    root = Path(tmp) / 'runs'
    _write_run(root, 60, cells)
    out = _file(tmp, _data({}))
    _run(refresh, _refresh_args(tmp, root, out))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['suite_weights'] == {
        'test_a.py': 1.0, 'test_b.py': 1.5, 'test_c.py': 0.5}, written
    assert '60' in written['measured_from'], written


def test_a_cell_with_no_reference_reading_is_refused_by_name(tmp):
    """Named cell, named suite -- refused, never stored as zero."""
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 70, {'cell-07': {'test_carried.py': 4.0},
                          'cell-08': {}}, reference=None)
    out = _file(tmp, _data({}))
    _out, err = _run(refresh, _refresh_args(tmp, root, out), expect=1)
    assert 'cell-07' in err and 'test_carried.py' in err, err
    assert 'reference' in err, err
    assert json.loads(out.read_text(encoding='utf-8'))['suite_weights'] == {}


def test_a_reference_reading_that_is_not_a_positive_number_is_refused(tmp):
    """The reading is DIVIDED by: zero and a non-number must both stop.

    `_reference` reads `seconds` and every suite's weight is that
    reading's divisor, so a `seconds` of 0 is a `ZeroDivisionError`
    traceback in a nightly job the moment the positivity check goes, and
    a string is a `TypeError` from the same line. The shipped code
    refuses both, naming the field and the offending value; this pins
    that, because every other reference fixture here writes a reading of
    2.0 and neither input reaches them. The refusal names the FIELD and
    not the cell, unlike the missing-file and missing-key branches
    above it -- the run that produced a bad reading is still in the
    walk's log.
    """
    refresh = _refresh()
    for seconds, value in ((0, '0'), ('fast', "'fast'"), (-1, '-1')):
        root = Path(tmp) / f'runs-{value}'
        run = _write_run(root, 71, {'cell-03': {'test_a.py': 4.0}},
                         reference=None)
        (run / 'cell-03' / 'reference.json').write_text(
            json.dumps({'seconds': seconds, 'iterations': 16}),
            encoding='utf-8')
        out = _file(tmp, _data({}), name=f'timings-{value}.json')
        _o, err = _run(refresh, _refresh_args(tmp, root, out), expect=1)
        assert 'reference.json seconds' in err, (value, err)
        assert value in err, (value, err)
        assert json.loads(out.read_text(encoding='utf-8'))[
            'suite_weights'] == {}


def test_a_run_count_below_one_is_refused_rather_than_walked(tmp):
    """`--runs 0` and `--runs -1` are a typo, not a request for every run.

    A non-positive sample would otherwise reach `select`, whose
    `len(selected) == wanted` never fires, and walk EVERY complete run
    under the root -- a different measurement from the one asked for,
    silently taken.
    """
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 90, {'cell-01': {'test_a.py': 4.0}})
    for runs in ('0', '-1'):
        out = _file(tmp, _data({}), name=f'timings-runs{runs}.json')
        _o, err = _run(refresh, _refresh_args(tmp, root, out, runs=int(runs)),
                       expect=1)
        assert '--runs must be at least one' in err, (runs, err)
        assert f'not {runs}' in err, (runs, err)
        assert json.loads(out.read_text(encoding='utf-8'))[
            'suite_weights'] == {}


def test_the_planner_reads_what_the_refresher_wrote_unchanged(tmp):
    """Round trip: no field dropped, no field invented."""
    refresh = _refresh()
    tree = _tree(tmp, ['test_a.py', 'test_b.py', 'test_unmeasured.py'])
    root = Path(tmp) / 'runs'
    _write_run(root, 80, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 2.0}})
    out = _file(tmp, _data({'test_a.py': 2.0}, target=3.0, max_cells=4))
    _out, _err = _run(refresh, _refresh_args(tmp, root, out, tree=tree))
    written = out.read_text(encoding='utf-8')
    planner = _planner()
    data = planner.read_timings(out)
    assert data == json.loads(written), data
    decision = planner.plan(tree, data)
    assert decision.estimated == ['test_unmeasured.py'], decision.estimated
    assert decision.stale == []
    assert sum(len(cell.suites) for cell in decision.cells) == 3


def test_a_run_with_no_artifacts_changes_nothing_and_does_not_crash(tmp):
    """The empty history: no runs at all, or runs with no cells."""
    refresh = _refresh()
    out = _file(tmp, _data({'test_a.py': 2.0}))
    before = out.read_text(encoding='utf-8')
    root = Path(tmp) / 'runs'
    root.mkdir()
    (root / '90').mkdir()
    (root / 'not-a-run').mkdir()
    _out, err = _run(refresh, _refresh_args(tmp, root, out))
    assert 'wrote nothing' in err, err
    assert out.read_text(encoding='utf-8') == before


def test_only_the_measured_head_rounds_are_read(tmp):
    """The warm-up is discarded and the base side is another tree."""
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 100, {'cell-01': {'test_a.py': 4.0}},
               decoys=('base-1', 'base-2', 'warmup'))
    out = _file(tmp, _data({}))
    _out, _err = _run(refresh, _refresh_args(tmp, root, out))
    written = json.loads(out.read_text(encoding='utf-8'))
    # 4.0 s per head round over a 2.0 s reference: 2.0 multiples. Had a
    # base or warm-up round been counted the weight would be larger.
    assert written['suite_weights'] == {'test_a.py': 2.0}, written


def test_a_file_in_seconds_is_rescaled_into_the_units_it_reports(tmp):
    """A units change rewrites the target with the weights, not alone."""
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 110, {'cell-01': {'test_a.py': 4.0}}, reference=2.0)
    out = _file(tmp, _data({'test_a.py': 4.0}, units='seconds', target=60.0))
    _out, err = _run(refresh, _refresh_args(tmp, root, out))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['units'] == 'reference-multiples', written
    # 4.0 s is 2.0 multiples of a 2.0 s reference; 60 s is 30 of them.
    assert written['target_cell_weight'] == 30.0, written
    assert 'units' in err or 'seconds' in err, err


def test_a_suite_that_appeared_or_disappeared_is_written(tmp):
    """Both membership changes count, whatever the weights did."""
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 120, {'cell-01': {'test_a.py': 4.0, 'test_new.py': 2.0}})
    out = _file(tmp, _data({'test_a.py': 2.0, 'test_gone.py': 1.0}))
    _out, err = _run(refresh, _refresh_args(tmp, root, out))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['suite_weights'] == {'test_a.py': 2.0,
                                        'test_new.py': 1.0}, written
    assert 'test_new.py' in err and 'test_gone.py' in err, err


def test_the_median_is_over_the_selected_runs_and_the_count_is_recorded(
        tmp):
    """`runs` is the sample the median used, never a claim of five.

    The newest run is measured, the next is a different cell set and is
    stepped over, and the sample is drawn from the three complete runs
    behind it -- one of which is the outlier.
    """
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 130, {'cell-01': {'test_a.py': 4.0},
                           'cell-02': {'test_b.py': 1.0}})
    _write_run(root, 129, {'cell-01': {'test_a.py': 4.0}})
    for run_id, seconds in ((128, 100.0), (127, 4.0), (126, 4.0)):
        _write_run(root, run_id,
                   {'cell-01': {'test_a.py': seconds},
                    'cell-02': {'test_b.py': 1.0}})
    out = _file(tmp, _data({}))
    _out, err = _run(refresh, _refresh_args(tmp, root, out, runs=3))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['suite_weights'] == {'test_a.py': 2.0,
                                        'test_b.py': 0.5}, written
    assert written['runs'] == 3, written
    assert '130, 128, 127' == written['measured_from'], written
    # It reached back over the run it stepped over, and said so.
    assert '4 runs' in err and 'incomplete' in err, err


def test_an_incomplete_run_set_is_skipped_and_an_older_one_is_used(tmp):
    """A run with fewer cells is an older matrix, not a candidate."""
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 200, {'cell-01': {'test_a.py': 4.0},
                           'cell-02': {'test_b.py': 2.0}})
    _write_run(root, 199, {'cell-01': {'test_a.py': 8.0}})
    out = _file(tmp, _data({}))
    _out, err = _run(refresh, _refresh_args(tmp, root, out, runs=1))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['measured_from'] == '200', written
    # The sample was one run, whatever the default is: a file that
    # claimed the default here would claim a median it never took.
    assert written['runs'] == 1, written
    assert 'incomplete' in err, err


def test_a_suite_file_that_cannot_be_read_is_refused(tmp):
    """A broken artifact names its cell, its run and its suite."""
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    run = _write_run(root, 300, {'cell-01': {'test_a.py': 4.0}})
    (run / 'cell-01' / 'head-1' / 'test_broken.json').write_text(
        json.dumps({'tests': {'test_x': 'quickly'}}), encoding='utf-8')
    out = _file(tmp, _data({}))
    _out, err = _run(refresh, _refresh_args(tmp, root, out), expect=1)
    assert 'test_broken' in err and 'cell-01' in err and '300' in err, err


def test_the_reference_reading_comes_from_the_workload_it_names(tmp):
    """The two halves agree: what the workload prints, refresh reads."""
    workload = _workload()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert workload.main(['--json', '--iterations', '1000']) == 0
    reading = json.loads(buffer.getvalue())
    assert reading['seconds'] > 0 and reading['iterations'] == 1000, reading
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    run = _write_run(root, 400,
                     {'cell-01': {'test_a.py': 3.0 * reading['seconds']}},
                     reference=None)
    (run / 'cell-01' / 'reference.json').write_text(
        json.dumps(reading), encoding='utf-8')
    out = _file(tmp, _data({}))
    _out, _err = _run(refresh, _refresh_args(tmp, root, out))
    written = json.loads(out.read_text(encoding='utf-8'))
    # Three seconds of suite per second of reference: three multiples,
    # and three only if the reading itself was the divisor.
    assert written['suite_weights'] == {'test_a.py': 3.0}, written


def test_the_seed_command_measures_seconds_and_explains_both_bounds(tmp):
    """Seed mode: raw seconds, a derived target, a derived bound."""
    refresh = _refresh()
    planner = _planner()
    suites = {f'test_s{index:02d}.py': seconds for index, seconds in
              enumerate([60.0, 30.0, 30.0, 30.0, 20.0, 20.0, 10.0, 5.0])}
    cells = {'cell-01': dict(list(suites.items())[:4]),
             'cell-02': dict(list(suites.items())[4:])}
    tree = _tree(tmp, sorted(suites) + ['test_unmeasured.py'])
    root = Path(tmp) / 'runs'
    _write_run(root, 36054336022, cells)
    out = _file(tmp, _data({}, units='seconds'), name='seed.json')
    _run(refresh, _refresh_args(tmp, root, out, seed=True, tree=tree))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['units'] == 'seconds', written
    assert written['runs'] == 1
    assert written['measured_from'] == '36054336022', written
    assert written['suite_weights'] == suites, written
    # Two cells measured today is the bound. The target is the smallest
    # five-second step whose plan holds the balance guarantee AND fits
    # that bound -- derived here with the planner, so the expectation
    # is the rule and not a transcribed number.
    expected = _smallest_balancing_target(planner, tree, suites, 2)
    assert written['max_cells'] == 2, written
    assert written['target_cell_weight'] == expected, written
    assert written['target_cell_weight'] % 5 == 0, written
    assert 'raw seconds' in written['basis'], written['basis']
    assert f'{expected:g}' in written['basis'], written['basis']
    assert '2 cells' in written['basis'], written['basis']
    # The seed says which tree suites the numbers do not cover, so a
    # reader of the file alone knows the remainder is estimated.
    assert 'test_unmeasured.py' in written['basis'], written['basis']
    assert "1 of the tree's 9 suites" in written['basis'], written['basis']


def _smallest_balancing_target(planner, tree, weights, max_cells):
    """The seed rule, worked out with the planner, for the test to share."""
    total = sum(weights.values())
    for target in range(5, int(total) + 5, 5):
        if math.ceil(total / target) > max_cells:
            continue
        data = _data(weights, units='seconds', target=target,
                     max_cells=max_cells)
        loads = [cell.weight for cell in planner.plan(tree, data).cells]
        if loads and max(loads) <= statistics.median(loads) * (
                1 + planner.CELL_WEIGHT_MARGIN):
            return float(target)
    return 0.0


def test_the_shipped_basis_is_what_this_generator_writes(tmp):
    """The committed prose is the CURRENT generator's, character for character.

    The `basis` field is written by `timings_bounds.basis_sentence` and
    nothing else, and it splits by clause owner. FILE-OWNED are the numbers
    and prose the file records: the recorded-weight total, the target's cell
    count and balance figures, the `max_cells` bound and its prose, the
    provenance and re-derive sentences. TREE-OWNED is the estimate list: the
    suites the tree carried at the last write the runs did not measure.

    The cell count INSIDE the `max_cells` clause is the one exception, and
    it is an INPUT rather than a derivation: the refresher supplies the
    cells the selected run measured (`refresh_timings.py:389`, and the
    `max_cells` it derived from that same run on a seed, `:427`). The file
    records the number only in that prose, so the compare covers every
    other clause and the PROSE of that one -- the wording, its position,
    the `1 cell`/`N cells` plural, the sentence around it -- and not the
    truth of the count.

    WHAT CHECKS THE COUNT. `verify_recorded_count` re-derives it through
    the refresher's own `discover_runs` and `select`, the two calls
    `refresh()` makes before it attaches a basis, and asserts the
    committed prose records what the selected run measured. It reads
    `<repo>/runs`, where the refresher is pointed, so it is live in
    exactly one place: `timed-timings.yml` downloads the runs there and
    its "Verify the change" step runs this suite in the same job with
    them on disk, so the number is pinned at the step that writes it.
    The pull-request `suites` job has no artifacts, so there the check
    cannot run; it says so on stderr rather than passing silently.

    The boundary is not where it first looks. The target clause's cell
    count, heaviest cell and median come from `plan_matrix`, and the plan
    packs the tree's ESTIMATED suites in at the median recorded weight, so
    those figures move when the live tree gains an unmeasured suite -- on a
    merge-tree checkout this file's median moved 61.47 -> 61.52. They are
    file-owned only relative to the file's OWN suite set, so the control
    derives that set FROM the file -- the recorded weights plus the
    estimated names the file's own clause lists -- and compares the whole
    sentence to the generator byte for byte over it. Recomputing over the
    working tree is the defect: the file describes its HEAD, the `suites`
    job checks out `refs/pull/N/merge`, and every time `main` gained a
    suite the merge tree disagreed with a committed artifact. Never reading
    the live tree makes a moved-on tree green by construction.

    This keeps the original protection: a sentence the shipped generator
    cannot emit stayed in the committed file once -- the phrase equating the
    bound with the measured cells -- and nothing went red, since the stale
    text was still true of the seed that produced it. That phrase is in the
    file-owned `max_cells` clause, so a planted `the bound is that number`
    fails the byte-for-byte compare. That helper also checks the tree-owned
    clause structurally, from the file alone: its count equals the names it
    lists, and its total is the recorded weights plus that count.
    """
    planner = _planner()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    assert_the_generator_wrote_the_basis(tmp, data)
    # The count is read out of the file, so nothing else checks it. This
    # prints what the cross-check did or could not do, on every run: a
    # skip that says nothing is indistinguishable from a pass.
    print(verify_recorded_count(data, ROOT / 'runs'), file=sys.stderr)


def test_the_shipped_file_satisfies_the_margin_it_names(tmp):
    """The tripwire: the SHIPPED file's own numbers must hold the margin.

    The planner only notes a target the margin forbids and exits 0, so
    a hand edit of `target_cell_weight` or of any weight in
    `.github/suite-timings.json` would otherwise reach a pull request
    with a full matrix printed from a target nothing admits. This reads
    the shipped file and plans the real tree with it.
    """
    planner = _planner()
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    loads = [cell.weight for cell in planner.plan(ROOT, data).cells]
    median = statistics.median(loads)
    assert max(loads) <= median * (1 + planner.CELL_WEIGHT_MARGIN), (
        f'the shipped file plans a heaviest cell of {max(loads):.3f} against '
        f'a {median:.3f} median, over the {planner.CELL_WEIGHT_MARGIN:g} '
        f'margin: {max(loads) / median:.3f}x')


def test_a_target_the_margin_forbids_is_re_derived_not_written_through(tmp):
    """The chokepoint: a write never leaves a forbidden target behind.

    A heavy suite beside a light one, with a target below the heavy
    suite's weight, leaves the heavy cell alone at 10x the median; the
    refresher re-derives the target (the smallest step the margin
    admits) and says so in the reason, even though no weight moved.
    """
    refresh = _refresh()
    tree = _tree(tmp, ['test_heavy.py', 'test_light.py'])
    root = Path(tmp) / 'runs'
    _write_run(root, 70, {'cell-01': {'test_heavy.py': 40.0,
                                      'test_light.py': 4.0}},
               reference=2.0)
    weights = {'test_heavy.py': 20.0, 'test_light.py': 2.0}
    out = _file(tmp, _data(weights, target=5.0, max_cells=4))
    _out, err = _run(refresh, _refresh_args(tmp, root, out, tree=tree))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert 'target re-derived' in err, err
    assert written['target_cell_weight'] == 25.0, written
    assert written['suite_weights'] == weights, written
    assert 'basis' in written, written


def test_a_refresh_from_a_seeded_file_carries_the_basis_forward(tmp):
    """A refresh must not drop the only text explaining the two bounds.

    The seed writes the basis into the file's `basis` field; the first
    scheduled refresh rewrites the whole file, so it has to write a
    basis too -- rebuilt for the refreshed numbers, naming the runs,
    the units, both bounds and the tree suites still estimated.
    """
    refresh = _refresh()
    tree = _tree(tmp, ['test_a.py', 'test_b.py', 'test_unmeasured.py'])
    seed_root = Path(tmp) / 'seed-runs'
    _write_run(seed_root, 1, {'cell-01': {'test_a.py': 40.0,
                                          'test_b.py': 4.0}},
               reference=None)
    out = _file(tmp, _data({}, units='seconds'), name='seed.json')
    _run(refresh, _refresh_args(tmp, seed_root, out, seed=True, tree=tree))
    assert 'raw seconds' in json.loads(
        out.read_text(encoding='utf-8'))['basis']
    root = Path(tmp) / 'runs'
    _write_run(root, 2, {'cell-01': {'test_a.py': 40.0, 'test_b.py': 4.0}},
               reference=2.0)
    _run(refresh, _refresh_args(tmp, root, out, tree=tree))
    written = json.loads(out.read_text(encoding='utf-8'))
    basis = written['basis']
    assert written['units'] == 'reference-multiples', written
    assert 'raw seconds' not in basis, basis
    assert 'reference-normalized medians over 1 run(s) (2)' in basis, basis
    # The basis describes the numbers THIS write produced, not a
    # remembered one: both bound names carry the file's own values.
    target = written['target_cell_weight']
    assert f'target_cell_weight {target:g}' in basis, basis
    assert f'max_cells {written["max_cells"]}' in basis, basis
    assert 'test_unmeasured.py' in basis, basis


def test_the_basis_does_not_equate_the_bound_with_the_measured_cells(tmp):
    """The file's bound and the measured run's cell count are two facts.

    On a refresh the bound is carried over from the file while the cell
    count is what the newest run measured, so the two need not coincide;
    a sentence that said the bound "is that number" would be
    self-contradictory on every refresh that changed the cell count.
    """
    refresh = _refresh()
    tree = _tree(tmp, ['test_a.py', 'test_b.py'])
    out = _file(tmp, _data({'test_a.py': 1.0, 'test_b.py': 1.0},
                           max_cells=5))
    root = Path(tmp) / 'runs'
    _write_run(root, 7, {'cell-01': {'test_a.py': 40.0},
                         'cell-02': {'test_b.py': 40.0}}, reference=2.0)
    _run(refresh, _refresh_args(tmp, root, out, tree=tree))
    basis = json.loads(out.read_text(encoding='utf-8'))['basis']
    assert 'max_cells 5' in basis, basis
    assert '2 cells' in basis, basis
    assert 'the bound is that number' not in basis, basis


def test_a_recorded_zero_weight_is_a_move_not_a_division(tmp):
    """The seed rounds to four decimals; a zero must not divide."""
    refresh = _refresh()
    # pylint: disable=protected-access
    assert refresh._moved({'test_tiny.py': 0.0},
                          {'test_tiny.py': 1.5}) == [
        ('test_tiny.py', 0.0, 1.5)]


def test_the_reference_workload_is_a_fixed_count_of_work(tmp):
    """Fixed work, not a seconds-target loop: the unit cannot normalize."""
    workload = _workload()
    seconds, checksum = workload.measure(1000)
    assert seconds < 2.0, seconds
    assert workload.work(1000) == checksum
    assert workload.work(2000) != checksum
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert workload.main(['--json', '--iterations', '1000']) == 0
    printed = json.loads(buffer.getvalue())
    assert printed['iterations'] == 1000, printed
    assert printed['seconds'] > 0, printed
    # The count is the unit: a weight in reference-multiples counts
    # iterations, so the constant is what the data file's numbers are
    # measured against, and it is the same on every runner. The default
    # is that constant, not a seconds target.
    assert isinstance(workload.ITERATIONS, int)
    assert workload.ITERATIONS % 1_000_000 == 0, workload.ITERATIONS
    assert workload.ITERATIONS == 16_000_000, workload.ITERATIONS


def test_the_balance_chokepoint_is_a_median_not_a_mean(tmp):
    """`plan_is_balanced` decides on a MEDIAN cell, and can be told apart.

    The chokepoint every write passes through compares the heaviest cell
    against `median(loads) * (1 + margin)`. On a RIGHT-skewed set the two
    statistics cannot disagree about a max -- the mean is the larger of
    the two, so a mean can only refuse more -- which is why the planted
    mean survived every right-skewed fixture the other suites use. This
    one is LEFT-skewed: two light cells under three heavy ones, so the
    mean falls below the median while the heaviest cell stays put. The
    load list is the packer's own output for the weights below, and the
    expectation is the chokepoint's own condition recomputed here with
    `statistics.median`; the mean-based verdict is asserted to be the
    OPPOSITE so this fixture cannot quietly stop discriminating.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    planner = _planner()
    suites = ['test_a.py', 'test_b.py', 'test_c.py', 'test_d.py',
              'test_e.py']
    weights = {'test_a.py': 1.0, 'test_b.py': 1.0, 'test_c.py': 30.0,
               'test_d.py': 30.0, 'test_e.py': 40.0}
    tree = _tree(tmp, suites)
    data = _data(weights, target=10.0, max_cells=5)
    loads = [cell.weight for cell in planner.plan(tree, data).cells]
    assert sorted(loads) == [1.0, 1.0, 30.0, 30.0, 40.0], loads
    median = statistics.median(loads)
    mean = statistics.mean(loads)
    assert mean < median, (mean, median)
    bound = 1 + planner.CELL_WEIGHT_MARGIN
    assert max(loads) <= median * bound, (loads, median)
    assert max(loads) > mean * bound, (loads, mean)
    assert bounds.plan_is_balanced(tree, data) is True, (loads, median)


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedrefresh_')


if __name__ == '__main__':
    raise SystemExit(main())
