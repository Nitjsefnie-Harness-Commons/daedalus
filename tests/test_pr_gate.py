#!/usr/bin/env python3
"""Pull-request gate state transitions and write ordering."""
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    BOT, GITHUB_HTML, MARKER, TEMPLATE,
    _api, _assert_gate_message, _assert_no_writes, _closed_event,
    _capture, _comment_body, _execute, _gate_comment, _gate_module,
    _html_body,
    _inline_marker_comment, _issue, _issue_gets, _issue_html, _layout_body,
    _markdown_code_spans, _PaginationApi, _recorded_writes, _run_script,
    _runtime_error, _script_fixtures, _text_html, _valid_body, _valid_html,
    _write_gh_stub, _write_sequence,
)
from _repo import ROOT  # noqa: E402
from _workflows import (  # noqa: E402
    _entry, _flow_sequence, _workflow_triggers,
)
from _yamlread import job_scalar, top_level_mapping  # noqa: E402
from _yamlsteps import step_mappings  # noqa: E402


OPEN_FIRST = (
    '@alice — this pull request needs changes before it can be reviewed.')
CLOSED_FIRST = (
    '@alice — closing this automatically; it is recoverable, read on.')
RESOLVED_FIRST = (
    '@alice — every condition now passes; nothing further is needed '
    'from you.')
REOPEN_FIRST = (
    '@alice — the body now names a claimed issue and matches the pull '
    'request')


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'pr-gate.yml').read_text(
        encoding='utf-8')


def test_admissible_open_without_prior_comment_does_not_write(tmp):
    del tmp
    code, writes, output, _error = _execute(_api(), _valid_body())
    assert code == 0
    _assert_no_writes(writes)
    assert '101' in output, output


def test_admissible_open_resolves_a_prior_gate_comment(tmp):
    del tmp
    api = _api(comments=[_gate_comment()])
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('PATCH', 'repos/owner/repo/issues/comments/7')]
    _assert_gate_message(writes[0], RESOLVED_FIRST)


def test_inline_marker_mention_is_not_a_closed_gate_comment(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_inline_marker_comment()],
        timeline=[_closed_event()])
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)
    assert api.pull['state'] == 'closed'


def test_inline_marker_mention_does_not_replace_a_new_comment(tmp):
    del tmp
    api = _api(
        comments=[_inline_marker_comment()],
        issues={'101': _issue('bob')})
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]


def test_contributor_marker_comment_is_not_selected(tmp):
    del tmp
    comment = {
        'id': 55,
        'user': {'login': 'alice'},
        'body': f'contributor note\n{MARKER}',
    }
    code, writes, _output, _error = _execute(
        _api(comments=[comment]), _valid_body())
    assert code == 0
    _assert_no_writes(writes)


def test_earliest_bot_marker_comment_is_selected(tmp):
    del tmp
    later = {
        'id': 8,
        'user': {'login': BOT},
        'body': f'later gate message\n{MARKER}',
    }
    code, writes, _output, _error = _execute(
        _api(comments=[_gate_comment(), later]), _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('PATCH', 'repos/owner/repo/issues/comments/7')]


def test_unclaimed_issue_comments_without_closing(tmp):
    del tmp
    api = _api(issues={'101': _issue('bob')})
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    _assert_gate_message(
        writes[0], OPEN_FIRST, ['No checked issue is assigned to you.'])


def test_missing_issue_comments_without_closing(tmp):
    del tmp
    code, writes, _output, _error = _execute(
        _api(issues={}), _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    _assert_gate_message(
        writes[0], OPEN_FIRST, ['No checked issue is assigned to you.'])


def test_pull_request_reference_comments_without_closing(tmp):
    del tmp
    api = _api(issues={'101': _issue(pull_request=True)})
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    _assert_gate_message(
        writes[0], OPEN_FIRST, ['No checked issue is assigned to you.'])


def test_layout_failure_with_reference_comments_then_closes(tmp):
    del tmp
    body = _layout_body(
        ('Related Issues and Pull Requests', 'Fixes #101'),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))
    rendered = _html_body(
        ('Related Issues and Pull Requests', f'Fixes {_issue_html(101)}'),
        ('Changes', _text_html('One change')),
        ('Testing', _text_html('Ran the suite.')))
    code, writes, _output, _error = _execute(
        _api(rendered=rendered), body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    _assert_gate_message(
        writes[0], CLOSED_FIRST,
        ['Required section "Summary" is missing.'], closed=True)
    assert writes[1][2] == {'state': 'closed'}


def test_related_without_reference_comments_then_closes(tmp):
    del tmp
    body = _valid_body('see the tracker')
    rendered = _valid_html(references=_text_html('see the tracker'))
    code, writes, _output, _error = _execute(
        _api(issues={}, rendered=rendered), body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    _assert_gate_message(
        writes[0], CLOSED_FIRST,
        ['No checked issue is assigned to you.'], closed=True)


def test_reference_outside_related_does_not_protect_from_close(tmp):
    del tmp
    body = _valid_body('none').replace(
        'One sentence.', 'Summary references #101.')
    summary = f'<p dir="auto">Summary references {_issue_html(101)}.</p>'
    rendered = _valid_html(references=_text_html('none')).replace(
        _text_html('One sentence.'), summary)
    code, writes, _output, _error = _execute(
        _api(issues={}, rendered=rendered), body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    _assert_gate_message(
        writes[0], CLOSED_FIRST,
        ['No checked issue is assigned to you.'], closed=True)


def test_none_body_reports_all_required_sections_and_closes(tmp):
    del tmp
    code, writes, _output, _error = _execute(
        _api(issues={}, rendered='<p dir="auto"></p>'), None)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    reasons = [
        f'Required section "{name}" is missing.' for name in (
            'Summary', 'Related Issues and Pull Requests', 'Changes',
            'Testing')]
    _assert_gate_message(
        writes[0], CLOSED_FIRST,
        [*reasons, 'No checked issue is assigned to you.'], closed=True)


def test_retained_instruction_comment_closes(tmp):
    del tmp
    instruction = re.search(r'<!--.*?-->', TEMPLATE, re.DOTALL).group(0)
    body = _valid_body().replace(
        '- One change', f'- One change\n{instruction}')
    code, writes, _output, _error = _execute(_api(), body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    _assert_gate_message(
        writes[0], CLOSED_FIRST,
        ['Remove the template instruction comments.'], closed=True)


def test_fenced_reference_protects_from_close_without_lookup(tmp):
    del tmp
    body = _valid_body('```\nFixes #101\n```')
    rendered = _valid_html(references=GITHUB_HTML['fenced_code'])
    api = _api(issues={}, rendered=rendered)
    code, writes, _output, _error = _execute(api, body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    _assert_gate_message(
        writes[0], OPEN_FIRST, ['No checked issue is assigned to you.'])
    assert _issue_gets(api) == []


def test_gate_closed_admissible_pull_is_commented_then_reopened(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment(closed=True)],
        timeline=[_closed_event()])
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('PATCH', 'repos/owner/repo/issues/comments/7'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    _assert_gate_message(writes[0], REOPEN_FIRST)
    assert writes[1][2] == {'state': 'open'}


def test_gate_closed_inadmissible_pull_updates_comment_and_stays_closed(tmp):
    del tmp
    body = _valid_body('none')
    rendered = _valid_html(references=_text_html('none'))
    api = _api(
        state='closed', issues={}, comments=[_gate_comment(closed=True)],
        timeline=[_closed_event()], rendered=rendered)
    code, writes, _output, _error = _execute(api, body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('PATCH', 'repos/owner/repo/issues/comments/7')]
    _assert_gate_message(
        writes[0], CLOSED_FIRST,
        ['No checked issue is assigned to you.'], closed=True)


def test_human_closed_pull_is_not_written(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment(closed=True)],
        timeline=[_closed_event('alice')])
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)


def test_bot_timeline_without_closed_marker_is_not_gate_owned(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment()],
        timeline=[_closed_event()])
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)


def test_unreadable_closed_timeline_fails_without_writes(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment(closed=True)],
        timeline=[_closed_event()], fail={'timeline'})
    code, writes, _output, error = _execute(api, _valid_body())
    assert code == 1
    _assert_no_writes(writes)
    assert error == (
        'pr gate failed: GitHub returned 500 for '
        'repos/owner/repo/issues/99/timeline\n')


def test_merged_pull_returns_before_comments_or_render(tmp):
    del tmp
    api = _api(merged=True, fail={'comments', 'markdown'})
    code, writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)
    assert api.calls == [
        ('GET', 'repos/owner/repo/pulls/99', None)]


def test_unusable_render_fails_without_writes(tmp):
    del tmp
    cases = (
        (
            _api(fail={'markdown'}),
            'pr gate failed: GitHub returned 500 for markdown\n',
        ),
        (
            _api(rendered='<h2>Summary</h2><p'),
            'pr gate failed: could not analyze rendered body: '
            'rendered HTML is structurally incomplete\n',
        ),
    )
    for api, expected in cases:
        code, writes, _output, error = _execute(api, _valid_body())
        assert code == 1
        _assert_no_writes(writes)
        assert error == expected


def test_issue_lookup_failure_fails_without_writes(tmp):
    del tmp
    api = _api(fail={'issues/101'})
    code, writes, _output, error = _execute(api, _valid_body())
    assert code == 1
    _assert_no_writes(writes)
    assert error == (
        'pr gate failed: GitHub returned 500 for '
        'repos/owner/repo/issues/101\n')


def test_reference_limit_checks_only_twenty_and_reports_overflow(tmp):
    del tmp
    numbers = list(range(1, 22))
    body = _valid_body(' '.join(f'#{number}' for number in numbers))
    rendered = _valid_html(references=' '.join(
        _issue_html(number) for number in numbers))
    issues = {str(number): _issue('bob') for number in numbers}
    api = _api(issues=issues, rendered=rendered)
    code, writes, _output, _error = _execute(api, body)
    assert code == 0
    assert len(_issue_gets(api)) == 20
    reason = ('This body names more than 20 issue references, so only the '
              'first 20 were checked.')
    comment = _assert_gate_message(writes[0], OPEN_FIRST, [reason])
    assert 'No checked issue is assigned to you.' not in comment

    numbers = numbers[:20]
    body = _valid_body(' '.join(f'#{number}' for number in numbers))
    rendered = _valid_html(references=' '.join(
        _issue_html(number) for number in numbers))
    api = _api(issues=issues, rendered=rendered)
    code, writes, _output, _error = _execute(api, body)
    assert code == 0
    assert len(_issue_gets(api)) == 20
    assert reason not in _comment_body(writes[0])


def test_failed_comment_write_prevents_state_change(tmp):
    del tmp
    body = _valid_body('none')
    rendered = _valid_html(references=_text_html('none'))
    api = _api(
        issues={}, rendered=rendered,
        fail={'POST repos/owner/repo/issues/99/comments'})
    code, writes, _output, error = _execute(api, body)
    assert code == 1
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    assert api.pull['state'] == 'open'
    assert error == (
        'pr gate failed: GitHub returned 500 for '
        'repos/owner/repo/issues/99/comments\n')


def test_one_edit_self_heals_gate_closed_state(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment(closed=True)],
        timeline=[_closed_event()])
    code, _writes, _output, _error = _execute(api, _valid_body())
    assert code == 0
    assert api.pull['state'] == 'open'


def test_unknown_section_name_cannot_inject_a_live_reference(tmp):
    del tmp
    name = 'x`#1'
    body = _valid_body() + f'\n## {name}\nUnknown.\n'
    rendered = _valid_html() + _html_body((name, _text_html('Unknown.')))
    code, writes, _output, _error = _execute(
        _api(rendered=rendered), body)
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    comment = _assert_gate_message(
        writes[0], CLOSED_FIRST,
        ['Section ``x`#1`` is not defined by the template.'], closed=True)
    spans = _markdown_code_spans(comment)
    for match in re.finditer(r'#1', comment):
        assert any(start <= match.start() < end for start, end in spans), (
            match.start(), spans, comment)


def test_workflow_shape(tmp):
    del tmp
    workflow = _workflow()
    triggers = _workflow_triggers(workflow, 'pr-gate.yml')
    assert set(triggers) == {'pull_request_target'}
    options = [entry for entry in (
        _entry(line, 'pr-gate.yml')
        for line in triggers['pull_request_target']) if entry]
    assert [(key, _flow_sequence(value, key, 'pr-gate.yml'))
            for _indent, key, value in options] == [
                ('types', ['opened', 'edited', 'reopened'])]
    assert top_level_mapping(workflow, 'permissions') == {
        'pull-requests': 'write', 'issues': 'read'}
    assert '  cancel-in-progress: false\n' in workflow
    assert 'cancel-in-progress: true' not in workflow
    job_header = workflow.partition('    steps:\n')[0]
    assert job_scalar(job_header, 'gate', 'if') == (
        "github.event.pull_request.user.type != 'Bot'")
    steps = step_mappings(workflow, 'gate')
    assert len(steps) == 2
    assert steps[0]['uses'] == (
        'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1')
    assert steps[0]['with'] == {'persist-credentials': 'false'}
    assert steps[1]['run'] == 'python3 scripts/ci/pr_gate.py'
    assert steps[1]['env'] == {
        'GH_TOKEN': '${{ github.token }}',
        'REPO': '${{ github.repository }}',
        'PR': '${{ github.event.pull_request.number }}',
        'ACTOR': '${{ github.event.pull_request.user.login }}',
    }
    assert ('  pull_request_target:  '
            '# zizmor: ignore[dangerous-triggers]') in workflow.splitlines()


def test_script_runs_through_gh_on_path(tmp):
    fixtures = {
        'pull': {'body': _valid_body(), 'state': 'open', 'merged': False},
        'comments': [],
        'timeline': [],
        'rendered': _valid_html(),
        'issues': {'101': _issue('alice')},
    }
    result, calls = _run_script(tmp, fixtures)
    assert result.returncode == 0, (result.stdout, result.stderr, calls)
    assert _recorded_writes(calls) == []
    forbidden = set('&|<>^')
    assert all(
        forbidden.isdisjoint(argument) for call in calls
        for argument in call['argv']), calls

    body = _valid_body('none')
    fixtures = {
        **fixtures,
        'pull': {'body': body, 'state': 'open', 'merged': False},
        'rendered': _valid_html(references=_text_html('none')),
        'issues': {},
    }
    other = Path(tmp) / 'closable'
    other.mkdir()
    result, calls = _run_script(other, fixtures)
    assert result.returncode == 0, (result.stdout, result.stderr, calls)
    assert all(
        forbidden.isdisjoint(argument) for call in calls
        for argument in call['argv']), calls
    writes = _recorded_writes(calls)
    assert [(method, endpoint) for method, endpoint, _payload in writes] == [
        ('POST', 'repos/owner/repo/issues/99/comments'),
        ('PATCH', 'repos/owner/repo/pulls/99')]
    comment = writes[0][2]['body']
    assert writes[1][2] == {'state': 'closed'}
    assert any(
        call['input'] == {
            'text': body, 'mode': 'gfm', 'context': 'owner/repo'}
        for call in calls)
    assert all(comment not in argument for call in calls
               for argument in call['argv'])


def test_gh_absent_from_path_is_reported(tmp):
    empty = Path(tmp) / 'empty'
    empty.mkdir()
    gate = _gate_module()
    with mock.patch.dict(os.environ, {'PATH': str(empty)}):
        api = gate.GhApi()
    assert api.gh is None
    assert _runtime_error(
        lambda: api.request('GET', 'repos/owner/repo/pulls/99')) == (
            'gh was not found on PATH')


def test_gh_oserror_is_reported(tmp):
    target = Path(tmp) / 'not-executable'
    target.write_text('not executable', encoding='utf-8')
    target.chmod(0o644)
    api = _gate_module().GhApi()
    api.gh = str(target)
    error = _runtime_error(
        lambda: api.request('GET', 'repos/owner/repo/pulls/99'))
    assert error.startswith('could not run gh: '), error


def test_invalid_gh_status_line_is_reported(tmp):
    result, _calls = _run_script(tmp, _script_fixtures(bad_status=True))
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr == (
        'pr gate failed: could not read gh response: '
        'no HTTP status in output\n')


def test_non_json_gh_body_is_reported(tmp):
    result, _calls = _run_script(tmp, _script_fixtures(non_json=True))
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr == (
        'pr gate failed: could not parse gh response body\n')


def test_paginated_page_must_be_a_list(tmp):
    del tmp
    gate = _gate_module()
    api = _PaginationApi([gate.Response(200, {'items': []})])
    error = _runtime_error(
        lambda: gate.GhApi.paginate(api, 'repos/x/issues/1/comments'))
    assert error == 'paginated gh response is not a list'


def test_pagination_short_page_uses_safe_fields(tmp):
    del tmp
    gate = _gate_module()
    api = _PaginationApi([gate.Response(200, [1, 2])])
    response = gate.GhApi.paginate(api, 'repos/x/issues/1/comments')
    assert response == gate.Response(200, [1, 2])
    assert api.calls == [
        ('GET', 'repos/x/issues/1/comments', None,
         ('per_page=100', 'page=1'))]


def test_pagination_stops_after_fifty_full_pages(tmp):
    del tmp
    gate = _gate_module()
    pages = [gate.Response(200, list(range(100))) for _ in range(51)]
    api = _PaginationApi(pages)
    error = _runtime_error(
        lambda: gate.GhApi.paginate(api, 'repos/x/issues/1/comments'))
    assert error == 'gh pagination exceeded 50 pages'
    assert len(api.calls) == 50


def test_runtime_error_is_reported_by_run(tmp):
    del tmp
    api = _api()

    def fail_request(_method, _endpoint, _payload=None):
        raise RuntimeError('transport unavailable')

    api.request = fail_request
    code, writes, _output, error = _execute(api, _valid_body())
    assert code == 1
    _assert_no_writes(writes)
    assert error == 'pr gate failed: transport unavailable\n'


def test_pull_response_must_be_an_object(tmp):
    del tmp
    gate = _gate_module()
    api = _api()
    request = api.request

    def wrong_pull(method, endpoint, payload=None):
        if method == 'GET' and endpoint.endswith('/pulls/99'):
            return gate.Response(200, [])
        return request(method, endpoint, payload)

    api.request = wrong_pull
    code, writes, _output, error = _execute(api, _valid_body())
    assert code == 1
    _assert_no_writes(writes)
    assert error == 'pr gate failed: pull request response is not an object\n'


def test_markdown_response_must_be_text(tmp):
    del tmp
    code, writes, _output, error = _execute(
        _api(rendered={'html': 'not text'}), _valid_body())
    assert code == 1
    _assert_no_writes(writes)
    assert error == 'pr gate failed: markdown response is not text\n'


def test_main_reports_missing_repo(tmp):
    del tmp
    gate = _gate_module()
    with mock.patch.dict(os.environ):
        os.environ.pop('REPO', None)
        code, output, error = _capture(gate.main)
    assert code == 1
    assert output == ''
    assert error == "pr gate failed: 'REPO'\n"


def test_gh_stub_refuses_unmodelled_calls(tmp):
    directory, command = _write_gh_stub(tmp)
    fixtures = Path(tmp) / 'fixtures.json'
    fixtures.write_text('{}', encoding='utf-8')
    calls = Path(tmp) / 'calls.jsonl'
    calls.write_text('', encoding='utf-8')
    environment = {
        **os.environ,
        'PATH': f'{directory}{os.pathsep}{os.environ["PATH"]}',
        'STUB_FIXTURES': str(fixtures),
        'STUB_CALLS': str(calls),
    }
    result = subprocess.run(
        [str(command), 'api', 'repos/owner/repo/labels'],
        env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert 'unsupported' in result.stderr, result.stderr

    stub = directory / ('gh.py' if os.name == 'nt' else 'gh')
    result = subprocess.run(
        [sys.executable, str(stub), 'api', '--include', '-X', 'GET',
         'repos/owner/repo/issues/99/comments?per_page=100&page=1'],
        env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert 'unsupported' in result.stderr, result.stderr


def test_unparsable_gh_failure_reports_one_stderr_line(tmp):
    fixtures = {
        'pull': {'body': _valid_body(), 'state': 'open', 'merged': False},
        'comments': [],
        'timeline': [],
        'rendered': _valid_html(),
        'issues': {'101': _issue('alice')},
        'unparsable': True,
    }
    result, _calls = _run_script(tmp, fixtures)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.splitlines() == [
        'pr gate failed: could not read gh response: first gh failure line '
        'second gh failure line'], result.stderr


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prgate_')


if __name__ == '__main__':
    raise SystemExit(main())
