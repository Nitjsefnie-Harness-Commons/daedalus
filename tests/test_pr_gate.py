#!/usr/bin/env python3
"""Pull-request workflow shell and state-table behavior."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    BOT, CLOSE_MARKER, MARKER_COMMENT, _GH_STUB,
    _assert_commented_then_closed, _assert_commented_then_reopened,
    _assert_no_mutation, _body_from, _closed_by, _issue, _issue_lookups,
    _layout_body, _run_workflow, _valid_body, _workflow, _workflow_script,
)
from _repo import ROOT  # noqa: E402
from _yamlread import job_scalar, top_level_mapping  # noqa: E402
from _yamlsteps import step_mappings  # noqa: E402
from _workflows import _workflow_triggers  # noqa: E402


def test_workflow_keeps_its_body_gate_shape(tmp):
    del tmp
    workflow = _workflow()
    assert top_level_mapping(workflow, 'permissions') == {
        'pull-requests': 'write',
        'issues': 'read',
    }
    triggers = _workflow_triggers(workflow, 'pr-gate.yml')
    assert set(triggers) == {'pull_request_target'}, sorted(triggers)
    types = next(
        line.partition('types:')[2].strip()
        for line in triggers['pull_request_target']
        if line.strip().startswith('types:'))
    assert types == '[opened, edited, reopened]', types
    assert ('pull_request_target:  '
            '# zizmor: ignore[dangerous-triggers]') in workflow
    condition = job_scalar(workflow, 'gate', 'if')
    assert condition == "github.event.pull_request.user.type != 'Bot'"
    steps = step_mappings(workflow, 'gate')
    checkout = [step for step in steps
                if str(step.get('uses', '')).startswith('actions/checkout@')]
    assert len(checkout) == 1, steps
    assert checkout[0]['uses'] == (
        'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1')
    assert checkout[0].get('with') == {'persist-credentials': 'false'}
    assert '${{' not in _workflow_script(), (
        'an expression is interpolated into contributor-controlled shell')
    script = _workflow_script()
    assert '$(cat <<' not in script, script
    assert '-F "body=@$comment_file"' in script
    assert 'leave the pull request open rather than close it silently' \
        in script.replace('\n# ', ' ')


def test_pull_request_gate_uses_general_names(tmp):
    del tmp
    expected = (
        ROOT / '.github' / 'workflows' / 'pr-gate.yml',
        ROOT / 'scripts' / 'ci' / 'pr_body.py',
        ROOT / 'tests' / 'test_pr_gate.py',
    )
    retired = (
        ROOT / '.github' / 'workflows' / 'pr-claim.yml',
        ROOT / 'scripts' / 'ci' / 'pr_claim.py',
        ROOT / 'tests' / 'test_pr_claim.py',
    )
    assert all(path.is_file() for path in expected), expected
    assert not any(path.exists() for path in retired), retired
    assert _workflow().splitlines()[0] == 'name: pr gate'
    contributing = (ROOT / 'CONTRIBUTING.md').read_text(encoding='utf-8')
    assert 'reopens it automatically' in contributing
    assert 'and reopen the same pull request' not in contributing


def _run_stub(tmp, *args):
    workdir = Path(tmp) / 'gh-headers'
    workdir.mkdir(exist_ok=True)
    stub = workdir / 'gh'
    stub.write_text(_GH_STUB, encoding='utf-8')
    calls_path = workdir / 'calls.jsonl'
    calls_path.write_text('', encoding='utf-8')
    fixture_path = workdir / 'issues.json'
    fixture_path.write_text(
        json.dumps({'1': _issue('alice')}), encoding='utf-8')
    env = {
        **os.environ,
        'STUB_ISSUES': str(fixture_path),
        'STUB_CALLS': str(calls_path),
    }
    return subprocess.run(
        [sys.executable, str(stub), *args],
        env=env, capture_output=True, text=True, timeout=30)


def test_gh_stub_models_include_response_headers(tmp):
    for flag in ('--include', '-i'):
        result = _run_stub(
            tmp, 'api', flag, 'repos/owner/repo/issues/1',
            '--jq', '.assignees[].login')
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[1:4] == [
            'cache-control: private, max-age=60',
            'content-type: application/json; charset=utf-8',
            'x-github-media-type: github.v3; format=json',
        ], result.stdout


def test_gh_stub_rejects_commands_it_does_not_model(tmp):
    result = _run_stub(
        tmp, 'pr', 'reopen', '99', '--repo', 'owner/repo')
    assert result.returncode != 0, result.stdout
    assert 'unsupported gh command' in result.stderr, result.stderr


def test_open_admissible_body_is_left_untouched(tmp):
    calls, result = _run_workflow(
        tmp, _valid_body('Refs #101 and #102'),
        {'101': _issue('bob'), '102': _issue('carol')},
        actor='carol', repo='acme/widget', parser_crlf=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'repos/acme/widget/issues/101' in calls[0], calls
    assert 'repos/acme/widget/issues/102' in calls[1], calls
    _assert_no_mutation(calls)


def test_claimed_literal_markdown_contexts_are_left_untouched(tmp):
    instruction = (
        '<!-- required: bullet list of concrete changes — files, modules, '
        'behavior. -->')
    cases = (
        _valid_body('The token `<!--` is literal; Fixes #101'),
        _valid_body().replace(
            '- One change', f'- Changed `{instruction}` to prose.'),
        _valid_body().replace(
            '- One change', f'    {instruction}\n- A visible change'),
        _valid_body().replace(
            '- One change', '<pre>\n## literal heading\n</pre>\n- A change'),
    )
    for index, body in enumerate(cases):
        calls, result = _run_workflow(
            Path(tmp) / str(index), body, {'101': _issue('alice')})
        assert result.returncode == 0, (result.stdout, result.stderr)
        _assert_no_mutation(calls)


def test_open_body_reports_both_failed_conditions_once(tmp):
    body = _layout_body(
        ('Summary', ''),
        ('Related Issues and Pull Requests', 'Fixes #103'),
        ('Changes', '- One change'), ('Testing', 'Ran tests.'))
    calls, result = _run_workflow(tmp, body, {'103': _issue('alicebob')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(
        calls, 'No checked issue is assigned to you.',
        'Section "Summary" is empty.')


def test_open_body_reports_each_single_failed_condition(tmp):
    unclaimed, first = _run_workflow(
        Path(tmp) / 'claim', _valid_body(), {'101': _issue()})
    malformed, second = _run_workflow(
        Path(tmp) / 'layout', _valid_body().replace('## Summary', '## Notes'),
        {'101': _issue('alice')})
    assert first.returncode == second.returncode == 0
    _assert_commented_then_closed(
        unclaimed, 'No checked issue is assigned to you.')
    _assert_commented_then_closed(
        malformed, 'Section `Notes` is not defined by the template.')


def test_open_rendered_empty_changes_is_closed(tmp):
    body = _valid_body().replace('- One change', '-')
    calls, result = _run_workflow(
        tmp, body, {'101': _issue('alice')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(calls, 'Section "Changes" is empty.')


def test_link_destination_does_not_satisfy_the_claim(tmp):
    calls, result = _run_workflow(
        tmp, _valid_body('[documentation](#101)'),
        {'101': _issue('alice')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(
        calls, 'No checked issue is assigned to you.')


def test_unknown_section_name_cannot_post_a_live_issue_reference(tmp):
    name = 'Notes ``for #255``'
    body = _valid_body().replace(
        '## Testing', f'## {name}\nUnknown.\n\n## Testing')
    calls, result = _run_workflow(
        tmp, body, {'101': _issue('alice')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    endpoint = 'repos/owner/repo/issues/99/comments'
    comment = _body_from(next(call for call in calls if endpoint in call))
    quoted = '```Notes ``for #255`````'
    assert f'Section {quoted} is not defined by the template.' in comment
    without_name = comment.replace(quoted, '')
    assert re.search(r'#[0-9]', without_name) is None, comment


def test_closed_owned_admissible_body_is_reopened(tmp):
    calls, result = _run_workflow(
        tmp, _valid_body(), {'101': _issue('alice')},
        pull={'state': 'closed'}, history={
            'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_reopened(calls)


def test_closed_by_someone_else_is_never_changed(tmp):
    cases = [('valid', _valid_body(), _closed_by('maintainer')),
             ('invalid', 'bad body', _closed_by('maintainer')),
             ('unknown', _valid_body(),
              _closed_by(BOT) + [{'event': 'closed', 'actor': {}}])]
    for name, body, timeline in cases:
        calls, result = _run_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            pull={'state': 'closed'}, history={
                'timeline': timeline, 'comments': MARKER_COMMENT})
        assert result.returncode == 0, (name, result.stderr)
        _assert_no_mutation(calls)


def test_closed_owned_without_marker_is_not_changed(tmp):
    spoof = [{'id': 8, 'user': {'login': 'alice'}, 'body': CLOSE_MARKER}]
    for name, comments in [('absent', ()), ('spoofed', spoof)]:
        calls, result = _run_workflow(
            Path(tmp) / name, _valid_body(), {'101': _issue('alice')},
            pull={'state': 'closed'}, history={
                'timeline': _closed_by(BOT), 'comments': comments})
        assert result.returncode == 0, (result.stdout, result.stderr)
        _assert_no_mutation(calls)


def test_closed_owned_inadmissible_body_stays_closed(tmp):
    calls, result = _run_workflow(
        tmp, 'bad body', {}, pull={'state': 'closed'}, history={
            'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_no_mutation(calls)


def test_merged_pull_request_is_never_touched(tmp):
    for name, body in [('valid', _valid_body()), ('invalid', 'bad body')]:
        calls, result = _run_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            pull={'state': 'closed', 'merged': 'true'}, history={
                'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT})
        assert result.returncode == 0, (name, result.stderr)
        assert calls == [], calls


def test_unreadable_ownership_evidence_never_reopens(tmp):
    first, one = _run_workflow(
        Path(tmp) / 'timeline', _valid_body(), {}, pull={'state': 'closed'},
        history={'timeline_status': 502})
    second, two = _run_workflow(
        Path(tmp) / 'marker', _valid_body(), {}, pull={'state': 'closed'},
        history={'timeline': _closed_by(BOT), 'comments_status': 502})
    assert one.returncode == two.returncode == 0
    _assert_no_mutation(first)
    _assert_no_mutation(second)


def test_reference_limit_checks_twenty_and_tells_the_truth(tmp):
    references = ' '.join(f'#{number}' for number in range(130, 151))
    issues = {str(number): _issue('alice' if number == 130 else 'bob')
              for number in range(130, 150)}
    calls, result = _run_workflow(tmp, _valid_body(references), issues)
    assert result.returncode == 0, (result.stdout, result.stderr)
    lookups = _issue_lookups(calls)
    assert len(lookups) == 20, lookups
    reason = ('This body names more than 20 issue references, so only the '
              'first 20 were checked.')
    _assert_commented_then_closed(calls, reason)
    assert 'No checked issue is assigned to you.' not in _body_from(calls[-2])


def test_lookup_failures_keep_the_safe_direction(tmp):
    missing, one = _run_workflow(
        Path(tmp) / 'missing', _valid_body('Refs #120 and #121'), {})
    failed, two = _run_workflow(
        Path(tmp) / 'failed', _valid_body('Fixes #122'),
        {'122': {'_http_status': 502}})
    assert one.returncode == 0
    assert len(_issue_lookups(missing)) == 2
    _assert_commented_then_closed(
        missing, 'No checked issue is assigned to you.')
    assert two.returncode != 0, (two.stdout, two.stderr)
    _assert_no_mutation(failed)


def test_pull_request_reference_does_not_satisfy_claim(tmp):
    calls, result = _run_workflow(
        tmp, _valid_body(), {'101': _issue('alice', pull_request=True)})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(
        calls, 'No checked issue is assigned to you.')


def test_failed_explanation_prevents_silent_state_change(tmp):
    calls, result = _run_workflow(
        tmp, _valid_body(), {'101': _issue()}, comment_status=502)
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not any(any(arg.startswith('state=') for arg in call)
                   for call in calls), calls


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prgate_')


if __name__ == '__main__':
    raise SystemExit(main())
