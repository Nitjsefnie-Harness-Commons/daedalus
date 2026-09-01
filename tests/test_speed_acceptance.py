#!/usr/bin/env python3
"""Accepted speed-change manifest behavior and lifecycle coverage."""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


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
    merge_base = 'MERGE_BASE_SHA'
    release = ACTIVE_BASELINE
    next_release = 'v0.23.0'
    accepted = _acceptance_file(
        tmp, [_acceptance('accepted', 40.0, [merge_base, release])])

    old_base = _durations_tree(
        tmp, 'old-base', [{'accepted': 0.28, 'steady': 1.0}])
    merged_head = _durations_tree(
        tmp, 'merged-head', [{'accepted': 8.20, 'steady': 1.0}])

    def run(base, head, label, name):
        return _run_comparator(compare, [
            '--base', *base, '--head', *head, '--accept', str(accepted),
            '--base-label', label, '--summary-file', str(Path(tmp) / name)])

    # Pull request: the merge base authorizes the accepted transition.
    assert run(old_base, merged_head, merge_base, 'pr-summary.md')[0] == 0
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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='speedacceptance_')


if __name__ == '__main__':
    raise SystemExit(main())
