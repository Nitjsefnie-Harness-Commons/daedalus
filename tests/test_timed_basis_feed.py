#!/usr/bin/env python3
"""Which cell count the shipped basis control hands the generator.

`test_timed_refresh.py` rebuilds `.github/suite-timings.json`'s `basis`
with `timings_bounds.basis_sentence` and compares it byte for byte, so
the count that prose states is an INPUT to the rebuild rather than
anything the file derives: a refresh supplies the cells the selected run
measured (`refresh_timings.py:389`) and a seed the `max_cells` it
derived from that same run (`refresh_timings.py:427`). The shipped
control fed the BOUND instead, which is green on any file whose prose
was written from that same bound -- the committed file carried 15 twice
until the first genuine bot refresh recorded the 13 the run really
measured and turned the control red.

The comparison lives here and the shipped control calls it, so a future
edit to the comparison is the code this suite exercises, and the shipped
suite stays under the module-size ceiling a second copy would push it
past.
"""
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _assert_the_generator_wrote_the_basis(tmp, data):
    """`data['basis']` compared to `basis_sentence`'s, byte for byte.

    The tree is derived from the FILE's own suite set -- the recorded
    weights plus the estimated names the file's own clause lists -- and
    never read from the working tree: the file describes its HEAD, the
    `suites` job checks out `refs/pull/N/merge`, and every time `main`
    gained a suite the merge tree disagreed with a committed artifact.

    The measured cell count is read out of the committed prose and fed
    back, for the reason this module's docstring gives. It is checked to
    parse and to sit in the file's own `1..max_cells` range before the
    compare, so a basis that lost the clause fails on the clause rather
    than on a diff of the whole sentence.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    basis = data['basis']
    match = re.search(
        r"(\d+) of the tree's (\d+) suites are not measured by these runs"
        r".*?: (.+?) Re-derive with ", basis)
    if match is None:
        assert 'every suite in the tree is measured' in basis, basis
        count, total, listed = 0, None, []
    else:
        count, total = int(match.group(1)), int(match.group(2))
        listed = [name.strip() for name in match.group(3).split(',')]
        # The tree-owned clause, checked from the file alone: the count it
        # states is the names it lists, and the tree it totals is the
        # recorded weights plus those names.
        assert count == len(listed), (count, listed)
        assert total == len(data['suite_weights']) + count, (
            total, len(data['suite_weights']), count)
    cells = re.search(r'the measured run ran (\d+) cells?,', basis)
    assert cells is not None, basis
    measured = int(cells.group(1))
    assert 1 <= measured <= data['max_cells'], (
        measured, data['max_cells'], basis)
    tree = _refresh._tree(tmp, sorted(set(data['suite_weights'])
                                      | set(listed)))
    recomputed = bounds.basis_sentence(
        tree, data, measured, bounds.estimated_count(tree, data))
    if recomputed != basis:
        raise AssertionError('\n'.join(difflib.unified_diff(
            basis.split('. '), recomputed.split('. '),
            'committed', 'generator', lineterm='', n=0)))


# `test_timed_refresh` owns the fixtures this suite reuses, and the
# shipped control imports the helper above back out of this module, so a
# mutual static import is the cycle pylint refuses. Loading it by path
# keeps the dependency one-directional; this must stay BELOW the helper,
# because the copy it loads imports that helper on its way in.
_refresh = _util.load(ROOT / 'tests' / 'test_timed_refresh.py',
                      'timed_refresh_fixtures')


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
    thing that can disagree.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    planner = _refresh._planner()
    suites = ['test_a.py', 'test_b.py', 'test_c.py']
    data = _refresh._data({'test_a.py': 1.0, 'test_b.py': 2.0,
                           'test_c.py': 3.0}, target=10.0, max_cells=5)
    written = _refresh._tree(Path(tmp) / 'written', suites)
    data['basis'] = bounds.basis_sentence(
        written, data, 2, bounds.estimated_count(written, data))
    stored = planner.read_timings(
        _refresh._file(tmp, data, name='suite-timings.json'))
    assert 'the measured run ran 2 cells' in stored['basis'], stored['basis']
    _assert_the_generator_wrote_the_basis(Path(tmp) / 'compared', stored)


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedfeed_')


if __name__ == '__main__':
    raise SystemExit(main())
