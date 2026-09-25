#!/usr/bin/env python3
"""ci_wait.py's verdicts: the superseded-cancelled rule, and exit 2."""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'ci_wait.py'


def _ci_wait():
    return _util.load(SOURCE, 'ci_wait_contract')


def _run(rid, conclusion, started, workflow=11, path=None, **fields):
    """One workflow run as the actions API reports it against a SHA.

    Passing `path` removes workflow_id, standing the path in alone.
    """
    run = {
        'id': rid,
        'name': f'run {rid}',
        'status': 'completed',
        'conclusion': conclusion,
        'run_started_at': started,
        'workflow_id': workflow,
        'html_url': f'https://github.com/o/r/actions/runs/{rid}',
    }
    if path is not None:
        del run['workflow_id']
        run['path'] = path
    run.update(fields)
    return run


def _verdict(runs):
    return _ci_wait().verdict(runs)


def test_superseded_cancelled_run_is_ignored(tmp):
    """The defect case: a re-run's cancelled remnant must not fail the wait."""
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, 'success', '2026-09-07T10:05:00Z'),
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


def test_an_in_progress_run_is_never_superseded(tmp):
    del tmp
    runs = [
        _run(1, None, '2026-09-07T10:00:00Z', status='in_progress'),
        _run(2, 'success', '2026-09-07T10:05:00Z'),
    ]
    assert _verdict(runs) == ('waiting', [])


def test_an_older_failure_is_never_superseded(tmp):
    del tmp
    runs = [
        _run(1, 'failure', '2026-09-07T10:00:00Z'),
        _run(2, 'success', '2026-09-07T10:05:00Z'),
    ]
    state, offenders = _verdict(runs)
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_equal_timestamps_tie_break_by_numeric_id(tmp):
    del tmp
    stamp = '2026-09-07T10:00:00Z'
    state, offenders = _verdict([
        _run(9, 'cancelled', stamp),
        _run(10, 'success', stamp),
    ])
    assert state == 'acceptable'
    state, offenders = _verdict([
        _run(10, 'cancelled', stamp),
        _run(9, 'success', stamp),
    ])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [10]


def test_created_at_stands_in_for_a_missing_run_started_at(tmp):
    del tmp
    state, offenders = _verdict([
        _run(1, 'cancelled', None, created_at='2026-09-07T10:00:00Z'),
        _run(2, 'success', None, created_at='2026-09-07T10:05:00Z'),
    ])
    assert state == 'acceptable'
    state, offenders = _verdict([
        _run(1, 'cancelled', None, created_at='2026-09-07T10:05:00Z'),
        _run(2, 'success', None, created_at='2026-09-07T10:00:00Z'),
    ])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_fractional_second_stamps_are_ordered_by_instant_not_text(tmp):
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, 'success', '2026-09-07T10:00:00.500Z'),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_the_workflow_path_groups_when_the_id_is_absent(tmp):
    del tmp
    same = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z',
             path='.github/workflows/ci.yml'),
        _run(2, 'success', '2026-09-07T10:05:00Z',
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
        _run(3, 'success', '2026-09-07T10:10:00Z'),
    ]
    assert _verdict(runs) == ('acceptable', [])
    state, offenders = _verdict(runs[:2])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [2]


def test_the_zero_and_green_contracts_are_unchanged(tmp):
    del tmp
    assert _verdict([]) == ('waiting', [])
    runs = [
        _run(1, 'success', '2026-09-07T10:00:00Z'),
        _run(2, 'neutral', '2026-09-07T10:05:00Z'),
        _run(3, 'skipped', '2026-09-07T10:10:00Z'),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_the_success_line_counts_judged_runs_only(tmp):
    del tmp
    mod = _ci_wait()
    runs = [
        _run(1, 'cancelled', '2026-09-07T10:00:00Z'),
        _run(2, 'success', '2026-09-07T10:05:00Z'),
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
        '  run 1: completed/cancelled\n'
        '  run 2: completed/success\n'
        'all 1 run(s) on aaaaaaaaaaaa acceptable'
        ' (1 superseded cancelled ignored)\n'
    )


def test_the_success_line_is_unchanged_without_ignored_runs(tmp):
    del tmp
    mod = _ci_wait()
    mod.runs_on = lambda repo, sha: [
        _run(1, 'success', '2026-09-07T10:00:00Z')]
    out = io.StringIO()
    code = mod.wait('o/r', 'b' * 40, 60, 60, out)
    assert code == 0
    assert out.getvalue() == (
        'bbbbbbbbbbbb 1 run(s)\n'
        '  run 1: completed/success\n'
        'all 1 run(s) on bbbbbbbbbbbb acceptable\n'
    )


class _Clock:
    """The clock both modules read, moved only by the sleeps themselves."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@contextlib.contextmanager
def _frozen_wait_clock(mod, clock):
    """One clock for ci_wait and the client it polls through.

    The bound is read from the client's own `time`, so a wait faked on
    ci_wait's clock alone would compare a real monotonic clock against the
    fake deadline and expire immediately, before any request. Nothing real
    is waited on, so a timeout here is a value, not a margin.
    """
    real = (mod.time, mod.gh_client.time)
    mod.time = clock
    mod.gh_client.time = clock
    try:
        yield clock
    finally:
        mod.time, mod.gh_client.time = real


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
        return [_run(1, 'success', '2026-09-07T10:00:00Z')]

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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwait_')


if __name__ == '__main__':
    raise SystemExit(main())
