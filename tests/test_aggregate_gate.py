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
    older = _run(1, None, None, status='in_progress',
                 created_at='2026-09-07T10:00:00Z')
    newer = _run(2, None, None, created_at='2026-09-07T10:05:00Z')
    assert mod.superseded(older, [older, newer])
    assert not mod.superseded(newer, [older, newer])


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
