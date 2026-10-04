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
is the state they had to be able to reach. The head-pull-request controls
were relocated out of this file by the same remedy when it reached the
ceiling, and the suite they went to is gone from the tree along with the
controls it carried.
"""
import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
# Aliased to the names these suites have always called them, so the
# extraction is the only thing the call sites see.
from _ci_wait_fixtures import (  # noqa: E402
    _ci_wait_run as _run,
    _ci_wait_clock as _Clock,
    _ci_wait_state as _state,
    _frozen_ci_wait_clock as _frozen_wait_clock,
    _ci_wait_verdict)

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
SOURCE = SKILL / 'ci_wait.py'


def _ci_wait():
    return _util.load(SOURCE, 'ci_wait_gate_contract')


def _workflow_verdict(runs):
    """`verdict` with the published check switched off.

    Every case here is about the required WORKFLOW, so each says so
    once here rather than each call carrying the keyword. Since issue
    1360 the default also requires a `gate freshness` CHECK RUN, and a
    head whose publisher wrote nothing is a different state;
    `tests/test_ci_wait_published.py` is where that state is under
    test, and the one place here where BOTH are absent is the
    refusal naming both, beside it.
    """
    return _ci_wait_verdict(runs, required_checks=frozenset())


def _workflow_wait(mod, repo, sha, interval, bound, out, grace=300):
    """`wait` with the published check switched off, as above.

    The parameters are named, never `*args`/`**kwargs`: a call that
    unpacks a mapping was refused by the retired layout audit, which
    could not tell a hidden `timeout=` from any other keyword.
    """
    return mod.wait(repo, sha, interval, bound, out, grace=grace,
                    required_checks=frozenset())


# ---- the required-workflow expectation (issue 1217) ----

def test_a_green_run_of_the_required_workflow_is_acceptable(tmp):
    del tmp
    runs = [
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='tests'),
    ]
    assert _workflow_verdict(runs) == ('acceptable', [])


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
    assert _workflow_verdict(runs) == ('incomplete', [])


def test_no_runs_at_all_is_waiting_not_incomplete(tmp):
    """Absence of the required workflow must not mask the "nothing ran" case,
    which is waiting and says so in the exit-2 report."""
    del tmp
    assert _workflow_verdict([]) == ('waiting', [])


def test_a_red_required_run_fails_rather_than_reading_as_incomplete(tmp):
    """Step 4 precedes step 5: a present-but-red gate is a failure, and the
    refusal that a missing gate earns must never swallow it."""
    del tmp
    state, offenders = _workflow_verdict(
        [_run(1, 'failure', '2026-09-20T10:00:00Z', name='tests')])
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_the_newest_run_of_the_required_workflow_satisfies_the_gate(tmp):
    """The filter runs first, so the name the check reads is the one the
    run it kept carries. Both runs here are of ONE workflow and both carry
    its name, which is the only shape the producer emits: `gh_client` takes
    every run's name from the run's own workflow record, so a workflow
    whose newest run dropped the name `tests` while an older one kept it is
    a fixture artefact, and it is the one this control used to assert. What
    the check now reads is the newest run, and it is named `tests`."""
    del tmp
    runs = [
        _run(1, 'cancelled', '2026-09-20T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='tests'),
    ]
    assert _workflow_verdict(runs) == ('acceptable', [])


def test_an_empty_required_set_reproduces_the_previous_verdicts(tmp):
    """Back-compat: the two call shapes the pre-1217 tests use still work,
    and an empty requirement is the old tool on the old inputs."""
    del tmp
    mod = _ci_wait()
    green = [_run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]
    assert mod.verdict(green, required=frozenset(),
                       required_checks=frozenset()) == (
                           'acceptable', [])
    assert mod.verdict([], required=frozenset(),
                       required_checks=frozenset()) == (
                           'waiting', [])
    red = [_run(1, 'failure', '2026-09-20T10:00:00Z', name='gate freshness')]
    state, offenders = mod.verdict(red, required=frozenset(),
                                   required_checks=frozenset())
    assert state == 'unacceptable'
    assert [run['id'] for run in offenders] == [1]


def test_a_resembling_name_does_not_satisfy_the_requirement(tmp):
    """The match is on the workflow's own name, exactly: a case difference
    or a decorated spelling is a different workflow, and treating it as the
    gate would reintroduce the false green under a new spelling."""
    del tmp
    for name in ('Tests', 'test', 'tests ', 'unit tests', 'tests.yml'):
        runs = [_run(1, 'success', '2026-09-20T10:00:00Z', name=name)]
        assert _workflow_verdict(runs) == ('incomplete', []), name


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
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='CodeQL')]))
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'CONFLICTING',
         'mergeStateStatus': 'DIRTY', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', sha, 60, 600, out, grace=300)
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


def test_each_limb_of_the_conflict_test_alone_still_refuses(tmp):
    """`_blocked` reads TWO fields, and every fixture reaching it with a
    blocking state set both at once, so neither limb had a fixture
    differing in it alone: either conjunct could be deleted and the suite
    stayed green. `assert 'DIRTY' in text` never closed that - it is the
    report echoing the fixture's own field, not the filter consulting it.

    Two single-limb fixtures, each on its own SHA so the two refusals are
    told apart in the output, and each asserted to refuse at once with the
    clock unmoved: the grace is the other answer, and a set that reaches it
    has waited `grace` seconds for nothing.
    """
    del tmp
    mod = _ci_wait()
    for sha, mergeable, merge_state, named in (
            ('7' * 40, 'MERGEABLE', 'DIRTY', 'DIRTY'),
            ('8' * 40, 'CONFLICTING', 'CLEAN', 'CONFLICTING')):
        clock = _Clock()
        setattr(mod, 'ci_on', lambda repo, s: _state([
            _run(1, 'success', '2026-09-20T10:00:00Z',
                 name='gate freshness')]))
        setattr(mod, 'prs_on', lambda repo, s, m=mergeable, t=merge_state: [
            {'number': 1122, 'state': 'OPEN', 'mergeable': m,
             'mergeStateStatus': t, 'headRefOid': s}])
        out, err = io.StringIO(), io.StringIO()
        with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
            code = _workflow_wait(mod, 'o/r', sha, 60, 600, out, grace=300)
        text = out.getvalue()
        assert code == 4, (mergeable, merge_state, text)
        assert clock.now == 1000.0, (mergeable, merge_state, clock.now)
        assert 'pull request #1122' in text, (mergeable, merge_state, text)
        assert named in text, (mergeable, merge_state, text)
        assert 'grace' not in text, (mergeable, merge_state, text)


def _pull(number, mergeable='MERGEABLE', merge_state='CLEAN'):
    return {'number': number, 'state': 'OPEN', 'mergeable': mergeable,
            'mergeStateStatus': merge_state}


def test_the_blocked_scan_returns_the_first_blocking_pull_request(tmp):
    """`_blocked` scans a LIST, and the list is the only thing here that had
    no fixture differing in it: every other case put one pull request in
    front of the scan, so a mutant that returned the first request whatever
    its state - or the last blocking one - would have survived.

    Every outcome the scan can have over a list, each differing from its
    neighbours in that outcome alone: nothing, one unblocked, one blocked
    (either limb), an unblocked request BEFORE a blocked one, a blocked one
    before an unblocked, and two blocked. The third-from-last is the gap the
    sweep found: it is the only row that tells "first blocking" apart from
    "first".
    """
    del tmp
    mod = _ci_wait()
    cases = (
        ('no pull request at all', [], None),
        ('one unblocked', [_pull(1)], None),
        ('blocked on mergeable alone', [_pull(2, 'CONFLICTING')], 2),
        ('blocked on merge state alone', [_pull(3, merge_state='DIRTY')], 3),
        ('unblocked first, blocked second',
         [_pull(1), _pull(2, 'CONFLICTING')], 2),
        ('blocked first, unblocked second',
         [_pull(1, 'CONFLICTING'), _pull(2)], 1),
        ('two blocked, the first wins',
         [_pull(1, merge_state='DIRTY'), _pull(2, 'CONFLICTING')], 1),
    )
    for label, pull_requests, wanted in cases:
        answer = mod._blocked(pull_requests)
        if wanted is None:
            assert answer is None, (label, answer)
        else:
            assert answer is not None, (label, answer)
            assert answer['number'] == wanted, (label, answer)


def test_the_refusal_names_the_blocking_request_not_the_first_one(tmp):
    """The same property through `wait()`, where it is the refusal line
    that carries the number: a non-blocking pull request in front of a
    blocking one must not turn the line into a refusal about the wrong
    pull request - or into no refusal at all."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
    setattr(mod, 'prs_on', lambda repo, s: [
        _pull(101), _pull(202, 'CONFLICTING')])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', '4' * 40, 60, 600, out, grace=300)
    text = out.getvalue()
    assert code == 4, text
    assert '#202' in text, text
    assert '#101' not in text, text
    assert clock.now == 1000.0, clock.now


def test_a_still_computing_pull_request_is_not_a_refusal(tmp):
    """`UNKNOWN` is GitHub still working out mergeability, the opposite of
    CONFLICTING. The wait must not refuse on it, so the refusal arrives
    through the grace and names the runs rather than a conflict - which is
    what tells the two refusals apart."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    sha = 'a' * 40
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'UNKNOWN',
         'mergeStateStatus': 'UNKNOWN', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', sha, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'grace' in text, text
    assert '1122' not in text, text
    assert 'gate freshness' in text, text


def test_a_pull_request_that_turns_conflicting_later_is_re_read(tmp):
    """The re-read is the reason the wait asks on every observation rather
    than once: `mergeable` is UNKNOWN while GitHub computes it, and can
    still come back CONFLICTING minutes later on the same head.

    A wait that re-read every tick but acted on the FIRST answer passes
    every other control here and turns this refusal into a 300-second grace
    refusal that names no pull request at all - so the control is the
    UNKNOWN-then-CONFLICTING shape, and what it asserts is the conflict:
    the exit is 4 at once, the line names the pull request, and the clock
    has not moved off the first observation.
    """
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    reads = []

    def _asks(repo, sha):
        reads.append(clock.now)
        mergeable = 'UNKNOWN' if len(reads) < 3 else 'CONFLICTING'
        return [{'number': 1122, 'state': 'OPEN', 'mergeable': mergeable,
                 'mergeStateStatus': 'UNKNOWN', 'headRefOid': sha}]

    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
    setattr(mod, 'prs_on', _asks)
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', '5' * 40, 10, 600, out, grace=300)
    text = out.getvalue()
    assert code == 4, text
    assert 'pull request #1122' in text, text
    assert len(reads) == 3, reads
    assert clock.now == 1020.0, clock.now
    assert 'grace' not in text, text


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
        return _state(runs)

    setattr(mod, 'ci_on', _polls)
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'MERGEABLE',
         'mergeStateStatus': 'BLOCKED', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', 'b' * 40, 10, 600, out, grace=300)
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
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='CodeQL')]))
    setattr(mod, 'prs_on', lambda repo, s: [])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', 'c' * 40, 7, 600, out, grace=30)
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
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
    setattr(mod, 'prs_on', lambda repo, s: [
        {'number': 1122, 'state': 'OPEN', 'mergeable': 'MERGEABLE',
         'mergeStateStatus': 'CLEAN', 'headRefOid': s}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', 'd' * 40, 7, 600, out, grace=30)
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

    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
    # The transport, not the accessor: this control is about the REAL
    # head_pull_requests propagating a failed read into the wait's handler.
    # ONE run, and its own output is what is asserted: the earlier version
    # restored the real transport in a `finally` and then ran a SECOND wait
    # whose output it asserted, so the verdict turned on a live
    # `gh api graphql` failing against a repository that does not exist -
    # and it still passed with gh off PATH, because a missing gh is an
    # OSError and so a QueryError, and the same branch ran.
    real = mod.gh_client.graphql
    setattr(mod.gh_client, 'graphql', _refuse)
    try:
        out, err = io.StringIO(), io.StringIO()
        with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
            code = _workflow_wait(mod, 'o/r', 'e' * 40, 7, 600, out, grace=30)
    finally:
        setattr(mod.gh_client, 'graphql', real)
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
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
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
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness')]))
    setattr(mod, 'prs_on', lambda repo, s: [])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', '9' * 40, 10, 30, out, grace=300)
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


def test_the_refusal_names_the_gate_the_judged_set_is_missing(tmp):
    """`_missing` answers with the required names the run set carries none
    of, and that read was documented and unpinned: an answer of nothing
    makes the refusal degrade to `no  run on <sha>` - a doubled space and no
    workflow name, which is issue #839's shape on this same file, a line
    that reads like a verdict while saying nothing useful.

    Two workflows, neither of them the gate, is what leaves the answer
    empty by nothing more than absence - a superseded `tests` run is not
    what made it, and could not be: the filter keeps the newest run per
    workflow and the producer names every run of a workflow alike, so the
    name a filter dropped is a name the kept run carries.
    """
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    setattr(mod, 'ci_on', lambda repo, s: _state([
        _run(1, 'success', '2026-09-20T10:00:00Z', name='gate freshness',
             workflow=11),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='CodeQL',
             workflow=22)]))
    setattr(mod, 'prs_on', lambda repo, s: [])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(err):
        code = _workflow_wait(mod, 'o/r', '6' * 40, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'no tests run on' in text, text
    assert 'no  run on' not in text, text


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

    And a `pull_request` trigger that FILTERS is the same rot in a form the
    first version of this control could not see: the trigger stays declared,
    so the gate is still waiting for a run the matching pull requests will
    never produce.

    **The admitted subset is the empty set, and that is the whole policy.**
    Every option under `pull_request` narrows when the event fires, so every
    one of them is a way to stop the gate running on a head that needs it:
    `paths` and `paths-ignore` by file, `branches` and `branches-ignore` by
    ref, `types` by which activity the event reports. An earlier version
    admitted `('paths', 'paths-ignore')` by name and so was blind to the
    other three, which the reviewer measured: `branches: [main]`,
    `types: [opened, synchronize]` and `branches-ignore: [dependabot/**]`
    each left the suite green. `types:` is not hypothetical - two
    workflows in this repository already declare that exact form on another
    trigger. The keys are read with the shared reader, which is the one that
    knows a deeper `paths-ignore:` belongs to something else, and the
    comparison is against no key at all rather than a list that would need
    extending every time GitHub adds a narrowing option.
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
            assert not keys, (
                f'the workflow named {wanted!r} narrows its pull_request '
                f'trigger by {sorted(keys)}, so some head gets no run of the '
                f'workflow this wait requires and the head is refused for a '
                f'matrix that was never asked for')


def _contract_of(text):
    """The exit-code list of a contract surface, in whichever shape it is.

    The docstring lists one exit per line, each starting with the code;
    SKILL.md states the same list as a wrapped paragraph. Returning the
    right span of each is what lets one completeness check cover both
    without either surface being rewritten to suit the test.
    """
    entries = [row for row in text.splitlines()
               if re.match(r'  [0-4]  ', row)]
    if entries:
        return ' '.join(entries)
    for block in text.split('\n\n'):
        if 'exit code is the verdict' in block:
            return ' '.join(block.split())
    raise AssertionError('no exit-code contract found in this surface')


def test_both_contract_surfaces_agree_on_what_exit_four_means(tmp):
    """The exit-code contract is stated twice - the docstring and SKILL.md -
    and both used to claim that exit 4 meant the gate "that decides the
    merge" was never dispatched, on a path that reaches a ratchet commit to
    `main` where no merge exists at all. The line the tool prints says the
    head is not certified, and both documents have to say what the line
    says, in the same words, so neither can drift into a claim the data
    does not support.

    Whitespace is normalised because a contract sentence is rewrapped as
    prose is edited, and a check that failed on a rewrap would be a check
    about typography rather than about the claim.
    """
    del tmp
    mod = _ci_wait()
    skill = (ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'
             ).read_text(encoding='utf-8')
    for name, text in (('the docstring', mod.__doc__), ('SKILL.md', skill)):
        flat = ' '.join((text or '').split())
        assert 'so this head is not certified' in flat, name
        assert 'the merge was never dispatched' not in flat, name
        assert 'gates the merge was never dispatched' not in flat, name
        # A COMPLETENESS check, not a phrase check: deleting a whole clause
        # left the docstring listing 0, 2, 3, 4 with every suite green,
        # which is how a contract drops a code and keeps a green suite
        # saying it has not. Each surface carries the list in its own
        # shape - one entry per line in the docstring, one wrapped
        # paragraph in SKILL.md - so each is sliced the way it is written
        # and then both are asked the same question.
        contract = _contract_of(text or '')
        for code in ('0', '1', '2', '3', '4'):
            assert re.search(rf'(?<![0-9]){code}(?![0-9])', contract), (
                name, code, 'the contract lists no clause for this exit')
    # The narrative about the cb67badf head is about a head that really was
    # merge-gated, and is the one place the word belongs.
    assert 'gates the merge' in ' '.join((mod.__doc__ or '').split()), (
        'the cb67badf narrative no longer says what gates it')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwaitgate_')


if __name__ == '__main__':
    raise SystemExit(main())
