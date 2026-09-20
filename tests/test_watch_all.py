#!/usr/bin/env python3
"""watch_all.py's hold: a success-only batch waits on the head's runs."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'watch_all.py'
SHA = 'a' * 40


def _watch_all():
    mod = _util.load(SOURCE, 'watch_all_contract')
    mod._repo_slug = lambda: 'o/r'
    return mod


def _run(rid, status, conclusion):
    """One workflow run as the actions API reports it against a SHA."""
    return {'id': rid, 'name': f'run {rid}', 'status': status,
            'conclusion': conclusion}


def _runs_body(*runs):
    return json.dumps({'workflow_runs': list(runs)})


def _fake_gh(mod, answers, seen=None):
    """Give the module a path-keyed subprocess double of its own.

    `answers` maps a path fragment to the stdout it yields, or to an
    exception to raise. An argv naming no known fragment is an
    AssertionError: the double must fail on what it does not model. The
    real module is left alone, so the next load's `git rev-parse` is real.
    """
    def run(argv, **kwargs):
        del kwargs
        if seen is not None:
            seen.append(list(argv))
        path = argv[-1]
        for fragment, answer in answers.items():
            if fragment in path:
                if isinstance(answer, BaseException):
                    raise answer
                return SimpleNamespace(stdout=answer, returncode=0)
        raise AssertionError(argv)
    mod.subprocess = SimpleNamespace(
        run=run, SubprocessError=subprocess.SubprocessError)


def test_a_queued_run_holds_even_when_every_check_run_is_complete(tmp):
    """The defect: the check-runs list is complete while a run is queued."""
    del tmp
    mod = _watch_all()
    check_runs = json.dumps({'check_runs': [
        {'id': 1, 'name': 'pylint', 'status': 'completed',
         'conclusion': 'success'},
        {'id': 2, 'name': 'pyright', 'status': 'completed',
         'conclusion': 'success'},
    ]})
    _fake_gh(mod, {
        '/check-runs': check_runs,
        '/actions/runs': _runs_body(_run(1, 'completed', 'success'),
                                    _run(2, 'queued', None)),
    })
    assert mod._all_concluded(SHA) is False


def test_no_run_yet_is_not_settled(tmp):
    del tmp
    mod = _watch_all()
    assert mod._settled([]) is None
    _fake_gh(mod, {'/actions/runs': _runs_body()})
    assert mod._all_concluded(SHA) is not True


def test_every_run_completed_is_settled(tmp):
    del tmp
    mod = _watch_all()
    runs = [_run(1, 'completed', 'success'),
            _run(2, 'completed', 'skipped'),
            _run(3, 'completed', 'neutral'),
            _run(4, 'completed', 'failure')]
    assert mod._settled(runs) is True
    _fake_gh(mod, {'/actions/runs': _runs_body(*runs)})
    assert mod._all_concluded(SHA) is True


def test_an_in_progress_run_is_not_settled(tmp):
    del tmp
    mod = _watch_all()
    runs = [_run(1, 'completed', 'success'),
            _run(2, 'in_progress', None)]
    assert mod._settled(runs) is False


def test_a_failed_query_cannot_look_settled(tmp):
    del tmp
    mod = _watch_all()
    _fake_gh(mod, {
        '/actions/runs': subprocess.CalledProcessError(1, 'gh')})
    assert mod._all_concluded(SHA) is None
    _fake_gh(mod, {'/actions/runs': 'not json'})
    assert mod._all_concluded(SHA) is None


def test_paginated_pages_are_all_read(tmp):
    del tmp
    mod = _watch_all()
    pages = (_runs_body(_run(1, 'completed', 'success')) + '\n'
             + _runs_body(_run(2, 'queued', None)) + '\n')
    _fake_gh(mod, {'/actions/runs': pages})
    assert mod._all_concluded(SHA) is False


def test_the_query_is_fresh_paginated_and_pinned_to_the_sha(tmp):
    del tmp
    mod = _watch_all()
    seen = []
    _fake_gh(mod, {'/actions/runs': _runs_body()}, seen)
    mod._all_concluded(SHA)
    assert len(seen) == 1
    argv = seen[0]
    assert '--paginate' in argv
    assert 'Cache-Control: no-cache' in argv
    assert f'actions/runs?head_sha={SHA}' in argv[-1]
    assert 'check-runs' not in argv[-1]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchall_')


if __name__ == '__main__':
    raise SystemExit(main())
