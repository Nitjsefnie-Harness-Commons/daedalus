#!/usr/bin/env python3
"""The timed-matrix planner: the schema it reads and the packing it does.

Every test drives the planner's `main()` over a temp tree and a temp
timings file, so a failure is the planner's behaviour. The temp tree's
suite files are empty: the planner reads file NAMES.
"""
import contextlib
import io
import json
import random
import re
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
        'measured_from': 'tests run 1',
        'runs': 1,
        'suite_weights': weights,
    }
    data.update(fields)
    return data


def _write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


def _run(planner, args, expect=0):
    """Run main() with stdout captured; return (exit code, stdout)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = planner.main(args)
    assert code == expect, (code, buffer.getvalue())
    return buffer.getvalue()


def _plan(tmp, suites, data, *flags, expect=0):
    """Run the CLI over a temp tree and file; return the Plan it made."""
    tree = _tree(tmp, suites)
    path = _write(Path(tmp) / 'timings.json', data)
    planner = _planner()
    out = _run(planner, ['--tree', str(tree), '--timings', str(path),
                         *flags], expect)
    return planner.last_plan(), out


def _summary(tmp, suites, data, name='summary.txt'):
    out = Path(tmp) / name
    _run(_planner(), [
        '--tree', str(_tree(Path(tmp), suites)), '--timings', str(
            _write(Path(tmp) / 'timings.json', data)),
        '--summary-file', str(out)])
    return out.read_text(encoding='utf-8')


def _seed_weights():
    """Weights with the measured distribution's SHAPE, not its values."""
    heavy = [60.0, 35.0, 32.0, 28.0, 27.0, 25.0, 23.0, 18.0]
    band = [16.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0,
            2.0]
    small = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2]
    return {f'test_seed_{index:02d}.py': weight
            for index, weight in enumerate(heavy + band + small)}


def test_every_suite_file_runs_in_exactly_one_cell(tmp):
    """The issue's first guarantee: the cells partition the tree."""
    suites = ['test_alpha.py', 'test_beta.py', 'test_gamma.py',
              'test_delta.py', 'test_epsilon.py', 'test_zeta.py']
    weights = {'test_alpha.py': 7.0, 'test_beta.py': 5.0,
               'test_gamma.py': 3.0, 'test_delta.py': 3.0,
               'test_epsilon.py': 1.0, 'test_zeta.py': 0.5}
    plan, _out = _plan(tmp, suites, _data(weights))
    placed = [name for cell in plan.cells for name in cell.suites]
    assert sorted(placed) == sorted(suites), plan.matrix
    assert len(placed) == len(set(placed)), placed
    for name in placed:
        assert (Path(tmp) / 'tree' / 'tests' / name).exists(), name


def test_the_generated_group_names_are_unique_and_check_run_safe(tmp):
    """A plan emits at least one cell whose groups are unique and safe.

    The `timed` job's `name:` is static, so a cell's `group` value is
    what reaches the check-run name GitHub builds for it.
    """
    plan, _out = _plan(
        tmp, [f'test_{index:02d}.py' for index in range(12)],
        _data({f'test_{index:02d}.py': float(20 - index)
               for index in range(12)}))
    groups = [cell['group'] for cell in plan.matrix]
    assert groups, 'the plan emitted no matrix cell'
    assert len(groups) == len(set(groups)), groups
    for group in groups:
        assert re.fullmatch(r'[a-z0-9][a-z0-9-]*', group), group


def test_no_cell_exceeds_the_median_cell_by_more_than_the_stated_margin(
        tmp):
    """The issue's second guarantee, against the MARGIN constant."""
    # The measured distribution's weights on a tree of this
    # repository's own suite names, at targets across the range the
    # run was measured at, and on a runner twice as slow. The heavy
    # tail sits alone in its cells at the lower targets, so the ratio
    # is `heaviest / median` there. The values are run 36070301583's own
    # per-suite medians; `plan_timed_matrix`'s module docstring is where
    # the margin's measured basis lives, and this fixture is the same
    # run's data rather than a second telling of it.
    measured = {
        'test_watcher_budget.py': 76.2, 'test_command_queue.py': 35.8,
        'test_cli.py': 32.3, 'test_overlap_harness.py': 31.5,
        'test_tab_routing_sequence_reads.py': 28.7,
        'test_tab_routing_collapse.py': 27.9,
        'test_dashboard_harness.py': 26.8,
        'test_real_browser_eval.py': 26.8, 'test_cmdqueue.py': 25.3,
        'test_tab_routing.py': 23.7, 'test_mcp_server.py': 18.3,
        'test_static_guard_regressions.py': 15.9,
        'test_dashboard_node_retry.py': 12.2,
        'test_stream_lifecycle.py': 12.1,
        'test_tab_routing_store_sweep.py': 11.2,
        'test_starvation_bounds.py': 10.8,
        'test_coverage_bindings.py': 10.5, 'test_bridge_streams.py': 10.3,
        'test_drain_bounds.py': 10.2, 'test_coverage_suites.py': 10.2}
    suites = sorted(
        path.name for path in (ROOT / 'tests').glob('test_*.py'))
    assert suites, 'no suite files on this tree'
    ladder = [0.3, 0.4, 0.5, 0.6, 0.9, 1.2, 1.8, 2.5]
    weights = {name: ladder[index % len(ladder)]
               for index, name in enumerate(suites)}
    weights.update(measured)
    for target in (56.0, 57.0, 58.0, 59.0, 66.0, 70.0, 80.0):
        for factor in (1.0, 2.0):
            scaled = {name: value * factor
                      for name, value in weights.items()}
            plan, _out = _plan(
                tmp, sorted(scaled), _data(
                    scaled, target=target * factor, max_cells=1000))
            loads = [cell.weight for cell in plan.cells]
            median = statistics.median(loads)
            bound = median * (1 + _planner().CELL_WEIGHT_MARGIN)
            if max(loads) > bound:
                # The guarantee is the margin; a target whose heaviest
                # cell sits above it is a target the file's target_cell
                # weight must not be, and the planner names it in the
                # run summary. The test pins the note that says so.
                summary = _summary(
                    tmp, sorted(scaled), _data(
                        scaled, target=target * factor, max_cells=1000),
                    'margin.txt')
                assert 'over the' in summary and 'margin' in summary, (
                    summary)
                assert f'target of {target * factor:g}' in summary, summary


def test_the_margin_holds_for_the_real_tree_with_its_own_weights(tmp):
    """The margin, on every suite this tree has, over a ladder of targets.

    The real tree is ~240 suites, so one misplacement is a visible share
    of a cell. The data file lands with Task 2, so the weights come from
    the tree's own suite names through a ladder of distinct weights, and
    the targets start at the ladder's span and double from there.
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
        plan, _out = _plan(
            tmp, suites, _data(weights, target=target, max_cells=1000))
        loads = [cell.weight for cell in plan.cells]
        median = statistics.median(loads)
        assert max(loads) <= median * (1 + planner.CELL_WEIGHT_MARGIN), (
            target, [round(value, 2) for value in sorted(loads)])


def test_a_uniform_scale_of_every_weight_gives_the_same_matrix(tmp):
    """A runner twice as fast, or half as fast, plans the same cells.

    The fixture is sized so a planner that rounded the scaled total UP
    would derive one cell more: 14 weights with target 10 is ceil(14),
    and 0.7 weights with target 0.5 is ceil(19.6) = 20 against the same
    14 when the scale is undone.
    """
    suites = [f'test_{index:02d}.py' for index in range(14)]
    weights = {name: 1.0 for name in suites}
    plan, first = _plan(tmp, suites, _data(weights, target=10.0))
    assert len(plan.matrix) == 2, plan.matrix
    for factor in (0.01, 0.5, 2.0, 1000.0):
        again, out = _plan(
            tmp, suites, _data(weights, target=10.0), '--scale', str(factor))
        assert again.matrix == plan.matrix, (factor, again.matrix)
        assert out == first, (factor, out)
    for factor in (0.01, 0.5, 2.0, 1000.0):
        scaled = {name: weight * factor for name, weight in weights.items()}
        again, out = _plan(
            tmp, suites, _data(scaled, target=10.0 * factor))
        assert out == first, (factor, out)


def test_the_cell_count_is_ceil_of_total_over_target(tmp):
    """`N = ceil(total / target)`, over the target boundary both ways."""
    # With no suite over the target the count is ceil(3w / target); with
    # every suite over it, one cell opens for a heavy suite and the
    # rest share. The rows cross the target from both sides.
    suites = ['test_a.py', 'test_b.py', 'test_c.py']
    for target, rows in (
            (3.0, ((0.5, 1), (1.0, 1), (1.1, 2), (2.0, 2))),
            (1.5, ((0.5, 1), (1.0, 2), (1.1, 3), (1.6, 2))),
            (1.0, ((0.5, 2), (0.9, 3), (1.1, 2), (2.0, 2))),
            (0.5, ((0.4, 3), (0.5, 3), (0.6, 2), (2.0, 2)))):
        for weight, expected in rows:
            weights = {name: weight for name in suites}
            plan, _out = _plan(tmp, suites, _data(weights, target=target))
            assert len(plan.matrix) == expected, (
                target, weight, plan.matrix)


def test_the_max_cells_bound_holds(tmp):
    """Weights that need more cells than the bound never exceed it.

    Four suites at 400/200/200/200 with a target of 1 derive 1000
    cells against a bound of 4; three are over the target, so the
    matrix is three cells. A planner that gave every heavy suite a cell
    would ship four.
    """
    suites = ['test_s.py', 'test_a.py', 'test_b.py', 'test_c.py']
    weights = {'test_s.py': 400.0, 'test_a.py': 200.0, 'test_b.py': 200.0,
               'test_c.py': 200.0}
    plan, _out = _plan(tmp, suites, _data(weights, target=1.0, max_cells=4))
    assert len(plan.matrix) <= 4, plan.matrix
    assert len(plan.matrix) == 3, plan.matrix
    summary = _summary(
        tmp, suites, _data(weights, target=1.0, max_cells=4), 'clamp.txt')
    assert 'clamped' in summary, 'clamp was silent'
    assert 'max_cells bound 4' in summary, summary


def test_max_cells_one_below_the_heavy_count_still_bounds_the_cells(tmp):
    """The bound holds when the heavy suites outnumber the cells.

    Three suites heavier than the target and one small one, with
    `max_cells` one below the heavy count: the matrix is the bound, the
    suites that gave up a cell are named, and every suite is placed
    exactly once. A packer that forced an extra shared cell past the
    bound shipped one cell too many while the summary still said the
    bound held.
    """
    suites = ['test_h1.py', 'test_h2.py', 'test_h3.py', 'test_s.py']
    weights = {'test_h1.py': 15.0, 'test_h2.py': 12.0, 'test_h3.py': 11.0,
               'test_s.py': 1.0}
    plan, _out = _plan(
        tmp, suites, _data(weights, target=10.0, max_cells=3))
    assert len(plan.matrix) == 3, plan.matrix
    placed = sorted(name for cell in plan.cells for name in cell.suites)
    assert placed == sorted(suites), plan.cells
    summary = _summary(tmp, suites, _data(weights, target=10.0,
                                          max_cells=3), 'heavy-bound.txt')
    assert 'max_cells bound 3' in summary, summary
    assert 'clamped' in summary, summary
    assert 'test_h3.py' in summary, summary
    assert 'split candidate' in summary, summary


def test_a_bound_below_every_heavy_count_still_bounds_the_cells(tmp):
    """A bound of one with every suite over the target is one cell."""
    suites = ['test_h1.py', 'test_h2.py', 'test_s.py']
    weights = {'test_h1.py': 40.0, 'test_h2.py': 30.0, 'test_s.py': 1.0}
    plan, _out = _plan(tmp, suites, _data(weights, target=10.0, max_cells=1))
    assert len(plan.matrix) == 1, plan.matrix
    placed = sorted(name for cell in plan.cells for name in cell.suites)
    assert placed == sorted(suites), plan.cells
    assert all(cell.suites for cell in plan.cells), plan.cells
    summary = _summary(tmp, suites, _data(weights, target=10.0, max_cells=1),
                       'bound-one.txt')
    assert 'test_h1.py, test_h2.py' in summary, summary


def test_a_bound_of_one_with_every_suite_heavy_returns_a_matrix(tmp):
    """The reviewer's repro: no cell to choose among is still a plan.

    A bound of one with every suite over the target leaves the heavy
    suites no cell of their own, so the first cell opens on the
    heaviest suite. Before this row the placement asked `min()` to
    choose among zero cells and the planner answered with a raw
    ValueError on a schema-valid file.
    """
    suites = ['test_a.py', 'test_b.py', 'test_c.py']
    weights = {'test_a.py': 20.0, 'test_b.py': 15.0, 'test_c.py': 12.0}
    plan, out = _plan(
        tmp, suites, _data(weights, target=10.0, max_cells=1))
    assert len(plan.matrix) == 1, plan.matrix
    placed = sorted(name for cell in plan.cells for name in cell.suites)
    assert placed == sorted(suites), plan.cells
    assert len(placed) == len(set(placed)), placed
    assert out.strip(), 'the CLI printed no matrix'
    single, _out = _plan(Path(tmp) / 'single', ['test_s.py'],
                         _data({'test_s.py': 20.0}, target=10.0,
                               max_cells=1))
    assert single.matrix == [
        {'group': 'cell-01', 'suites': 'test_s.py'}], single.matrix


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
    plan, _out = _plan(tmp, suites, _data(
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
    plan, _out = _plan(
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


def test_an_unrecorded_suite_is_estimated_at_the_median_not_the_mean(tmp):
    """The estimate is a median, and a fixture only a median survives.

    THREE recorded weights, lopsided: the recorded weights sum to 12 and
    their median is 1 where their mean is 4. One cell holds the whole
    tree, so the plan's own cell weight carries the estimate the planner
    gave the fourth suite, and the expectation is the guard's own
    condition -- `statistics.median` of what the file recorded -- rather
    than a number chosen here. A planner that estimated at the mean
    would place the same suite at 4 and every other assertion in the
    suite would still pass: the two-equals/one-recorded fixtures the
    rest of this file uses are exactly the shapes on which the two
    statistics agree.
    """
    suites = ['test_a.py', 'test_b.py', 'test_c.py', 'test_new.py']
    recorded = {'test_a.py': 1.0, 'test_b.py': 1.0, 'test_c.py': 10.0}
    plan, _out = _plan(tmp, suites, _data(recorded, target=1.0, max_cells=1))
    assert plan.estimated == ['test_new.py'], plan.estimated
    estimate = statistics.median(list(recorded.values()))
    assert estimate != statistics.mean(list(recorded.values())), recorded
    assert len(plan.cells) == 1, plan.cells
    assert plan.cells[0].weight == sum(recorded.values()) + estimate, (
        plan.cells[0], 'the estimate is not the recorded median')


def test_the_packing_is_deterministic(tmp):
    """The same file twice, and the file's own order permuted: one matrix."""
    suites = [f'test_{chr(ord("a") + i)}.py' for i in range(8)]
    weights = {name: 1.0 + (index % 3) for index, name in enumerate(suites)}
    first, _out = _plan(tmp, suites, _data(weights))
    second, _out = _plan(tmp, suites, _data(weights))
    assert first.matrix == second.matrix
    shuffled = list(weights.items())
    random.Random(7).shuffle(shuffled)
    permuted, _out = _plan(tmp, suites, _data(dict(shuffled)))
    assert permuted.matrix == first.matrix, permuted.matrix


def test_a_missing_timings_file_is_a_named_refusal(tmp):
    """No data file yet is a refusal with a remedy, not a traceback."""
    tree = _tree(tmp, ['test_a.py'])
    planner = _planner()
    stderr = _captured_stderr(planner, [
        '--tree', str(tree), '--timings', str(Path(tmp) / 'absent.json')])
    assert stderr.startswith('plan_timed_matrix:'), stderr
    assert 'no timings data at' in stderr, stderr
    _run(planner, [
        '--tree', str(tree), '--timings', str(Path(tmp) / 'absent.json')], 1)


def _captured_stderr(planner, args):
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        planner.main(args)
    return buffer.getvalue()


def test_a_stale_weight_does_not_consume_a_cell_and_is_named(tmp):
    """A weight for a suite the tree no longer holds is reported, dropped."""
    plan, _out = _plan(
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
    _run(planner, ['--tree', str(tree), '--timings', str(path),
                   '--out', str(out)])
    assert '\n' not in out.read_text(encoding='utf-8')
    cells = json.loads(out.read_text(encoding='utf-8'))
    assert list(cells[0]) == ['group', 'suites'], cells[0]
    assert cells[0]['group'] == 'cell-01', cells
    # The brief's own path is stdout: one line, no trailing newline.
    line = _run(planner, ['--tree', str(tree), '--timings', str(path)])
    assert '\n' not in line, repr(line)
    assert line == out.read_text(encoding='utf-8'), (line, out.read_text())


def test_the_schema_rejects_a_field_it_does_not_own(tmp):
    """The data file's fields are the module's, not the writer's to extend."""
    tree = _tree(tmp, ['test_a.py'])
    path = _write(Path(tmp) / 'bad.json',
                  _data({'test_a.py': 1.0}, surprise=1))
    stderr = _captured_stderr(
        _planner(), ['--tree', str(tree), '--timings', str(path)])
    assert 'unknown field: surprise' in stderr, stderr
    _run(_planner(), ['--tree', str(tree), '--timings', str(path)], 1)


def test_the_schema_requires_provenance_and_type_checks_it(tmp):
    """A weight with no run behind it is a weight no refresh can check.

    Every provenance field is required and type-checked, and the
    dropped `reference_normalized` is now an unknown field, so a file
    cannot claim both raw seconds and reference-normalized weights.
    """
    tree = _tree(tmp, ['test_a.py'])
    for drop, fields, reason in (
            (('measured_from',), {}, 'missing field: measured_from'),
            (('runs',), {}, 'missing field: runs'),
            ((), {'measured_from': 12345}, 'measured_from must be a'),
            ((), {'measured_from': '   '}, 'measured_from must be a'),
            ((), {'runs': 'lots'}, 'runs must be an integer'),
            ((), {'runs': 0}, 'runs must be an integer'),
            ((), {'units': 'minutes'}, "units 'minutes' must be one of"),
            ((), {'reference_normalized': True},
             'unknown field: reference_normalized')):
        data = _data({'test_a.py': 1.0})
        for name in drop:
            data.pop(name)
        data.update(fields)
        path = _write(Path(tmp) / 'prov.json', data)
        stderr = _captured_stderr(
            _planner(), ['--tree', str(tree), '--timings', str(path)])
        assert reason in stderr, (fields, stderr)
        _run(_planner(), ['--tree', str(tree), '--timings', str(path)], 1)
    # A file that says where its numbers came from is read.
    data = _data({'test_a.py': 1.0}, measured_from='tests run 1', runs=1)
    path = _write(Path(tmp) / 'ok.json', data)
    out = _run(_planner(), ['--tree', str(tree), '--timings', str(path)])
    assert '\n' not in out, repr(out)


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
