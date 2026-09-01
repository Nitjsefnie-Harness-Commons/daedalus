#!/usr/bin/env python3
"""The timing instrument records only the suite that it directly runs."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _time_tests():
    return _util.load(ROOT / 'scripts' / 'ci' / 'time_tests.py')


def _script(*lines):
    return '; '.join(f'print({line!r})' for line in lines)


def _selection_tree(tmp, script=None):
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    script = script or _script('  PASS  test_a', '1/1 passed')
    for name in ('test_bridge_one.py', 'test_bridge_two.py', 'test_cli.py'):
        (tree / 'tests' / name).write_text(script, encoding='utf-8')
    return tree


def _timed_names(tmp, script, extra=()):
    tree = _selection_tree(tmp, script)
    out = Path(tmp) / 'out'
    args = ['--tree', str(tree), '--python', sys.executable,
            '--out', str(out), *extra]
    assert _time_tests().main(args) == 0
    report = out / 'test_bridge_one.json'
    return json.loads(report.read_text(encoding='utf-8'))['tests']


def test_timing_marker_collisions_fall_back_to_legacy(tmp):
    cases = (
        (_script('  PASS  own_before', '=== innocent heading ===',
                 '  PASS  own_after'), {'own_before', 'own_after'}),
        (_script(
            '  PASS  outer_before', '=== fixture.py ===', '  PASS  test_a',
            '--- timed 1 passing tests in other.py', '  PASS  test_b',
            '--- timed 2 passing tests in fixture.py',
            '  PASS  outer_after'), {'outer_before', 'outer_after'}),
        (_script(
            '  PASS  outer_before', '=== fixture.py ===', '  PASS  test_a',
            '--- timed 2 passing tests in fixture.py', '  PASS  test_b',
            '  PASS  outer_after'),
         {'outer_before', 'test_a', 'test_b', 'outer_after'}),
        (_script(
            '  PASS  outer_before', '=== fixture.py ===', '  PASS  test_a',
            '=== innocent heading ===', '  PASS  test_b',
            '--- timed 2 passing tests in fixture.py',
            '  PASS  outer_after'),
         {'outer_before', 'test_a', 'test_b', 'outer_after'}),
    )
    for index, (script, expected) in enumerate(cases):
        names = _timed_names(Path(tmp) / str(index), script)
        assert set(names) == expected


def test_timing_requires_a_count_on_a_relay_end_marker(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  outer_before', '=== fixture.py ===', '  PASS  test_a',
        '--- timed passing tests in fixture.py', '  PASS  test_b',
        '--- timed 2 passing tests in fixture.py', '  PASS  outer_after'))
    assert set(names) == {'outer_before', 'outer_after'}


def test_timing_keeps_nested_relay_spans_on_the_stack(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  outer_before', '=== middle.py ===', '  PASS  middle_before',
        '=== inner.py ===', '  PASS  inner',
        '--- timed 1 passing tests in inner.py', '  PASS  middle_after',
        '--- timed 2 passing tests in middle.py', '  PASS  outer_after'))
    assert set(names) == {'outer_before', 'outer_after'}


def test_timing_keeps_the_interval_across_a_relayed_span(tmp):
    tree = _selection_tree(tmp, _script(
        '  PASS  outer_before', '=== fixture.py ===', '  PASS  test_a',
        '--- timed 1 passing tests in fixture.py', '  PASS  outer_after'))
    timing = _time_tests()
    ticks = iter(range(6))
    original_time = timing.time
    timing.time = type('Clock', (), {'monotonic': lambda: next(ticks)})
    try:
        durations = timing.time_suite(
            sys.executable, tree / 'tests' / 'test_bridge_one.py', tree)
    finally:
        timing.time = original_time
    assert durations == {'outer_before': 1, 'outer_after': 4}


def test_timing_does_not_suppress_results_after_an_unmatched_closer(tmp):
    """An end marker without an open span leaves later results visible."""
    names = _timed_names(tmp, _script(
        '--- timed 1 passing tests in fixture.py', '  PASS  x', '1/1 passed'))
    assert set(names) == {'x'}


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='timetests_')


if __name__ == '__main__':
    raise SystemExit(main())
