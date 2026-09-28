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

The artifacts are fixtures under a temp tree: no API, no `gh`, no
network. Each test builds a run the way the timed job leaves one --
`<run>/<cell>/head-N/<suite>.json`, one JSON per suite, plus the cell's
reference reading -- and drives `refresh_timings.main()` over it.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _timed_basis import write_run as _write_run  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _refresh():
    return _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                      'refresh_timings')


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


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


def _refreshed(refresh, args, expect=0):
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
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 140, {'cell-01': {'test_a.py': 4.0}})
    for run_id in (139, 138):
        _write_run(root, run_id, {'cell-01': {'test_a.py': 8.0},
                                  'cell-02': {'test_b.py': 2.0}})
    out = _file(tmp, _data({'test_a.py': 2.0, 'test_b.py': 1.0}),
                name='timings.json')
    _out, err = _refreshed(refresh, _refresh_args(tmp, root, out, runs=3))
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['measured_from'] == '139, 138', written
    assert written['suite_weights'] == {'test_a.py': 4.0,
                                        'test_b.py': 1.0}, written
    assert '140' in err and 'one cell' in err, err


def test_a_refresh_with_only_a_collapsed_matrix_to_choose_from_is_refused(
        tmp):
    """Nothing usable under the root: a named refusal, and no write.

    The healthy limb of the control above removes the richer runs, and
    the single one-cell run left is not a partition of a tree this file
    bounds at fifteen cells. The file must come back byte-identical
    rather than narrowed to whatever that run happened to measure.
    """
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 150, {'cell-01': {'test_a.py': 4.0}})
    out = _file(tmp, _data({'test_a.py': 2.0, 'test_b.py': 1.0}))
    before = out.read_text(encoding='utf-8')
    _out, err = _refreshed(refresh, _refresh_args(tmp, root, out))
    assert 'wrote nothing' in err, err
    assert '150' in err and 'one cell' in err, err
    assert 'max_cells bound of 15' in err, err
    assert out.read_text(encoding='utf-8') == before


def test_a_one_cell_run_that_measured_everything_is_not_a_collapse(tmp):
    """The refusal needs BOTH conditions, and this is the one that is not.

    A single cell against a bound of fifteen is on its own the shape a
    small tree leaves, not evidence of anything: this file records both
    suites, the run measured both of them, and the refresh is an
    ordinary measurement that leaves the file alone. A guard firing
    here would stop the refresher on every hand-built runs root, and the
    two conditions exist so that it does not.
    """
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 160, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 2.0}})
    out = _file(tmp, _data({'test_a.py': 2.0, 'test_b.py': 1.0}))
    before = out.read_text(encoding='utf-8')
    _out, err = _refreshed(refresh, _refresh_args(tmp, root, out))
    assert 'wrote nothing' in err, err
    assert 'one cell' not in err, err
    assert out.read_text(encoding='utf-8') == before


def test_a_file_bounded_at_one_cell_takes_a_one_cell_measurement(tmp):
    """A seed derives its bound from the run that measured it.

    A tree small enough to be one cell has a file bounded at one, so a
    one-cell run of that tree is exactly the partition the bound says
    to expect, and refusing it would stop the refresher on every small
    tree.
    """
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    _write_run(root, 170, {'cell-01': {'test_a.py': 4.0}})
    out = _file(tmp, _data({'test_a.py': 2.0, 'test_b.py': 1.0}, max_cells=1))
    _out, err = _refreshed(refresh, _refresh_args(tmp, root, out))
    assert 'carried forward' in err, err
    assert 'one cell' not in err, err




def test_the_shipped_file_describes_the_tree_it_plans(tmp):
    """The other half of the tripwire, and the half that was missing.

    `test_timed_refresh.py` checks that the shipped file's own numbers
    hold the balance margin they name. That file satisfied it while
    recording 28 of the tree's 326 suites, because a total that is
    wrong by 3.15x is balanced with itself: the check reads the file
    against ITSELF and cannot see that it describes a corner of the
    tree.

    So this reads the file against the TREE. Every suite the tree holds
    is either recorded a weight or named in the file's own estimated
    clause, and the coverage guard accepts the plan those weights make.
    Both fail on the file that shipped: 28 recorded, 288 estimated, and
    10 suites the file never heard of because they arrived after it was
    written.
    """
    planner = _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                         'plan_timed_matrix')
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = set(planner.suite_names(ROOT))
    listed = set(_util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                            'timings_bounds').estimated_count(ROOT, data))
    missing = names - set(data['suite_weights']) - listed
    assert not missing, sorted(missing)
    # The guard is the same chokepoint the planner's CLI calls, so this
    # is a refusal on the shipped file rather than a restatement of the
    # arithmetic: a 28-weight file raises here.
    planner.verify_measured(ROOT, data)
    assert listed, 'a fully measured file is the state this guards'


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
    refresh = _refresh()
    root = Path(tmp) / 'runs'
    for run_id, seconds in ((30, 4.0), (29, 6.0), (28, 8.0)):
        _write_run(root, run_id, {'cell-01': {'test_a.py': seconds}})
    out = _file(tmp, _data({'test_a.py': 2.0}))
    message = Path(tmp) / 'subject.txt'
    args = _refresh_args(tmp, root, out, runs=3)
    args += ['--message-file', str(message)]
    _out, err = _refreshed(refresh, args)
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['measured_from'] == '30, 29, 28', written
    subject = message.read_text(encoding='utf-8')
    assert subject.strip() == refresh.commit_message([30, 29, 28]), subject
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
