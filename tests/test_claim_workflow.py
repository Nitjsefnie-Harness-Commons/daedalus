#!/usr/bin/env python3
"""Pin the claim action caller's prefilter, scope, release and policy."""
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

# The current claim release, pinned by the commit it names.
CLAIM_RELEASE = '2.0.3'
CLAIM_COMMIT = '0c79a0325d8ab789a60c2eeaf751690d2875c39c'

# The policy claim's reference block passes: per-role caps on concurrent
# claims, and the days an idle claim survives.
CLAIM_POLICY = {
    'max-claims': 'read=2, triage=4, write=6, maintain=10, admin=-1',
    'expire': '7',
}

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


def _claim_step(workflow):
    """Return claim.yml's one action step, decoded whole."""
    steps = step_mappings(workflow, 'claim')
    assert steps is not None and len(steps) == 1, (
        'claim.yml must have exactly one action step')
    return steps[0]


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


def test_claim_pins_the_current_release(tmp):
    del tmp
    workflow = _claim_text()
    uses = _claim_step(workflow).get('uses')
    assert uses == f'Nitjsefnie-Actions/claim@{CLAIM_COMMIT}', (
        'claim must pin the current release by its commit')
    line = re.search(r'^\s*-\s+uses:.*$', workflow, re.MULTILINE)
    assert line and re.search(
        rf'#\s+v{re.escape(CLAIM_RELEASE)}\b', line.group()), (
        f'claim must name v{CLAIM_RELEASE} in the comment on the pin, so the '
        'two cannot drift apart silently')


def test_claim_passes_the_reference_claim_policy(tmp):
    del tmp
    workflow = _claim_text()
    assert _claim_step(workflow).get('with') == CLAIM_POLICY, (
        'claim must pass the reference per-role caps and expiry, so one '
        'account cannot hold unlimited claims and an idle claim cannot '
        'outlive the issue it was taken for')


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='claimworkflow_'))
