#!/usr/bin/env python3
"""The published `gate freshness` verdict, which is a check-run and not a run.

Issue 1360: `ci_wait.py` certified a head whose `gate freshness` CHECK-RUN
was red. The `gate freshness` workflow's own run concludes `success` on
every such head, because publishing the verdict is that run's job - the
publisher writes a check run of its own through the Checks API, and the
rulesets read the check, not the run. So the run that had to be read was
the one nothing gates on, and every seat on this fleet read exit 0.

`8ddfec21f32d484657e7ebc56c46a1b395670ca9` is the shape: all seven of its
workflow runs concluded `success` and its `gate freshness` check concluded
FAILURE. Every control here drives the REAL `ci_wait.py` as a process
against the fake `gh` in `_fake_gh.py`, so the query, the read and the
verdict are the shipped ones and only the transport is a double.

The suite-level controls sit here beside the end-to-end ones because they
are the same question asked of a smaller surface: the predicate and the
read that both waiters would otherwise have to grow their own.
"""
import contextlib
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
# Aliased to the names these suites have always called them by, so the
# extraction is the only thing the call sites see.
from _ci_wait_fixtures import (  # noqa: E402
    _ci_wait_clock as _Clock,
    _frozen_ci_wait_clock as _frozen_wait_clock)
from _watcher_fixtures import SHA  # noqa: E402
from _watcher_fixtures import runs_page  # noqa: E402
from _watcher_fixtures import suite  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
SOURCE = SKILL / 'ci_wait.py'
OTHER_REPO = 'example/other'
PUBLISHED = 'gate freshness'
# The check run the publisher POSTs, and the one every control below is
# about: id 7 and the run URL the live verdict carried.
VERDICT_URL = 'https://github.com/o/r/runs/7'


def _node(rid, name, conclusion='SUCCESS', status='COMPLETED'):
    """One check-run node, as the commit's check suites report it."""
    return {'databaseId': rid, 'name': name, 'status': status,
            'conclusion': conclusion,
            'completedAt': '2026-09-20T10:10:00Z',
            'detailsUrl': f'https://github.com/o/r/runs/{rid}'}


def _check(name, conclusion='success', status='completed', rid=7):
    """One check run already normalised, as a caller of `ci_state` gets it."""
    return {'id': rid, 'name': name, 'status': status,
            'conclusion': conclusion, 'html_url': VERDICT_URL,
            'completed_at': '2026-09-20T10:10:00Z'}


def _verdict_suite(conclusion='SUCCESS'):
    """The suite the Checks API creates for the verdict a publisher writes.

    It is not a workflow run's suite: the publisher POSTs a check run of
    its own, so the suite carries no `workflowRun` and the run list never
    sees it. That is the whole defect - the run says success and the gate
    is the check.
    """
    return suite(7, conclusion=conclusion, workflow=None, check_runs=[
        _node(7, PUBLISHED, conclusion)])


def _answers(suites):
    """The two queries `ci_wait.py` makes on a head it must answer about."""
    return {
        'checkSuites': runs_page(suites),
        'associatedPullRequests': {'data': {'repository': {'object': {
            'associatedPullRequests': {'nodes': []}}}}}}


def _wait(tmp, argv, suites, limit=120):
    """(the finished process, the fake) for one real `ci_wait.py` run."""
    fake = _fake_gh.FakeGh(tmp, _answers(suites))
    done = subprocess.run(
        [sys.executable, '-u', str(SOURCE), SHA, '--interval', '1',
         '--timeout', '30', *argv],
        env=fake.env(), capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=limit)
    return done, fake


def _green(*names):
    """One completed green run per named workflow, each its own workflow."""
    return [suite(rid, name=name, workflow=rid * 11)
            for rid, name in enumerate(names, start=1)]


# ---- the defect: a red published verdict on a head of green runs ----

def test_a_red_published_verdict_fails_a_head_of_green_runs(tmp):
    """The live shape, unchanged: the `gate freshness` run concludes
    SUCCESS - publishing the verdict is that run's job and it did it - and
    the check it published is FAILURE. Reading only runs certifies this
    head, which is issue 1360.

    What is asserted is the correct contract, not the defect's: exit 1, and
    the line naming the check, its conclusion and its URL, so a reader can
    go and look at it.
    """
    done, _ = _wait(tmp, [], _green('tests', PUBLISHED)
                    + [_verdict_suite(conclusion='FAILURE')])
    text = done.stdout
    assert done.returncode == 1, (done.returncode, text, done.stderr)
    assert f'{PUBLISHED}: failure' in text, text
    assert VERDICT_URL in text, text
    # Not merely a nonzero exit: the head must not be certified, and the
    # run that concluded SUCCESS must not be what the line names.
    assert 'acceptable' not in text, text
    assert 'failure' in text, text


def test_a_green_published_verdict_certifies_the_same_head(tmp):
    """The other half of the same fixture: identical runs, the check
    concluded SUCCESS, and the head is certified. A control that only ever
    refused would pass the control above."""
    done, _ = _wait(tmp, [], _green('tests', PUBLISHED)
                    + [_verdict_suite()])
    text = done.stdout
    assert done.returncode == 0, (done.returncode, text, done.stderr)
    assert 'acceptable' in text, text


def test_a_red_verdict_outranks_a_missing_required_run(tmp):
    """The ordering, which is the whole reason the conclusion is judged
    before the set: the `tests` run is absent, so the exit-4 refusal is
    reachable, and a refusal for an absent gate must never swallow a real
    failure. Exit 1, not exit 4."""
    done, _ = _wait(tmp, ['--grace', '1'], _green(PUBLISHED)
                    + [_verdict_suite(conclusion='FAILURE')])
    text = done.stdout
    assert done.returncode == 1, (done.returncode, text, done.stderr)
    assert f'{PUBLISHED}: failure' in text, text
    assert 'not certified' not in text, text


def test_a_job_check_is_not_a_published_verdict(tmp):
    """The selection is by name, so the job checks a `pull_request` run's
    suites carry are not the required check. A red job check beside a
    green published verdict is the run's own business, and treating every
    check run as a gate would refuse a head over a failed job the matrix
    already reported."""
    del tmp
    mod = _ci_wait()
    checks = [_check('tests (3.13, ubuntu-24.04)', 'failure'),
              _check(PUBLISHED, 'success')]
    assert mod.verdict([_head_run('tests')], checks) == (
        'acceptable', []), checks


# ---- an absent verdict: waited out, then refused ----

def test_an_absent_published_verdict_is_waited_out_and_then_refused(tmp):
    """A head whose publisher has not written its check yet is a wait, not
    a pass and not a red: on a mergeable head the publisher's own run is
    still going. The same grace that governs an absent workflow governs it,
    and past the grace the refusal names the CHECK, not only the run.

    The clock is the frozen one the other ci_wait suites drive, so what is
    asserted is what the wait DID - the polls it spent and the instant it
    stopped - rather than a margin against a real clock.
    """
    mod = _ci_wait()
    clock = _Clock()
    fake = _fake_gh.FakeGh(tmp, _answers(_green('tests')))
    out, err = io.StringIO(), io.StringIO()
    with fake.activate(), _frozen_wait_clock(mod, clock), \
            contextlib.redirect_stderr(err):
        code = mod.wait(OTHER_REPO, SHA, 7, 600, out, grace=30)
    text = out.getvalue()
    assert code == 4, text
    assert 'no gate freshness check' in text, text
    assert '30s grace' in text, text
    assert 'not certified' in text, text
    # Polls at 1000, 1007, ... and the refusal at the first observation
    # 30s or more on. A margin would be a claim about this machine.
    assert clock.now == 1035.0, clock.now


def test_a_missing_run_and_a_missing_check_are_named_together(tmp):
    """The refusal names BOTH kinds of absence, because either alone leaves
    the reader unable to tell which gate is missing."""
    mod = _ci_wait()
    clock = _Clock()
    fake = _fake_gh.FakeGh(tmp, _answers(_green('CodeQL')))
    out, err = io.StringIO(), io.StringIO()
    with fake.activate(), _frozen_wait_clock(mod, clock), \
            contextlib.redirect_stderr(err):
        code = mod.wait(DEFAULT_REPO, SHA, 7, 600, out, grace=1)
    text = out.getvalue()
    assert code == 4, text
    assert 'no tests run and no gate freshness check on' in text, text


# ---- the direction rule (issue #1318, applied to the check) ----

def test_another_repository_requires_no_published_check(tmp):
    """`gate freshness` is THIS repository's published gate, and on another
    repository the name is a guess - exactly the position `tests` is in, so
    the same asymmetry: a foreign repository with no published check is not
    refused for it, and the tool's own gate is named by `--required`."""
    done, _ = _wait(tmp, ['--repo', OTHER_REPO, '--required', 'ci'],
                    _green('ci'))
    text = done.stdout
    assert done.returncode == 0, (done.returncode, text, done.stderr)
    assert 'gate freshness check' not in text, text


def test_the_note_says_a_foreign_repository_checks_no_published_check(tmp):
    """The refusal a foreign repository earns for its absent workflow
    carries the note, and the note is what tells the caller the published
    check is not among the defaults - so it must not claim otherwise, and
    the refusal must not name a check this repository never required."""
    done, _ = _wait(tmp, ['--repo', OTHER_REPO, '--grace', '1'],
                    _green('ci'))
    text = done.stdout
    assert done.returncode == 4, (done.returncode, text, done.stderr)
    assert 'only tests is checked by default' in text, text
    assert 'no gate freshness check' not in text, text


def test_this_repository_cannot_switch_the_published_check_off(tmp):
    """The half of the asymmetry that protects the false green: on this
    repository no argument removes the check from the required set, and
    `--required` only ever ADDS. The fixture carries every run green and a
    red check, and the refusal is still the check's."""
    done, _ = _wait(tmp, ['--required', 'ci'], _green('ci', 'tests')
                    + [_verdict_suite(conclusion='FAILURE')])
    text = done.stdout
    assert done.returncode == 1, (done.returncode, text, done.stderr)
    assert f'{PUBLISHED}: failure' in text, text


# ---- --once ----

def test_once_prints_the_published_check_run(tmp):
    """A trial call that cannot see the check runs cannot tell a head whose
    publisher has not written yet from one that never will."""
    done, _ = _wait(tmp, ['--once'], _green('tests')
                    + [_verdict_suite(conclusion='FAILURE')])
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert f'{PUBLISHED}: completed/failure' in done.stderr, done.stderr
    assert 'state: unacceptable' in done.stderr, done.stderr


# ---- the predicate, on its own ----

def test_red_published_names_only_an_exact_match(tmp):
    """The same exact-name rule `missing_required` reads workflows by: a
    case difference or a decorated spelling is a different check, and
    treating it as the gate would refuse a head over a check that is not
    this repository's."""
    del tmp
    mod = _ci_gate()
    assert [c['name'] for c in mod.red_published(
        [_check(PUBLISHED, 'failure')])] == [PUBLISHED]
    for name in ('Gate freshness', 'gate freshness ', 'gate-freshness'):
        assert mod.red_published([_check(name, 'failure')]) == [], name
    # And a red check nobody required is not this tool's business.
    assert mod.red_published([_check('pylint', 'failure')]) == []


def test_red_published_returns_whole_checks_for_the_offender_line(tmp):
    """The offender loop prints a run's name, conclusion and URL, so the
    answer is the check itself rather than a name: a control that wanted
    only the names would have to build them again here."""
    del tmp
    mod = _ci_gate()
    offenders = mod.red_published([_check(PUBLISHED, 'failure')])
    assert [o['conclusion'] for o in offenders] == ['failure'], offenders
    assert [o['html_url'] for o in offenders] == [VERDICT_URL], offenders


def test_missing_published_names_the_absent_check(tmp):
    del tmp
    mod = _ci_gate()
    assert mod.missing_published([]) == [PUBLISHED]
    assert mod.missing_published([_check(PUBLISHED, 'failure')]) == []
    assert mod.missing_published([_check('pylint')]) == [PUBLISHED]


def test_the_predicate_ignores_which_conclusions_are_acceptable(tmp):
    """The set of acceptable conclusions is `ci_wait`'s and not a second
    copy: `ci_gate.ACCEPTABLE` is the one definition and `ci_wait`'s name
    is an alias of the same object, read here through identity.

    Identity within the ONE loaded world, not across two loads: a suite
    that loaded each module separately would be comparing two equally
    named constants living in two module objects, which says nothing
    about whether either is a copy of the other.
    """
    del tmp
    mod = _ci_gate()
    wait = _ci_wait()
    assert wait.ACCEPTABLE is wait.ci_gate.ACCEPTABLE, (
        'ci_wait.ACCEPTABLE is a copy, so the two sets can drift')
    assert mod.ACCEPTABLE == wait.ACCEPTABLE, (mod.ACCEPTABLE, wait.ACCEPTABLE)
    assert wait.ACCEPTABLE == frozenset({'success', 'neutral', 'skipped'})
    for conclusion in mod.ACCEPTABLE:
        assert mod.red_published([_check(PUBLISHED, conclusion)]) == [], (
            conclusion)
    assert mod.red_published([_check(PUBLISHED, 'failure')]), 'nothing red'


# ---- the read, on its own ----

def test_ci_state_answers_both_questions_from_one_walk(tmp):
    """One query, two answers. `ci_wait` must not read the runs and then
    the checks over a second request, so the call count is the control:
    a single page of suites, and a single `gh` call to read it."""
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': runs_page([
        suite(1, name='tests', check_runs=[
            _node(2, 'tests (3.13, ubuntu-24.04)')]),
        _verdict_suite()])})
    with fake.activate():
        runs, checks = client.ci_state('o', 'r', SHA)
    assert len(fake.calls()) == 1, [call['request'][:60]
                                     for call in fake.calls()]
    assert [run['id'] for run in runs] == [1], runs
    # Normalised to the keys the run dicts already use, so one offender
    # loop prints either, and lowercased, because the API spells them in
    # caps: the printed conclusion is compared against ACCEPTABLE.
    assert [(c['name'], c['conclusion'], c['status'], c['html_url'])
            for c in checks] == [
                ('tests (3.13, ubuntu-24.04)', 'success', 'completed',
                 'https://github.com/o/r/runs/2'),
                (PUBLISHED, 'success', 'completed', VERDICT_URL)], checks
    assert [c['id'] for c in checks] == [2, 7], checks


def test_a_check_run_still_running_normalises_to_no_conclusion(tmp):
    """`conclusion` is null until a check concludes, and a reader that
    left the API's null in place would compare None against the
    acceptable set by accident rather than by decision."""
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': runs_page([
        suite(1, name='tests', check_runs=[
            _node(3, PUBLISHED, None, status='IN_PROGRESS')])])})
    with fake.activate():
        _runs, checks = client.ci_state('o', 'r', SHA)
    assert checks[0]['conclusion'] is None, checks
    assert checks[0]['status'] == 'in_progress', checks


def test_a_check_run_past_the_first_page_is_still_read(tmp):
    """The published verdict is a suite of its own, so on a head whose
    matrix is large it can sit on a later page than the runs. Reading only
    the first page is the read that found every run and no gate."""
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': [
        _page([suite(1, name='tests')], has_next=True, cursor='CURSOR-1'),
        _page([_verdict_suite()])]})
    with fake.activate():
        runs, checks = client.ci_state('o', 'r', SHA)
    assert len(fake.calls()) == 2, len(fake.calls())
    assert [run['id'] for run in runs] == [1], runs
    assert [c['name'] for c in checks] == [PUBLISHED], checks


def test_a_suite_carrying_no_check_runs_reads_as_none(tmp):
    """A suite whose check-runs the query did not answer is no evidence of
    an absent gate - it is the shape every suite has before the
    connection exists, and reading it as an empty list would refuse every
    head the moment one suite answers no nodes."""
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': runs_page(
        [suite(1, name='tests')])})
    with fake.activate():
        runs, checks = client.ci_state('o', 'r', SHA)
    assert [run['id'] for run in runs] == [1], runs
    assert checks == [], checks


def test_workflow_runs_is_the_first_answer_and_not_a_second_query(tmp):
    """`watch_all.py` reads only the runs, and it must not pay for the
    checks it does not ask about: the same answer, off the same one
    request."""
    client = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': runs_page(
        [suite(1, name='tests'), _verdict_suite()])})
    with fake.activate():
        runs = client.workflow_runs('o', 'r', SHA)
        state_runs, _checks = client.ci_state('o', 'r', SHA)
    assert runs == state_runs, (runs, state_runs)
    assert len(fake.calls()) == 2, len(fake.calls())


def _page(suites, has_next=False, cursor=None):
    """One page of the commit's check suites, paging by cursor."""
    return {'data': {'repository': {'object': {'checkSuites': {
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'nodes': list(suites)}}}}}


def _head_run(name, conclusion='success'):
    """One workflow run, as `verdict` reads it."""
    return {'id': 1, 'name': name, 'status': 'completed',
            'conclusion': conclusion, 'html_url': 'https://github.com/o/r/actions/runs/1'}


def _client():
    return _util.load(SKILL / 'gh_client.py', 'gh_client_published_contract')


def _ci_gate():
    return _util.load(SKILL / 'ci_gate.py', 'ci_gate_published_contract')


def _ci_wait():
    return _util.load(SOURCE, 'ci_wait_published_contract')


DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwaitpub_')


if __name__ == '__main__':
    raise SystemExit(main())
