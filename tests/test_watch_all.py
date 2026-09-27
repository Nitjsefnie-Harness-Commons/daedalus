#!/usr/bin/env python3
"""watch_all.py's hold: a success-only batch waits on the head's runs."""
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'watch_all.py'

# The shared client is loaded by path, the way the script under test loads
# it: the skill directory is not an import root, so a plain `import
# gh_client` here would resolve nothing.
_CLIENT = _util.load(SOURCE.parent / 'gh_client.py', 'gh_client_here')
QueryError = getattr(_CLIENT, 'QueryError')
RateLimited = getattr(_CLIENT, 'RateLimited')
Watcher = getattr(_CLIENT, 'Watcher')

SHA = 'a' * 40


def _watch_all():
    mod = _util.load(SOURCE, 'watch_all_contract')
    mod._repo_slug = lambda: 'o/r'
    return mod


def _hold_run(rid, status, conclusion, name=None):
    """One workflow run as the shared client reports it against a SHA.

    A different shape from the ci_wait suites' run builder, and named apart
    from it on purpose: that one is a shared helper in
    `tests/_ci_wait_fixtures.py`, and a second builder called `_run` is the
    collision `tests/_unconsolidated_names.py` exists to catch.

    `name` overrides the workflow's own name, which is how a fixture
    carries the gating workflow: a run set with no run of it is not a
    settled matrix (issue 1223), so a fixture standing in for a head whose
    matrix ran names that workflow.
    """
    return {'id': rid, 'name': name or f'run {rid}', 'status': status,
            'conclusion': conclusion}


def _fake_runs(mod, answer, seen=None):
    """A client double installed on the module alone: `answer` is the run
    list the query returns, or the exception it raises. The `gh` invocation
    behind it is exercised end to end in `test_gh_client.py`; what is under
    test here is what this script does with the answer.
    """
    def workflow_runs(owner, name, sha):
        if seen is not None:
            seen.append((owner, name, sha))
        if isinstance(answer, BaseException):
            raise answer
        return answer
    setattr(mod, 'gh_client', SimpleNamespace(
        workflow_runs=workflow_runs, QueryError=QueryError,
        RateLimited=RateLimited))


def test_a_queued_run_holds_even_when_every_check_run_is_complete(tmp):
    """The defect: the list is complete while a run is queued. The query no
    longer reads check runs, so what must still hold is that one queued run
    keeps the batch from reading as settled.
    """
    del tmp
    mod = _watch_all()
    _fake_runs(mod, [_hold_run(1, 'completed', 'success'),
                     _hold_run(2, 'queued', None),
                     _hold_run(3, 'completed', 'success', name='tests')])
    assert mod._all_concluded(SHA) is False


def test_no_run_yet_is_not_settled(tmp):
    del tmp
    mod = _watch_all()
    assert mod._settled([]) is None
    _fake_runs(mod, [])
    assert mod._all_concluded(SHA) is not True


def test_every_run_completed_is_settled(tmp):
    del tmp
    mod = _watch_all()
    runs = [_hold_run(1, 'completed', 'success'),
            _hold_run(2, 'completed', 'skipped'),
            _hold_run(3, 'completed', 'neutral'),
            _hold_run(4, 'completed', 'failure'),
            _hold_run(5, 'completed', 'success', name='tests')]
    assert mod._settled(runs) is True
    _fake_runs(mod, runs)
    assert mod._all_concluded(SHA) is True


def test_an_in_progress_run_is_not_settled(tmp):
    del tmp
    mod = _watch_all()
    runs = [_hold_run(1, 'completed', 'success'),
            _hold_run(2, 'in_progress', None),
            _hold_run(3, 'completed', 'success', name='tests')]
    assert mod._settled(runs) is False


def test_a_failed_query_cannot_look_settled(tmp):
    del tmp
    mod = _watch_all()
    _fake_runs(mod, QueryError('gh failed'))
    assert mod._all_concluded(SHA) is None
    _fake_runs(mod, [])
    assert mod._all_concluded(SHA) is None


def test_without_a_repo_slug_nothing_is_queried(tmp):
    del tmp
    mod = _watch_all()
    mod._repo_slug = lambda: None
    seen = []
    _fake_runs(mod, [], seen)
    assert mod._all_concluded(SHA) is None
    assert seen == []


def test_every_run_the_client_reports_is_considered(tmp):
    """No run is dropped between the query and the hold: the list is paged by
    the shared client, and this script must not read a prefix of it.
    """
    del tmp
    mod = _watch_all()
    runs = [_hold_run(index, 'completed', 'success') for index in range(1, 6)]
    runs.append(_hold_run(6, 'completed', 'success', name='tests'))
    _fake_runs(mod, runs)
    assert mod._all_concluded(SHA) is True
    _fake_runs(mod, [*runs, _hold_run(7, 'queued', None)])
    assert mod._all_concluded(SHA) is False


def test_the_query_is_fresh_and_pinned_to_the_sha(tmp):
    del tmp
    mod = _watch_all()
    seen = []
    _fake_runs(mod, [], seen)
    mod._all_concluded(SHA)
    mod._all_concluded(SHA)
    assert seen == [('o', 'r', SHA), ('o', 'r', SHA)]


def _fake_runs_in_order(mod, answers, seen=None):
    """A client double whose successive answers are given in order."""
    def workflow_runs(owner, name, sha):
        if seen is not None:
            seen.append(sha)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
    setattr(mod, 'gh_client', SimpleNamespace(
        workflow_runs=workflow_runs, QueryError=QueryError,
        RateLimited=RateLimited))


def test_a_rate_limited_completion_query_waits_rather_than_holding(tmp):
    """A refusal is a known wait, not a failed query: it must not hold."""
    del tmp
    mod = _watch_all()
    seen = []
    _fake_runs_in_order(
        mod,
        [RateLimited('rate limited', time.time() + 2),
         [_hold_run(1, 'completed', 'success', name='tests')]], seen)
    out = io.StringIO()
    assert mod._all_concluded(SHA, Watcher('watch_all', out=out)) is True
    assert len(seen) == 2, seen
    assert len([line for line in out.getvalue().splitlines()
                if 'rate limit' in line]) == 1, out.getvalue()


def test_a_cap_release_is_announced_in_the_batch(tmp):
    del tmp
    mod = _watch_all()
    cap_line = mod._cap_line(SHA, 600.0)
    assert SHA in cap_line and '600s' in cap_line
    other = mod._cap_line('b' * 7, 30.0)
    assert 'b' * 7 in other and '30s' in other and other != cap_line
    assert mod._hold_release(None, 600.0, 600.0, SHA) == [cap_line]
    assert mod._hold_release(False, 601.0, 600.0, SHA) == [cap_line]
    batch = [f'[ci] CI b {SHA} pylint: success https://github.com/o/r/1',
             f'[ci] CI b {SHA} pyright: success https://github.com/o/r/2']
    batch.extend(mod._hold_release(None, 600.0, 600.0, SHA))
    lines = mod._condense(batch, 1000, 'log').splitlines()
    assert lines[0] == f'CI {SHA}: 2 success'
    assert lines[1].startswith('[watch_all] hold cap 600s reached on ' + SHA)
    assert lines[1].endswith('tally is partial')


def test_a_settled_release_carries_no_cap_line(tmp):
    del tmp
    mod = _watch_all()
    assert mod._hold_release(True, 0.0, 600.0, SHA) == []
    assert mod._hold_release(True, 601.0, 600.0, SHA) == []


def test_under_the_cap_an_unsettled_batch_keeps_holding(tmp):
    del tmp
    mod = _watch_all()
    assert mod._hold_release(None, 0.0, 600.0, SHA) is None
    assert mod._hold_release(False, 599.0, 600.0, SHA) is None


def test_a_head_with_no_gating_run_is_not_settled(tmp):
    """Issue #1223, the cb67badf set: a head that conflicts with its base
    dispatches no pull_request workflow, so the `tests` matrix is never
    created, while gate freshness and CodeQL run and conclude. Every run
    that exists has concluded, which is what the hold used to read as
    settled - a batch looking exactly like a green matrix with the gating
    matrix silently absent.

    It reads as its own answer rather than as `False`: a run still open
    resolves, and this may never. `False` would also be a lie the other
    way, naming a run that is not open.
    """
    del tmp
    mod = _watch_all()
    runs = [_hold_run(1, 'completed', 'success', name='gate freshness'),
            _hold_run(2, 'completed', 'success', name='CodeQL - Code Quality')]
    verdict = mod._settled(runs)
    assert verdict is not True
    assert verdict is not False and verdict is not None
    assert list(verdict.missing) == ['tests'], verdict
    _fake_runs(mod, runs)
    assert mod._all_concluded(SHA) is not True
    assert mod._hold_release(verdict, 0.0, 600.0, SHA) is None


def test_a_head_with_a_gating_run_still_settles(tmp):
    """The one-limb counterpart: the same two green runs and nothing else
    changed, so a check that varies the gating limb alone is what holds the
    predicate in place. The two cases differ in that limb and in no other.
    """
    del tmp
    mod = _watch_all()
    runs = [_hold_run(1, 'completed', 'success', name='gate freshness'),
            _hold_run(2, 'completed', 'success', name='CodeQL - Code Quality'),
            _hold_run(3, 'completed', 'success', name='tests')]
    assert mod._settled(runs) is True
    _fake_runs(mod, runs)
    assert mod._all_concluded(SHA) is True
    assert mod._hold_release(True, 0.0, 600.0, SHA) == []


def test_a_red_gating_run_is_present_and_not_absent(tmp):
    """A `tests` run that concluded `failure` is present. The hold is
    conclusion-blind on purpose - a completed failure settles the matrix as
    much as a completed success does, and the batch's business is to report
    it - so a red gate must read as settled-and-reported, never as the
    missing gate, which would hold a batch that will never improve.
    """
    del tmp
    mod = _watch_all()
    runs = [_hold_run(1, 'completed', 'success', name='tests'),
            _hold_run(2, 'completed', 'failure', name='speed')]
    assert mod._settled(runs) is True
    _fake_runs(mod, runs)
    assert mod._all_concluded(SHA) is True


def test_the_cap_line_names_the_absent_gate(tmp):
    """The cap releases the batch whatever the reason is, so the line is the
    only thing that carries it. A cap release that said "unknown" for a
    head whose gating matrix was never dispatched would read as a partial
    tally of a matrix that exists - the shape issue #839 was filed about,
    reached here by the other route.
    """
    del tmp
    mod = _watch_all()
    runs = [_hold_run(1, 'completed', 'success', name='gate freshness')]
    verdict = mod._settled(runs)
    batch = [f'[ci] CI b {SHA} gate freshness: success '
             'https://github.com/o/r/1']
    batch.extend(mod._hold_release(verdict, 600.0, 600.0, SHA))
    lines = mod._condense(batch, 1000, 'log').splitlines()
    caps = [line for line in lines if 'hold cap' in line]
    assert len(caps) == 1, lines
    cap = caps[0]
    assert cap.startswith('[watch_all] hold cap 600s reached on ' + SHA), cap
    assert cap.endswith('tally is partial'), cap
    assert 'tests' in cap, cap
    assert 'runs still open or unknown' not in cap, cap
    # The other reason keeps its own wording, so the two cannot drift.
    other = mod._hold_release(False, 600.0, 600.0, SHA)[0]
    assert 'runs still open or unknown' in other, other
    assert 'tests' not in other, other
    assert other.endswith('tally is partial'), other


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchall_')


if __name__ == '__main__':
    raise SystemExit(main())
