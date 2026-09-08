#!/usr/bin/env python3
"""Pin the claim action caller's permissions, prefilter and runner shape."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfjobs import jobs_mapping  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402


def test_the_claim_workflow_keeps_its_least_privilege_shape(tmp):
    del tmp
    workflow = (_util.ROOT / '.github' / 'workflows' / 'claim.yml').read_text(
        encoding='utf-8')
    decoded = workflow_mapping(workflow)
    assert decoded.get('on') == {
        'issue_comment': {'types': ['created']},
    }, 'claim must run only for newly created issue comments'
    assert decoded.get('permissions') == {'issues': 'write'}, (
        'claim token must grant exactly issues: write')
    jobs = jobs_mapping(workflow)
    assert jobs is not None and set(jobs) == {'claim'}
    job = jobs['claim']
    assert 'permissions' not in job, (
        'claim job must inherit workflow permissions without an override')
    # Two claims racing must both be answered, so the group never cancels.
    concurrency = decoded.get('concurrency')
    assert isinstance(concurrency, dict), 'claim must declare concurrency'
    assert concurrency.get('cancel-in-progress') == 'false', (
        'claim concurrency must not cancel an in-progress run')
    condition = job.get('if')
    assert isinstance(condition, str), 'claim must declare an if scalar'
    expected_condition = (
        'github.event.issue.pull_request == null '
        "&& github.event.issue.state == 'open' "
        "&& github.event.comment.user.type != 'Bot' "
        "&& (contains(github.event.comment.body, '/claim') "
        "|| contains(github.event.comment.body, '/unclaim') "
        "|| contains(github.event.comment.body, '/release'))"
    )
    assert ' '.join(condition.split()) == expected_condition, (
        'claim if must exactly match the guarded command prefilter')
    assert not re.search(r'^\s*(?:-\s+)?run:', workflow, re.MULTILINE), (
        'claim.yml must not contain a run block')
    _, marker, steps = workflow.partition('    steps:\n')
    assert marker, 'claim.yml must declare steps'
    entries = [line for line in steps.splitlines() if line.strip()]
    assert len(entries) == 1, 'claim.yml must have exactly one action step'
    assert re.fullmatch(
        r'      - uses: Nitjsefnie-Actions/claim@[0-9a-fA-F]{40}'
        r'  # v[0-9]+\.[0-9]+\.[0-9]+', entries[0]), (
            'claim action must use a full SHA pin with a version comment')
    assert job.get('runs-on') == 'ubuntu-latest', (
        'claim must remain a runner job')
    assert job.get('timeout-minutes') == '5', (
        'claim runner must keep its five-minute timeout')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='claimworkflow_'))
