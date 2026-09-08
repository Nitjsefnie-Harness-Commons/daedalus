#!/usr/bin/env python3
"""Pin the shared admission action's privileged caller contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402


def _workflow():
    path = _util.ROOT / '.github' / 'workflows' / 'pr-gate.yml'
    return workflow_mapping(path.read_text(encoding='utf-8'))


def test_only_admission_events_run_the_gate(_tmp):
    workflow = _workflow()
    assert set(workflow) == {
        'name', 'on', 'permissions', 'concurrency', 'jobs'}, workflow
    assert workflow['on'] == {
        'pull_request_target': {'types': ['opened', 'edited', 'reopened']},
    }, 'admission must run only on opened, edited and reopened PRs'


def test_gate_token_has_exactly_the_required_permissions(_tmp):
    assert _workflow()['permissions'] == {
        'contents': 'read', 'issues': 'read', 'pull-requests': 'write',
    }, 'admission token must retain exactly its three required scopes'


def test_each_pull_request_queues_without_cancelling(_tmp):
    assert _workflow()['concurrency'] == {
        'group': 'pr-gate-${{ github.event.pull_request.number }}',
        'cancel-in-progress': 'false',
    }, 'admission must serialize each PR without cancelling repairs'


def test_only_one_bounded_runner_job_excludes_bots(_tmp):
    jobs = _workflow()['jobs']
    assert set(jobs) == {'gate'}, 'admission must have only the gate job'
    job = jobs['gate']
    assert set(job) == {'if', 'runs-on', 'timeout-minutes', 'steps'}, (
        'gate must not override permissions or add job controls')
    assert job['if'] == "github.event.pull_request.user.type != 'Bot'", (
        'gate must exclude exactly Bot authors')
    assert job['runs-on'] == 'ubuntu-latest', 'gate must run on Ubuntu'
    assert job['timeout-minutes'] == '5', 'gate must keep its timeout'


def test_gate_calls_only_the_reviewed_action_with_exact_inputs(_tmp):
    assert _workflow()['jobs']['gate']['steps'] == [{
        'uses': ('Nitjsefnie-Actions/pr-gate@'
                 '44437212f1b931f53433b16455bb05aff67ad21e'),
        'with': {
            'github-token': '${{ github.token }}',
            'repository': '${{ github.repository }}',
            'pull-request-number': '${{ github.event.pull_request.number }}',
            'pull-request-author': (
                '${{ github.event.pull_request.user.login }}'),
        },
    }], 'gate must call only the immutable shared action with exact inputs'


def test_consumer_template_exists_at_the_action_default_path(_tmp):
    template = _util.ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md'
    assert template.is_file(), 'the shared action needs the consumer template'
    assert template.read_text(encoding='utf-8').strip(), (
        'the consumer template must not be empty')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='prgate_'))
