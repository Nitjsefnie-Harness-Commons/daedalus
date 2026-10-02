#!/usr/bin/env python3
"""The aggregate gate: allowed sets, the superseded-cancel rule, the caller.

scripts/ci/aggregate_gate.py owns the aggregate job's whole decision; these
tests drive its functions in-process and pin how the workflow invokes the
module: the checkout, the narrowed permissions and the env indirection.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

SOURCE = ROOT / 'scripts' / 'ci' / 'aggregate_gate.py'
CHECKOUT = 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1'
STRICT_JOBS = ('changes', 'pycodestyle', 'pylint', 'pyright', 'eslint')
SKIPPABLE_JOBS = ('actionlint', 'suites', 'wheel', 'coverage-matrix',
                  'coverage')


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


def _recorder(own, pages):
    """A gh read answering the own-run query and the branch pages."""
    calls = []

    def read(argv):
        calls.append(argv)
        if '/actions/workflows/' in argv[-1]:
            return ''.join(json.dumps(page) for page in pages)
        return json.dumps(own)

    return calls, read


def _query_error(mod, call):
    """Return the QueryError `call` raised; fail the test when it did not."""
    try:
        call()
    except mod.QueryError as exc:
        return exc
    raise AssertionError('QueryError was not raised')


MINE = _run(1, 'cancelled', '2026-09-07T10:00:00Z')
NEWER = _run(2, 'success', '2026-09-07T10:05:00Z')


def test_all_dependencies_succeeding_passes(tmp):
    del tmp
    verdict, message = _gate().decide(
        _needs(changes='success', suites='success'))
    assert verdict == 'passed'
    assert message == 'All dependencies succeeded: changes, suites'


def test_an_allowed_skip_passes(tmp):
    del tmp
    verdict, _ = _gate().decide(_needs(changes='success', suites='skipped'))
    assert verdict == 'passed'


def test_a_completed_failure_fails_with_the_standing_message(tmp):
    del tmp
    verdict, message = _gate().decide(
        _needs(changes='success', suites='failure'))
    assert verdict == 'failed'
    assert message == 'Dependencies not successful: suites=failure'


def test_a_strict_dependency_skipped_fails(tmp):
    del tmp
    verdict, message = _gate().decide(
        _needs(changes='success', pycodestyle='skipped'))
    assert verdict == 'strict-skipped'
    assert message == 'Dependencies not successful: pycodestyle=skipped'


def test_a_superseded_cancel_passes_and_names_the_newer_run(tmp):
    del tmp
    mod = _gate()
    verdict, message = mod.decide(
        _needs(changes='success', suites='cancelled'), MINE,
        [MINE, NEWER])
    assert verdict == 'cancelled-superseded'
    assert verdict in mod.GREEN
    assert 'https://github.com/o/r/actions/runs/2' in message, message


def test_a_deliberate_cancel_fails(tmp):
    del tmp
    mod = _gate()
    verdict, message = mod.decide(_needs(suites='cancelled'), MINE, [MINE])
    assert verdict == 'cancelled-deliberate'
    assert verdict not in mod.GREEN
    assert 'deliberate' in message and 'suites=cancelled' in message


def test_a_failed_query_fails_conservatively(tmp):
    del tmp
    mod = _gate()
    verdict, message = mod.decide(_needs(suites='cancelled'), None, None)
    assert verdict == 'query-failed'
    assert verdict not in mod.GREEN
    assert 'query failed' in message, message


def test_only_a_cancelled_dependency_needs_the_query(tmp):
    del tmp
    calls, read = _recorder({}, [])
    verdict, _ = _gate().evaluate(_needs(changes='failure'),
                                  'o/r', '1', 'main', read)
    assert verdict == 'failed'
    assert calls == []


def test_missing_context_fails_without_a_query(tmp):
    del tmp
    calls, read = _recorder({}, [])
    verdict, message = _gate().evaluate(_needs(suites='cancelled'),
                                        '', '', '', read)
    assert verdict == 'query-failed'
    assert 'query failed' in message, message
    assert calls == []


def test_an_older_failure_stays_red_beside_a_newer_run(tmp):
    del tmp
    verdict, _ = _gate().decide(_needs(suites='failure'), MINE,
                                [MINE, NEWER])
    assert verdict == 'failed'


def test_a_strict_skip_is_not_excused_by_supersession(tmp):
    del tmp
    verdict, _ = _gate().decide(_needs(changes='skipped'), MINE,
                                [MINE, NEWER])
    assert verdict == 'strict-skipped'


def test_equal_start_ties_break_by_id_both_ways(tmp):
    del tmp
    mod = _gate()
    stamp = '2026-09-07T10:00:00Z'
    older_id = _run(9, None, stamp, status='in_progress')
    newer_id = _run(10, 'success', stamp)
    assert mod.superseded(older_id, [older_id, newer_id])
    assert not mod.superseded(newer_id, [older_id, newer_id])


def test_created_at_stands_in_for_a_missing_started_at(tmp):
    del tmp
    mod = _gate()
    started = _run(1, None, '2026-09-07T10:00:00Z', status='in_progress')
    created = _run(2, None, None, created_at='2026-09-07T10:05:00Z')
    assert mod.superseded(started, [started, created])
    assert not mod.superseded(created, [started, created])


def test_equal_keys_are_not_superseded(tmp):
    del tmp
    mod = _gate()
    stamp = '2026-09-07T10:00:00Z'
    mine = _run(7, None, stamp, status='in_progress')
    twin = _run(7, 'success', stamp)
    assert not mod.superseded(mine, [mine, twin])


def test_a_newer_run_of_another_workflow_does_not_supersede(tmp):
    del tmp
    mod = _gate()
    assert not mod.superseded(
        MINE, [MINE, _run(2, 'success', '2026-09-07T10:05:00Z',
                          workflow=22)])


def test_naive_timestamps_are_treated_as_utc(tmp):
    del tmp
    mod = _gate()
    mine = _run(1, None, '2026-09-07T10:00:00', status='in_progress')
    east = _run(2, 'success', '2026-09-07T11:00:00+02:00')
    assert not mod.superseded(mine, [mine, east])
    later = _run(3, 'success', '2026-09-07T13:00:00')
    assert mod.superseded(mine, [mine, later])


def test_the_queries_target_the_own_run_and_this_workflow(tmp):
    del tmp
    mod = _gate()
    calls, read = _recorder(MINE, [{'workflow_runs': [MINE, NEWER]}])
    verdict, message = mod.evaluate(_needs(suites='cancelled'),
                                    'o/r', '1', 'fix/x', read)
    assert verdict == 'cancelled-superseded', message
    assert ['gh', 'api', '-H', 'Cache-Control: no-cache',
            'repos/o/r/actions/runs/1'] in calls
    assert ['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
            'repos/o/r/actions/workflows/.github/workflows/tests.yml/runs'
            '?branch=fix%2Fx&per_page=100'] in calls


def test_paginated_pages_are_read_as_concatenated_json(tmp):
    del tmp
    mod = _gate()
    calls, read = _recorder(
        MINE, [{'workflow_runs': [MINE]}, {'workflow_runs': [NEWER]}])
    verdict, _ = mod.evaluate(_needs(suites='cancelled'),
                              'o/r', '1', 'main', read)
    assert verdict == 'cancelled-superseded'
    assert len(calls) == 2


def test_a_failed_gh_read_answers_query_failed(tmp):
    del tmp
    mod = _gate()

    def read(_argv):
        raise mod.QueryError('refused')

    verdict, message = mod.evaluate(_needs(suites='cancelled'),
                                    'o/r', '1', 'main', read)
    assert verdict == 'query-failed'
    assert 'query failed' in message, message


def test_a_malformed_started_at_sorts_oldest_and_ignores_created_at(tmp):
    del tmp
    mod = _gate()
    broken = _run(7, None, 'definitely-not-a-stamp',
                  created_at='2026-09-07T10:05:00Z', status='in_progress')
    earlier = _run(8, 'success', '2026-09-07T09:00:00Z')
    assert mod._started_key(broken) == (mod.OLDEST, 7)
    assert mod.superseded(broken, [broken, earlier])
    assert not mod.superseded(earlier, [broken, earlier])


def test_the_raw_decoder_answers_an_empty_list_for_an_empty_payload(tmp):
    del tmp
    decode = _gate()._decode
    assert decode('') == []
    assert decode(' \n\t ') == []


def test_the_raw_decoder_reads_whitespace_separated_documents(tmp):
    del tmp
    assert _gate()._decode('{"a": 1}\n  [2]') == [{'a': 1}, [2]]


def test_the_raw_decoder_refuses_garbage_after_a_valid_document(tmp):
    del tmp
    mod = _gate()
    error = _query_error(mod, lambda: mod._decode('{"a": 1} oops'))
    assert 'unparseable gh output' in str(error), str(error)


def test_gh_read_turns_a_timeout_or_oserror_into_a_query_error(tmp):
    del tmp
    mod = _gate()
    for effect in (subprocess.TimeoutExpired(cmd='gh', timeout=120),
                   OSError(5, 'input/output error')):
        with mock.patch.object(mod.subprocess, 'run', side_effect=effect):
            error = _query_error(mod, lambda: mod.gh_read(['gh', 'api']))
        assert 'gh failed' in str(error), str(error)


def test_gh_read_refuses_a_nonzero_exit_with_the_clipped_stderr(tmp):
    del tmp
    mod = _gate()
    proc = mock.Mock(returncode=1, stdout='irrelevant',
                     stderr=' ' + 'e' * 501)
    run = mock.Mock(return_value=proc)
    with mock.patch.object(mod.subprocess, 'run', run):
        error = _query_error(mod, lambda: mod.gh_read(['gh', 'api']))
    assert str(error) == 'e' * 400, str(error)


def test_gh_read_returns_the_stdout_when_gh_exits_zero(tmp):
    del tmp
    mod = _gate()
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
            green = state == 'success' or (
                state == 'skipped' and name not in STRICT_JOBS)
            assert (verdict in mod.GREEN) is green, (name, state, verdict)
            assert verdict == {
                'success': 'passed',
                'failure': 'failed',
                'skipped': 'passed' if name not in STRICT_JOBS
                else 'strict-skipped',
                'cancelled': 'query-failed',
            }[state], (name, state, verdict)


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
                    if 'cancelled' in states:
                        contexts = JOINT_CONTEXTS
                    else:
                        contexts = NO_CANCEL_CONTEXT
                    for expected, mine, runs in contexts:
                        verdict, message = mod.decide(needs, mine, runs)
                        assert (verdict, message) == _joint_expectation(
                            names, states, expected), (
                            names, states, expected, verdict, message)


def test_main_exits_zero_only_for_green_verdicts(tmp):
    del tmp
    mod = _gate()
    calls, read = _recorder(MINE, [{'workflow_runs': [MINE, NEWER]}])
    cases = (
        (json.dumps(_needs(suites='cancelled')), 0),
        (json.dumps(_needs(suites='failure')), 1),
        (json.dumps(_needs(changes='skipped')), 1),
        ('{', 1),
    )
    for needs_json, expected in cases:
        saved = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update({
                'NEEDS_JSON': needs_json,
                'REPOSITORY': 'o/r',
                'RUN_ID': '1',
                'HEAD_BRANCH': 'main',
            })
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(mod, 'gh_read', read):
                with contextlib.redirect_stdout(out):
                    with contextlib.redirect_stderr(err):
                        code = mod.main()
            assert code == expected, (needs_json, code, err.getvalue())
            assert bool(out.getvalue()) is (expected == 0), out.getvalue()
        finally:
            os.environ.clear()
            os.environ.update(saved)
    assert calls, 'the cancelled case never queried'


def test_the_aggregate_job_runs_the_module(tmp):
    del tmp
    job = complete_job_mapping(_tests_yml(), 'aggregate')
    checkouts = [step for step in job['steps']
                 if str(step.get('uses', '')).startswith('actions/checkout')]
    assert len(checkouts) == 1, job['steps']
    assert checkouts[0]['uses'] == CHECKOUT
    assert checkouts[0]['with'] == {'persist-credentials': 'false'}
    assert job['permissions'] == {'contents': 'read', 'actions': 'read'}
    gates = [step for step in job['steps'] if 'run' in step]
    assert len(gates) == 1, job['steps']
    assert gates[0]['run'] == 'python3 scripts/ci/aggregate_gate.py'
    assert '${{' not in gates[0]['run']
    assert gates[0]['env'] == {
        'NEEDS_JSON': '${{ toJSON(needs) }}',
        'GH_TOKEN': '${{ github.token }}',
        'REPOSITORY': '${{ github.repository }}',
        'RUN_ID': '${{ github.run_id }}',
        'HEAD_BRANCH': '${{ github.event.pull_request.head.ref'
                       ' || github.ref_name }}',
    }


def test_the_head_branch_env_falls_back_to_the_pushed_branch(tmp):
    del tmp
    job = complete_job_mapping(_tests_yml(), 'aggregate')
    gates = [step for step in job['steps'] if 'run' in step]
    value = gates[0]['env']['HEAD_BRANCH']
    assert 'github.event.pull_request.head.ref' in value, value
    assert '||' in value, value
    assert 'github.ref_name' in value, value


def test_the_checkout_pin_is_the_one_the_sibling_jobs_use(tmp):
    del tmp
    pins = set(re.findall(r'actions/checkout@([0-9a-f]{40})', _tests_yml()))
    assert pins == {CHECKOUT.split('@')[1]}, pins


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='aggregategate_')


if __name__ == '__main__':
    raise SystemExit(main())
