#!/usr/bin/env python3
"""Executable contracts for the coverage comment workflow."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _coverage_comment_steps import GH_CHECK_STUB  # noqa: E402
import test_coverage_comment_workflow as commenter  # noqa: E402


_BASE_REPO = 'owner/repo'
_FORK_REPO = 'fork-owner/repo'
_PR_NUMBER = '170'
_HEAD_SHA = 'e07e7fa764bdce17a5ced3d36d8706b0f378a00f'
_RESOLVE_STEP = 'Resolve the target pull request from the event'


_GH_FORK_STUB = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ['STUB_CALLS']).open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(args) + chr(10))

target = next((value for value in args if value.startswith('repos/')), '')
if '/commits/' in target:
    # The base repository associates no pull request with a fork's head
    # commit, so only the fork answers this query.
    if target.startswith('repos/' + os.environ['STUB_FORK_REPO'] + '/'):
        print(os.environ['STUB_PR_NUMBER'])
elif '/pulls/' in target:
    print(os.environ['CURRENT_HEAD'])
"""


def _endpoints(calls):
    """Return every repos/ endpoint the recording double was asked for."""
    return [
        value for line in calls.read_text(encoding='utf-8').splitlines()
        for value in json.loads(line) if value.startswith('repos/')]


def _run_resolve_block(tmp, event_numbers):
    """Run the Resolve block against a fork-aware recording double."""
    workdir = Path(tmp) / 'resolve'
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    commenter._write_executable(  # pylint: disable=protected-access
        workdir / 'bin' / 'gh', _GH_FORK_STUB)
    calls = workdir / 'calls.jsonl'
    calls.write_text('', encoding='utf-8')
    output = workdir / 'github-output'
    output.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub',
        'REPO': _BASE_REPO,
        'HEAD_REPO': _FORK_REPO,
        'HEAD_SHA': _HEAD_SHA,
        'EVENT_NUMBERS': event_numbers,
        'GITHUB_OUTPUT': str(output),
        'STUB_CALLS': str(calls),
        'STUB_FORK_REPO': _FORK_REPO,
        'STUB_PR_NUMBER': _PR_NUMBER,
        'CURRENT_HEAD': _HEAD_SHA,
    }
    workflow = commenter._workflow()  # pylint: disable=protected-access
    script = commenter._run_block(  # pylint: disable=protected-access
        workflow, _RESOLVE_STEP)
    result = commenter._run_shell_block(  # pylint: disable=protected-access
        workdir, script, env)
    return result, calls, output


def test_the_fork_fallback_asks_the_head_repository(tmp):
    """A fork run resolves through the fork's own commit association."""
    result, calls, output = _run_resolve_block(tmp, '[]')
    assert result.returncode == 0, (result.stdout, result.stderr)
    text = output.read_text(encoding='utf-8')
    assert f'number={_PR_NUMBER}' in text, text
    assert 'stale=false' in text, text
    endpoints = _endpoints(calls)
    fork_query = f'repos/{_FORK_REPO}/commits/{_HEAD_SHA}/pulls'
    assert fork_query in endpoints, endpoints
    assert not any(
        endpoint.startswith(f'repos/{_BASE_REPO}/commits/')
        for endpoint in endpoints), endpoints


def test_the_base_repository_still_verifies_the_candidate(tmp):
    """The head-SHA verification read stays on the base repository."""
    result, calls, _output = _run_resolve_block(tmp, '[]')
    assert result.returncode == 0, (result.stdout, result.stderr)
    endpoints = _endpoints(calls)
    assert f'repos/{_BASE_REPO}/pulls/{_PR_NUMBER}' in endpoints, endpoints
    assert not any(
        endpoint.startswith(f'repos/{_FORK_REPO}/pulls/')
        for endpoint in endpoints), endpoints


def test_a_same_repository_run_never_reaches_the_fallback(tmp):
    """A non-empty event answers without any commit query at all."""
    result, calls, output = _run_resolve_block(tmp, f'[{_PR_NUMBER}]')
    assert result.returncode == 0, (result.stdout, result.stderr)
    endpoints = _endpoints(calls)
    assert not any('/commits/' in endpoint for endpoint in endpoints), \
        endpoints
    text = output.read_text(encoding='utf-8')
    assert f'number={_PR_NUMBER}' in text, text
    assert 'stale=false' in text, text


_MISSING_HEAD_SHA = 'b' * 40
_MARKER = '<!-- daedalus-diff-coverage -->'
_PRIOR_MARKER = [{
    'id': 1,
    'user': {'login': 'github-actions[bot]'},
    'body': _MARKER,
}]


def _run_publication(tmp, status):
    workdir = Path(tmp) / 'publish'
    (workdir / 'bin').mkdir(parents=True, exist_ok=True)
    commenter._write_executable(workdir / 'bin' / 'gh', GH_CHECK_STUB)
    state = workdir / 'state.json'
    state.write_text(json.dumps({'checks': []}), encoding='utf-8')
    calls = workdir / 'calls.jsonl'
    calls.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub',
        'REPO': 'owner/repo',
        'HEAD_SHA': _MISSING_HEAD_SHA,
        'RUN_URL': 'https://github.com/owner/repo/actions/runs/7',
        'STATUS': status,
        'STUB_STATE': str(state),
        'STUB_CALLS': str(calls),
    }
    result = commenter._run_shell_block(
        workdir,
        commenter._run_block(commenter._workflow(), 'Publish coverage check'),
        env)
    return result, json.loads(state.read_text(encoding='utf-8'))


def _run_missing_artifact_case(tmp, prior_comments):
    artifact, output = commenter._run_artifact_check(
        tmp, {'total_count': 0, 'artifacts': []})
    assert artifact.returncode == 0, (artifact.stdout, artifact.stderr)
    assert output == '', output

    resolved, _state, _calls, output = commenter._run_comment_block(
        tmp, 'Resolve the target pull request from the event', state=[],
        head_sha=_MISSING_HEAD_SHA, current_head=_MISSING_HEAD_SHA)
    assert resolved.returncode == 0, (resolved.stdout, resolved.stderr)
    text = output.read_text(encoding='utf-8')
    assert 'number=170' in text, text
    assert 'stale=false' in text, text

    context = {
        'steps': {
            'artifact': {'outputs': {}},
            'pr': {'outputs': {'stale': 'false'}},
        },
        'status': {'success': True, 'failure': False, 'cancelled': False},
    }
    condition = commenter._step_condition(
        commenter._workflow(), 'Mark missing patch coverage')
    assert evaluate_if(condition, context) is True, condition

    marked, comments, _calls, _output = commenter._run_comment_block(
        tmp, 'Mark missing patch coverage', state=prior_comments,
        head_sha=_MISSING_HEAD_SHA, current_head=_MISSING_HEAD_SHA)
    status = 'failure' if marked.returncode else 'success'
    published, checks = _run_publication(tmp, status)
    assert published.returncode == 0, (published.stdout, published.stderr)
    assert checks['checks'][0]['conclusion'] != 'success', checks
    return marked, comments, checks


def test_missing_artifact_fails_named_check_without_prior_marker(tmp):
    marked, comments, checks = _run_missing_artifact_case(
        Path(tmp) / 'without-marker', [])
    assert marked.returncode != 0, (marked.stdout, marked.stderr)
    assert comments == [], comments
    assert checks['checks'][0]['conclusion'] == 'failure', checks


def test_missing_artifact_fails_named_check_with_prior_marker(tmp):
    marked, comments, checks = _run_missing_artifact_case(
        Path(tmp) / 'with-marker', _PRIOR_MARKER)
    assert marked.returncode != 0, (marked.stdout, marked.stderr)
    assert len(comments) == 1, comments
    assert 'not measured' in comments[0]['body'], comments
    assert checks['checks'][0]['conclusion'] == 'failure', checks


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
