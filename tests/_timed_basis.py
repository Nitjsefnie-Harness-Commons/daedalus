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
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))

# The count the prose states, in the two spellings `_plural` produces.
_MEASURED_CELLS_CLAUSE = r'the measured run ran (\d+) cells?,'
# The estimated-suite clause: how many, of how many in the tree, which
# are CARRIED at the weight the file already recorded and which are
# estimated at the recorded median, and which suites -- so the tree can
# be rebuilt from the file rather than from the working tree the
# `suites` job checks out. Each half is optional: the union write
# carries every weight it has and estimates only the suites that
# arrived after it, so either half can be empty and the other is the
# whole clause.
_ESTIMATED_CLAUSE = (
    r"(\d+) of the tree\'s (\d+) suites are not measured by these runs, "
    r"(?:(\d+) carried at the weight this file already recorded"
    r"(?: and)? )?"
    r"(?:(\d+) estimated at the median of the recorded weights)?"
    r": (.+?) Re-derive with "
)


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


def unmeasured_names(basis):
    """`(count, tree, names, carried, estimated)` from the coverage clause.

    The clause names the tree suites these runs did not measure, which
    is the fact a reader of the file alone has, and the fact the
    generator is fed. It is read out of the prose rather than
    recomputed, because the file does not record it anywhere else and
    a computation from the file's own weights is the defect this clause
    exists to fix: the write is a union, so a carried suite is recorded
    whatever the runs did.

    The two counts are the SPLIT, and they are not interchangeable: a
    carried suite keeps its own recorded weight and an unrecorded one
    is priced by the planner at the recorded median, so a clause that
    called both of them estimates would name a number the planner does
    not use. Either half is empty whenever the other is the whole
    clause, so each is optional and reads as zero.
    """
    match = re.search(_ESTIMATED_CLAUSE, basis)
    if match is None:
        assert 'every suite in the tree is measured' in basis, basis
        return 0, 0, [], 0, 0
    return (int(match.group(1)), int(match.group(2)),
            [name.strip() for name in match.group(5).split(',')],
            int(match.group(3) or 0), int(match.group(4) or 0))


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

    The suites the runs did not MEASURE are read out of the prose for
    the same reason and handed to the generator as itself, not
    recomputed: the write is a union, so the file records the carried
    suites too, and the generator's clause is about the runs. The
    clause's own split is checked before the compare, because a basis
    that lost a half of it has to fail on the split rather than on a
    diff of the whole sentence.
    """
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    basis = data['basis']
    count, total, listed, carried, estimated = unmeasured_names(basis)
    assert carried + estimated == count == len(listed), (
        count, len(listed), carried, estimated)
    if listed:
        # The tree-owned clause, checked from the file alone: the tree it
        # totals is the recorded weights plus the names it lists, and a
        # carried name is one the file already records.
        assert total == len(set(data['suite_weights']) | set(listed)), (
            total, len(data['suite_weights']), count)
        assert carried <= len(data['suite_weights']), (
            carried, len(data['suite_weights']))
    measured = recorded_cell_count(basis)
    assert 1 <= measured <= data['max_cells'], (
        measured, data['max_cells'], basis)
    tree = fixture_tree(tmp, sorted(set(data['suite_weights']) | set(listed)))
    recomputed = bounds.basis_sentence(tree, data, measured, listed)
    if recomputed != basis:
        raise AssertionError('\n'.join(difflib.unified_diff(
            basis.split('. '), recomputed.split('. '),
            'committed', 'generator', lineterm='', n=0)))


def verify_recorded_count(data, runs_root):
    """Re-derive the recorded cell count from the run the FILE names.

    The count is the one clause the byte compare reads out of the file
    rather than out of the runs, so this is the half that closes. The
    run it asks about is the one `data['measured_from']` names FIRST,
    which is exactly the `selected[0]` the refresher attached the basis
    from (`refresh_timings.py:388-389` writes the run ids and counts the
    same entry) -- and NOT whatever the runs root selects today.

    Asking about a re-selection instead compares the file with a run it
    was never written from, and there is a reachable shape of that: a
    newer run carrying a cell with head rounds and a reference reading
    but no suite summaries changes no weight and adds no suite, so the
    refresher correctly writes nothing, the file stays byte-identical
    and correct for its own provenance, and a re-selection counts the
    newer run's larger cell set and disagrees with a file it never
    wrote. That is a false red in the one job this runs in. Resolving
    the run the file names removes the class, not the instance.

    Discovery and the per-run parse are the refresher's own
    (`discover_runs`, `read_run`), and nothing here writes.

    Returns a one-line report of what it did, including every way it
    could do nothing, so the caller can print it and a skip is visible
    rather than silent. Raises AssertionError only where it had both
    numbers and they disagree.
    """
    if not runs_root.is_dir():
        return (f'no runs root at {runs_root}: the recorded cell count went '
                f'UNCHECKED here')
    named = data['measured_from'].split(',')[0].strip()
    runs = _util.load(ROOT / 'scripts' / 'ci' / 'timings_runs.py',
                      'timings_runs')
    found = [(run_id, path)
             for run_id, path in runs.discover_runs(runs_root)
             if str(run_id) == named]
    if not found:
        return (f'{runs_root} does not carry run {named}, the one the file '
                f'names: the recorded cell count went UNCHECKED here')
    run_id, path = found[0]
    try:
        _weights, references = runs.read_run(path, run_id)
    except runs.RefreshError as error:
        return (f'run {run_id} under {runs_root} cannot be read ({error}): '
                f'the recorded cell count went UNCHECKED here')
    measured = len(references)
    recorded = recorded_cell_count(data['basis'])
    assert recorded == measured, (
        f'the committed basis records the measured run ran {recorded} cells, '
        f'and run {run_id} under {runs_root} measured {measured}: the file '
        f'and the run it was written from disagree')
    return (f'{runs_root}: run {run_id} measured {measured} cells, which is '
            f'what the committed basis records')


def verify_unmeasured_list(data, runs_root):
    """Check the file's UNMEASURED list against the runs it names.

    The clause is read out of the prose and handed back to the generator,
    so the byte compare is self-consistent by construction: a generator
    and a file can be wrong together and green. This is the half that
    cannot be, wherever the downloaded runs are on disk -- the timed
    job alone.

    Every run the file names has to be under the root, not just the
    first: the basis's unmeasured set is the complement of the whole
    SAMPLE, so a partial root would compare one run's measurement
    against the union's and call the difference a disagreement. The run
    ids are `measured_from`'s, for the same reason
    `verify_recorded_count` asks about the run the file names rather
    than whatever the root selects today.

    Returns a one-line report including every way it could do nothing,
    so a skip is visible rather than silent, and raises only where it
    had both numbers and they disagree.
    """
    if not runs_root.is_dir():
        return (f'no runs root at {runs_root}: the unmeasured suite list went '
                f'UNCHECKED here')
    runs = _util.load(ROOT / 'scripts' / 'ci' / 'timings_runs.py',
                      'timings_runs')
    by_id = {str(run_id): path
             for run_id, path in runs.discover_runs(runs_root)}
    named = [part.strip() for part in data['measured_from'].split(',')]
    missing = [run_id for run_id in named if run_id not in by_id]
    if missing:
        return (f'{runs_root} does not carry run {", ".join(missing)}, which '
                f'the file names: the unmeasured suite list went UNCHECKED '
                f'here')
    measured = set()
    for run_id in named:
        try:
            measured |= runs.measured_suites(by_id[run_id], int(run_id))
        except (runs.RefreshError, ValueError) as error:
            return (f'run {run_id} under {runs_root} cannot be read '
                    f'({error}): the unmeasured suite list went UNCHECKED '
                    f'here')
    _c, _t, listed, _carried, _est = unmeasured_names(data['basis'])
    claims = set(data['suite_weights']) | set(listed)
    unmeasured = claims - measured
    assert set(listed) == unmeasured, (
        f'the committed basis says these runs did not measure '
        f'{sorted(listed)}, and the runs themselves measured '
        f'{sorted(claims - unmeasured)} of the file\'s {len(claims)} suites '
        f'and not {sorted(unmeasured)}: the file and the runs it was written '
        f'from disagree')
    return (f'{runs_root}: the run(s) {", ".join(named)} measured '
            f'{len(claims) - len(unmeasured)} of the file\'s {len(claims)} '
            f'suites, which is what the committed basis records')


def suite_file(path, seconds):
    """One `time_tests.py` summary: a tests map and no outcomes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'tests': {'test_a': seconds},
                                'outcomes': {}}), encoding='utf-8')


def write_run(root, run_id, cells, reference: float | None = 2.0, decoys=()):
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
                suite_file(run / cell / round_name / name, seconds)
        for decoy in decoys:
            for suite, seconds in suites.items():
                name = f'{Path(suite).stem}.json'
                suite_file(run / cell / decoy / name, seconds * 10)
        if reference is not None:
            (run / cell).mkdir(parents=True, exist_ok=True)
            (run / cell / 'reference.json').write_text(
                json.dumps({'seconds': reference, 'iterations': 16}),
                encoding='utf-8')
    return run
