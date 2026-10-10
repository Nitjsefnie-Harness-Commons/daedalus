#!/usr/bin/env python3
"""Pin the claim action caller's prefilter, scope and job shape."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfjobs import jobs_mapping  # noqa: E402
from _yamlsteps import (  # noqa: E402
    complete_job_mapping,
    step_mappings,
    workflow_mapping,
)

# The prefilter, as the operator's semantics: a bot never starts a runner,
# and a body carrying a command word is a candidate. The closed-issue and
# pull-request checks are the action's, not this job's.
CLAIM_PREFILTER = (
    "github.event.comment.user.type != 'Bot' "
    "&& (contains(github.event.comment.body, '/claim') "
    "|| contains(github.event.comment.body, '/unclaim') "
    "|| contains(github.event.comment.body, '/release'))"
)


def _claim_text():
    """Return the tracked claim workflow's own bytes."""
    return (_util.ROOT / '.github' / 'workflows' / 'claim.yml').read_text(
        encoding='utf-8')


def _claim_job(workflow):
    """Return claim.yml's one job, decoded whole."""
    jobs = jobs_mapping(workflow)
    assert jobs is not None and set(jobs) == {'claim'}, (
        'claim.yml must declare exactly one job named claim')
    job = complete_job_mapping(workflow, 'claim')
    assert job is not None, 'claim.yml must declare claim as a job mapping'
    return job


def test_the_claim_trigger_is_a_new_issue_comment(tmp):
    del tmp
    decoded = workflow_mapping(_claim_text())
    assert decoded.get('on') == {
        'issue_comment': {'types': ['created']},
    }, 'claim must run only for newly created issue comments'


def test_the_claim_prefilter_matches_the_operator_semantics(tmp):
    del tmp
    workflow = _claim_text()
    condition = _claim_job(workflow).get('if')
    assert isinstance(condition, str), 'claim must declare an if scalar'
    assert ' '.join(condition.split()) == CLAIM_PREFILTER, (
        'claim if must match the reference prefilter exactly: a bot never '
        'starts a runner, a body carrying a command word is a candidate, and '
        'the closed-issue and pull-request checks belong to the action, which '
        'declines them loudly instead of the job skipping the run')


def test_claim_scopes_its_permission_to_the_job(tmp):
    del tmp
    workflow = _claim_text()
    decoded = workflow_mapping(workflow)
    job = _claim_job(workflow)
    assert 'permissions' not in decoded, (
        'claim must declare no workflow-level permissions block, or the job '
        'scope that replaces it is decorative')
    assert 'permissions' in job, (
        'claim must declare the scope on the job, not inherit it')
    effective = job.get('permissions')
    assert effective == {'issues': 'write', 'pull-requests': 'write'}, (
        "claim's effective scope must be exactly issues: write plus "
        'pull-requests: write, which the two assertions above leave on the '
        'job: a claim on a pull request has to be answerable on it')


# The claim action's own inputs (max-claims, expire) are contract and are
# deliberately unread — fleet-rules "Merging and CI" with:-inputs ruling.


def test_claim_keeps_its_serialization_and_runner_shape(tmp):
    del tmp
    workflow = _claim_text()
    decoded = workflow_mapping(workflow)
    assert decoded.get('concurrency') == {
        'group': 'claim-${{ github.event.issue.number }}',
        'cancel-in-progress': 'false',
        'queue': 'max',
    }, ('claim concurrency must serialize per issue, never cancel a run, and '
        'queue a pending one instead of replacing it')
    job = _claim_job(workflow)
    assert job.get('runs-on') == 'ubuntu-latest', (
        'claim must remain a runner job')
    assert job.get('timeout-minutes') == '5', (
        'claim runner must keep its five-minute timeout')
    assert not re.search(r'^\s*(?:-\s+)?run:', workflow, re.MULTILINE), (
        'claim.yml must not contain a run block')
    # Exactly one action step: a second `uses:` in a job scoped
    # issues/pull-requests: write must be a named failure (the invariant
    # the deleted policy test kept alive; it asserts no with: input, no
    # value, no revision — pure workflow-derived structure).
    steps = step_mappings(workflow, 'claim')
    assert steps is not None and len(steps) == 1, (
        'claim.yml must have exactly one action step')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='claimworkflow_'))
