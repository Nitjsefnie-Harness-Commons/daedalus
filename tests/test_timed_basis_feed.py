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
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _timed_basis import (  # noqa: E402
    assert_the_generator_wrote_the_basis, fixture_tree)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _data(weights, target=10.0, max_cells=5):
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
        'measured_from': 'tests run 1',
        'runs': 1,
        'suite_weights': weights,
    }


def _file(tmp, data, name='suite-timings.json'):
    path = Path(tmp) / name
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return path


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


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedfeed_')


if __name__ == '__main__':
    raise SystemExit(main())
