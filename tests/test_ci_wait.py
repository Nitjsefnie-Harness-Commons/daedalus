#!/usr/bin/env python3
"""ci_wait.py's verdicts: the newest-run rule, and exit 2."""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
# Aliased to the names these suites have always called them, so the
# extraction is the only thing the call sites see.
from _ci_wait_fixtures import (  # noqa: E402
    _ci_wait_run as _run,
    _ci_wait_clock as _Clock,
    _frozen_ci_wait_clock as _frozen_wait_clock)

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'ci_wait.py'


def _ci_wait():
    return _util.load(SOURCE, 'ci_wait_contract')


def _verdict(runs):
    return _ci_wait().verdict(runs)


def test_superseded_cancelled_run_is_ignored(tmp):
    """The defect case: a re-run's cancelled remnant must not fail the wait."""
    del tmp
    # Named as the gating workflow because an `acceptable` verdict now also
    # requires that workflow to have a run at all (issue 1217); a green set
    # without it reads as incomplete, which is the defect itself.
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])
    assert _verdict(list(reversed(runs))) == ('acceptable', [])


def test_a_deliberate_cancel_is_still_unacceptable(tmp):
    del tmp
    state, offenders = _verdict(
        [_run(1, 'cancelled', '2026-09-07T10:00:00Z')])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_a_newer_run_of_another_workflow_does_not_supersede(tmp):
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z', workflow=11),
        _run(2, 'success', '2026-09-07T10:05:00Z', workflow=22),
    ]
    state, offenders = _verdict(runs)
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_the_ignored_run_cannot_complete_the_wait_either(tmp):
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, None, '2026-09-07T10:05:00Z', status='in_progress'),
    ]
    assert _verdict(runs) == ('waiting', [])


def test_a_superseded_open_run_does_not_hold_the_verdict(tmp):
    """Whether a run is still open never decided whether it is judged -
    supersession did, and does. So an older run that has not concluded is
    dropped like any other superseded run, and the newer one answers. This
    replaces a control that asserted the opposite on the strength of a
    status that the filter never consulted."""
    del tmp
    runs = [
        _run(1, None, '2026-09-07T10:00:00Z', status='in_progress',
             name='tests'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_the_newest_run_decides_even_when_it_is_still_queued(tmp):
    """The other order, and the one that is a real dispatch: a re-run of a
    workflow leaves its predecessor's green behind, and the run the verdict
    reads is the queued one. A superseded completed run must not settle the
    wait - otherwise the wait certifies a head whose newest run has not run."""
    del tmp
    runs = [
        _run(1, 'success', '2026-09-07T10:00:00Z', name='tests'),
        _run(2, None, '2026-09-07T10:05:00Z', status='in_progress',
             name='tests'),
    ]
    assert _verdict(runs) == ('waiting', [])


def test_a_superseded_failure_beside_a_newer_green_run_is_acceptable(tmp):
    """Issue 1249's repro, as a control. A close/reopen dispatches a second
    run of a workflow against a new merge ref while the first run's failure
    still hangs on the same SHA, and the verdict used to count both: the
    older failure outvoted the newer green and the head read unacceptable.
    The verdict for a workflow is the run GitHub's required-check status
    reports, which is the newest one."""
    del tmp
    runs = [
        _run(1, 'failure', '2026-09-07T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])
    assert _verdict(list(reversed(runs))) == ('acceptable', [])


def test_a_failure_nobody_re_ran_is_still_unacceptable(tmp):
    """The other limb, and the one a filter that dropped unconditionally
    would empty: supersession is what takes a run out of the judged set, so
    a failure with no newer run of its own workflow is still an offender.
    With the newest-run filter replaced by `runs[:1]` or by an empty set,
    every red head reads as a wait."""
    del tmp
    state, offenders = _verdict(
        [_run(1, 'failure', '2026-09-07T10:00:00Z', name='tests')])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_equal_timestamps_tie_break_by_numeric_id(tmp):
    del tmp
    stamp = '2026-09-07T10:00:00Z'
    state, offenders = _verdict([
        _run(9, 'cancelled', stamp),
        _run(10, 'success', stamp, name='tests'),
    ])
    assert state == 'acceptable'
    state, offenders = _verdict([
        _run(10, 'cancelled', stamp),
        _run(9, 'success', stamp, name='tests'),
    ])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [10]


def test_created_at_stands_in_for_a_missing_run_started_at(tmp):
    del tmp
    state, offenders = _verdict([
        _run(1, 'cancelled', None, created_at='2026-09-07T10:00:00Z'),
        _run(2, 'success', None, name='tests',
             created_at='2026-09-07T10:05:00Z'),
    ])
    assert state == 'acceptable'
    state, offenders = _verdict([
        _run(1, 'cancelled', None, created_at='2026-09-07T10:05:00Z'),
        _run(2, 'success', None, name='tests',
             created_at='2026-09-07T10:00:00Z'),
    ])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_a_start_time_beats_a_creation_time_that_disagrees(tmp):
    """The `run_started_at` preference, on the only shape that can see it.

    Every other fixture gives a run the same stamp under both keys, so
    reading `created_at` alone orders them exactly as reading
    `run_started_at` does and the whole suite stays green - the preference
    could be deleted and nothing here would notice. Here the two runs are
    ordered one way by the instant each began and the other way by the
    instant each was created, so only the preference says which is newest."""
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z', name='tests',
             created_at='2026-09-07T09:00:00Z'),
        _run(2, 'success', '2026-09-07T09:00:00Z',
             created_at='2026-09-07T10:00:00Z'),
    ]
    state, offenders = _verdict(runs)
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_fractional_second_stamps_are_ordered_by_instant_not_text(tmp):
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, 'success', '2026-09-07T10:00:00.500Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_the_workflow_path_groups_when_the_id_is_absent(tmp):
    del tmp
    same = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z',
             path='.github/workflows/ci.yml'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests',
             path='.github/workflows/ci.yml'),
    ]
    assert _verdict(same) == ('acceptable', [])
    other = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z',
             path='.github/workflows/ci.yml'),
        _run(2, 'success', '2026-09-07T10:05:00Z',
             path='.github/workflows/tests.yml'),
    ]
    state, offenders = _verdict(other)
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_only_the_newest_cancelled_run_of_a_workflow_survives(tmp):
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, 'cancelled', '2026-09-07T10:05:00Z'),
        _run(3, 'success', '2026-09-07T10:10:00Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])
    state, offenders = _verdict(runs[:2])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [2]


def test_the_zero_and_green_contracts_are_unchanged(tmp):
    del tmp
    assert _verdict([]) == ('waiting', [])
    # One workflow per run, as the producer emits it: three runs sharing a
    # workflow id are three runs of ONE workflow, and the filter keeps only
    # the newest of them - so the three acceptable conclusions cannot be
    # judged from one workflow at all.
    runs = [
        _run(1, 'success', '2026-09-07T10:00:00Z', name='tests',
             workflow=11),
        _run(2, 'neutral', '2026-09-07T10:05:00Z', name='gate freshness',
             workflow=22),
        _run(3, 'skipped', '2026-09-07T10:10:00Z', name='CodeQL', workflow=33),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_the_acceptable_line_names_the_failure_it_discarded(tmp):
    """The cost the rule pays, and what pays it back. A superseded failure
    is real evidence - an intermittent failure on the very merge ref the
    newer run cleared - so a caller handed a green it cannot audit is
    exactly what the naming refuses. Every run the filter dropped is named
    with its workflow, its run id, its conclusion and its URL."""
    del tmp
    mod = _ci_wait()
    runs = [
        _run(1, 'failure', '2026-09-07T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests'),
    ]
    setattr(mod, 'runs_on', lambda repo, sha: runs)
    out = io.StringIO()
    code = mod.wait('o/r', 'a' * 40, 60, 60, out)
    text = out.getvalue()
    assert code == 0, text
    assert 'all 1 run(s) on aaaaaaaaaaaa acceptable' in text, text
    assert '  tests (run 1): failure ' in text, text
    assert 'https://github.com/o/r/actions/runs/1' in text, text


def test_the_discarded_count_agrees_with_the_runs_it_names(tmp):
    """A count the line does not add up to is a count nobody can audit, so
    the two are pinned together: two runs of one workflow go by in front of
    its newest, and the two the verdict dropped are the two it names."""
    del tmp
    mod = _ci_wait()
    runs = [
        _run(1, 'failure', '2026-09-07T10:00:00Z', name='tests'),
        _run(2, 'cancelled', '2026-09-07T10:05:00Z', name='tests'),
        _run(3, 'success', '2026-09-07T10:10:00Z', name='tests'),
    ]
    setattr(mod, 'runs_on', lambda repo, sha: runs)
    out = io.StringIO()
    code = mod.wait('o/r', 'a' * 40, 60, 60, out)
    text = out.getvalue()
    assert code == 0, text
    assert 'all 1 run(s) on aaaaaaaaaaaa acceptable ' \
           '(2 superseded run(s) ignored)' in text, text
    assert text.endswith(
        '  tests (run 1): failure https://github.com/o/r/actions/runs/1\n'
        '  tests (run 2): cancelled https://github.com/o/r/actions/runs/2\n'
    ), text


def test_every_run_of_one_workflow_carries_the_workflow_name(tmp):
    """The producer invariant the required-workflow check rests on.

    The check reads the names of the runs the filter left, and the filter
    keeps the newest run per workflow, so a workflow whose newest run
    carried a different name from its older ones would have its `tests` run
    dropped and the head read incomplete. `gh_client` cannot emit that: it
    takes every run's name from the run's own workflow record, so two runs
    of one workflow come out with one name whatever their ids and start
    times. Read through the producer rather than asserted here in prose -
    two suites of one workflow, two runs, one name, and a second workflow
    with a name of its own so the equality is not a constant."""
    del tmp
    from _watcher_fixtures import suite
    client = _util.load(ROOT / '.claude' / 'skills' / 'changing-daedalus'
                        / 'gh_client.py', 'gh_client_run_names')
    same = [client._run_from_suites([suite(rid)]) for rid in (101, 102)]
    other = client._run_from_suites([suite(103, workflow=22)])
    assert len({run['workflow_id'] for run in same}) == 1, same
    assert len({run['name'] for run in same}) == 1, same
    assert other['name'] != same[0]['name'], (same, other)


def test_the_grouping_is_by_workflow_and_not_by_run_name(tmp):
    """The discriminator the control above cannot be, because on the shape
    the producer emits the two groupings agree. Two runs of ONE workflow
    whose names differ - a shape the producer does not emit, and the reason
    the fixtures that carried it were corrected - are still one workflow,
    and the newer still supersedes the older. A filter grouping on `name`
    sees two workflows here and judges both."""
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_the_success_line_counts_judged_runs_only(tmp):
    del tmp
    mod = _ci_wait()
    # One name for both runs of the one workflow, which is what the
    # producer emits; the matrix then prints the workflow twice, as it does
    # on a head a re-run left two runs on.
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-07T10:05:00Z', name='tests'),
    ]
    # Direct on purpose, unlike the setattr elsewhere: these two stubs
    # (here and in the next test) are the file's recorded type errors,
    # and setattr would zero that count and graduate the baseline entry.
    mod.runs_on = lambda repo, sha: runs
    out = io.StringIO()
    code = mod.wait('o/r', 'a' * 40, 60, 60, out)
    assert code == 0
    assert out.getvalue() == (
        'aaaaaaaaaaaa 2 run(s)\n'
        '  tests: completed/cancelled\n'
        '  tests: completed/success\n'
        'all 1 run(s) on aaaaaaaaaaaa acceptable'
        ' (1 superseded run(s) ignored)\n'
        '  tests (run 1): cancelled'
        ' https://github.com/o/r/actions/runs/1\n'
    )


def test_the_success_line_is_unchanged_without_ignored_runs(tmp):
    del tmp
    mod = _ci_wait()
    mod.runs_on = lambda repo, sha: [
        _run(1, 'success', '2026-09-07T10:00:00Z', name='tests')]
    out = io.StringIO()
    code = mod.wait('o/r', 'b' * 40, 60, 60, out)
    assert code == 0
    assert out.getvalue() == (
        'bbbbbbbbbbbb 1 run(s)\n'
        '  tests: completed/success\n'
        'all 1 run(s) on bbbbbbbbbbbb acceptable\n'
    )


def test_a_timeout_names_the_runs_still_open(tmp):
    """Issue 1088: a bound reached with no refusal pending is a plain
    timeout, so it must name the runs that are still open."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'runs_on', lambda repo, sha: [
        _run(1, 'success', '2026-09-07T10:00:00Z'),
        _run(2, None, '2026-09-07T10:05:00Z', status='in_progress')])
    out = io.StringIO()
    err = io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'c' * 40, 30, 30, out)
    text = out.getvalue()
    assert code == 2, text
    assert clock.now == 1030.0, clock.now
    assert 'rate limited' not in text, text
    assert text.endswith('wait exceeded 30s on cccccccccccc: still open: '
                         'run 2 (in_progress)\n'), text


def test_a_timeout_before_any_run_says_so(tmp):
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'runs_on', lambda repo, sha: [])
    out = io.StringIO()
    err = io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'd' * 40, 30, 30, out)
    text = out.getvalue()
    assert code == 2, text
    assert 'rate limited' not in text, text
    assert text.endswith('wait exceeded 30s on dddddddddddd: no workflow '
                         'run ever appeared\n'), text


def test_a_pause_that_ends_the_wait_still_reports_the_limit(tmp):
    """The other half of the rate-limit path: a refusal whose pause
    consumed the whole remaining budget ends the wait, and says so."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    polls = []

    def _refuse_first(repo, sha):
        polls.append(clock.now)
        if len(polls) == 1:
            raise mod.gh_client.RateLimited(
                'slow down', resume_at=clock.now + 3600)
        return []

    setattr(mod, 'runs_on', _refuse_first)
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'e' * 40, 30, 30, out)
    assert code == 2, out.getvalue()
    assert polls == [1000.0], polls
    assert clock.now == 1030.0, clock.now
    assert out.getvalue() == (
        'wait exceeded 30s on eeeeeeeeeeee: still rate limited, '
        'no verdict to report\n'), out.getvalue()
    assert len([line for line in err.getvalue().splitlines()
                if 'rate limit reached' in line]) == 1, err.getvalue()


def test_a_refusal_that_ended_early_still_answers_the_wait(tmp):
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    polls = []

    def _refuse_first(repo, sha):
        polls.append(clock.now)
        if len(polls) == 1:
            raise mod.gh_client.RateLimited(
                'slow down', resume_at=clock.now + 5)
        return [_run(1, 'success', '2026-09-07T10:00:00Z', name='tests')]

    setattr(mod, 'runs_on', _refuse_first)
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'f' * 40, 30, 60, out)
    assert code == 0, out.getvalue()
    assert polls == [1000.0, 1005.0], polls
    assert 'acceptable' in out.getvalue(), out.getvalue()
    assert len([line for line in err.getvalue().splitlines()
                if 'rate limit reached' in line]) == 1, err.getvalue()


def test_a_pause_that_ended_early_does_not_label_the_next_timeout(tmp):
    """The discriminator: the polls after a pause that ended well before
    the bound are ordinary polls, so a bound that passes among them is an
    ordinary timeout. Recording that a refusal happened at all would make
    this one report the rate limit again."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    polls = []

    def _refuse_first(repo, sha):
        polls.append(clock.now)
        if len(polls) == 1:
            raise mod.gh_client.RateLimited(
                'slow down', resume_at=clock.now + 5)
        return [_run(1, None, '2026-09-07T10:00:00Z', status='in_progress')]

    setattr(mod, 'runs_on', _refuse_first)
    out = io.StringIO()
    err = io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'a' * 40, 10, 30, out)
    text = out.getvalue()
    assert code == 2, text
    assert polls == [1000.0, 1005.0, 1015.0, 1025.0], polls
    assert clock.now == 1030.0, clock.now
    assert 'rate limited' not in text, text
    assert text.endswith('wait exceeded 30s on aaaaaaaaaaaa: still open: '
                         'run 1 (in_progress)\n'), text


def _counting_watcher(mod, raised):
    """The real Watcher, counting its bound's escape hatch firing.

    Both exit-2 routes print the same line, so the report alone cannot
    tell them apart: the WaitExpired handler and the `remaining <= 0`
    exit are observationally identical from outside. This counts the one
    signal that differs - a WaitExpired actually raised by the real poll
    loop - so a control can prove which exit the wait took.
    """
    real = mod.gh_client.Watcher

    class _Counting(real):
        def poll(self, call):
            try:
                return super().poll(call)
            except mod.gh_client.WaitExpired:
                raised.append(True)
                raise

    return _Counting


def test_a_poll_that_overruns_the_bound_reports_the_runs(tmp):
    """The `remaining <= 0` exit: a poll that takes longer than the whole
    budget leaves nothing to wait for, so the bound is reached with the
    runs already in hand - no refusal, and no WaitExpired, which is what
    `raised == []` pins: with that branch deleted the loop sleeps zero and
    the very next poll raises WaitExpired, printing the same line by the
    other route."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    raised = []
    real_watcher = mod.gh_client.Watcher
    setattr(mod.gh_client, 'Watcher', _counting_watcher(mod, raised))

    def _overrunning(repo, sha):
        clock.now += 100
        return [_run(1, None, '2026-09-07T10:00:00Z', status='in_progress')]

    setattr(mod, 'runs_on', _overrunning)
    out = io.StringIO()
    err = io.StringIO()
    try:
        with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
            code = mod.wait('o/r', 'a' * 40, 10, 30, out)
    finally:
        setattr(mod.gh_client, 'Watcher', real_watcher)
    text = out.getvalue()
    assert code == 2, text
    assert clock.now == 1100.0, clock.now
    assert raised == [], raised
    assert 'rate limited' not in text, text
    assert text.endswith('wait exceeded 30s on aaaaaaaaaaaa: still open: '
                         'run 1 (in_progress)\n'), text


def test_a_bound_before_the_first_poll_reports_from_empty_runs(tmp):
    """The pre-first-poll report: `runs = []` is what lets it name a state
    the API was never asked about. At timeout 0 the bound is already at the
    first poll's top-of-loop check, so WaitExpired fires before any request
    and the handler reports the no-runs line off an empty list. main()
    refuses timeout <= 0, so this is reachable only by an in-process
    caller; what it pins is that the line is a report, not a crash."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    out = io.StringIO()
    err = io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'b' * 40, 5, 0, out)
    text = out.getvalue()
    assert code == 2, text
    assert clock.now == 1000.0, clock.now
    assert text == ('wait exceeded 0s on bbbbbbbbbbbb: no workflow run '
                    'ever appeared\n'), text


def test_a_non_positive_timeout_is_refused(tmp):
    """A bound that fires before the first poll would report on runs the
    wait never asked for, and --timeout -5 a negative elapsed time. The CLI
    refuses both, the way it refuses --interval."""
    del tmp
    mod = _ci_wait()
    for value in ('0', '-5'):
        clock = _Clock()
        err = io.StringIO()
        with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
            code = mod.main(['a' * 40, '--timeout', value])
        assert code == 3, (value, code, err.getvalue())
        assert err.getvalue() == (
            f'--timeout must be positive, got {value}\n'), err.getvalue()


def test_a_malformed_sha_is_refused(tmp):
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    err = io.StringIO()
    # --timeout 0 is an inert guard: with this refusal deleted the
    # fall-through would reach a poll, and this stops it at the timeout
    # refusal so the control still dies here, finitely, with no request.
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.main(['notasha', '--timeout', '0'])
    assert code == 3, (code, err.getvalue())
    assert err.getvalue() == (
        "not a 40-character commit SHA: 'notasha'\n"), err.getvalue()


def test_a_non_positive_interval_is_refused(tmp):
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    err = io.StringIO()
    # --timeout 0 is the same inert guard: an interval of 0 deleted from
    # here would reach a poll and then spin a wait that never advances.
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.main(['a' * 40, '--interval', '0', '--timeout', '0'])
    assert code == 3, (code, err.getvalue())
    assert err.getvalue() == (
        '--interval must be positive, got 0\n'), err.getvalue()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwait_')


if __name__ == '__main__':
    raise SystemExit(main())
