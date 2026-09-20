#!/usr/bin/env python3
"""ci_wait.py's verdict: the superseded-cancelled rule and its edges."""
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwait_')


if __name__ == '__main__':
    raise SystemExit(main())
