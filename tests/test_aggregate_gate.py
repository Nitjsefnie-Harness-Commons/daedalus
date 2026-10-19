#!/usr/bin/env python3
"""Aggregate verdicts in-process and the workflow wiring that calls them."""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_names, _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

SOURCE = ROOT / 'scripts' / 'ci' / 'aggregate_gate.py'
GATE_RUN = 'python3 scripts/ci/aggregate_gate.py'
STRICT_JOBS = ('changes', 'pycodestyle', 'pylint', 'pyright', 'eslint')
SKIPPABLE_JOBS = ('actionlint', 'suites', 'wheel', 'coverage-matrix',
                  'coverage')
HEAD = 'c2ae15819d9374fda4720e59a46c3a991c0319be'
# Non-zero origin: at 0.0 a deadline ignoring the clock is the same number.
EPOCH = 1e6
FROZEN = (lambda: EPOCH, lambda _seconds: None)


def _gate():
    return _util.load(SOURCE, 'aggregate_gate_contract')


def _needs(**results):
    return {name: {'result': result} for name, result in results.items()}


def _run(rid, conclusion, started, workflow=11, **fields):
    """One workflow run as the actions API reports it."""
    run = {
        'id': rid,
        'name': f'run {rid}',
        'status': 'completed',
        'conclusion': conclusion,
        'run_started_at': started,
        'workflow_id': workflow,
        'html_url': f'https://github.com/o/r/actions/runs/{rid}',
    }
    run.update(fields)
    return run


def _recorder(own, pages, polls=None, jobs=None):
    """A gh read; `polls` is one page-LIST per poll of the secrets wait."""
    calls = []
    remaining = list(polls or [[]])

    def read(argv):
        calls.append(argv)
        assert len(calls) < 1000, 'the gate never stopped'
        target = argv[-1]
        if '/jobs?' in target:
            rid = int(target.split('/runs/')[1].split('/')[0])
            found = jobs if not isinstance(jobs, dict) else jobs.get(rid)
            return json.dumps({'jobs': found})
        # Dispatch on the query's SHAPE, not on a constant.
        if 'head_sha=' in target:
            page = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            return json.dumps({'workflow_runs': page})
        if '/actions/workflows/' in target:
            return ''.join(json.dumps(page) for page in pages)
        return json.dumps(own)

    return calls, read


def _secrets_run(rid, conclusion='success', status='completed',
                 started='2026-09-07T11:00:00Z'):
    return _run(rid, conclusion if status == 'completed' else None,
                started, name='secrets', status=status)


def _scan_job(conclusion='success', name='gitleaks'):
    return {'id': 1, 'name': name, 'conclusion': conclusion}


def _scripted_clock(readings):
    later = list(readings)[1:]
    current = [readings[0]]

    def now():
        return current[0]

    def sleep(seconds):
        current[0] = later.pop(0) if later else current[0] + seconds

    return now, sleep


def _polls(calls):
    return [call for call in calls if 'head_sha=' in call[-1]]


def _drive_scan(polls, jobs=None, readings=(EPOCH,)):
    mod = _gate()
    calls, read = _recorder(MINE, [], polls=polls, jobs=jobs)
    now, sleep = _scripted_clock(readings)
    verdict, message = mod.require_secret_scan(
        'o/r', HEAD, mod.DEFAULT_POLL_BOUND_S, read, now, sleep)
    return calls, verdict, message


def _query_error(mod, call):
    """Return the QueryError `call` raised; fail the test when it did not."""
    try:
        call()
    except mod.QueryError as exc:
        return exc
    raise AssertionError('QueryError was not raised')


MINE = _run(1, 'cancelled', '2026-09-07T10:00:00Z')
NEWER = _run(2, 'success', '2026-09-07T10:05:00Z')


def test_only_observed_unstarted_cancelled_dependencies_are_diagnosed(tmp):
    del tmp
    mod = _gate()
    matrix = 'suites (macos-latest, 3.11)'
    cases = (
        ('suites', [matrix, 'suites (macos-latest, 3.12)'],
         'cancelled', [], [matrix, 'suites (macos-latest, 3.12)'], None, []),
        ('suites', ['suites'], 'cancelled', [], ['suites'], None, []),
        ('coverage', ['coverage'], 'cancelled', [], ['coverage'], None, []),
        ('suites', ['suites-extra (macos-latest, 3.11)'],
         'cancelled', [], [], None, []),
        ('suites', ['suites extra (macos-latest, 3.11)'],
         'cancelled', [], [], None, []),
        ('coverage', ['coverage-matrix (macos-latest, 3.11)', matrix],
         'cancelled', [], [], None, []),
        ('suites', [matrix], 'success', [], [], None, []),
        ('suites', [matrix], 'cancelled', [{'name': 'Set up job'}],
         [], None, []),
        ('suites', [matrix], 'cancelled', None, [], None, []),
        ('suites', [matrix], 'cancelled', ..., [], None, []),
        ('suites', [None], 'cancelled', [], [], None, []),
        ('suites', [], 'cancelled', [], [], None, []),
        ('suites', [matrix], 'cancelled', [], [matrix], 'cancelled',
         [{'name': 'Set up job'}]),
        ('suites', [matrix], 'cancelled', [{'name': 'Set up job'}],
         ['wheel'], 'cancelled', []),
        ('suites', [], 'cancelled', [], [], 'skipped', []),
    )
    for key, names, conclusion, steps, unstarted, wheel, wheel_steps in cases:
        needs = {key: 'cancelled'}
        if wheel:
            needs['wheel'] = wheel
        jobs = [{'id': i + 1, 'name': name, 'conclusion': conclusion}
                for i, name in enumerate(names)]
        if steps is not ...:
            for job in jobs:
                job['steps'] = steps
        jobs.append({'id': 99, 'name': 'wheel', 'conclusion': 'cancelled',
                     'steps': wheel_steps})
        calls, read = _recorder(MINE, [{'workflow_runs': [MINE]}], jobs=jobs)
        verdict, message = mod.evaluate(_needs(**needs),
                                        'o/r', '39273610768', 'main', read)
        assert verdict not in mod.GREEN
        assert len(calls) == 3, calls
        assert calls[-1] == [
            'gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
            'repos/o/r/actions/runs/39273610768/jobs?per_page=100']
        if unstarted:
            assert (verdict, message) == (
                'cancelled-unstarted',
                'cancelled dependency jobs have 0 recorded steps: '
                + ', '.join(unstarted)
                + '; retry with gh run rerun --failed 39273610768')
        else:
            assert (verdict, message) == (
                'cancelled-deliberate', _CANCEL_MESSAGES[
                    'cancelled-deliberate'] + f'{key}=cancelled')


def test_unreadable_cancelled_jobs_preserve_the_prior_verdict(tmp):
    del tmp
    mod = _gate()
    calls, read = _recorder(MINE, [{'workflow_runs': [MINE]}])
    with mock.patch.object(mod, 'scan_jobs', return_value=None) as jobs:
        result = mod.evaluate(_needs(suites='cancelled'),
                              'o/r', '1', 'main', read)
    jobs.assert_called_once_with('o/r', '1', read)
    assert len(calls) == 2
    assert result == ('cancelled-deliberate',
                      _CANCEL_MESSAGES['cancelled-deliberate']
                      + 'suites=cancelled')


def test_missing_context_fails_without_a_query(tmp):
    del tmp
    calls, read = _recorder({}, [])
    verdict, message = _gate().evaluate(_needs(suites='cancelled'),
                                        '', '', '', read)
    assert verdict == 'query-failed'
    assert 'query failed' in message, message
    assert calls == []


def test_equal_start_ties_break_by_id_both_ways(tmp):
    del tmp
    mod = _gate()
    stamp = '2026-09-07T10:00:00Z'
    cases = (
        (_run(9, None, stamp), _run(10, 'success', stamp), True),
        (MINE, _run(2, None, None,
                    created_at='2026-09-07T10:05:00Z'), True),
        (MINE, dict(MINE, conclusion='success'), False),
        (MINE, dict(NEWER, workflow_id=22), False),
        (_run(1, None, '2026-09-07T10:00:00'),
         _run(2, 'success', '2026-09-07T11:00:00+02:00'), False),
        (_run(1, None, '2026-09-07T10:00:00'),
         _run(3, 'success', '2026-09-07T13:00:00'), True),
    )
    for mine, other, expected in cases:
        assert mod.superseded(mine, [mine, other]) is expected
        if expected:
            assert not mod.superseded(other, [mine, other])


def test_the_queries_target_the_own_run_and_this_workflow(tmp):
    del tmp
    mod = _gate()
    calls, read = _recorder(MINE, [{'workflow_runs': [MINE, NEWER]}])
    verdict, message = mod.evaluate(_needs(suites='cancelled'),
                                    'o/r', '1', 'fix/x', read)
    assert verdict == 'cancelled-superseded', message
    assert [['gh', 'api', '-H', 'Cache-Control: no-cache',
            'repos/o/r/actions/runs/1'], [
            'gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
            'repos/o/r/actions/workflows/tests.yml/runs'
            '?branch=fix%2Fx&per_page=100']] == calls

    calls, read = _recorder(
        MINE, [{'workflow_runs': [MINE]}, {'workflow_runs': [NEWER]}])
    verdict, _ = mod.evaluate(_needs(suites='cancelled'),
                              'o/r', '1', 'main', read)
    assert verdict == 'cancelled-superseded'
    assert len(calls) == 2


def test_evaluate_preserves_hard_and_query_failures_without_reading_jobs(tmp):
    del tmp
    mod = _gate()
    cases = (
        ('failure', False, ('failed', 'Dependencies not successful: '
                            'wheel=failure, suites=cancelled')),
        (None, True, ('query-failed', _CANCEL_MESSAGES['query-failed']
                      + 'suites=cancelled')),
    )
    for wheel, refused, expected in cases:
        calls, record = _recorder(MINE, [{'workflow_runs': [MINE]}],
                                  jobs=[dict(_scan_job('cancelled', 'suites'),
                                             steps=[])])

        def read(argv, record=record, refused=refused):
            payload = record(argv)
            if refused and '/jobs?' not in argv[-1]:
                raise mod.QueryError('refused')
            return payload

        needs = {'suites': 'cancelled', **({'wheel': wheel} if wheel else {})}
        assert mod.evaluate(_needs(**needs), 'o/r', '1', 'main', read) == (
            expected)
        assert len(calls) == 2, calls
        assert not any('/jobs?' in call[-1] for call in calls), calls


def test_a_malformed_started_at_sorts_oldest_and_ignores_created_at(tmp):
    del tmp
    mod = _gate()
    broken = _run(7, None, 'definitely-not-a-stamp',
                  created_at='2026-09-07T10:05:00Z', status='in_progress')
    earlier = _run(8, 'success', '2026-09-07T09:00:00Z')
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    assert mod._started_key(broken) == (oldest, 7)
    assert mod.superseded(broken, [broken, earlier])
    assert not mod.superseded(earlier, [broken, earlier])


def test_the_raw_decoder_reads_whitespace_separated_documents(tmp):
    del tmp
    mod = _gate()
    assert mod._decode('{"a": 1}\n  [2]') == [{'a': 1}, [2]]
    error = _query_error(mod, lambda: mod._decode('{"a": 1} oops'))
    assert 'unparseable gh output' in str(error), str(error)


def test_gh_read_reports_failures_and_returns_stdout(tmp):
    del tmp
    mod = _gate()
    for effect in (subprocess.TimeoutExpired(cmd='gh', timeout=120),
                   OSError(5, 'input/output error')):
        with mock.patch.object(mod.subprocess, 'run', side_effect=effect):
            error = _query_error(mod, lambda: mod.gh_read(['gh', 'api']))
        assert 'gh failed' in str(error), str(error)
    proc = mock.Mock(returncode=0, stdout='the payload', stderr='noise')
    run = mock.Mock(return_value=proc)
    with mock.patch.object(mod.subprocess, 'run', run):
        assert mod.gh_read(['gh', 'api', 'repos/o/r']) == 'the payload'
    assert run.call_args.args == (['gh', 'api', 'repos/o/r'],)


def test_every_single_dependency_result_is_tabled(tmp):
    """The standing per-job table: what each result means for the verdict."""
    del tmp
    mod = _gate()
    states = ('success', 'failure', 'cancelled', 'skipped')
    for name in (*STRICT_JOBS, *SKIPPABLE_JOBS):
        for state in states:
            needs = _needs(**{dep: 'success'
                              for dep in (*STRICT_JOBS, *SKIPPABLE_JOBS)})
            needs[name] = {'result': state}
            verdict, _ = mod.decide(needs)
            expected = {
                'success': 'passed',
                'failure': 'failed',
                'skipped': 'passed' if name not in STRICT_JOBS
                else 'strict-skipped',
                'cancelled': 'query-failed',
            }[state]
            assert verdict == expected, (name, state, verdict)
            assert (verdict in mod.GREEN) is (expected == 'passed')


JOINT_STATES = ('success', 'failure', 'skipped', 'cancelled')
JOINT_CONTEXTS = (
    ('query-failed', None, None),
    ('cancelled-deliberate', MINE, [MINE]),
    ('cancelled-superseded', MINE, [MINE, NEWER]),
)
NO_CANCEL_CONTEXT = JOINT_CONTEXTS[:1]
_CANCEL_MESSAGES = {
    'query-failed':
        'the supersession query failed, so the cancel could not be '
        'proven superseded: ',
    'cancelled-deliberate':
        'no newer run of this workflow exists, so the cancel is '
        'deliberate: ',
    'cancelled-superseded':
        'the cancelled dependencies were superseded by run '
        + str(NEWER['id']) + ' (' + NEWER['html_url'] + '): ',
}


def _joint_expectation(names, states, context):
    """The joint contract: verdict and exact message for two deps."""
    deviants = [
        (name, state) for name, state in zip(names, states)
        if state != 'success'
        and (state != 'skipped' or name in STRICT_JOBS)]
    if not deviants:
        return 'passed', (
            'All dependencies succeeded: ' + ', '.join(sorted(names)))
    hard = [pair for pair in deviants if pair[1] != 'cancelled']
    cancelled = [pair for pair in deviants if pair[1] == 'cancelled']
    named = ', '.join(
        f'{name}={state}' for name, state in hard + cancelled)
    if hard:
        verdict = 'failed'
        if all(state == 'skipped' for _, state in hard):
            verdict = 'strict-skipped'
        return verdict, f'Dependencies not successful: {named}'
    return context, _CANCEL_MESSAGES[context] + named


def test_two_dependencies_are_decided_jointly(tmp):
    """The joint table: two deviating dependencies, one verdict."""
    del tmp
    mod = _gate()
    deps = STRICT_JOBS + SKIPPABLE_JOBS
    for index, first in enumerate(deps):
        for second in deps[index + 1:]:
            for first_state in JOINT_STATES:
                for second_state in JOINT_STATES:
                    names = (first, second)
                    states = (first_state, second_state)
                    needs = _needs(**dict(zip(names, states)))
                    contexts = (JOINT_CONTEXTS if 'cancelled' in states
                                else NO_CANCEL_CONTEXT)
                    for expected, mine, runs in contexts:
                        verdict, message = mod.decide(needs, mine, runs)
                        assert (verdict, message) == _joint_expectation(
                            names, states, expected), (
                            names, states, expected, verdict, message)


def test_main_exits_zero_only_for_green_verdicts(tmp):
    del tmp
    mod = _gate()
    cases = (
        (json.dumps(_needs(suites='cancelled')), 0, CLEAN_JOBS, [NEWER], ''),
        (json.dumps(_needs(suites='failure')), 1, CLEAN_JOBS, [], ''),
        (json.dumps(_needs(changes='skipped')), 1, CLEAN_JOBS, [], ''),
        ('{', 1, CLEAN_JOBS, [], ''),
        (json.dumps(_needs(changes='success', suites='success')), 1,
         [_scan_job(conclusion='failure')], [], 'concluded failure'),
        (json.dumps(_needs(suites='cancelled')), 1,
         [dict(_scan_job('cancelled', 'suites'), steps=[])], [],
         'gh run rerun --failed 1'),
    )
    for needs_json, expected, jobs, runs, diagnostic in cases:
        calls, read = _recorder(MINE, [{'workflow_runs': [MINE] + runs}],
                                polls=[[CLEAN]], jobs=jobs)
        with mock.patch.dict(os.environ, {
                'NEEDS_JSON': needs_json,
                'REPOSITORY': 'o/r',
                'RUN_ID': '1',
                'HEAD_BRANCH': 'main',
                'SECRETS_HEAD_SHA': HEAD,
        }, clear=True):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(mod, 'gh_read', read):
                with contextlib.redirect_stdout(out):
                    with contextlib.redirect_stderr(err):
                        code = mod.main()
            assert code == expected, (needs_json, code, err.getvalue())
            assert bool(out.getvalue()) is (expected == 0), out.getvalue()
            assert bool(err.getvalue()) is (expected == 1), err.getvalue()
            assert diagnostic in err.getvalue(), err.getvalue()
        assert len(_polls(calls)) == int(
            expected == 0 or diagnostic == 'concluded failure'), calls


def test_the_aggregate_job_runs_the_module(tmp):
    del tmp
    job = complete_job_mapping(_tests_yml(), 'aggregate') or {}
    assert job.get('if') == '${{ always() }}', job.get('if')
    assert 'if' not in _gate_step()
    checkouts = [step for step in job['steps']
                 if str(step.get('uses', '')).startswith('actions/checkout')]
    assert len(checkouts) == 1, job['steps']
    assert checkouts[0]['with'] == {'persist-credentials': 'false'}
    assert job['permissions'] == {'contents': 'read', 'actions': 'read'}
    gate = _gate_step()
    assert gate['env'] == {
        'NEEDS_JSON': '${{ toJSON(needs) }}',
        'GH_TOKEN': '${{ github.token }}',
        'REPOSITORY': '${{ github.repository }}',
        'RUN_ID': '${{ github.run_id }}',
        'HEAD_BRANCH': '${{ github.event.pull_request.head.ref'
                       ' || github.ref_name }}',
        'SECRETS_HEAD_SHA': '${{ github.event.pull_request.head.sha'
                            ' || github.sha }}',
        'SECRETS_POLL_BOUND_S': '600',
    }


MARKER_RUN = (
    '# git grep exits 1 for "no match" and above 1 for an error it could\n'
    '# not run, and a guard that reads its own error as a clean tree is\n'
    '# the false green this step exists to prevent.\n'
    'found=0\n'
    "git grep -nI -E '^(<{7}( |$)|>{7}( |$)|={7}$)' -- . || found=$?\n"
    'if [ "$found" -gt 1 ]; then\n'
    '  echo "git grep exited $found, so no marker was looked for."\n'
    '  exit "$found"\n'
    'fi\n'
    'if [ "$found" -eq 0 ]; then\n'
    '  echo "A merge-conflict marker is committed. Resolve the conflict,'
    ' rebase onto the target branch and push again."\n'
    '  exit 1\n'
    'fi\n'
)
MARKER_STEP = {
    'name': 'Check no tracked file carries a merge-conflict marker',
    'shell': 'bash',
    'run': MARKER_RUN,
}


def test_the_marker_step_is_the_claim_port_verbatim(tmp):
    """Keep the marker port verbatim and immediately before the gate."""
    del tmp
    steps = (complete_job_mapping(_tests_yml(), 'aggregate') or {})['steps']
    matched = [s for s in steps if s.get('name') == MARKER_STEP['name']]
    assert matched == [MARKER_STEP], steps
    marker = next(i for i, s in enumerate(steps)
                  if s.get('name') == MARKER_STEP['name'])
    gate = next(i for i, s in enumerate(steps)
                if s.get('run') == GATE_RUN)
    assert marker + 1 == gate, steps


def test_the_checkout_pin_is_the_one_the_sibling_jobs_use(tmp):
    del tmp
    # Derived from tests.yml itself: one revision across its checkouts —
    # a split hand edit reddens, a coordinated bump stays green.
    refs = set(re.findall(r'actions/checkout@\S+', _tests_yml()))
    assert len(refs) == 1, refs


CLEAN = _secrets_run(7)
CLEAN_JOBS = [_scan_job(), _scan_job(name='summarize')]


def test_the_deadline_keeps_a_found_verdict_and_refuses_an_absent_one(tmp):
    """The bound governs the CONTINUE, so a verdict found at it stands."""
    del tmp
    cases = (
        ([[], [CLEAN]], (EPOCH, EPOCH + 600.0), 'passed', 2),
        ([[]], (EPOCH, EPOCH + 600.0), 'secrets-unreported', 2),
        ([[], [], [CLEAN]], (EPOCH, EPOCH + 420.0, EPOCH + 590.0),
         'passed', 3),
        ([[_secrets_run(7, status='in_progress')], [CLEAN]],
         (EPOCH, EPOCH + 30.0, EPOCH + 60.0), 'passed', 2),
    )
    for pages, readings, expected, count in cases:
        calls, verdict, message = _drive_scan(pages, CLEAN_JOBS, readings)
        assert verdict == expected, message
        assert (verdict in _gate().GREEN) is (expected == 'passed')
        assert len(_polls(calls)) == count, calls
        if expected == 'passed':
            assert 'gitleaks' in message and 'concluded success' in message
        else:
            bound = _gate().DEFAULT_POLL_BOUND_S
            assert 'secrets' in message and f'{bound:g} s' in message


def test_both_secrets_queries_carry_the_head_sha_and_the_workflow(tmp):
    """A query whose arguments are dropped reads some other run entirely."""
    del tmp
    calls, _verdict, _message = _drive_scan([[CLEAN]], CLEAN_JOBS)
    assert ['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
            f'repos/o/r/actions/workflows/{_gate().SECRETS_WORKFLOW}/runs'
            f'?head_sha={HEAD}&per_page=100'] in calls
    assert ['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
            f'repos/o/r/actions/runs/{CLEAN["id"]}/jobs?per_page=100'] in calls


def test_every_scan_the_gate_cannot_read_clean_is_red_and_says_why(tmp):
    del tmp
    _, absent, missing = _drive_scan([[CLEAN]], [_scan_job(name='setup')])
    assert absent == 'secrets-missing', absent
    assert 'setup' in missing and str(CLEAN['id']) in missing, missing
    for state in ('failure', 'skipped', 'cancelled', 'timed_out',
                  'neutral', None):
        _, verdict, message = _drive_scan(
            [[CLEAN]], [dict(_scan_job(), conclusion=state)])
        assert verdict == 'secrets-failed', (state, verdict)
        assert verdict not in _gate().GREEN
        assert 'gitleaks' in message and CLEAN['html_url'] in message
        if state == 'failure':
            assert 'failure' in message, message

    runs = [_secrets_run(1), _secrets_run(2, started='2026-09-07T11:05:00Z')]
    jobs = {1: [_scan_job(conclusion='failure')], 2: [_scan_job()]}
    _, verdict, message = _drive_scan([runs], jobs)
    assert verdict in _gate().GREEN, message


def test_an_unreadable_answer_is_red_not_an_absent_run_and_not_retried(tmp):
    """One subject, three refusals: the answer, the transport, the retry."""
    del tmp
    mod = _gate()
    run = mock.Mock(return_value=mock.Mock(
        returncode=1, stdout='', stderr='rate limited'))
    with mock.patch.object(mod.subprocess, 'run', run):
        verdict, message = mod.require_secret_scan(
            'o/r', HEAD, mod.DEFAULT_POLL_BOUND_S, mod.gh_read, *FROZEN)
    assert verdict == 'secrets-query-failed'
    assert verdict not in mod.GREEN
    assert 'could not be judged' in message, message
    assert run.call_count == 1, run.call_count

    def good_on(marker):
        def read(argv):
            return (json.dumps({'workflow_runs': [CLEAN]})
                    if marker in argv[-1] else 'not json at all')
        return read

    refused = mock.Mock(side_effect=mod.QueryError('HTTP 502'))

    for name, read in (('runs body', good_on('/jobs?')),
                       ('jobs body', good_on('head_sha=')),
                       ('refused', refused)):
        verdict, message = mod.require_secret_scan(
            'o/r', HEAD, mod.DEFAULT_POLL_BOUND_S, read, *FROZEN)
        assert verdict == 'secrets-query-failed', (name, verdict)
        assert verdict not in mod.GREEN
        assert 'could not be judged' in message, (name, message)
    calls, read = _recorder(MINE, [], polls=[[]])
    verdict, _ = mod.require_secret_scan(
        'o/r', '', mod.DEFAULT_POLL_BOUND_S, read, *FROZEN)
    assert verdict == 'secrets-query-failed' and calls == [], calls


def test_the_poll_bound_is_read_and_refused_outside_its_range(tmp):
    """Honour an in-range override and refuse values on either side."""
    del tmp
    mod = _gate()
    with mock.patch.dict(os.environ, {}, clear=True):
        assert mod.poll_bound() == mod.DEFAULT_POLL_BOUND_S
        os.environ[mod.POLL_BOUND_ENV] = '90'
        assert mod.poll_bound() == 90.0
        for raw in ('0', str(mod.MAX_POLL_BOUND_S + 1)):
            os.environ[mod.POLL_BOUND_ENV] = raw
            _query_error(mod, mod.poll_bound)
    with mock.patch.dict(os.environ, {mod.POLL_BOUND_ENV: 'soon'}):
        verdict, message = mod._secret_scan()
    assert verdict == 'secrets-query-failed'
    assert mod.POLL_BOUND_ENV in message, message


def _gate_step():
    job = complete_job_mapping(_tests_yml(), 'aggregate') or {}
    steps = [s for s in job['steps'] if s.get('run') == GATE_RUN]
    assert len(steps) == 1, job['steps']
    return steps[0]


def test_an_unidentified_identity_is_refused_rather_than_read(tmp):
    """What the API will not identify is not read, and the gate reds."""
    del tmp
    mod = _gate()
    for repository, run_id in (('o/r', '7a'), ('not-a-repo', 7)):
        assert mod.scan_jobs(repository, run_id, lambda _argv: '') is None
    for polls in ([[_secrets_run('7a')]], [[{'id': '7a'}]]):
        _calls, verdict, _message = _drive_scan(polls, CLEAN_JOBS)
        assert verdict == 'secrets-query-failed', polls


def _named_workflow(mod, constant):
    """A selector names a shipped file, not its API-refused path."""
    name = getattr(mod, constant)
    path = ROOT / '.github' / 'workflows' / name
    assert path.is_file(), f'{name} names no workflow file'
    return path


def test_both_selectors_name_shipped_workflows_and_the_right_job(tmp):
    """The gate matches `jobs[].name`: a declared `name:`, else the job id."""
    del tmp
    mod = _gate()
    assert _named_workflow(mod, 'WORKFLOW').is_file()
    source = _named_workflow(mod, 'SECRETS_WORKFLOW').read_text(
        encoding='utf-8')
    assert mod.SECRETS_JOB in _job_names(source)
    job = complete_job_mapping(source, mod.SECRETS_JOB) or {}
    assert job.get('name') in (None, mod.SECRETS_JOB), job.get('name')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='aggregategate_')


if __name__ == '__main__':
    raise SystemExit(main())
