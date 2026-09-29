#!/usr/bin/env python3
"""Which cell count the shipped basis control hands the generator.

`test_timed_refresh.py` rebuilds `.github/suite-timings.json`'s `basis`
with `timings_bounds.basis_sentence` and compares it byte for byte, so
the count that prose states is an INPUT to the rebuild rather than
anything the file derives: a refresh supplies the cells the selected run
measured (`refresh_timings.py:389`) and a seed the `max_cells` it
derived from that same run (`:427`). The shipped control fed the BOUND
instead, which is green on any file whose prose was written from that
same bound -- the committed file carried 15 twice until the first
genuine bot refresh recorded the 13 the run really measured and turned
the control red. The comparison itself lives in `_timed_basis.py` and
both suites call it, so neither imports the other and a future edit to
the comparison is the code this suite exercises.

The second half of this suite pins the CROSS-CHECK that guards the one
clause the comparison reads out of the file instead of deriving from
it. That guard is live only where the downloaded runs are on disk, which
is the timed-timings job alone, so without rows here its whole live arm
would run nowhere but that job and every pull request would exercise
only the branch that declines to check.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _timed_basis import (  # noqa: E402
    assert_the_generator_wrote_the_basis, fixture_tree,
    verify_clause_against_tree, verify_recorded_count,
    verify_unmeasured_list, write_run)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _data(weights, target=10.0, max_cells=5, measured_from='tests run 1'):
    """A timings data file with the shape `_written` gives one.

    The same fields `test_timed_refresh._data` builds, written out here
    rather than imported: a suite must not reach into a sibling suite's
    module, and a control's own fixture is the thing under test.
    """
    return {
        'schema_version': _planner().SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': 'reference-multiples',
        'measured_from': measured_from,
        'runs': 1,
        'suite_weights': weights,
    }


def _file(tmp, data, name='suite-timings.json'):
    path = Path(tmp) / name
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return path


def _timings(tmp, name, measured_from, suites, measured, max_cells=5):
    """A data file whose basis says the run named first ran `measured`.

    Written through `basis_sentence` and read back through
    `read_timings`, so the guard is handed the same kind of file the
    refresher leaves rather than a dict assembled to suit it.
    `measured_from` is a whole provenance string: the refresher writes
    every run of the sample, in selection order, so the run the count
    came from is the FIRST of them.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    data = _data({suite: 1.0 for suite in suites}, max_cells=max_cells,
                 measured_from=measured_from)
    tree = fixture_tree(Path(tmp) / f'tree-{name}', suites)
    data['basis'] = bounds.basis_sentence(
        tree, data, measured, bounds.estimated_count(tree, data))
    return _planner().read_timings(_file(tmp, data, name=f'{name}.json'))


# Two cells carrying THREE suites, and never one suite per cell. A cell
# artifact is a directory of one JSON per suite stem
# (`suite_seconds` globs `round_dir.glob('*.json')`), and the planner
# packs several suites into a cell -- the shipped file records 273
# suites over 13 cells. A fixture of one suite per cell would make the
# suite count and the cell count coincide, and a guard counting the
# wrong one would be indistinguishable from a right one.
_SUITES = ['test_a.py', 'test_b.py', 'test_c.py']
_TWO_CELLS = {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0},
              'cell-02': {'test_c.py': 4.0}}
_THREE_CELLS = dict(_TWO_CELLS, **{'cell-03': {'test_d.py': 4.0}})


def test_the_measured_count_is_not_the_bound(tmp):
    """A file whose basis says 2 cells under a bound of 5, compared as such.

    The shipped control fed `data['max_cells']` to `basis_sentence` where
    the refresher feeds the cells the run measured. That only ever passed
    while the committed file happened to carry the same number twice: it
    said 15 and its bound was 15, left behind by the two hand writes
    before the bot, and the first genuine bot refresh recorded the 13 the
    run really measured and turned the control red. The bound is 15 and the
    measured count is 13 in the shipped file, and those are different facts.

    So the naive version passes for a reason that has nothing to do with
    the generator: feed the bound and the compare is green on any file
    whose prose was written from that same bound, and a file written from a
    run that measured a different number is the only thing that can tell
    the two feeds apart. This builds exactly that file -- a basis written by
    `basis_sentence` with 2 measured cells and a `max_cells` of 5 -- and
    runs the shipped control's own comparison over it. The tree is the
    file's own suite set, written from the same weights the basis was
    written from, so every OTHER clause agrees and the count is the only
    thing that can disagree. One of the three suites is left unrecorded on
    purpose, so the tree-owned clause and the suite list it names are on
    the path a shipped control exercises rather than only the other one.

    The second half is why the fixture can discriminate at all: the
    sentence the shipped generator would produce for the BOUND is a
    DIFFERENT sentence, so the compare above is agreeing with this file
    for a reason the naive feed could not have produced. That expectation
    is computed from the generator and the file, not transcribed from the
    fixture's own arguments, and it fails if the two ever coincide -- which
    is what would happen if a future fixture put the count back on the
    bound, the exact state in which the shipped control has no opinion.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    planner = _planner()
    suites = ['test_a.py', 'test_b.py', 'test_c.py']
    data = _data({'test_a.py': 1.0, 'test_b.py': 2.0}, max_cells=5)
    written = fixture_tree(Path(tmp) / 'written', suites)
    data['basis'] = bounds.basis_sentence(
        written, data, 2, bounds.estimated_count(written, data))
    stored = planner.read_timings(_file(tmp, data))
    # The comparison IS the assertion: it raises on a one-clause
    # difference, and it is fed the count the prose states.
    assert_the_generator_wrote_the_basis(Path(tmp) / 'compared', stored)
    on_the_bound = bounds.basis_sentence(
        written, stored, stored['max_cells'],
        bounds.estimated_count(written, stored))
    assert on_the_bound != stored['basis'], (
        'the generator produced the same sentence for the bound and for the '
        'measured count, so this file cannot tell the two feeds apart: '
        f'{stored["basis"]}')


def test_the_guard_reports_a_named_run_that_agrees(tmp):
    """The healthy direction, on a root the file can be matched to.

    Without this the guard's whole live arm -- the discovery, the parse,
    the count, the comparison and the line it returns -- runs nowhere
    but the daily job, and every pull request exercises only the early
    return that declines to check. The report is the evidence, so this
    asserts on it: a guard that compared nothing would have nothing to
    name.

    The provenance names two runs, the second of which the root does not
    carry, so the guard has to ask about the FIRST: the refresher writes
    its sample in selection order, and asking about the last would be
    N2 again one level down -- a run that never wrote this file.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, _TWO_CELLS)
    report = verify_recorded_count(
        _timings(tmp, 'match', '100, 999', _SUITES, 2), root)
    assert 'run 100' in report, report
    assert '2 cells' in report, report
    assert 'UNCHECKED' not in report, report


def test_the_guard_reds_a_named_run_that_disagrees(tmp):
    """The true direction, and the message names both numbers.

    Three suites over two cells, so `measured 2` here is a CELL count and
    not the suite count the same artifacts would give.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, _TWO_CELLS)
    try:
        verify_recorded_count(
            _timings(tmp, 'wrong', '100, 999', _SUITES, 1), root)
    except AssertionError as error:
        said = str(error)
        assert 'run 100' in said, said
        assert 'ran 1 cells' in said, said
        assert 'measured 2' in said, said
    else:
        raise AssertionError(
            'a data file recording one cell was accepted against the '
            'two-cell run 100 it names')


def test_a_newer_run_the_file_does_not_name_is_not_its_run(tmp):
    """The false red, pinned. A re-selection is not the run in question.

    Run 300 is newer, complete, and carries a THIRD cell, so a guard
    that re-selects from the current runs root counts three references
    and disagrees with a file that says two -- while the refresher, for
    that same artifact, correctly writes nothing at all and leaves the
    file correct for its own provenance. That assertion is between two
    unrelated facts, and it reds the timed job's pre-commit gate on a
    good file. The guard asks about the run the file NAMES, so the
    newer one is ignored and the check still happens.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, _TWO_CELLS)
    write_run(root, 300, _THREE_CELLS)
    report = verify_recorded_count(
        _timings(tmp, 'named', '100, 999', _SUITES, 2), root)
    assert 'run 100' in report, report
    assert 'UNCHECKED' not in report, report


def test_the_count_goes_unchecked_where_there_is_nothing_to_check(tmp):
    """Every way the guard declines, asserted to decline VISIBLY.

    Three of the four: no runs root at all; a runs root that does not
    carry the run the file names, which is what a no-op refresh leaves
    behind when the next job downloaded a different sample; and a run
    that is on disk but cannot be read, which the refresher refuses
    rather than guesses at. Each says UNCHECKED and names what it could
    not do, so a future edit cannot turn a visible report into a silent
    pass -- the disclosure the control's docstring makes is itself
    pinned here.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, _TWO_CELLS)
    no_root = verify_recorded_count(
        _timings(tmp, 'a', '100', _SUITES, 2), Path(tmp) / 'nowhere')
    assert 'no runs root' in no_root and 'UNCHECKED' in no_root, no_root
    not_there = verify_recorded_count(
        _timings(tmp, 'b', '999', _SUITES, 2), root)
    assert '999' in not_there and 'UNCHECKED' in not_there, not_there
    unreadable = Path(tmp) / 'unreadable'
    write_run(unreadable, 100, _TWO_CELLS, reference=None)
    cannot = verify_recorded_count(
        _timings(tmp, 'c', '100', _SUITES, 2), unreadable)
    assert 'run 100' in cannot and 'UNCHECKED' in cannot, cannot


def _file_naming(tmp, name, measured_from, recorded, unmeasured, max_cells=5):
    """A data file whose basis NAMES `unmeasured` rather than deriving it.

    Written through `basis_sentence` from an explicit list, because the
    point of the control is the run's answer against the prose's, and a
    list the file derived from its own weights would be the thing under
    test.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    data = _data(recorded, max_cells=max_cells, measured_from=measured_from)
    tree = fixture_tree(Path(tmp) / f'tree-{name}',
                        sorted(set(recorded) | set(unmeasured)))
    data['basis'] = bounds.basis_sentence(tree, data, 2, unmeasured)
    return _planner().read_timings(_file(tmp, data, name=f'{name}.json'))


def test_the_unmeasured_list_is_checked_against_the_run(tmp):
    """The half of the clause no byte compare can settle.

    `assert_the_generator_wrote_the_basis` reads the unmeasured list out
    of the prose and hands it back to the generator, so a file and a
    generator that are wrong together compare equal. The run is the only
    independent witness, and this suite exists because the arm that asks
    it runs only where the downloaded runs are on disk.

    Run 100 measures `test_a.py` and `test_b.py` and never saw
    `test_c.py`, so a file that names `test_c.py` unmeasured agrees with
    it, and a file that names `test_b.py` -- which the run did measure
    -- does not.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0}})
    honest = _file_naming(
        tmp, 'honest', '100', {'test_a.py': 1.0, 'test_b.py': 1.0},
        ['test_c.py'])
    report = verify_unmeasured_list(honest, root)
    assert '100 measured 2' in report, report
    assert 'UNCHECKED' not in report, report
    lying = _file_naming(
        tmp, 'lying', '100', {'test_a.py': 1.0, 'test_c.py': 1.0},
        ['test_b.py'])
    try:
        verify_unmeasured_list(lying, root)
    except AssertionError as error:
        said = str(error)
        assert 'test_b.py' in said, said
        assert 'did not measure' in said, said
    else:
        raise AssertionError(
            'a basis naming a measured suite unmeasured was accepted against '
            'the run 100 that measured it')


def test_the_unmeasured_list_declines_visibly(tmp):
    """Every way the run-side check does nothing, asserted to say so.

    The same three shapes the cell-count control declines, applied to
    this one, because a control that compares nothing and reports
    nothing is indistinguishable from one that compared and agreed.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, _TWO_CELLS)
    data = _file_naming(tmp, 'd', '100', {'test_a.py': 1.0}, ['test_c.py'])
    no_root = verify_unmeasured_list(data, Path(tmp) / 'nowhere')
    assert 'no runs root' in no_root and 'UNCHECKED' in no_root, no_root
    other = _file_naming(tmp, 'e', '999', {'test_a.py': 1.0}, ['test_c.py'])
    not_there = verify_unmeasured_list(other, root)
    assert '999' in not_there and 'UNCHECKED' in not_there, not_there
    unreadable = Path(tmp) / 'unreadable'
    write_run(unreadable, 100, _TWO_CELLS)
    (unreadable / '100' / 'cell-01' / 'head-1' / 'test_b.json').write_text(
        'not json', encoding='utf-8')
    cannot = verify_unmeasured_list(data, unreadable)
    assert 'cannot be read' in cannot and 'UNCHECKED' in cannot, cannot


def test_a_caller_naming_no_unmeasured_suite_cannot_certify_the_tree(tmp):
    """The generator's own floor, on the input that broke the file.

    `basis_sentence` takes the unmeasured set from its caller, and the
    committed file was written by a caller that had none: a
    regeneration reading the list back out of the prose it was
    regenerating, so a basis naming no unmeasured suite fed back a basis
    naming no unmeasured suite, and the byte compare agreed with it
    while the file recorded 328 of the tree's 340 suites. The sentence
    it wrote -- "every suite in the tree is measured by these runs" --
    was false, and the generator emitted it while holding the tree that
    disproved it.

    So the tree here holds `test_c.py` and the file records only
    `test_a.py` and `test_b.py`, which is the shape the write being a
    union forbids: a suite the runs measured would have been written
    into `suite_weights`, so one the tree holds and the file records
    nothing about was not measured by the runs that wrote the file. The
    caller reports none, and the sentence must still name it.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    suites = ['test_a.py', 'test_b.py', 'test_c.py']
    data = _data({'test_a.py': 1.0, 'test_b.py': 2.0}, max_cells=5,
                 measured_from='100')
    tree = fixture_tree(Path(tmp) / 'tree', suites)
    said = bounds.basis_sentence(tree, data, 2, [])
    assert 'every suite in the tree is measured' not in said, said
    assert "1 of the tree's 3 suites are not measured" in said, said
    assert 'test_c.py' in said, said
    # And the two clauses cannot disagree about the count: the weight
    # clause adds what the coverage clause names, and it is the same
    # set now that the generator does not read the caller's list twice.
    assert 'the 1 suite these runs did not measure' in said, said


def test_the_clause_is_checked_against_the_tree_too(tmp):
    """The tree witness: a clause that agrees with the file and not the tree.

    `verify_unmeasured_list` compares the clause with the runs over the
    FILE's claims, so a file claiming less than its tree is invisible to
    it. The committed file once read "every suite in the tree is measured
    by these runs" while recording 328 of 340 suites, and every
    file-alone control agreed with it, because every one of them was
    reading the 328 back.

    Run 100 measures `test_a.py` and `test_b.py`. A file naming
    `test_c.py` unmeasured, with a tree holding exactly the three, is
    right about both the runs and the tree. A file naming none is wrong
    about the tree while remaining right about its own claims, which is
    the shape that got through.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0}})
    tree = fixture_tree(Path(tmp) / 'tree', ['test_a.py', 'test_b.py',
                                             'test_c.py'])
    honest = _file_naming(
        tmp, 'f', '100', {'test_a.py': 1.0, 'test_b.py': 1.0},
        ['test_c.py'])
    report = verify_clause_against_tree(honest, root, tree)
    assert '100 measured 2' in report, report
    assert 'UNCHECKED' not in report, report
    blind = _file_naming(tmp, 'g', '100',
                         {'test_a.py': 1.0, 'test_b.py': 1.0}, [])
    try:
        verify_clause_against_tree(blind, root, tree)
    except AssertionError as error:
        said = str(error)
        assert 'test_c.py' in said, said
        assert 'did not cover' in said, said
    else:
        raise AssertionError(
            'a basis naming no unmeasured suite was accepted against a tree '
            'holding a suite the runs it names did not measure')


def test_the_clause_against_the_tree_declines_visibly(tmp):
    """Every way the tree check does nothing, asserted to say so.

    The same three shapes the other two run-side controls decline, for
    the same reason: a control that compares nothing and reports nothing
    is indistinguishable from one that compared and agreed.
    """
    root = Path(tmp) / 'runs'
    write_run(root, 100, _TWO_CELLS)
    tree = fixture_tree(Path(tmp) / 'tree', ['test_a.py', 'test_b.py',
                                             'test_c.py'])
    data = _file_naming(tmp, 'h', '100', {'test_a.py': 1.0}, ['test_c.py'])
    no_root = verify_clause_against_tree(data, Path(tmp) / 'nowhere', tree)
    assert 'no runs root' in no_root and 'UNCHECKED' in no_root, no_root
    other = _file_naming(tmp, 'i', '999', {'test_a.py': 1.0}, ['test_c.py'])
    not_there = verify_clause_against_tree(other, root, tree)
    assert '999' in not_there and 'UNCHECKED' in not_there, not_there
    unreadable = Path(tmp) / 'unreadable'
    write_run(unreadable, 100, _TWO_CELLS)
    (unreadable / '100' / 'cell-01' / 'head-1' / 'test_b.json').write_text(
        'not json', encoding='utf-8')
    cannot = verify_clause_against_tree(data, unreadable, tree)
    assert 'cannot be read' in cannot and 'UNCHECKED' in cannot, cannot


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedfeed_')


if __name__ == '__main__':
    raise SystemExit(main())
