#!/usr/bin/env python3
"""The required-workflow expectation, and the refusal an absent gate earns.

Issue 1217: ci_wait.py answered "every run that happened to exist
concluded acceptably", which is indistinguishable from "every run that
should exist did". On a head that conflicts with its base the `tests`
matrix is never dispatched, so the tool exited 0 on two unrelated green
runs and every seat on this fleet read that as a green read.

The controls live here rather than in test_ci_wait.py because that suite
is within forty lines of its 700-line ceiling, and scripts/ci/
size_baseline.py's own remedy for a file over it is to relocate the code
into a new module. The verdict cases that already existed stay there: this
is the state they had to be able to reach.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from test_ci_wait import _Clock, _frozen_wait_clock, _run  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
SOURCE = SKILL / 'ci_wait.py'
HEAD_PRS_SOURCE = SKILL / 'gh_head_prs.py'


def _ci_wait():
    return _util.load(SOURCE, 'ci_wait_gate_contract')


def _head_prs():
    return _util.load(HEAD_PRS_SOURCE, 'gh_head_prs_contract')


def _verdict(runs):
    return _ci_wait().verdict(runs)


# ---- the required-workflow expectation (issue 1217) ----

def test_a_green_run_of_the_required_workflow_is_acceptable(tmp):
    del tmp
    runs = [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='tests'),
    ]
    assert _verdict(runs) == ('acceptable', [])


def test_a_green_set_without_the_required_run_is_incomplete(tmp):
    """Issue 1217: PR #1122's head conflicts with its base, so the merge ref
    cannot be built, no pull_request workflow is dispatched and the `tests`
    matrix has no run at all. Two unrelated green runs are a wait, and a
    wait that never ends is a head nothing verified."""
    del tmp
    runs = [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='CodeQL'),
    ]
    assert _verdict(runs) == ('incomplete', [])


def test_no_runs_at_all_is_waiting_not_incomplete(tmp):
    """Absence of the required workflow must not mask the "nothing ran" case,
    which is waiting and says so in the exit-2 report."""
    del tmp
    assert _verdict([]) == ('waiting', [])


def test_a_red_required_run_fails_rather_than_reading_as_incomplete(tmp):
    """Step 4 precedes step 5: a present-but-red gate is a failure, and the
    refusal that a missing gate earns must never swallow it."""
    del tmp
    state, offenders = _verdict(
        [_run(1, 'failure', '2026-09-20T10:00:00Z', name='tests')])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_a_superseded_cancelled_required_run_does_not_satisfy_the_gate(tmp):
    """The filter runs first, so a name the verdict never reads cannot
    satisfy the requirement. The newer run shares the workflow, so it
    supersedes the cancelled one, and it is the one that is judged."""
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-20T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='gate freshness'),
    ]
    assert _verdict(runs) == ('incomplete', [])


def test_an_empty_required_set_reproduces_the_previous_verdicts(tmp):
    """Back-compat: the two call shapes the pre-1217 tests use still work,
    and an empty requirement is the old tool on the old inputs."""
    del tmp
    mod = _ci_wait()
    green = [_run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]
    assert mod.verdict(green, required=frozenset()) == ('acceptable', [])
    assert mod.verdict([], required=frozenset()) == ('waiting', [])
    red = [_run(1, 'failure', '2026-09-20T10:00:00Z', name='gate freshness')]
    state, offenders = mod.verdict(red, required=frozenset())
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_a_resembling_name_does_not_satisfy_the_requirement(tmp):
    """The match is on the workflow's own name, exactly: a case difference
    or a decorated spelling is a different workflow, and treating it as the
    gate would reintroduce the false green under a new spelling."""
    del tmp
    for name in ('Tests', 'test', 'tests ', 'unit tests', 'tests.yml'):
        runs = [_run(1, 'success', '2026-09-20T10:00:00Z', name=name)]
        assert _verdict(runs) == ('incomplete', []), name


# ---- the refusal on an incomplete set (issue 1217) ----

def test_a_conflicting_head_refuses_before_the_grace_elapses(tmp):
    """The control that reproduces cb67badf: the head conflicts with its
    base, so the merge ref cannot be built and the gate workflow is never
    dispatched. That is permanent rather than slow, so the refusal costs no
    wait at all, and it names the pull request and the workflow that is
    missing from it."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    sha = 'cb67badf' + '0' * 32
    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='CodeQL')])
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'CONFLICTING',
         'mergeStateStatus': 'DIRTY', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', sha, 60, 600, out, grace=300)
    text = out.getvalue()
    assert code == 4, text
    assert clock.now == 1000.0, clock.now
    assert '1122' in text, text
    assert 'tests' in text, text
    assert 'CONFLICTING' in text, text
    # This is the one refusal that is about a merge, and the pull request
    # is what makes it so: it names the pull request, its number and the
    # state that keeps the workflow from being dispatched. The grace it did
    # not spend is what tells it apart from the other refusal, which
    # claims nothing about a pull request at all.
    assert 'DIRTY' in text, text
    assert 'grace' not in text, text


def test_a_still_computing_pull_request_is_not_a_refusal(tmp):
    """`UNKNOWN` is GitHub still working out mergeability, the opposite of
    CONFLICTING. The wait must not refuse on it, so the refusal arrives
    through the grace and names the runs rather than a conflict - which is
    what tells the two refusals apart."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    sha = 'a' * 40
    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')])
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'UNKNOWN',
         'mergeStateStatus': 'UNKNOWN', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', sha, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'grace' in text, text
    assert '1122' not in text, text
    assert 'gate freshness' in text, text


def test_an_incomplete_set_inside_the_grace_keeps_polling(tmp):
    """The second false green 1217 closes: on a mergeable head the short
    workflows conclude while the matrix is still being created. Inside the
    grace that is a wait, so the wait must go on polling and answer 0 once
    the gate appears - never 0 on the first observation."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    polls = []

    def _polls(repo, sha):
        polls.append(clock.now)
        runs = [_run(1, 'success', '2026-09-20T10:00:00Z',
                     name='gate freshness')]
        if len(polls) == 3:
            runs.append(_run(2, None, '2026-09-20T10:05:00Z', name='tests',
                             status='in_progress'))
        elif len(polls) >= 4:
            runs.append(_run(2, 'success', '2026-09-20T10:05:00Z',
                             name='tests'))
        return runs

    setattr(mod, 'runs_on', _polls)
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'MERGEABLE',
         'mergeStateStatus': 'BLOCKED', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'b' * 40, 10, 600, out, grace=300)
    text = out.getvalue()
    assert code == 0, text
    assert polls == [1000.0, 1010.0, 1020.0, 1030.0], polls
    assert 'acceptable' in text, text


def test_an_incomplete_set_past_the_grace_refuses_without_a_pull_request(tmp):
    """A push to main, or a branch with no pull request open, is neither a
    conflict nor exempt: the grace governs it, and past the grace it names
    the missing workflow, the grace it waited out and the runs that do
    exist.

    There is no pull request here, so the line may not talk about one, and
    it may not talk about a merge either: a push to main is not a merge,
    and this refusal is reached on that path with nothing in the data to
    support the word. What it does claim is what the data supports on
    every path - a required workflow has no run for this SHA, so the head
    is not certified.
    """
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='CodeQL')])
    setattr(mod, 'prs_on', lambda repo, s: [])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'c' * 40, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'tests' in text, text
    assert '30s' in text, text
    assert 'gate freshness' in text and 'CodeQL' in text, text
    assert 'pull request' not in text, text
    assert 'merge' not in text, text
    assert 'not certified' in text, text


def test_an_incomplete_set_past_the_grace_refuses_on_a_mergeable_head(tmp):
    """Absence past the grace refuses even with nothing wrong with the pull
    request: a head whose gate workflow never appears is a head no matrix
    ran on, and the mergeable state is not evidence that one did."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')])
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'MERGEABLE',
         'mergeStateStatus': 'CLEAN', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'd' * 40, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'tests' in text, text
    assert 'grace' in text, text


def test_a_failed_pull_request_lookup_still_refuses_on_the_grace(tmp):
    """The disambiguation is secondary, so its failure is reported once and
    the wait carries on - but the grace is what refuses, so a lookup that
    never succeeds degrades to a slower correct answer, never to a green."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    polls = []

    def _refuse(query, variables=None):
        polls.append(clock.now)
        raise mod.gh_client.QueryError('gh exited 1')

    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')])
    # The transport, not the accessor: this control is about the REAL
    # head_pull_requests propagating a failed read into the wait's handler.
    real = mod.gh_client.graphql
    setattr(mod.gh_client, 'graphql', _refuse)
    try:
        out, err = io.StringIO(), io.StringIO()
        with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
            code = mod.wait('o/r', 'e' * 40, 7, 600, out, grace=30)
    finally:
        setattr(mod.gh_client, 'graphql', real)
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', 'e' * 40, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'tests' in text, text
    assert len(polls) > 1, polls
    assert len([line for line in err.getvalue().splitlines()
                if line.strip()]) == 1, err.getvalue()
    assert 'head_pull_requests' in err.getvalue(), err.getvalue()


def test_once_reports_incomplete_and_still_exits_zero(tmp):
    """--once is a trial call, not a verdict: it names the state and leaves
    the exit code to the query's own success."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')])
    err = io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.main(['f' * 40, '--once'])
    assert code == 0, err.getvalue()
    assert 'state: incomplete' in err.getvalue(), err.getvalue()


def test_a_bound_shorter_than_the_grace_names_the_missing_gate(tmp):
    """Every run in an incomplete set HAS concluded, so the exit-2 report
    cannot be the one that lists the open ones: it would name nothing and
    hand the caller a reason the runs do not carry. The bound still ends
    the wait with exit 2, before the grace turns it into the exit 4."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'runs_on', lambda repo, s: [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')])
    setattr(mod, 'prs_on', lambda repo, s: [])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = mod.wait('o/r', '9' * 40, 10, 30, out, grace=300)
    text = out.getvalue()
    assert code == 2, text
    assert 'still open' not in text, text
    assert 'tests' in text, text
    assert '300s grace' in text, text
    # The same two claims as the grace refusal, on the other exit: no
    # pull request is named and no merge is asserted, because this path is
    # reached with an open pull request, without one, and on main.
    assert 'pull request' not in text, text
    assert 'merge' not in text, text


def test_a_non_positive_grace_is_refused(tmp):
    """A grace of zero would refuse the first observation, which is the
    second false green in a new coat. The CLI refuses it the way it refuses
    --interval."""
    del tmp
    mod = _ci_wait()
    for value in ('0', '-5'):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = mod.main(['a' * 40, '--grace', value])
        assert code == 3, (value, code, err.getvalue())
        assert err.getvalue() == (
            f'--grace must be positive, got {value}\n'), err.getvalue()


# ---- the rot control ----

def test_the_required_workflows_still_name_a_real_pull_request_gate(tmp):
    """A required name no workflow carries, or one whose workflow lost its
    `pull_request` trigger, turns this gate into a no-op: every head would
    read incomplete and no real head could satisfy it. Renaming the
    workflow has to fail the suite rather than pass it silently.

    A `pull_request` trigger that FILTERS is the same rot in a form the
    first version of this control could not see: a `paths-ignore` under it
    leaves the trigger declared, so the gate is still waiting for a run that
    the matching pull requests will never produce. The event's own option
    keys are read with the shared reader, which is the one that knows a
    deeper `paths-ignore:` belongs to something else.
    """
    del tmp
    mod = _ci_wait()
    from _workflows import _entry, _event_option_keys, _workflow_triggers
    assert mod.REQUIRED_WORKFLOWS, 'the gate names no workflow at all'
    by_name = {}
    for path in sorted((ROOT / '.github' / 'workflows').iterdir()):
        if path.suffix not in ('.yml', '.yaml'):
            continue
        text = path.read_text(encoding='utf-8')
        for line in text.splitlines():
            entry = _entry(line, path.name)
            if entry is not None and entry[0] == 0 and entry[1] == 'name':
                by_name.setdefault(entry[2].strip('\'"'), []).append(text)
                break
    for wanted in sorted(mod.REQUIRED_WORKFLOWS):
        found = by_name.get(wanted)
        assert found, (
            f'no workflow in .github/workflows is named {wanted!r}')
        for text in found:
            triggers = _workflow_triggers(text, wanted)
            assert 'pull_request' in triggers, (
                f'the workflow named {wanted!r} declares '
                f'{sorted(triggers)} and never runs on a pull request, so a '
                f'head of a pull request has no run of it to wait for')
            keys = _event_option_keys(triggers['pull_request'], wanted)
            filters = ('paths', 'paths-ignore')
            filtered = [key for key in filters if key in keys]
            assert not filtered, (
                f'the workflow named {wanted!r} filters its pull_request '
                f'trigger by {filtered}, so a pull request whose changes are '
                f'all filtered out gets no run of it and this wait would '
                f'refuse a head the merge never had to gate')


# ---- the head's pull requests ----

def _pr_page(pull_requests, null_object=False):
    """One page of the associated-pull-requests query."""
    obj = (None if null_object
           else {'associatedPullRequests': {'nodes': list(pull_requests)}})
    return {'data': {'repository': {'object': obj}}}


def _pull(number, state='OPEN', mergeable='MERGEABLE',
          merge_state='CLEAN', head='a' * 40):
    return {'number': number, 'state': state, 'mergeable': mergeable,
            'mergeStateStatus': merge_state, 'headRefOid': head}


def test_a_merged_pull_request_of_another_head_is_not_this_heads(tmp):
    """Issue 1217: the origin/main tip is an ancestor of a merged branch's
    head, and the API answers that tip with that MERGED pull request. Both
    filters are load-bearing - headRefOid for the ancestor, state for the
    pull request that is already merged - and unfiltered every ancestor of
    a merged branch would look like an open pull request of its own.

    The third fixture is the one the other two could not hold: it is this
    head's own pull request, already merged, and it differs from an accepted
    one in the state limb alone. The first two vary state and headRefOid
    together, so the headRefOid clause answers both and nothing would say
    the state clause is there at all.
    """
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1139, state='MERGED', head='f' * 40)])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1139, state='OPEN', head='f' * 40)])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1122, state='MERGED')])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []


def test_the_open_pull_request_of_the_head_is_answered(tmp):
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1122), _pull(1139, state='MERGED', head='f' * 40)])})
    with fake.activate():
        found = mod.head_pull_requests('o', 'r', 'a' * 40)
    assert [pull['number'] for pull in found] == [1122]
    payload = json.loads(fake.calls()[0]['request'])
    assert payload['variables']['sha'] == 'a' * 40, payload['variables']


def test_a_commit_with_no_pull_request_reads_as_none(tmp):
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []


def test_an_unknown_sha_reads_as_no_pull_request(tmp):
    """A SHA the repository does not have answers with a null object, which
    is a head with no pull request rather than a failed query.

    A non-zero OID on purpose. Measured against this repository, an
    unresolvable OID of any shape answers `data.repository.object: null` -
    which is the body pinned here - and the all-zero OID is the one input
    that does not: it answers `data: null` with no errors array, which the
    next control pins instead. Naming it here would have made this control
    assert a shape its own input never produces.
    """
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page(
        [], null_object=True)})
    with fake.activate():
        assert mod.head_pull_requests(
            'o', 'r', 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef') == []


def test_the_all_zero_oid_is_a_failed_query_rather_than_an_empty_answer(tmp):
    """The one unknown SHA that does not come back as a null object.

    GitHub answers the all-zero OID with `data: null` and no errors array,
    and a body carrying no data is a failed read as far as `gh_client` is
    concerned - the same answer a truncated or errored response gets. It is
    a `QueryError` and not an empty list, because a caller that supplied
    the all-zero OID is owed a refusal rather than a confident `[]`: the
    wait reports it once and carries on to its grace, and a wrong answer
    here would tell it the head has no pull request.
    """
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {
        'associatedPullRequests': {'status': 200, 'body': {'data': None}}})
    with fake.activate():
        try:
            mod.head_pull_requests('o', 'r', '0' * 40)
        except mod.gh_client.QueryError as failure:
            assert 'no data' in str(failure), failure
        else:
            raise AssertionError(
                'a body carrying no data must fail the read, not answer []')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwaitgate_')


if __name__ == '__main__':
    raise SystemExit(main())
