#!/usr/bin/env python3
"""Pull-request gate state transitions and write ordering."""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _prgate  # noqa: E402
import _util  # noqa: E402
from _prgate import (  # noqa: E402
    BOT, CLOSED_MARKER, GITHUB_HTML, MARKER, FakeApi, TEMPLATE,
    _assert_no_writes, _comment_body, _html_body, _issue, _issue_html,
    _layout_body, _markdown_code_spans, _text_html, _valid_body,
    _valid_html, run_gate,
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
    '@alice — every condition now passes; nothing further is needed from you.')
REOPEN_FIRST = (
    '@alice — the body now names a claimed issue and matches the pull request')


def _pull(state='open', merged=False):
    return {'body': '', 'state': state, 'merged': merged}


def _api(*, state='open', merged=False, issues=None, **kwargs):
    if issues is None:
        issues = {'101': _issue('alice')}
    return FakeApi(
        pull=_pull(state, merged), issues=issues, **kwargs)


def _gate_comment(closed=False):
    lines = ['old gate message', MARKER]
    if closed:
        lines.append(CLOSED_MARKER)
    return {'id': 7, 'user': {'login': BOT}, 'body': '\n'.join(lines)}


def _closed_event(actor=BOT):
    return {'event': 'closed', 'actor': {'login': actor}}


def _execute(api, body):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code, writes = run_gate(api, body)
    return code, writes, output.getvalue()


def _write_sequence(writes):
    return [(method, endpoint) for method, endpoint, _payload in writes]


def _assert_gate_message(write, first, reasons=(), closed=False):
    body = _comment_body(write)
    lines = body.splitlines()
    assert lines[0] == first, body
    assert MARKER in lines, body
    assert (CLOSED_MARKER in lines) is closed, body
    for reason in reasons:
        assert f'- {reason}' in lines, (reason, body)
    return body


def _issue_gets(api):
    return [call for call in api.calls if re.fullmatch(
        r'repos/owner/repo/issues/[0-9]+', call[1])]


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'pr-gate.yml').read_text(
        encoding='utf-8')


def _write_gh_stub(tmp):
    source = getattr(_prgate, 'GH_STUB', None)
    assert isinstance(source, str), 'GH_STUB is not implemented'
    directory = Path(tmp) / 'bin'
    directory.mkdir()
    if os.name == 'nt':
        script = directory / 'gh.py'
        script.write_text(source, encoding='utf-8')
        command = directory / 'gh.bat'
        command.write_text(
            '@python "%~dp0gh.py" %*\n', encoding='utf-8')
    else:
        command = directory / 'gh'
        command.write_text(source, encoding='utf-8')
        command.chmod(0o755)
    return directory, command


def _run_script(tmp, fixtures):
    directory, _command = _write_gh_stub(tmp)
    fixtures_path = Path(tmp) / 'fixtures.json'
    fixtures_path.write_text(json.dumps(fixtures), encoding='utf-8')
    calls_path = Path(tmp) / 'calls.jsonl'
    calls_path.write_text('', encoding='utf-8')
    environment = {
        **os.environ,
        'PATH': f'{directory}{os.pathsep}{os.environ["PATH"]}',
        'GH_TOKEN': 'stub',
        'REPO': 'owner/repo',
        'PR': '99',
        'ACTOR': 'alice',
        'STUB_FIXTURES': str(fixtures_path),
        'STUB_CALLS': str(calls_path),
    }
    result = subprocess.run(
        [sys.executable, 'scripts/ci/pr_gate.py'], cwd=ROOT,
        env=environment, capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in calls_path.read_text(
        encoding='utf-8').splitlines()]
    return result, calls


def _recorded_writes(calls):
    writes = []
    for call in calls:
        argv = call['argv']
        if '-X' not in argv:
            continue
        method = argv[argv.index('-X') + 1]
        endpoint = argv[argv.index('-X') + 2]
        if method != 'GET' and endpoint != 'markdown':
            writes.append((method, endpoint, call['input']))
    return writes


def test_admissible_open_without_prior_comment_does_not_write(tmp):
    del tmp
    code, writes, output = _execute(_api(), _valid_body())
    assert code == 0
    _assert_no_writes(writes)
    assert '101' in output, output


def test_admissible_open_resolves_a_prior_gate_comment(tmp):
    del tmp
    api = _api(comments=[_gate_comment()])
    code, writes, _output = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('PATCH', 'repos/owner/repo/issues/comments/7')]
    _assert_gate_message(writes[0], RESOLVED_FIRST)


def test_unclaimed_issue_comments_without_closing(tmp):
    del tmp
    api = _api(issues={'101': _issue('bob')})
    code, writes, _output = _execute(api, _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    _assert_gate_message(
        writes[0], OPEN_FIRST, ['No checked issue is assigned to you.'])


def test_missing_issue_comments_without_closing(tmp):
    del tmp
    code, writes, _output = _execute(_api(issues={}), _valid_body())
    assert code == 0
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    _assert_gate_message(
        writes[0], OPEN_FIRST, ['No checked issue is assigned to you.'])


def test_pull_request_reference_comments_without_closing(tmp):
    del tmp
    api = _api(issues={'101': _issue(pull_request=True)})
    code, writes, _output = _execute(api, _valid_body())
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
    code, writes, _output = _execute(_api(rendered=rendered), body)
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
    code, writes, _output = _execute(
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
    code, writes, _output = _execute(
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
    code, writes, _output = _execute(
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
    code, writes, _output = _execute(_api(), body)
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
    code, writes, _output = _execute(api, body)
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
    code, writes, _output = _execute(api, _valid_body())
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
    code, writes, _output = _execute(api, body)
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
    code, writes, _output = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)


def test_bot_timeline_without_closed_marker_is_not_gate_owned(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment()],
        timeline=[_closed_event()])
    code, writes, _output = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)


def test_unreadable_closed_timeline_fails_without_writes(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment(closed=True)],
        timeline=[_closed_event()], fail={'timeline'})
    code, writes, _output = _execute(api, _valid_body())
    assert code == 1
    _assert_no_writes(writes)


def test_merged_pull_returns_before_comments_or_render(tmp):
    del tmp
    api = _api(merged=True, fail={'comments', 'markdown'})
    code, writes, _output = _execute(api, _valid_body())
    assert code == 0
    _assert_no_writes(writes)
    assert api.calls == [
        ('GET', 'repos/owner/repo/pulls/99', None)]


def test_unusable_render_fails_without_writes(tmp):
    del tmp
    cases = (
        _api(fail={'markdown'}),
        _api(rendered='<h2>Summary</h2><p'),
    )
    for api in cases:
        code, writes, _output = _execute(api, _valid_body())
        assert code == 1
        _assert_no_writes(writes)


def test_issue_lookup_failure_fails_without_writes(tmp):
    del tmp
    api = _api(fail={'issues/101'})
    code, writes, _output = _execute(api, _valid_body())
    assert code == 1
    _assert_no_writes(writes)


def test_reference_limit_checks_only_twenty_and_reports_overflow(tmp):
    del tmp
    numbers = list(range(1, 22))
    body = _valid_body(' '.join(f'#{number}' for number in numbers))
    rendered = _valid_html(references=' '.join(
        _issue_html(number) for number in numbers))
    issues = {str(number): _issue('bob') for number in numbers}
    api = _api(issues=issues, rendered=rendered)
    code, writes, _output = _execute(api, body)
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
    code, writes, _output = _execute(api, body)
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
    code, writes, _output = _execute(api, body)
    assert code == 1
    assert _write_sequence(writes) == [
        ('POST', 'repos/owner/repo/issues/99/comments')]
    assert api.pull['state'] == 'open'


def test_one_edit_self_heals_gate_closed_state(tmp):
    del tmp
    api = _api(
        state='closed', comments=[_gate_comment(closed=True)],
        timeline=[_closed_event()])
    code, _writes, _output = _execute(api, _valid_body())
    assert code == 0
    assert api.pull['state'] == 'open'


def test_unknown_section_name_cannot_inject_a_live_reference(tmp):
    del tmp
    name = 'x`#1'
    body = _valid_body() + f'\n## {name}\nUnknown.\n'
    rendered = _valid_html() + _html_body((name, _text_html('Unknown.')))
    code, writes, _output = _execute(_api(rendered=rendered), body)
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
