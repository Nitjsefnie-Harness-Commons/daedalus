#!/usr/bin/env python3
"""How the speed gate decides, and what it refuses to decide from.

The comparison sums the tests present and passing on both sides, pairs whole
rounds rather than per-test minima, and takes the median of the paired ratios.
Each of those is a decision about what a number is allowed to describe, so
these tests drive the comparison with rounds that disagree.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


ACTIVE_BASELINE = 'v0.22.0'


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


def _acceptance_file(tmp, acceptances):
    path = Path(tmp) / 'accepted.json'
    path.write_text(json.dumps({'acceptances': acceptances}),
                    encoding='utf-8')
    return path


def _run_comparator(compare, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(stdout),
          contextlib.redirect_stderr(stderr)):
        try:
            code = compare.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stdout.getvalue() + stderr.getvalue()


def _acceptance(test, max_ratio=40.0, through_baseline=None):
    if through_baseline is None:
        through_baseline = [ACTIVE_BASELINE]
    return {
        'test': test,
        'max_ratio': max_ratio,
        'reason': 'accepted for this test',
        'through_baseline': through_baseline,
    }


def test_speed_comparison_excludes_accepted_test_from_totals(tmp):
    """An accepted test is gated by its bound, not shared-set budget."""
    compare = _compare_durations()
    base = _durations_tree(
        tmp, 'base', [{'accepted': 0.28, 'steady': 1.0},
                      {'accepted': 0.28, 'steady': 1.0}])
    head = _durations_tree(
        tmp, 'head', [{'accepted': 8.20, 'steady': 1.0},
                      {'accepted': 8.20, 'steady': 1.0}])
    accepted = _acceptance_file(tmp, [_acceptance('accepted')])
    summary = Path(tmp) / 'summary.md'
    code, _output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(accepted),
        '--base-label', ACTIVE_BASELINE,
        '--summary-file', str(summary)])
    assert code == 0, code
    text = summary.read_text(encoding='utf-8')
    assert '| 1 | 1.00s | 1.00s | 1.000 |' in text, text
    assert 'Accepted speed changes' in text, text
    assert '| `accepted` | 0.28s | 8.20s |' in text, text
    assert '| 40.000 | PASS |' in text, text


def test_speed_comparison_respects_each_acceptance_bound(tmp):
    """A covered acceptance passes within its bound and fails beyond it."""
    compare = _compare_durations()
    base = _durations_tree(
        tmp, 'base', [{'accepted': 1.0, 'steady': 1.0}])
    accepted = _acceptance_file(tmp, [_acceptance('accepted', 1.30)])
    within = _durations_tree(
        tmp, 'within', [{'accepted': 1.2, 'steady': 1.0}])
    past = _durations_tree(
        tmp, 'past', [{'accepted': 1.4, 'steady': 1.0}])
    args = ['--base', *base, '--accept', str(accepted),
            '--base-label', ACTIVE_BASELINE, '--max-regression', '0.30']
    assert _run_comparator(compare, args + ['--head', *within])[0] == 0
    assert _run_comparator(compare, args + ['--head', *past])[0] == 1


def test_speed_comparison_different_regression_still_fails(tmp):
    """An acceptance cannot make a different test exceed the cell budget."""
    compare = _compare_durations()
    base = _durations_tree(
        tmp, 'base', [{'accepted': 1.0, 'other': 1.0}])
    head = _durations_tree(
        tmp, 'head', [{'accepted': 20.0, 'other': 1.4}])
    accepted = _acceptance_file(tmp, [_acceptance('accepted')])
    summary = Path(tmp) / 'summary.md'
    assert _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(accepted),
        '--base-label', ACTIVE_BASELINE, '--max-regression', '0.30',
        '--summary-file',
        str(summary)])[0] == 1
    text = summary.read_text(encoding='utf-8')
    assert 'covered-set median paired ratio 1.400 exceeds' in text, text
    assert 'every acceptance bound holds' in text, text


def test_speed_comparison_stale_acceptance_is_visible_but_inert(tmp):
    """An acceptance absent from this shared set is reported, not enforced."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'steady': 1.0}])
    head = _durations_tree(tmp, 'head', [{'steady': 1.0}])
    accepted = _acceptance_file(tmp, [_acceptance('stale')])
    summary = Path(tmp) / 'summary.md'
    code, _output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(accepted),
        '--base-label', ACTIVE_BASELINE,
        '--summary-file', str(summary)])
    assert code == 0, code
    text = summary.read_text(encoding='utf-8')
    assert ('| `stale` | — | — | — | 40.000 | not measured this run |'
            in text), text


def test_speed_comparison_missing_acceptance_file_matches_today(tmp):
    """A missing --accept path has exactly the no-acceptance behavior."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'steady': 1.0}])
    head = _durations_tree(tmp, 'head', [{'steady': 1.4}])
    missing = Path(tmp) / 'missing.json'
    plain_summary = Path(tmp) / 'plain.md'
    missing_summary = Path(tmp) / 'missing.md'
    plain = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--summary-file',
        str(plain_summary)])[0]
    with_missing = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(missing),
        '--summary-file', str(missing_summary)])[0]
    assert with_missing == plain == 1
    assert (missing_summary.read_text(encoding='utf-8')
            == plain_summary.read_text(encoding='utf-8'))


def test_speed_comparison_rejects_malformed_acceptance_files(tmp):
    """Every malformed manifest is an error with or without measurements."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'steady': 1.0}])
    head = _durations_tree(tmp, 'head', [{'steady': 1.0}])
    cases = [
        ('bad-json', 'not json', 'JSON'),
        ('wrong-root', json.dumps([]), 'object'),
        ('root-key', json.dumps({'acceptances': [], 'extra': 1}),
         'unknown key'),
        ('item-key', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'extra': 1}]}), 'unknown key'),
        ('missing-key', json.dumps({'acceptances': [{
            'test': 'steady', 'max_ratio': 1.0, 'reason': 'x'}]}),
         'missing key'),
        ('non-object', json.dumps({'acceptances': [1]}), 'must be an object'),
        ('not-a-list', json.dumps({'acceptances': {}}),
         'must be a list'),
        ('non-numeric', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'max_ratio': 'slow'}]}),
         'max_ratio'),
        ('infinity', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'max_ratio': float('inf')}]}),
         'max_ratio'),
        ('nan', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'max_ratio': float('nan')}]}),
         'max_ratio'),
        ('non-positive', json.dumps({'acceptances': [
            _acceptance('steady', 0.0)]}), 'positive'),
        ('empty-test', json.dumps({'acceptances': [
            {**_acceptance(''), 'test': ''}]}), 'test name'),
        ('padded-test', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'test': ' steady '}]}), 'test name'),
        ('duplicate-name', json.dumps({'acceptances': [
            _acceptance('steady'), _acceptance('steady')]}),
         'duplicate test name'),
        ('non-string-reason', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'reason': 1}]}),
         'reason must be a string'),
        ('empty-baseline-list', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'through_baseline': []}]}),
         'non-empty list'),
        ('non-string-baseline-entry', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'through_baseline': [1]}]}),
         'string'),
        ('empty-baseline-entry', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'through_baseline': ['']}]}),
         'through_baseline entries must be non-empty strings'),
        ('whitespace-baseline-entry', json.dumps({'acceptances': [
            {**_acceptance('steady'), 'through_baseline': ['   ']}]}),
         'through_baseline entries must be non-empty strings'),
        ('duplicate-baseline-entry', json.dumps({'acceptances': [
            {**_acceptance('steady'),
             'through_baseline': [ACTIVE_BASELINE, ACTIVE_BASELINE]}]}),
         'unique'),
        ('bare-string-baseline', json.dumps({'acceptances': [
            {**_acceptance('steady'),
             'through_baseline': ACTIVE_BASELINE}]}),
         'list'),
        ('duplicate-root',
         '{"acceptances": [], "acceptances": []}', 'duplicate'),
        ('duplicate-entry-key',
         '{"acceptances": [{"test": "steady", "test": "steady", '
         '"max_ratio": 1.0, "reason": "x", '
         '"through_baseline": "v0.22.0"}]}', 'duplicate'),
    ]
    for name, content, reason in cases:
        path = Path(tmp) / f'{name}.json'
        path.write_text(content, encoding='utf-8')
        for extra in ([], ['--require-measurements']):
            code, output = _run_comparator(compare, [
                '--base', *base, '--head', *head, '--accept', str(path),
                *extra])
            assert code == 1, (name, extra, code, output)
            assert reason in output, (name, extra, output)

    unreadable = Path(tmp) / 'unreadable'
    unreadable.mkdir()
    code, output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(unreadable)])
    assert code == 1, output
    assert 'unreadable' in output, output


def test_speed_comparison_rejects_manifest_before_reading_durations(tmp):
    """Malformed manifests fail before either duration tree is read."""
    compare = _compare_durations()
    malformed = Path(tmp) / 'malformed.json'
    malformed.write_text('{"acceptances": [], "acceptances": []}',
                         encoding='utf-8')
    calls = []
    original = compare.side_rounds

    def spy(directories):
        calls.append(directories)
        return original(directories)

    compare.side_rounds = spy
    try:
        code, output = _run_comparator(compare, [
            '--base', str(Path(tmp) / 'missing-base'), '--head',
            str(Path(tmp) / 'missing-head'), '--accept', str(malformed)])
    finally:
        compare.side_rounds = original
    assert code == 1, output
    assert 'duplicate' in output, output
    assert calls == [], calls


def test_speed_comparison_acceptance_uses_median_of_paired_ratios(tmp):
    """Accepted bounds use the workflow's per-pair ratio median."""
    compare = _compare_durations()
    base = _durations_tree(
        tmp, 'base', [{'accepted': 1.0, 'steady': 1.0},
                      {'accepted': 100.0, 'steady': 1.0},
                      {'accepted': 100.0, 'steady': 1.0}])
    head = _durations_tree(
        tmp, 'head', [{'accepted': 2.0, 'steady': 1.0},
                      {'accepted': 101.0, 'steady': 1.0},
                      {'accepted': 10000.0, 'steady': 1.0}])
    accepted = _acceptance_file(tmp, [_acceptance('accepted', 1.30)])
    summary = Path(tmp) / 'summary.md'
    code, _output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(accepted),
        '--base-label', ACTIVE_BASELINE, '--summary-file', str(summary)])
    assert code == 1, code
    text = summary.read_text(encoding='utf-8')
    assert '| `accepted` | 100.00s | 101.00s | 2.000 | 1.300 | FAIL |' \
        in text, text


def test_speed_comparison_acceptance_expires_after_baseline_advance(tmp):
    """An acceptance authorizes only its recorded baseline transition."""
    compare = _compare_durations()
    accepted = _acceptance_file(
        tmp, [_acceptance('accepted', 40.0, [ACTIVE_BASELINE])])

    old_base = _durations_tree(
        tmp, 'old-base', [{'accepted': 0.28, 'steady': 1.0}])
    accepted_head = _durations_tree(
        tmp, 'accepted-head', [{'accepted': 8.20, 'steady': 1.0}])
    assert _run_comparator(compare, [
        '--base', *old_base, '--head', *accepted_head,
        '--accept', str(accepted), '--base-label', ACTIVE_BASELINE])[0] == 0

    advanced_base = _durations_tree(
        tmp, 'advanced-base', [{'accepted': 8.20, 'steady': 1.0}])
    steady_head = _durations_tree(
        tmp, 'steady-head', [{'accepted': 8.20, 'steady': 1.0}])
    steady_summary = Path(tmp) / 'steady-summary.md'
    assert _run_comparator(compare, [
        '--base', *advanced_base, '--head', *steady_head,
        '--accept', str(accepted), '--base-label', 'v0.23.0',
        '--summary-file', str(steady_summary)])[0] == 0
    steady_text = steady_summary.read_text(encoding='utf-8')
    assert 'expired at baseline v0.23.0' in steady_text, steady_text
    assert '| 1 | 9.20s | 9.20s | 1.000 |' in steady_text, steady_text

    regressed_head = _durations_tree(
        tmp, 'regressed-head', [{'accepted': 300.0, 'steady': 1.0}])
    regressed_summary = Path(tmp) / 'regressed-summary.md'
    assert _run_comparator(compare, [
        '--base', *advanced_base, '--head', *regressed_head,
        '--accept', str(accepted), '--base-label', 'v0.23.0',
        '--summary-file', str(regressed_summary)])[0] == 1
    regressed_text = regressed_summary.read_text(encoding='utf-8')
    assert 'expired at baseline v0.23.0' in regressed_text, regressed_text
    assert 'covered-set median paired ratio' in regressed_text, regressed_text


def test_speed_comparison_acceptance_survives_pr_main_and_expires(tmp):
    """One accepted slowdown survives both workflow baseline labels."""
    compare = _compare_durations()
    pr_base = 'BASE_SHA'
    release = ACTIVE_BASELINE
    next_release = 'v0.23.0'
    accepted = _acceptance_file(
        tmp, [_acceptance('accepted', 40.0, [pr_base, release])])

    old_base = _durations_tree(
        tmp, 'old-base', [{'accepted': 0.28, 'steady': 1.0}])
    merged_head = _durations_tree(
        tmp, 'merged-head', [{'accepted': 8.20, 'steady': 1.0}])

    def run(base, head, label, name):
        return _run_comparator(compare, [
            '--base', *base, '--head', *head, '--accept', str(accepted),
            '--base-label', label, '--summary-file', str(Path(tmp) / name)])

    # Pull request: the base SHA authorizes the accepted transition.
    assert run(old_base, merged_head, pr_base, 'pr-summary.md')[0] == 0
    # Main push after merge: the release tag authorizes that same transition.
    assert run(old_base, merged_head, release, 'main-summary.md')[0] == 0

    new_base = _durations_tree(
        tmp, 'new-base', [{'accepted': 8.20, 'steady': 1.0}])
    steady_head = _durations_tree(
        tmp, 'steady-head', [{'accepted': 8.20, 'steady': 1.0}])
    next_code, _ = run(new_base, steady_head, next_release,
                       'next-summary.md')
    assert next_code == 0
    next_text = (Path(tmp) / 'next-summary.md').read_text(encoding='utf-8')
    assert f'expired at baseline {next_release}' in next_text, next_text
    assert '| 1 | 9.20s | 9.20s | 1.000 |' in next_text, next_text

    regressed_head = _durations_tree(
        tmp, 'regressed-head', [{'accepted': 300.0, 'steady': 1.0}])
    regression_code, _ = run(new_base, regressed_head, next_release,
                             'regression-summary.md')
    assert regression_code == 1
    regression_text = (Path(tmp) / 'regression-summary.md').read_text(
        encoding='utf-8')
    assert f'expired at baseline {next_release}' in regression_text, (
        regression_text)
    assert 'covered-set median paired ratio' in regression_text, (
        regression_text)


def test_speed_comparison_all_accepted_cell_has_no_fictitious_measurement(tmp):
    """An empty covered set reports bounds without synthetic zero totals."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'accepted': 1.0}])
    head = _durations_tree(tmp, 'head', [{'accepted': 2.0}])
    accepted = _acceptance_file(tmp, [_acceptance('accepted', 2.0)])
    summary = Path(tmp) / 'summary.md'
    code, _output = _run_comparator(compare, [
        '--base', *base, '--head', *head, '--accept', str(accepted),
        '--base-label', ACTIVE_BASELINE, '--summary-file', str(summary)])
    assert code == 0, code
    text = summary.read_text(encoding='utf-8')
    assert 'no non-accepted shared tests; only acceptance bounds apply' in text
    assert '| round | baseline | this commit | ratio |' not in text
    assert 'median paired ratio' not in text
    assert text.count('### Accepted speed changes') == 1, text
    assert text.count('**OK**') + text.count('**FAIL**') == 1, text


def test_speed_comparison_handles_zero_base_acceptance_medians(tmp):
    """Zero medians use a unit ratio only when both sides are zero."""
    compare = _compare_durations()
    base = _durations_tree(
        tmp, 'base', [{'zero': 0.0, 'steady': 1.0},
                      {'zero': 0.0, 'steady': 1.0}])
    accepted = _acceptance_file(tmp, [_acceptance('zero', 1.0)])
    both_zero = _durations_tree(
        tmp, 'both-zero', [{'zero': 0.0, 'steady': 1.0},
                           {'zero': 0.0, 'steady': 1.0}])
    positive = _durations_tree(
        tmp, 'positive', [{'zero': 0.5, 'steady': 1.0},
                          {'zero': 0.5, 'steady': 1.0}])
    args = ['--base', *base, '--accept', str(accepted),
            '--base-label', ACTIVE_BASELINE]
    assert _run_comparator(compare, args + ['--head', *both_zero])[0] == 0
    assert _run_comparator(compare, args + ['--head', *positive])[0] == 1

    mixed_base = _durations_tree(
        tmp, 'mixed-base', [{'zero': 0.0, 'steady': 1.0},
                            {'zero': 1.0, 'steady': 1.0}])
    mixed_head = _durations_tree(
        tmp, 'mixed-head', [{'zero': 0.0, 'steady': 1.0},
                            {'zero': 1.0, 'steady': 1.0}])
    mixed_args = ['--base', *mixed_base, '--accept', str(accepted),
                  '--base-label', ACTIVE_BASELINE]
    mixed_summary = Path(tmp) / 'mixed-summary.md'
    mixed_code, _ = _run_comparator(
        compare, mixed_args + ['--head', *mixed_head, '--summary-file',
                               str(mixed_summary)])
    assert mixed_code == 0
    mixed_text = mixed_summary.read_text(encoding='utf-8')
    assert '| `zero` | 0.50s | 0.50s | 1.000 | 1.000 | PASS |' in mixed_text

    mixed_positive_head = _durations_tree(
        tmp, 'mixed-positive-head', [{'zero': 0.5, 'steady': 1.0},
                                     {'zero': 1.0, 'steady': 1.0}])
    positive_summary = Path(tmp) / 'mixed-positive-summary.md'
    positive_code, _ = _run_comparator(
        compare, mixed_args + ['--head', *mixed_positive_head,
                               '--summary-file', str(positive_summary)])
    assert positive_code == 1
    positive_text = positive_summary.read_text(encoding='utf-8')
    assert '| `zero` | 0.50s | 0.75s | inf | 1.000 | FAIL |' in positive_text

    positive_base = _durations_tree(
        tmp, 'positive-base', [{'zero': 1.0, 'steady': 1.0}])
    zero_head = _durations_tree(
        tmp, 'zero-head', [{'zero': 0.0, 'steady': 1.0}])
    zero_summary = Path(tmp) / 'zero-summary.md'
    zero_code, _ = _run_comparator(
        compare, ['--base', *positive_base, '--head', *zero_head,
                  '--accept', str(accepted), '--base-label', ACTIVE_BASELINE,
                  '--summary-file', str(zero_summary)])
    assert zero_code == 0
    zero_text = zero_summary.read_text(encoding='utf-8')
    assert '| `zero` | 1.00s | 0.00s | 0.000 | 1.000 | PASS |' in zero_text


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


def test_speed_comparison_active_unmeasured_acceptance_not_expired(tmp):
    """An active acceptance stays active when no comparison is measurable."""
    compare = _compare_durations()
    base = Path(tmp) / 'base'
    head = Path(tmp) / 'head'
    base.mkdir()
    head.mkdir()
    accepted = _acceptance_file(
        tmp, [_acceptance('accepted', through_baseline=['old', 'baseline'])])
    active_summary = Path(tmp) / 'active-summary.md'
    code, _output = _run_comparator(compare, [
        '--base', str(base), '--head', str(head), '--accept', str(accepted),
        '--base-label', 'baseline', '--summary-file', str(active_summary)])
    assert code == 0
    active_text = active_summary.read_text(encoding='utf-8')
    assert ('| `accepted` | — | — | — | 40.000 | not measured this run |'
            in active_text), active_text
    assert 'expired' not in active_text, active_text

    expired = _acceptance_file(
        tmp, [_acceptance('accepted', through_baseline=['old', 'new'])])
    expired_summary = Path(tmp) / 'expired-summary.md'
    _run_comparator(compare, [
        '--base', str(base), '--head', str(head), '--accept', str(expired),
        '--base-label', 'baseline', '--summary-file', str(expired_summary)])
    expired_text = expired_summary.read_text(encoding='utf-8')
    assert ('expired at baseline baseline; not measured this run'
            in expired_text), expired_text


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
