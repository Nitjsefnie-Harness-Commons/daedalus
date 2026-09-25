#!/usr/bin/env python3
"""The timed-matrix planner: the schema it reads and the packing it does.

Every test drives the planner's `main()` over a temp tree and a temp timings
file, so a failure is the planner's behaviour and never this suite's
fixtures. The temp tree's suite files are empty: the planner reads the file
NAMES, exactly as the timing instrument does, and none of these tests is
about a suite's contents.
"""
import contextlib
import io
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


def _planner():
    return _util.load(
        ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
        'plan_timed_matrix')


def _tree(tmp, suites):
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True, exist_ok=True)
    for name in suites:
        (tree / 'tests' / name).write_text('pass\n', encoding='utf-8')
    return tree


def _data(weights, target=10.0, max_cells=30, **fields):
    data = {
        'schema_version': _planner().SCHEMA_VERSION,
        'target_cell_weight': target,
        'max_cells': max_cells,
        'units': 'seconds',
        'suite_weights': weights,
    }
    data.update(fields)
    return data


def _write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


def _plan(tmp, suites, data, *flags, expect=0):
    """Run the CLI over a temp tree and file; return the Plan it made."""
    tree = _tree(tmp, suites)
    path = _write(Path(tmp) / 'timings.json', data)
    planner = _planner()
    assert planner.main(
        ['--tree', str(tree), '--timings', str(path), *flags]) == expect
    return planner.last_plan(), tree


def _summary(tmp, suites, data, name='summary.txt'):
    out = Path(tmp) / name
    planner = _planner()
    assert planner.main([
        '--tree', str(_tree(Path(tmp), suites)), '--timings', str(
            _write(Path(tmp) / 'timings.json', data)),
        '--summary-file', str(out)]) == 0
    return out.read_text(encoding='utf-8')


def _seed_weights():
    """A weight set with the shape the tree's measurement has.

    A handful of suites over ten times the target, a band of suites
    between one and ten times it, and a long field of small ones: the
    distribution the planner is asked to balance, at a size that keeps
    the fixture cheap. The numbers are the measured distribution's
    SHAPE, not its values, so the fixture does not carry the data
    file's numbers.
    """
    heavy = [60.0, 35.0, 32.0, 28.0, 27.0, 25.0, 23.0, 18.0]
    band = [16.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0,
            2.0]
    small = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2]
    return {f'test_seed_{index:02d}.py': weight
            for index, weight in enumerate(heavy + band + small)}


def test_every_suite_file_runs_in_exactly_one_cell(tmp):
    """The issue's first guarantee: the cells partition the tree.

    Both halves are checked against the tree's own file list: a suite no
    cell names would silently stop being measured, and a cell naming a
    suite the tree does not hold would time nothing.
    """
    suites = ['test_alpha.py', 'test_beta.py', 'test_gamma.py',
              'test_delta.py', 'test_epsilon.py', 'test_zeta.py']
    weights = {'test_alpha.py': 7.0, 'test_beta.py': 5.0,
               'test_gamma.py': 3.0, 'test_delta.py': 3.0,
               'test_epsilon.py': 1.0, 'test_zeta.py': 0.5}
    plan, tree = _plan(tmp, suites, _data(weights))
    placed = [name for cell in plan.cells for name in cell.suites]
    assert sorted(placed) == sorted(suites), plan.matrix
    assert len(placed) == len(set(placed)), placed
    for name in placed:
        assert (tree / 'tests' / name).exists(), name


def test_no_cell_exceeds_the_median_cell_by_more_than_the_stated_margin(
        tmp):
    """The issue's second guarantee, against the MARGIN constant.

    The margin is a property of the packer over a real weight set, not
    a preference: the module's docstring records the measured ratio it
    covers. The fixture is the tree's own measured shape (a handful of
    heavy suites over a field of small ones), which is the shape the
    margin has to hold on.
    """
    weights = _seed_weights()
    plan, _ = _plan(tmp, sorted(weights), _data(weights, target=80.0))
    loads = [cell.weight for cell in plan.cells]
    median = statistics.median(loads)
    assert max(loads) <= median * (1 + _planner().CELL_WEIGHT_MARGIN), (
        [round(value, 2) for value in sorted(loads)])


def test_the_margin_holds_for_the_real_tree_with_its_own_weights(tmp):
    """The margin, on every suite this tree has, at any target in range.

    The real tree is ~240 suites, so one misplacement is a visible share
    of a cell. The data file lands with Task 2, so the weights come
    from the tree's own suite names through a fixed ladder of distinct
    weights, and the targets span the range where the derived cell
    count is at or above the number of distinct weights -- where a
    packer that stops packing stops balancing. The docstring records
    the measured ratio the margin covers.
    """
    planner = _planner()
    suites = sorted(
        path.name for path in (ROOT / 'tests').glob('test_*.py'))
    assert suites, 'no suite files on this tree'
    ladder = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 18.0]
    weights = {name: ladder[index % len(ladder)]
               for index, name in enumerate(suites)}
    span = max(ladder) - min(ladder)
    for target in (span, 1.5 * span, 2 * span, 3 * span, 1e4):
        plan, _ = _plan(
            tmp, suites, _data(weights, target=target, max_cells=1000))
        loads = [cell.weight for cell in plan.cells]
        median = statistics.median(loads)
        assert max(loads) <= median * (1 + planner.CELL_WEIGHT_MARGIN), (
            target, [round(value, 2) for value in sorted(loads)])


def test_a_uniform_scale_of_every_weight_gives_the_same_matrix(tmp):
    """A runner twice as fast, or half as fast, plans the same cells.

    The matrix is `strategy.matrix`, and a different number of cells
    changes how many check runs the gate has to wait for, so the
    derivation scales the target with the weights rather than measuring
    either against a constant. The fixture is sized so a planner that
    rounded the scaled total UP would derive one cell more: 14 weights
    with target 10 is ceil(14), and 0.7 weights with target 0.5 is
    ceil(19.6) = 20 against the same 14 when the scale is undone.
    """
    suites = [f'test_{index:02d}.py' for index in range(14)]
    weights = {name: 1.0 for name in suites}
    plan, _ = _plan(tmp, suites, _data(weights, target=10.0))
    assert len(plan.matrix) == 2, plan.matrix
    for factor in (0.01, 0.5, 2.0, 1000.0):
        scaled = {name: weight * factor for name, weight in weights.items()}
        again, _ = _plan(
            tmp, suites, _data(scaled, target=10.0 * factor))
        assert again.matrix == plan.matrix, (factor, again.matrix)


def test_the_cell_count_is_ceil_of_total_over_target(tmp):
    """`N = ceil(total / target)`, and nothing else in the tested range.

    Three suites of one weight each against a target of 3: the cell
    count is ceil(3w/3) = ceil(w), and the table walks it over the
    ceiling's exact case (w = 3 is one cell), one step over it (w > 3
    is two) and the case where every suite is a split candidate
    (w = 1 with a target of 3 gives one cell, not three).
    """
    suites = ['test_a.py', 'test_b.py', 'test_c.py']
    for weight, expected in ((0.5, 1), (1.0, 1), (1.5, 2), (2.0, 2),
                             (2.5, 3), (3.0, 3), (3.5, 3), (4.0, 3),
                             (5.0, 3), (10.0, 3)):
        weights = {name: weight for name in suites}
        plan, _ = _plan(tmp, suites, _data(weights, target=3.0))
        assert len(plan.matrix) == expected, (weight, plan.matrix)
    for weight, expected in ((1.0, 2), (1.5, 3), (2.0, 3), (2.5, 3),
                             (3.0, 3), (4.0, 3), (5.0, 3), (7.0, 3)):
        weights = {name: weight for name in suites}
        plan, _ = _plan(tmp, suites, _data(weights, target=1.5))
        assert len(plan.matrix) == expected, (weight, plan.matrix)
    plan, _ = _plan(tmp, suites, _data(
        {name: 1.0 for name in suites}, target=1.0))
    assert len(plan.matrix) == 3, plan.matrix
    plan, _ = _plan(tmp, suites, _data(
        {name: 1.0 for name in suites}, target=1.1))
    assert len(plan.matrix) == 3, plan.matrix


def test_the_max_cells_bound_holds(tmp):
    """Weights that need more cells than the bound get exactly the bound."""
    suites = ['test_s.py', 'test_a.py', 'test_b.py', 'test_c.py']
    weights = {'test_s.py': 400.0, 'test_a.py': 200.0, 'test_b.py': 200.0,
               'test_c.py': 200.0}
    plan, _ = _plan(tmp, suites, _data(weights, target=1.0, max_cells=4))
    assert len(plan.matrix) == 4, plan.matrix
    root = Path(tmp) / 'clamp'
    root.mkdir()
    tree = _tree(root, suites)
    path = _write(root / 'timings.json',
                  _data(weights, target=1.0, max_cells=4))
    out = root / 'summary.txt'
    planner = _planner()
    assert planner.main([
        '--tree', str(tree), '--timings', str(path),
        '--summary-file', str(out)]) == 0
    assert 'clamped' in out.read_text(encoding='utf-8'), 'clamp was silent'


def test_a_target_of_zero_is_a_refusal_with_its_reason(tmp):
    tree = _tree(tmp, ['test_a.py'])
    path = _write(Path(tmp) / 'zero.json', _data(
        {'test_a.py': 1.0}, target=0.0))
    stderr = _captured_stderr(
        _planner(), ['--tree', str(tree), '--timings', str(path)])
    assert 'target_cell_weight must be above zero' in stderr, stderr


def test_a_heavier_than_target_suite_lands_alone_and_is_a_candidate(tmp):
    """A suite over the target gets a cell to itself, and is named."""
    suites = ['test_heavy.py', 'test_a.py', 'test_b.py', 'test_c.py',
              'test_d.py']
    plan, _ = _plan(tmp, suites, _data(
        {'test_heavy.py': 25.0, 'test_a.py': 2.0, 'test_b.py': 2.0,
         'test_c.py': 1.0, 'test_d.py': 1.0},
        target=10.0, max_cells=4))
    assert plan.split_candidates == ['test_heavy.py'], plan.split_candidates
    assert [cell.suites for cell in plan.cells][0] == ['test_heavy.py'], (
        plan.cells)
    assert sorted(name for cell in plan.cells[1:]
                  for name in cell.suites) == ['test_a.py', 'test_b.py',
                                               'test_c.py', 'test_d.py'], (
        plan.cells)
    summary = _summary(tmp, suites, _data(
        {'test_heavy.py': 25.0, 'test_a.py': 2.0, 'test_b.py': 2.0,
         'test_c.py': 1.0, 'test_d.py': 1.0},
        target=10.0, max_cells=4), 'heavy.txt')
    assert 'split candidate' in summary, summary
    assert 'test_heavy.py' in summary, summary


def test_a_suite_with_no_recorded_weight_is_placed_and_named(tmp):
    """An unmeasured suite is measured at the median, and says so."""
    plan, _ = _plan(
        tmp, ['test_known.py', 'test_unknown.py'],
        _data({'test_known.py': 2.0}, max_cells=1))
    assert sorted(sum((cell.suites for cell in plan.cells), [])) == [
        'test_known.py', 'test_unknown.py']
    assert plan.estimated == ['test_unknown.py'], plan.estimated
    summary = _summary(tmp, ['test_known.py', 'test_unknown.py'],
                       _data({'test_known.py': 2.0}, max_cells=1),
                       'estimated.txt')
    assert 'test_unknown.py' in summary, summary
    assert 'estimated' in summary, summary


def test_the_packing_is_deterministic(tmp):
    """The same file twice, and the file's own order permuted: one matrix.

    A Python dict preserves insertion order, so reading the JSON in a
    different order is a different input unless the packer sorts first.
    """
    suites = [f'test_{chr(ord("a") + i)}.py' for i in range(8)]
    weights = {name: 1.0 + (index % 3) for index, name in enumerate(suites)}
    first, _ = _plan(tmp, suites, _data(weights))
    second, _ = _plan(tmp, suites, _data(weights))
    assert first.matrix == second.matrix
    shuffled = list(weights.items())
    random.Random(7).shuffle(shuffled)
    permuted, _ = _plan(tmp, suites, _data(dict(shuffled)))
    assert permuted.matrix == first.matrix, permuted.matrix


def test_a_missing_timings_file_is_a_named_refusal(tmp):
    """No data file yet is a refusal with a remedy, not a traceback."""
    tree = _tree(tmp, ['test_a.py'])
    planner = _planner()
    stderr = _captured_stderr(planner, [
        '--tree', str(tree), '--timings', str(Path(tmp) / 'absent.json')])
    assert stderr.startswith('plan_timed_matrix:'), stderr
    assert 'no timings data at' in stderr, stderr
    assert planner.main([
        '--tree', str(tree), '--timings', str(Path(tmp) / 'absent.json')]) == 1


def _captured_stderr(planner, args):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        planner.main(args)
    return buffer.getvalue()


def test_a_stale_weight_does_not_consume_a_cell_and_is_named(tmp):
    """A weight for a suite the tree no longer holds is reported, dropped."""
    plan, _ = _plan(
        tmp, ['test_a.py', 'test_b.py'],
        _data({'test_a.py': 1.0, 'test_b.py': 1.0, 'test_gone.py': 9.0},
              max_cells=1))
    assert plan.stale == ['test_gone.py'], plan.stale
    assert sorted(sum((c.suites for c in plan.cells), [])) == [
        'test_a.py', 'test_b.py']
    summary = _summary(tmp, ['test_a.py', 'test_b.py'], _data(
        {'test_a.py': 1.0, 'test_b.py': 1.0, 'test_gone.py': 9.0},
        max_cells=1), 'stale.txt')
    assert 'stale' in summary, summary
    assert 'test_gone.py' in summary, summary


def test_the_matrix_line_is_one_line_of_json_the_workflow_can_take(tmp):
    """`echo "matrix=$json" >> $GITHUB_OUTPUT` needs a newline-free value."""
    root = Path(tmp) / 'cli'
    root.mkdir()
    tree = _tree(root, ['test_a.py', 'test_b.py', 'test_c.py'])
    path = _write(root / 'timings.json', _data(
        {'test_a.py': 4.0, 'test_b.py': 2.0, 'test_c.py': 1.0}))
    out = root / 'matrix.json'
    planner = _planner()
    assert planner.main([
        '--tree', str(tree), '--timings', str(path), '--out', str(out)]) == 0
    assert '\n' not in out.read_text(encoding='utf-8')
    cells = json.loads(out.read_text(encoding='utf-8'))
    assert list(cells[0]) == ['group', 'suites'], cells[0]
    assert cells[0]['group'] == 'cell-01', cells


def test_the_schema_rejects_a_field_it_does_not_own(tmp):
    """The data file's fields are the module's, not the writer's to extend.

    This is what `ci-thresholds.json` does for its own keys, and it is
    why a refresher cannot quietly add a field no test reads.
    """
    tree = _tree(tmp, ['test_a.py'])
    path = _write(Path(tmp) / 'bad.json',
                  _data({'test_a.py': 1.0}, surprise=1))
    stderr = _captured_stderr(
        _planner(), ['--tree', str(tree), '--timings', str(path)])
    assert 'unknown field: surprise' in stderr, stderr
    assert _planner().main([
        '--tree', str(tree), '--timings', str(path)]) == 1


def test_the_planner_refuses_a_tree_with_no_suites(tmp):
    """Nothing to time is a refusal, the instrument's own rule."""
    tree = Path(tmp) / 'bare'
    (tree / 'tests').mkdir(parents=True)
    path = _write(Path(tmp) / 't5.json', _data({}))
    stderr = _captured_stderr(_planner(), [
        '--tree', str(tree), '--timings', str(path)])
    assert 'no suites found under' in stderr, stderr


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='timedplan_')


if __name__ == '__main__':
    raise SystemExit(main())
