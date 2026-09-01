#!/usr/bin/env python3
"""How the speed gate decides, and what it refuses to decide from.

The comparison sums the tests present and passing on both sides, pairs whole
rounds rather than per-test minima, and takes the median of the paired ratios.
Each of those is a decision about what a number is allowed to describe, so
these tests drive the comparison with rounds that disagree.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _durations_tree(tmp, side, rounds):
    """Write one summary directory per round, as run_tests.py would."""
    dirs = []
    for index, tests in enumerate(rounds, start=1):
        d = Path(tmp) / f'{side}-{index}'
        d.mkdir(parents=True)
        (d / 'test_suite.json').write_text(json.dumps({
            'total': len(tests), 'passed': len(tests),
            'skipped': 0, 'failed': 0, 'tests': tests,
        }), encoding='utf-8')
        dirs.append(str(d))
    return dirs


def _compare_durations():
    return _util.load(ROOT / 'scripts' / 'ci' / 'compare_durations.py')


def _time_tests():
    return _util.load(ROOT / 'scripts' / 'ci' / 'time_tests.py')


def _render_speed_summary(compare, shared, pairs, movements, limit=10):
    lines = []
    compare.render(lines, 'baseline', shared, pairs, movements, limit=limit)
    return lines


def _longest_rows(lines):
    header = '| test | head median | share of covered set |'
    assert header in lines, 'longest-running-tests table is missing'
    start = lines.index(header)
    return lines[start + 2:]


def _movement_rows(lines):
    """Rows of the movements table, which is not the summary's last block."""
    header = '| test | baseline | head | delta |'
    assert header in lines, 'largest-individual-movements table is missing'
    start = lines.index(header) + 2
    return lines[start:lines.index('', start)]


def test_speed_comparison_pairs_whole_rounds_rather_than_per_test_minima(tmp):
    """Every total reported has to be one a complete round actually achieved.

    Summing each test's independent minimum builds a side total no run ever
    produced, and it takes different amounts of noise off each side: on a
    recorded artifact it removed 2.4s from the baseline and 22.4s from the
    head, reporting 0.999 where every complete pair of rounds was at least
    1.07.

    Here each side is noisy on a different test in each round, so per-test
    minima would report 2.00s for a side whose every round took 11.00s.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'slow': 10.0, 'fast': 1.0},
                                         {'slow': 1.0, 'fast': 10.0}])
    head = _durations_tree(tmp, 'head', [{'slow': 11.0, 'fast': 1.0},
                                         {'slow': 1.0, 'fast': 11.0}])
    shared, pairs, _moves = compare.compare(compare.side_rounds(base),
                                            compare.side_rounds(head))
    assert shared == ['fast', 'slow'], shared
    assert [(base_total, head_total) for base_total, head_total, _r in pairs] \
        == [(11.0, 12.0), (11.0, 12.0)], pairs
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_gates_on_the_median_of_the_pairs(tmp):
    """One spoiled pair must not decide the verdict, and a majority must.

    A paired ratio can be ruined outright by a noisy neighbour landing on one
    round, so gating on any single pair would be a coin toss. The median only
    moves once most of the pairs agree.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}, {'a': 1.0}, {'a': 1.0}])
    spoiled = _durations_tree(tmp, 'spoiled',
                              [{'a': 1.0}, {'a': 5.0}, {'a': 1.0}])
    assert compare.main(['--base', *base, '--head', *spoiled,
                         '--max-regression', '0.30']) == 0
    real = _durations_tree(tmp, 'real', [{'a': 1.4}, {'a': 5.0}, {'a': 1.4}])
    assert compare.main(['--base', *base, '--head', *real,
                         '--max-regression', '0.30']) == 1


def test_speed_comparison_refuses_to_pair_unequal_round_counts(tmp):
    """A side that lost a round cannot be paired against one that did not.

    Pairing the first N and dropping the rest would answer with a comparison
    narrower than the one that was asked for, and say nothing about it.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}, {'a': 1.0}])
    head = _durations_tree(tmp, 'head', [{'a': 1.0}])
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head, '--summary-file', str(summary)]
    assert compare.main(argv) == 0
    assert compare.main(argv + ['--require-measurements']) == 1
    assert 'cannot be paired' in summary.read_text(encoding='utf-8')


def test_speed_comparison_ignores_a_test_only_one_side_ran(tmp):
    """Adding or removing a test must not move the number.

    This is the whole reason the comparison is per-test rather than a suite
    total: a release that grew three tests would otherwise read as a
    regression, and one that deleted three as an improvement.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'shared': 1.0, 'gone': 50.0}])
    head = _durations_tree(tmp, 'head', [{'shared': 1.0, 'added': 50.0}])
    shared, pairs, _moves = compare.compare(compare.side_rounds(base),
                                            compare.side_rounds(head))
    assert shared == ['shared'], shared
    assert pairs == [(1.0, 1.0, 1.0)], pairs
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_fails_only_past_its_budget(tmp):
    """A slowdown inside the budget passes; one past it fails."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}])
    within = _durations_tree(tmp, 'within', [{'a': 1.2}])
    past = _durations_tree(tmp, 'past', [{'a': 1.4}])
    assert compare.main(['--base', *base, '--head', *within,
                         '--max-regression', '0.30']) == 0
    assert compare.main(['--base', *base, '--head', *past,
                         '--max-regression', '0.30']) == 1


def test_speed_comparison_can_refuse_an_unmeasurable_run(tmp):
    """CI must not read "no data" as "no regression".

    Both no-data paths returned 0, so a speed job whose timing step produced
    nothing rendered a Skipped note and went green — the shape that reports
    clean forever. The lenient exit is still available for a hand run against
    a baseline that predates the durations format; --require-measurements is
    what the workflow passes.
    """
    empty_base = Path(tmp) / 'base'
    empty_head = Path(tmp) / 'head'
    for side in (empty_base, empty_head):
        side.mkdir(parents=True)
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', str(empty_base), '--head', str(empty_head),
            '--base-label', 'baseline', '--summary-file', str(summary)]
    assert _compare_durations().main(argv) == 0
    assert _compare_durations().main(argv + ['--require-measurements']) == 1

    # A baseline that measured something, against a head that shares nothing
    # with it, is the second no-data path and answers the same way.
    (empty_base / 'suite.json').write_text(
        json.dumps({'tests': {'only_on_base': 1.0}}), encoding='utf-8')
    (empty_head / 'suite.json').write_text(
        json.dumps({'tests': {'only_on_head': 1.0}}), encoding='utf-8')
    assert _compare_durations().main(argv) == 0
    assert _compare_durations().main(argv + ['--require-measurements']) == 1


def test_speed_comparison_passes_when_a_side_was_never_measured(tmp):
    """An unmeasured side is not a fast side, and must not divide the total.

    The timing instrument belongs to the comparison rather than to either
    checkout, so an empty side means its timing step failed. A comparator that
    treated that as zero seconds would report an infinite speedup.
    """
    compare = _compare_durations()
    old = Path(tmp) / 'base-1'
    old.mkdir(parents=True)
    (old / 'test_suite.json').write_text(json.dumps({
        'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,
    }), encoding='utf-8')
    head = _durations_tree(tmp, 'head', [{'a': 1.0}])
    summary = Path(tmp) / 'summary.md'
    assert compare.main(['--base', str(old), '--head', *head,
                         '--summary-file', str(summary)]) == 0
    assert 'produced no per-test durations' in summary.read_text(encoding='utf-8')


def _round_tree(tmp, side, rounds):
    """Per-suite reports for several rounds, as time_tests.py names them."""
    dirs = []
    for index, suites in enumerate(rounds, start=1):
        directory = Path(tmp) / f'{side}-{index}'
        directory.mkdir(parents=True)
        for suite, tests in suites.items():
            (directory / f'{suite}.json').write_text(
                json.dumps({'tests': tests}), encoding='utf-8')
        dirs.append(str(directory))
    return dirs


def _suite_tree(tmp, side, suites):
    """One round of per-suite reports, named as time_tests.py names them."""
    return _round_tree(tmp, side, [suites])


def test_speed_comparison_fails_when_a_suite_times_zero_on_both_sides(tmp):
    """A suite that produced nothing on both sides is a shrunken covered set.

    The comparison intersects on test name, so a suite that timed zero tests
    on both sides drops out of every total without anything reporting it --
    that is exactly how a cell whose virtualenv was missing a dependency
    still reported a verdict over 131 tests while 41 more sat unmeasured.
    """
    compare = _compare_durations()
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_broken': {}})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0},
                                     'test_broken': {}})
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head,
            '--summary-file', str(summary)]
    assert compare.main(argv) == 1
    text = summary.read_text(encoding='utf-8')
    assert '`test_broken.py`' in text, text
    assert 'no timed tests' in text, text


def test_speed_comparison_allows_a_suite_that_times_zero_on_one_side(tmp):
    """Zero on one side alone is a legitimate feature gap, not a lost suite."""
    compare = _compare_durations()
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_feature': {}})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0},
                                     'test_feature': {'covered': 1.0}})
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head,
            '--summary-file', str(summary)]
    assert compare.main(argv) == 0
    assert 'is within the 1.30 budget' in summary.read_text(encoding='utf-8')


def test_speed_comparison_allows_a_suite_absent_from_one_side(tmp):
    """A report only one side has is not a suite the covered set loses.

    test_nothing.py is the live shape: it exists on the baseline alone, so
    the head side has no report for it and nothing is silently dropped.
    """
    compare = _compare_durations()
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_nothing': {}})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0}})
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_zero_on_one_side_alone_is_not_a_loss(tmp):
    """Zero on one side alone is not a loss.

    The baseline's report for the suite is empty in one round and recorded
    tests in the next, while this commit recorded the test throughout: a
    feature gap on one side, which the comparison excludes by design rather
    than something it silently lost. Both sides ran both rounds, so the
    verdict comes from this shape and not from an unpaired round.
    """
    compare = _compare_durations()
    rounds = [{'test_suite': {'shared': 1.0}, 'test_flaky': {}},
              {'test_suite': {'shared': 1.0},
               'test_flaky': {'recovered': 1.0}}]
    base = _round_tree(tmp, 'base', rounds)
    head = _round_tree(tmp, 'head', [
        {'test_suite': {'shared': 1.0}, 'test_flaky': {'recovered': 1.0}},
        {'test_suite': {'shared': 1.0}, 'test_flaky': {'recovered': 1.0}}])
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head,
            '--summary-file', str(summary)]
    assert compare.main(argv) == 0
    assert ('over 1 tests present and passing'
            in summary.read_text(encoding='utf-8'))


def test_speed_comparison_fails_when_a_report_is_absent_from_both_rounds(tmp):
    """A shipped suite missing from the same round on both sides is lost.

    The suite ran in the second round of both sides and left no report in
    the first, so the intersection drops its tests from every total even
    though a run that recorded them happened on both sides.
    """
    compare = _compare_durations()
    base = _round_tree(tmp, 'base', [{'test_suite': {'shared': 1.0}},
                                     {'test_suite': {'shared': 1.0},
                                      'test_gap': {'lost_a': 1.0,
                                                   'lost_b': 2.0}}])
    head = _round_tree(tmp, 'head', [{'test_suite': {'shared': 1.0}},
                                     {'test_suite': {'shared': 1.0},
                                      'test_gap': {'lost_a': 1.0,
                                                   'lost_b': 2.0}}])
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head,
            '--summary-file', str(summary)]
    assert compare.main(argv) == 1
    assert '`test_gap.py`' in summary.read_text(encoding='utf-8')


def test_speed_comparison_a_report_that_records_nothing_does_not_ship(tmp):
    """A report that exists but records nothing does not ship the suite.

    The head side wrote an empty report in its first round and no report at
    all in its second: no round of that side ever recorded a test, so the
    absence is the feature-gap carve-out rather than a lost round. This is
    the shape that pins _ships against counting reports instead of tests.
    """
    compare = _compare_durations()
    base = _round_tree(tmp, 'base', [{'test_suite': {'shared': 1.0},
                                      'test_gap': {'lost': 1.0}},
                                     {'test_suite': {'shared': 1.0},
                                      'test_gap': {'lost': 1.0}}])
    head = _round_tree(tmp, 'head', [{'test_suite': {'shared': 1.0},
                                      'test_gap': {}},
                                     {'test_suite': {'shared': 1.0}}])
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_fails_when_a_report_is_absent_from_one_round(tmp):
    """A shipped suite's missing report is emptiness, not non-shipping.

    The comparison needs a suite's tests in every round of both sides, so a
    round whose report is missing drops the suite from every total -- the
    shape a timing run that died mid-round leaves behind, which the
    workflow reports and carries on from. The suite is present in this
    side's other round, so the side ships it and the absence is emptiness.
    """
    compare = _compare_durations()
    tests = {'lost_a': 1.0, 'lost_b': 2.0}
    base = _round_tree(tmp, 'base', [{'test_suite': {'shared': 1.0},
                                      'test_gap': tests},
                                     {'test_suite': {'shared': 1.0},
                                      'test_gap': tests}])
    head = _round_tree(tmp, 'head', [{'test_suite': {'shared': 1.0},
                                      'test_gap': tests},
                                     {'test_suite': {'shared': 1.0}}])
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head,
            '--summary-file', str(summary)]
    assert compare.main(argv) == 1
    assert '`test_gap.py`' in summary.read_text(encoding='utf-8')


def test_speed_comparison_allows_a_suite_absent_from_a_whole_side(tmp):
    """Absence from every round of a side is a suite that side does not ship.

    The multi-round form of the test_nothing shape: the baseline has the
    suite in both rounds and this commit has it in neither, so nothing the
    comparison could have measured is silently dropped.
    """
    compare = _compare_durations()
    base = _round_tree(tmp, 'base', [{'test_suite': {'shared': 1.0},
                                      'test_nothing': {}},
                                     {'test_suite': {'shared': 1.0},
                                      'test_nothing': {}}])
    head = _round_tree(tmp, 'head', [{'test_suite': {'shared': 1.0}},
                                     {'test_suite': {'shared': 1.0}}])
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_fails_when_a_suite_is_empty_in_a_round_on_both(tmp):
    """A suite empty in the same round on both sides is lost from every total.

    The comparison intersects on test name across every round, so one round
    that recorded nothing for a suite drops its tests from the covered set
    however much that suite's other rounds recorded.
    """
    compare = _compare_durations()
    rounds = [{'test_suite': {'shared': 1.0}, 'test_partial': {}},
              {'test_suite': {'shared': 1.0}, 'test_partial': {'later': 2.0}}]
    base = _round_tree(tmp, 'base', rounds)
    head = _round_tree(tmp, 'head', rounds)
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head,
            '--summary-file', str(summary)]
    assert compare.main(argv) == 1
    assert '`test_partial.py`' in summary.read_text(encoding='utf-8')


def test_speed_comparison_fails_when_a_report_is_corrupt_on_both_sides(tmp):
    """A report neither side could read records nothing the comparison keeps.

    time_tests.py writes each report with a plain non-atomic write, so a
    mid-write truncation leaves a file the completeness check still counts
    and the comparison silently reads as nothing.
    """
    compare = _compare_durations()
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_corrupt': {}})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0},
                                     'test_corrupt': {}})
    for side in (base[0], head[0]):
        (Path(side) / 'test_corrupt.json').write_text(
            '{"tests": {"lost": ', encoding='utf-8')
    assert compare.main(['--base', *base, '--head', *head]) == 1


def test_speed_comparison_fails_when_a_report_holds_no_test_map(tmp):
    """A report that parses but records no test map is empty, not absent."""
    compare = _compare_durations()
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_shapeless': {}})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0},
                                     'test_shapeless': {}})
    for side in (base[0], head[0]):
        (Path(side) / 'test_shapeless.json').write_text(
            '{"tests": [1, 2, 3]}', encoding='utf-8')
    assert compare.main(['--base', *base, '--head', *head]) == 1


def test_speed_comparison_fails_when_a_report_is_not_an_object(tmp):
    """A report whose body is not an object records no tests at all.

    JSON that parses to a list or a scalar is neither a report nor an error:
    the comparison would accept nothing from it, so it is emptiness in that
    round and the guard has to be able to say so.
    """
    compare = _compare_durations()
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_flat': {}})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0},
                                     'test_flat': {}})
    for side in (base[0], head[0]):
        (Path(side) / 'test_flat.json').write_text('[]', encoding='utf-8')
    assert compare.main(['--base', *base, '--head', *head]) == 1


def test_speed_comparison_counts_only_tests_the_comparison_accepts(tmp):
    """A duration the comparison would reject leaves the suite with nothing.

    The guard's emptiness has to be the comparison's own: a suite whose every
    duration is a value the covered set would refuse is a suite that
    contributes nothing to it.
    """
    compare = _compare_durations()
    tests = {'lost_a': 'oops', 'lost_b': None, 'lost_c': True}
    base = _suite_tree(tmp, 'base', {'test_suite': {'shared': 1.0},
                                     'test_unread': tests})
    head = _suite_tree(tmp, 'head', {'test_suite': {'shared': 1.0},
                                     'test_unread': tests})
    assert compare.main(['--base', *base, '--head', *head]) == 1


def test_shared_speed_modules_import_by_package(tmp):
    """Shared workflow modules must import without a sys.path test shim.

    compare_durations imports the zero-suite guard from a sibling module, so
    both spellings have to work: the one CI runs the comparator with, and the
    package import a promoted module is otherwise reachable through.
    """
    del tmp
    imported = subprocess.run(
        [sys.executable, '-c', 'import scripts.ci.zeroed_suites; '
         + 'import scripts.ci.compare_durations'],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert imported.returncode == 0, (imported.stdout, imported.stderr)


def test_speed_summary_longest_table_orders_head_medians_and_applies_limit(
        tmp):
    """The expensive table sorts all movements before applying its limit."""
    del tmp
    compare = _compare_durations()
    lines = _render_speed_summary(
        compare, ['small', 'middle', 'large'], [(6.0, 12.0, 2.0)], [
            ('middle', 4.0, 6.0, 2.0),
            ('large', 5.0, 9.0, 4.0),
            ('small', 1.0, 2.0, 1.0),
            ('late', 2.0, 12.0, 10.0),
        ], limit=2)
    rows = _longest_rows(lines)
    assert [row.split('|')[1].strip() for row in rows] == [
        '`late`', '`large`'], rows


def test_speed_summary_longest_table_shows_head_median_column(tmp):
    """The duration column is the current commit's median, even when faster."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'test': 1.0, 'improving': 9.0}])
    head = _durations_tree(tmp, 'head', [{'test': 9.0, 'improving': 3.0}])
    shared, pairs, movements = compare.compare(
        compare.side_rounds(base), compare.side_rounds(head))
    rows = _longest_rows(_render_speed_summary(compare, shared, pairs,
                                               movements))
    assert rows == [
        '| `test` | 9.00s | 0.750 |',
        '| `improving` | 3.00s | 0.250 |',
    ], rows


def test_speed_summary_longest_table_shares_only_the_covered_set(tmp):
    """Shares use every covered head median, not round or displayed totals."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [
        {'shared_alpha': 1.0, 'shared_beta': 1.0, 'shared_gamma': 1.0,
         'base_only': 50.0},
        {'shared_alpha': 1.0, 'shared_beta': 1.0, 'shared_gamma': 1.0,
         'base_only': 50.0},
    ])
    head = _durations_tree(tmp, 'head', [
        {'shared_alpha': 4.0, 'shared_beta': 7.0, 'shared_gamma': 1.0,
         'head_only': 100.0},
        {'shared_alpha': 8.0, 'shared_beta': 3.0, 'shared_gamma': 5.0,
         'head_only': 100.0},
    ])
    shared, pairs, movements = compare.compare(
        compare.side_rounds(base), compare.side_rounds(head))
    assert shared == ['shared_alpha', 'shared_beta', 'shared_gamma'], shared
    lines = _render_speed_summary(compare, shared, pairs, movements, limit=2)
    rows = _longest_rows(lines)
    assert rows == [
        '| `shared_alpha` | 6.00s | 0.429 |',
        '| `shared_beta` | 5.00s | 0.357 |',
    ], rows
    assert ('Shares sum head medians over the same covered set used by '
            'the paired ratio.') in lines


def test_speed_summary_longest_table_renders_zero_head_share(tmp):
    """A covered set with no head time renders a guarded zero share."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'zero': 1.0}])
    head = _durations_tree(tmp, 'head', [{'zero': 0.0}])
    shared, pairs, movements = compare.compare(
        compare.side_rounds(base), compare.side_rounds(head))
    rows = _longest_rows(_render_speed_summary(compare, shared, pairs,
                                               movements))
    assert rows == ['| `zero` | 0.00s | 0.000 |'], rows


def test_speed_summary_longest_table_preserves_empty_shared_early_return(tmp):
    """A shared set gets the table; empty shared stays table-free."""
    del tmp
    compare = _compare_durations()
    populated = []
    assert compare.render(populated, 'baseline', ['test'], [(1.0, 2.0, 2.0)],
                          [('test', 1.0, 2.0, 1.0)]) == 2.0
    assert '| test | head median | share of covered set |' in populated

    empty = []
    assert compare.render(empty, 'baseline', [], [], []) is None
    assert not any('Longest-running tests' in line for line in empty), empty


def test_speed_summary_movement_table_orders_by_absolute_delta(tmp):
    """The largest speedup remains visible when more than ten tests move.

    `steady` is the largest test on both sides but does not move, so a table
    ordered by duration would lead with it; the block is pinned whole so the
    limit's cut tail is part of the assertion too.
    """
    compare = _compare_durations()
    small_names = [f'small_{index}' for index in range(1, 12)]
    base_tests = {name: 1.00 for name in small_names}
    base_tests['speedup'] = 43.12
    base_tests['steady'] = 100.0
    head_tests = {name: 1.01 for name in small_names}
    head_tests['speedup'] = 3.46
    head_tests['steady'] = 100.0
    base = _durations_tree(tmp, 'base', [base_tests])
    head = _durations_tree(tmp, 'head', [head_tests])
    shared, pairs, movements = compare.compare(
        compare.side_rounds(base), compare.side_rounds(head))
    lines = _render_speed_summary(compare, shared, pairs, movements)
    rows = _movement_rows(lines)
    assert rows == [
        '| `speedup` | 43.12s | 3.46s | -39.66s |',
        '| `small_1` | 1.00s | 1.01s | +0.01s |',
        '| `small_10` | 1.00s | 1.01s | +0.01s |',
        '| `small_11` | 1.00s | 1.01s | +0.01s |',
        '| `small_2` | 1.00s | 1.01s | +0.01s |',
        '| `small_3` | 1.00s | 1.01s | +0.01s |',
        '| `small_4` | 1.00s | 1.01s | +0.01s |',
        '| `small_5` | 1.00s | 1.01s | +0.01s |',
        '| `small_6` | 1.00s | 1.01s | +0.01s |',
        '| `small_7` | 1.00s | 1.01s | +0.01s |',
    ], rows


def _selection_tree(tmp):
    """A tree with three suites in two areas, every test passing."""
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    for name in ('test_bridge_one.py', 'test_bridge_two.py', 'test_cli.py'):
        (tree / 'tests' / name).write_text(
            'import sys\n'
            'print("  PASS  test_a")\n'
            'print("1/1 passed")\n'
            'sys.exit(0)\n', encoding='utf-8')
    return tree


def test_timing_runs_the_whole_tree_without_a_selection(tmp):
    """No selection flags, every suite timed — the callers' existing shape."""
    tree = _selection_tree(tmp)
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out)])
    assert code == 0, code
    assert sorted(path.name for path in out.glob('*.json')) == [
        'test_bridge_one.json', 'test_bridge_two.json', 'test_cli.json']


def test_timing_selects_only_the_suites_a_glob_names(tmp):
    """`--only` narrows a run to the suites its globs match.

    Match semantics are the pathlib ones over the suite file NAME, so a group
    is spelled as globs such as `test_cli*.py` without repeating `tests/`.
    """
    tree = _selection_tree(tmp)
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out),
         '--only', 'test_bridge_*.py', 'test_cli*.py'])
    assert code == 0, code
    assert sorted(path.name for path in out.glob('*.json')) == [
        'test_bridge_one.json', 'test_bridge_two.json', 'test_cli.json']


def test_timing_selects_one_area_of_a_partition(tmp):
    """A cell takes a disjoint slice, and the other suites never run."""
    tree = _selection_tree(tmp)
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out),
         '--only', 'test_cli*.py'])
    assert code == 0, code
    assert [path.name for path in out.glob('*.json')] == ['test_cli.json']


def test_timing_selection_excludes_the_suites_an_except_names(tmp):
    """`--except` drops what its globs match, before `--only` is applied.

    The complement of the named groups is how the catch-all cell takes
    everything the named groups do not match: `--only '*'` minus every named
    glob is exactly that complement, on whatever tree the cell lands on.
    """
    tree = _selection_tree(tmp)
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out),
         '--only', '*', '--except', 'test_cli*.py'])
    assert code == 0, code
    assert sorted(path.name for path in out.glob('*.json')) == [
        'test_bridge_one.json', 'test_bridge_two.json']


def test_timing_selection_matching_nothing_is_a_failure(tmp):
    """A selection that matches no suite is a setup failure, not a fast one.

    The unfiltered shape already refuses a tree with no suites at all; a
    filtered shape that filtered everything out is the same measurement that
    did not happen, so it refuses the same way.
    """
    tree = _selection_tree(tmp)
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out),
         '--only', 'test_missing_*.py'])
    assert code == 1, code


def test_timing_a_tree_that_yields_nothing_is_a_failure(tmp):
    """A tree whose every suite fails measured nothing; it is not fast."""
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    (tree / 'tests' / 'test_nothing.py').write_text(
        'import sys\n'
        'print("  FAIL  test_a: deliberate")\n'
        'print("0/1 passed")\n'
        'sys.exit(1)\n', encoding='utf-8')
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out)])
    assert code == 1, code


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedmeasurement_')


if __name__ == '__main__':
    raise SystemExit(main())
