"""The timed matrix's PUBLISHED shape, and the expansion it must survive.

`plan-matrix` publishes one JSON line to `$GITHUB_OUTPUT` and the `timed`
job hands it to `strategy.matrix` through `fromJSON`. The property that
matters is not that the value parses — a bare array of the same cells is
perfect JSON — but that the runner's matrix evaluation EXPANDS it into the
cells the planner planned. This module holds the two halves: a model of
that expansion, and a runner that drives the planner's real `main()` to
the value the workflow actually publishes.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index  # noqa: E402


def matrix_cells(matrix):
    """The cells `strategy.matrix` expands a value into, as GitHub does.

    A matrix is an OBJECT: every key other than `include`/`exclude` is a
    dimension whose value-lists multiply, `exclude` drops combinations, and
    each `include` entry either extends a combination or adds a new one. A
    value that is not an object has no dimension key and no `include`, so it
    expands to NOTHING — the defect this models: a bare array of cells is
    valid JSON and yields zero runner instances, so the `timed` job is never
    created and only the required `speed` context fails.
    """
    if not isinstance(matrix, dict):
        return []
    dimensions = {key: values for key, values in matrix.items()
                  if key not in ('include', 'exclude')}
    combos = [{}]
    for key, values in dimensions.items():
        combos = [dict(combo, **{key: value})
                  for combo in combos for value in values]
    if not dimensions:
        # No dimension product to extend: each `include` entry stands alone.
        combos = []
    for excluded in matrix.get('exclude') or []:
        combos = [combo for combo in combos
                  if not all(combo.get(key) == value
                             for key, value in excluded.items())]
    for entry in matrix.get('include') or []:
        for combo in combos:
            if all(combo[key] == value for key, value in entry.items()
                   if key in combo):
                combo.update(entry)
                break
        else:
            combos.append(dict(entry))
    return combos


def published_matrix(tmp):
    """The planner's PUBLISHED value over a fixture tree, and its Plan.

    `main()` is run with stdout captured and the result parsed, so this is
    the one line the `plan-matrix` job writes to `$GITHUB_OUTPUT` and the
    `timed` job's `fromJSON` reads — not the Plan's own `.matrix` list, which
    is a different thing and cannot see a wrong published shape.
    """
    planner = _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                         'plan_timed_matrix')
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    names = [f'test_{index:02d}.py' for index in range(6)]
    for name in names:
        (tree / 'tests' / name).write_text('pass\n', encoding='utf-8')
    git_index(tree, 'init', '-q')
    git_index(tree, 'add', '--', 'tests/')
    # A schema-valid timings file, because `main()` reads it through
    # `read_timings` — the same validation the shipped data file passes.
    data = {
        'schema_version': planner.SCHEMA_VERSION,
        'target_cell_weight': 10.0,
        'max_cells': 4,
        'units': 'seconds',
        'measured_from': 'tests run 1',
        'runs': 1,
        'suite_weights': {name: float(7 - index)
                          for index, name in enumerate(names)},
    }
    timings = Path(tmp) / 'timings.json'
    timings.write_text(json.dumps(data), encoding='utf-8')
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = planner.main(['--tree', str(tree), '--timings', str(timings)])
    assert code == 0, buffer.getvalue()
    return json.loads(buffer.getvalue()), planner.last_plan()
