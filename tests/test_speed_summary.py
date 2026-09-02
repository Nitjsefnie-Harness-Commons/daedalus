#!/usr/bin/env python3
"""The speed summary's covered set, stated against what the runs recorded."""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _comparator():
    return _util.load(ROOT / 'scripts' / 'ci' / 'compare_durations.py')


def _summary_tree(tmp, side, rounds):
    """Per-suite reports per round, as time_tests.py writes them."""
    dirs = []
    for index, report in enumerate(rounds, start=1):
        directory = Path(tmp) / f'{side}-{index}'
        directory.mkdir(parents=True)
        (directory / 'test_suite.json').write_text(json.dumps(report),
                                                   encoding='utf-8')
        dirs.append(str(directory))
    return dirs


def _run_comparator(compare, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(stdout),
          contextlib.redirect_stderr(stderr)):
        try:
            code = compare.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stdout.getvalue() + stderr.getvalue()


def test_the_summary_states_the_covered_set_against_the_total(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {'a': 1.0, 'b': 2.0}}])
    head = _summary_tree(tmp, 'head', [
        {'tests': {'a': 1.5, 'b': 2.5, 'c': 9.0},
         'outcomes': {'d': 'FAIL'}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 1, output
    text = summary.read_text(encoding='utf-8')
    assert ('over 2 of 4 tests present and passing in every round on both '
            'sides.') in text, text
    assert '**FAIL**' in text, text


def test_report_without_outcomes_still_states_the_total(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {'a': 1.0, 'b': 2.0}}])
    head = _summary_tree(
        tmp, 'head', [{'tests': {'a': 1.0, 'b': 2.0, 'c': 1.0}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 0, output
    text = summary.read_text(encoding='utf-8')
    assert ('over 2 of 3 tests present and passing in every round on both '
            'sides.') in text, text
    assert '**OK**' in text, text


def test_the_all_drop_skip_line_names_the_total(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [
        {'tests': {'a': 1.0}, 'outcomes': {'b': 'FAIL'}}])
    head = _summary_tree(tmp, 'head', [
        {'tests': {'c': 2.0}, 'outcomes': {'d': 'SKIP'}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 0, output
    text = summary.read_text(encoding='utf-8')
    assert ('Skipped: no test passed in every round on both sides among the '
            '4 tests the suites recorded, so the comparison has no shared '
            'set to sum.') in text, text


def test_the_measured_comparison_exports_its_ratio(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {'a': 1.0, 'b': 1.0}}])
    head = _summary_tree(tmp, 'head', [{'tests': {'a': 1.042, 'b': 1.0}}])
    ratio_file = Path(tmp) / 'ratio.txt'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head,
        '--ratio-file', str(ratio_file)])
    assert code == 0, output
    assert ratio_file.read_text(encoding='utf-8') == '1.021\n', output


def test_a_comparison_with_no_shared_set_writes_no_ratio_file(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {'a': 1.0}}])
    head = _summary_tree(tmp, 'head', [{'tests': {'b': 1.0}}])
    ratio_file = Path(tmp) / 'ratio.txt'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--ratio-file', str(ratio_file)])
    assert code == 0, output
    assert not ratio_file.exists(), output


def test_an_unwritable_ratio_file_leaves_the_verdict_alone(tmp):
    compare = _comparator()
    block = Path(tmp) / 'block'
    block.write_text('a regular file, not a directory', encoding='utf-8')
    base = _summary_tree(tmp, 'base', [{'tests': {'a': 1.0}}])
    head = _summary_tree(tmp, 'head', [{'tests': {'a': 1.0}}])
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head,
        '--ratio-file', str(block / 'ratio.txt')])
    assert code == 0, output
    assert '**OK**' in output, output


def test_an_unwritable_summary_file_leaves_the_verdict_alone(tmp):
    compare = _comparator()
    block = Path(tmp) / 'block'
    block.write_text('a regular file, not a directory', encoding='utf-8')
    base = _summary_tree(tmp, 'base', [{'tests': {'a': 1.0}}])
    head = _summary_tree(tmp, 'head', [{'tests': {'a': 1.0}}])
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head,
        '--summary-file', str(block / 'summary.md')])
    assert code == 0, output
    assert ('over 1 of 1 tests present and passing in every round on both '
            'sides.') in output, output


def _table(text, heading, end):
    """The data rows of one summary table, delimited by two headings."""
    assert heading in text, text
    assert end in text, text
    start = text.index(heading) + len(heading)
    stop = text.index(end, start)
    return [line for line in text[start:stop].splitlines()
            if line.startswith('| `')]


def test_the_ratio_table_ranks_a_multiplier_above_ten_drifts(tmp):
    compare = _comparator()
    drifts = {f'test_drift_{index:02d}': 40.0 for index in range(10)}
    base = _summary_tree(tmp, 'base', [
        {'tests': {**drifts, 'test_multiplier': 0.40}}])
    grown = {name: 42.0 for name in drifts}
    head = _summary_tree(tmp, 'head', [
        {'tests': {**grown, 'test_multiplier': 2.00}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 0, output
    text = summary.read_text(encoding='utf-8')
    absolute = _table(text, 'Largest individual movements',
                      'Largest relative changes')
    relative = _table(text, 'Largest relative changes',
                      'Longest-running tests')
    assert len(relative) == 10
    assert relative[0].startswith('| `test_multiplier` | 0.40s | 2.00s |')
    assert relative[0].endswith('| 5.000 |')
    assert not any('test_multiplier' in row for row in absolute)


def test_the_noise_floor_omits_sub_floor_movements(tmp):
    # Pinned from both signs and both sides of the floor: 0.20s - 0.10s is
    # exactly the floor, 0.15s - 0.05s computes one ulp under it, and a
    # qualifying speedup appears with its ratio below 1.
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {
        'test_steady': 40.00, 'test_barely_moving': 1.00,
        'test_at_the_floor': 0.10, 'test_under_the_floor': 0.05,
        'test_ten_x': 0.04, 'test_faster': 2.00}}])
    head = _summary_tree(tmp, 'head', [{'tests': {
        'test_steady': 40.00, 'test_barely_moving': 1.09,
        'test_at_the_floor': 0.20, 'test_under_the_floor': 0.15,
        'test_ten_x': 0.40, 'test_faster': 1.00}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 0, output
    text = summary.read_text(encoding='utf-8')
    relative = _table(text, 'Largest relative changes',
                      'Longest-running tests')
    assert len(relative) == 3, relative
    assert relative[0] == ('| `test_ten_x` | 0.04s | 0.40s | +0.36s '
                           '| 10.000 |')
    assert relative[1] == ('| `test_at_the_floor` | 0.10s | 0.20s | +0.10s '
                           '| 2.000 |')
    assert relative[2] == ('| `test_faster` | 2.00s | 1.00s | -1.00s '
                           '| 0.500 |')


def test_a_zero_baseline_renders_inf_and_sorts_first(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {
        'test_steady': 20.00, 'test_doubled': 0.10, 'test_from_zero': 0.0}}])
    head = _summary_tree(tmp, 'head', [{'tests': {
        'test_steady': 20.00, 'test_doubled': 0.20, 'test_from_zero': 0.50}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 0, output
    text = summary.read_text(encoding='utf-8')
    relative = _table(text, 'Largest relative changes',
                      'Longest-running tests')
    assert relative[0].startswith('| `test_from_zero` | 0.00s | 0.50s |')
    assert relative[0].endswith('| inf |')
    assert relative[1].endswith('| 2.000 |')


def test_an_all_quiet_comparison_names_the_noise_floor(tmp):
    compare = _comparator()
    base = _summary_tree(tmp, 'base', [{'tests': {'test_quiet': 1.00}}])
    head = _summary_tree(tmp, 'head', [{'tests': {'test_quiet': 1.00}}])
    summary = Path(tmp) / 'summary.md'
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file', str(summary)])
    assert code == 0, output
    text = summary.read_text(encoding='utf-8')
    assert 'No test moved beyond the 0.10s noise floor.' in text, text
    assert 'Largest relative changes' not in text, text


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedsummary_')


if __name__ == '__main__':
    raise SystemExit(main())
