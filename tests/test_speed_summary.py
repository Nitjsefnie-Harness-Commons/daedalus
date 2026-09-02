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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedsummary_')


if __name__ == '__main__':
    raise SystemExit(main())
