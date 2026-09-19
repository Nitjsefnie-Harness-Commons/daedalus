#!/usr/bin/env python3
"""Timing selection and refusal of runs that measure nothing."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _time_tests():
    return _util.load(ROOT / 'scripts' / 'ci' / 'time_tests.py')


def _selection_tree(tmp):
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    script = 'import sys; print("  PASS  test_a"); print("1/1 passed"); '
    for name in ('test_bridge_one.py', 'test_bridge_two.py', 'test_cli.py'):
        (tree / 'tests' / name).write_text(script, encoding='utf-8')
    return tree


def _run_timing(tmp, tree, extra=(), expected=0):
    out = Path(tmp) / 'out'
    args = ['--tree', str(tree), '--python', sys.executable,
            '--out', str(out), *extra]
    assert _time_tests().main(args) == expected
    return out


def _report_names(tmp, extra=(), expected=0):
    out = _run_timing(tmp, _selection_tree(tmp), extra, expected)
    return sorted(path.name for path in out.glob('*.json'))


def test_timing_does_not_record_a_relayed_fixture_result(tmp):
    tree = _selection_tree(tmp)
    (tree / 'tests' / 'test_bridge_one.py').write_text(
        'print("  PASS  own_before"); print("=== fixture.py ==="); '
        'print("  PASS  test_a"); '
        'print("--- timed 1 passing tests in fixture.py"); '
        'print("  PASS  own_after")', encoding='utf-8')
    out = _run_timing(tmp, tree, ('--only', 'test_bridge_one.py'))
    report = out / 'test_bridge_one.json'
    names = json.loads(report.read_text(encoding='utf-8'))['tests']
    assert set(names) == {'own_before', 'own_after'}


def test_timing_runs_the_whole_tree_without_a_selection(tmp):
    """No selection flags, every suite timed — the callers' existing shape."""
    assert _report_names(tmp) == [
        'test_bridge_one.json', 'test_bridge_two.json', 'test_cli.json']


def test_timing_selects_only_the_suites_a_glob_names(tmp):
    """`--only` narrows a run to the suites its globs match.

    Match semantics are the pathlib ones over the suite file NAME, so a group
    is spelled as globs such as `test_cli*.py` without repeating `tests/`.
    """
    assert _report_names(
        tmp, ('--only', 'test_bridge_*.py', 'test_cli*.py')) == [
        'test_bridge_one.json', 'test_bridge_two.json', 'test_cli.json']


def test_timing_selects_one_area_of_a_partition(tmp):
    """A cell takes a disjoint slice, and the other suites never run."""
    assert _report_names(tmp, ('--only', 'test_cli*.py')) == [
        'test_cli.json']


def test_timing_selection_excludes_the_suites_an_except_names(tmp):
    """`--except` drops what its globs match, before `--only` is applied.

    The complement of the named groups is how the catch-all cell takes
    everything the named groups do not match: `--only '*'` minus every named
    glob is exactly that complement, on whatever tree the cell lands on.
    """
    assert _report_names(
        tmp, ('--only', '*', '--except', 'test_cli*.py')) == [
        'test_bridge_one.json', 'test_bridge_two.json']


def test_timing_selection_matching_nothing_is_a_failure(tmp):
    """A selection that matches no suite is a setup failure, not a fast one.

    The unfiltered shape already refuses a tree with no suites at all; a
    filtered shape that filtered everything out is the same measurement that
    did not happen, so it refuses the same way.
    """
    _report_names(tmp, ('--only', 'test_missing_*.py'), expected=1)


def test_timing_a_tree_that_yields_nothing_is_a_failure(tmp):
    """A tree whose every suite fails measured nothing; it is not fast."""
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    (tree / 'tests' / 'test_nothing.py').write_text(
        'import sys\n'
        'print("  FAIL  test_a: deliberate")\n'
        'print("0/1 passed")\n'
        'sys.exit(1)\n', encoding='utf-8')
    _run_timing(tmp, tree, expected=1)


def main():
    return _util.runner(_util.collect(globals()))


if __name__ == '__main__':
    raise SystemExit(main())
