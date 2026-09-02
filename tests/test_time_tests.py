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


def test_timing_replays_a_span_when_a_parent_chain_breaks(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  outer_before', '=== fixture.py ===', '  PASS  test_a',
        '=== innocent ===', '  PASS  test_b',
        '--- timed 1 passing tests in other.py',
        '--- timed 2 passing tests in fixture.py',
        '  PASS  outer_after',
        '--- timed 2 passing tests in innocent'))
    assert set(names) == {
        'outer_before', 'test_a', 'test_b', 'outer_after'}


def test_timing_replays_a_child_when_its_parent_later_breaks(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  suite_before', '=== outer.py ===',
        '  PASS  outer_direct_before', '=== inner.py ===',
        '  PASS  inner_real', '--- timed 1 passing tests in inner.py',
        '  PASS  outer_direct_after',
        '--- timed 1 passing tests in outer.py',
        '--- timed 2 passing tests in outer.py', '  PASS  suite_after'))
    assert set(names) == {
        'suite_before', 'outer_direct_before', 'inner_real',
        'outer_direct_after', 'suite_after'}


def test_timing_replays_a_child_when_its_poisoned_parent_is_open(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  suite_before', '=== outer.py ===',
        '  PASS  outer_direct_before', '=== inner.py ===',
        '  PASS  inner_real', '--- timed 1 passing tests in inner.py',
        '--- timed 0 passing tests in outer.py',
        '  PASS  outer_after', '  PASS  suite_after'))
    assert set(names) == {
        'suite_before', 'outer_direct_before', 'inner_real',
        'outer_after', 'suite_after'}


def test_timing_replays_a_child_when_its_parent_is_unclosed(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  suite_before', '=== outer.py ===',
        '  PASS  outer_before', '=== inner.py ===', '  PASS  inner_real',
        '--- timed 1 passing tests in inner.py',
        '  PASS  outer_after', '  PASS  suite_after'))
    assert set(names) == {
        'suite_before', 'outer_before', 'inner_real',
        'outer_after', 'suite_after'}


def test_timing_replays_a_deep_child_when_its_parent_chain_is_open(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  suite_before', '=== outer.py ===',
        '  PASS  outer_direct_before', '=== middle.py ===',
        '  PASS  middle_direct_before', '=== inner.py ===',
        '  PASS  inner_real', '--- timed 1 passing tests in inner.py',
        '=== dangling.py ===', '  PASS  dangling_real',
        '--- timed 0 passing tests in outer.py',
        '  PASS  outer_after', '  PASS  suite_after'))
    assert set(names) == {
        'suite_before', 'outer_direct_before', 'middle_direct_before',
        'inner_real', 'dangling_real', 'outer_after', 'suite_after'}


def test_timing_does_not_pop_a_poisoned_frame_on_a_later_match(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  suite_before', '=== outer.py ===',
        '  PASS  outer_direct', '--- timed 2 passing tests in outer.py',
        '--- timed 1 passing tests in outer.py', '  PASS  suite_after'))
    assert set(names) == {'suite_before', 'outer_direct', 'suite_after'}


def test_timing_does_not_validate_a_poisoned_frame_after_count_drift(tmp):
    names = _timed_names(tmp, _script(
        '  PASS  suite_before', '=== outer.py ===',
        '  PASS  outer_before', '--- timed 0 passing tests in outer.py',
        '  PASS  outer_after', '--- timed 2 passing tests in outer.py',
        '  PASS  suite_after'))
    assert set(names) == {
        'suite_before', 'outer_before', 'outer_after', 'suite_after'}


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
        durations, _outcomes = timing.time_suite(
            sys.executable, tree / 'tests' / 'test_bridge_one.py', tree)
    finally:
        timing.time = original_time
    assert durations == {'outer_before': 1, 'outer_after': 4}


def test_timing_keeps_interval_across_suppressed_failures(tmp):
    tree = _selection_tree(tmp, _script(
        '=== fixture.py ===', '  FAIL  test_fail', '  SKIP  test_skip',
        '--- timed 0 passing tests in fixture.py', '  PASS  outer_after'))
    timing = _time_tests()
    ticks = iter(range(6))
    original_time = timing.time
    timing.time = type('Clock', (), {'monotonic': lambda: next(ticks)})
    try:
        durations, _outcomes = timing.time_suite(
            sys.executable, tree / 'tests' / 'test_bridge_one.py', tree)
    finally:
        timing.time = original_time
    assert durations == {'outer_after': 5}


def test_timing_does_not_suppress_results_after_an_unmatched_closer(tmp):
    """An end marker without an open span leaves later results visible."""
    names = _timed_names(tmp, _script(
        '--- timed 1 passing tests in fixture.py', '  PASS  x', '1/1 passed'))
    assert set(names) == {'x'}


def test_timing_records_non_passing_outcomes(tmp):
    tree = _selection_tree(tmp, _script(
        '  PASS  test_ok', '  FAIL  test_bad', '  SKIP  test_skipped',
        '  ERROR  test_boom', '1/4 passed'))
    out = Path(tmp) / 'out'
    assert _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable,
         '--out', str(out)]) == 0
    report = json.loads((out / 'test_bridge_one.json').read_text('utf-8'))
    assert report['outcomes'] == {
        'test_bad': 'FAIL', 'test_skipped': 'SKIP', 'test_boom': 'ERROR'}
    assert set(report['tests']) == {'test_ok'}


def test_outcomes_stay_outside_suppressed_relay_spans(tmp):
    tree = _selection_tree(tmp, _script(
        '=== fixture.py ===', '  FAIL  test_hidden', '  SKIP  test_too',
        '--- timed 0 passing tests in fixture.py', '  PASS  test_visible'))
    out = Path(tmp) / 'out'
    assert _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable,
         '--out', str(out)]) == 0
    report = json.loads((out / 'test_bridge_one.json').read_text('utf-8'))
    assert report['outcomes'] == {}
    assert set(report['tests']) == {'test_visible'}


def test_the_latest_visible_outcome_wins_for_a_name(tmp):
    """A repeated name is reported where its latest result line put it.

    A PASS after a non-PASS drops the outcome; a non-PASS after a PASS
    writes the outcome and leaves the earlier duration standing, so one
    name can land in both maps.
    """
    cases = (
        (_script('  FAIL  test_x', '  PASS  test_x', '1/2 passed'),
         {}, {'test_x'}),
        (_script('  PASS  test_x', '  FAIL  test_x', '1/2 passed'),
         {'test_x': 'FAIL'}, {'test_x'}),
        (_script('  PASS  test_ok', '  FAIL  test_x', '  ERROR  test_x',
                 '1/3 passed'),
         {'test_x': 'ERROR'}, {'test_ok'}),
    )
    for index, (script, outcomes, passing) in enumerate(cases):
        tree = _selection_tree(Path(tmp) / str(index), script)
        out = Path(tmp) / str(index) / 'out'
        assert _time_tests().main(
            ['--tree', str(tree), '--python', sys.executable,
             '--out', str(out)]) == 0
        report = json.loads(
            (out / 'test_bridge_one.json').read_text('utf-8'))
        assert report['outcomes'] == outcomes, (index, report)
        assert set(report['tests']) == passing, (index, report)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='timetests_')


if __name__ == '__main__':
    raise SystemExit(main())
