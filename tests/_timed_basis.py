#!/usr/bin/env python3
"""The `basis` sentence the timings data file records, and how it is fed.

`.github/suite-timings.json` carries a `basis` field written by
`timings_bounds.basis_sentence` and by nothing else, and two suites
check it. `test_timed_refresh.py` checks the COMMITTED one against the
shipped generator; `test_timed_basis_feed.py` checks that a file whose
measured cell count differs from its bound is still rebuilt correctly.
Both call the comparison here, so an edit to it is exercised by both and
neither suite has to import the other.

THE MEASURED CELL COUNT IS AN INPUT, NOT A DERIVATION.
`basis_sentence` takes the cell count as an argument because the
refresher has it and the data file does not: a refresh supplies
`len(selected[0][2])`, the cells the selected run measured
(`refresh_timings.py:389`), and a seed supplies the `max_cells` it
derived from the same run (`:427`). The bound is carried over from the
file while the count is what the newest run measured, so the two are
different facts that need not coincide. The count is therefore read
back out of the committed prose and handed to the generator, and
`verify_recorded_count` re-derives it from the downloaded runs wherever
those are on disk -- which is the half of it that a data file alone
cannot settle.
"""
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))

# The count the prose states, in the two spellings `_plural` produces.
_MEASURED_CELLS_CLAUSE = r'the measured run ran (\d+) cells?,'
# The estimated-suite clause: how many, of how many in the tree, and
# which, so the tree can be rebuilt from the file rather than from the
# working tree the `suites` job checks out.
_ESTIMATED_CLAUSE = (
    r"(\d+) of the tree's (\d+) suites are not measured by these runs"
    r".*?: (.+?) Re-derive with ")


def fixture_tree(tmp, suites):
    """A git-indexed tree carrying `suites` under `tests/`.

    The same shape as `test_timed_refresh._tree`, which the shipped
    suite's own controls use. It is not shared across a suite boundary:
    a helper this branch has made load-bearing should not be the reason
    a control in another module cannot import, and the two shapes sit
    beside their callers so a change to one is visible as such.
    """
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


def recorded_cell_count(basis):
    """The measured cell count the prose states, or refuse the file.

    A basis that lost the clause, or spelled it a way `_plural` never
    would, fails here rather than half way into a whole-sentence diff.
    """
    cells = re.search(_MEASURED_CELLS_CLAUSE, basis)
    assert cells is not None, basis
    return int(cells.group(1))


def assert_the_generator_wrote_the_basis(tmp, data):
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
    match = re.search(_ESTIMATED_CLAUSE, basis)
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
    measured = recorded_cell_count(basis)
    assert 1 <= measured <= data['max_cells'], (
        measured, data['max_cells'], basis)
    tree = fixture_tree(tmp, sorted(set(data['suite_weights']) | set(listed)))
    recomputed = bounds.basis_sentence(
        tree, data, measured, bounds.estimated_count(tree, data))
    if recomputed != basis:
        raise AssertionError('\n'.join(difflib.unified_diff(
            basis.split('. '), recomputed.split('. '),
            'committed', 'generator', lineterm='', n=0)))


def verify_recorded_count(data, runs_root):
    """Re-derive the recorded cell count from the downloaded runs.

    The count is the one clause the byte compare reads out of the file
    rather than out of the runs, so this is the half that closes. It
    uses the refresher's OWN `discover_runs` and `select` -- the same
    two calls `refresh()` makes before it attaches a basis -- rather
    than a second implementation of the selection, and it never writes.

    Returns a one-line report of what it did, including when it could
    do nothing, so the caller can print it and a skip is visible rather
    than silent. Raises AssertionError only where it had both numbers
    and they disagree.
    """
    if not runs_root.is_dir():
        return (f'no runs root at {runs_root}: the recorded cell count went '
                f'UNCHECKED here')
    refresh = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                         'refresh_timings')
    selected, report = refresh.select(refresh.discover_runs(runs_root),
                                      refresh.SAMPLE_RUNS)
    if not selected:
        return (f'{runs_root} holds no complete run set ({report["empty"]} '
                f'with no cell artifacts, {len(report["incomplete"])} '
                f'with a different cell set): the recorded cell count went '
                f'UNCHECKED here')
    run_id, _weights, references = selected[0]
    measured = len(references)
    recorded = recorded_cell_count(data['basis'])
    assert recorded == measured, (
        f'the committed basis records the measured run ran {recorded} cells, '
        f'and run {run_id} under {runs_root} measured {measured}: the file '
        f'and the runs it was written from disagree')
    return (f'{runs_root}: run {run_id} measured {measured} cells, which is '
            f'what the committed basis records')
