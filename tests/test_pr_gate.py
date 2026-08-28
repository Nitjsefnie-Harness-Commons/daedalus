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
    BOT, CLOSE_MARKER, GITHUB_FOOTNOTE_HTML, GITHUB_FOOTNOTE_MARKDOWN,
    GITHUB_HTML, GITHUB_MARKDOWN, MARKER_COMMENT, PR_BODY, _GH_STUB,
    _assert_commented_not_closed, _assert_commented_then_closed,
    _assert_commented_then_reopened, _assert_no_mutation,
    _assert_unusable_render, _body_from, _closed_by, _issue, _issue_lookups,
    _html_body, _issue_html, _layout_body, _run_complete_workflow,
    _sentinel_attack_cases, _text_html, _truncated_render_cases,
    _valid_body, _valid_html, _workflow, _workflow_script, _write_calls,
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
    logical_script = script.replace('\\\n', ' ')
    endpoints = re.findall(
        r'\bgh api (?:--paginate |--include |-i '
        r'|(?:-X|--method) PATCH )?["\']?'
        r'([^"\'\s\\]+)', logical_script)
    assert endpoints, logical_script
    assert len(endpoints) == logical_script.count('gh api '), endpoints
    assert not any(endpoint.startswith('/') for endpoint in endpoints), (
        endpoints)
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


def test_gh_stub_rejects_wrong_write_methods_and_fields(tmp):
    comment = _run_stub(
        tmp, 'api', '-X', 'DELETE',
        'repos/owner/repo/issues/99/comments')
    reopen = _run_stub(
        tmp, 'api', '-X', 'PATCH', 'repos/owner/repo/pulls/99',
        '-f', 'state=open', '-f', 'title=mutated', '--silent')
    assert comment.returncode != 0, comment.stdout
    assert reopen.returncode != 0, reopen.stdout
    assert 'unsupported gh api call' in comment.stderr, comment.stderr
    assert 'unsupported gh api call' in reopen.stderr, reopen.stderr


def test_gh_stub_accepts_the_documented_method_alias(tmp):
    result = _run_stub(
        tmp, 'api', '--method', 'PATCH',
        'repos/owner/repo/pulls/99', '-f', 'state=open', '--silent')
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_workflow_renders_the_event_body_once_in_repository_context(tmp):
    repository = 'acme/context-pin'
    actor = 'render-author'
    body = _valid_body('Fixes #101\n\nUnique render payload.')
    sentinels = []
    for index in range(2):
        calls, result = _run_complete_workflow(
            Path(tmp) / str(index), body, {'101': _issue(actor)},
            actor=actor, repo=repository)
        assert result.returncode == 0, (result.stdout, result.stderr)
        renders = [call for call in calls if 'markdown' in call]
        assert len(renders) == 1, calls
        render = renders[0]
        assert render[:2] == ['api', 'markdown'], render
        text = render[render.index('-F') + 1]
        match = re.fullmatch(
            re.escape(f'text={body}')
            + r'\n\n(pr-gate-sentinel-[0-9a-f]{64})\n', text)
        assert match is not None, render
        sentinels.append(match.group(1))
        assert render.count('-f') == 2, render
        assert 'mode=gfm' in render, render
        assert f'context={repository}' in render, render
        _assert_no_mutation(calls)
    assert len(set(sentinels)) == 2, sentinels


def test_github_visible_markdown_constructs_stay_admissible(tmp):
    repository = 'Nitjsefnie-Harness-Commons/daedalus'
    names = (
        'nested_list',
        'paragraph_continuation',
        'escaped_backticks',
        'angle_prose',
        'undefined_reference',
        'malformed_inline',
    )
    failures = []
    for name in names:
        body = _valid_body(GITHUB_MARKDOWN[name])
        rendered = _valid_html(
            references=GITHUB_HTML[name], repo=repository)
        calls, result = _run_complete_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            repo=repository, rendered_html=rendered)
        if result.returncode != 0 or _write_calls(calls):
            failures.append((name, result.returncode, calls, result.stderr))
    assert failures == [], failures


def test_github_footnotes_and_named_anchors_stay_admissible(tmp):
    named_body = _valid_body().replace(
        '- One change', '<a name="spot"></a>\n- One change')
    named_html = _valid_html(
        changes=GITHUB_HTML['named_anchor']
        + '<ul dir="auto"><li>One change</li></ul>')
    cases = (
        ('footnote', GITHUB_FOOTNOTE_MARKDOWN, GITHUB_FOOTNOTE_HTML,
         'Nitjsefnie-Harness-Commons/daedalus'),
        ('named-anchor', named_body, named_html, 'owner/repo'),
    )
    failures = []
    for name, body, rendered, repo in cases:
        calls, result = _run_complete_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            repo=repo, rendered_html=rendered)
        if result.returncode != 0 or _write_calls(calls):
            failures.append((name, result.returncode, calls, result.stderr))
    assert failures == [], failures


def test_github_nontext_constructs_do_not_satisfy_the_claim(tmp):
    names = (
        'balanced_destination',
        'quoted_attribute',
        'multiline_attribute',
        'image_destination',
    )
    failures = []
    for name in names:
        body = _valid_body(GITHUB_MARKDOWN[name])
        rendered = _valid_html(references=GITHUB_HTML[name])
        calls, result = _run_complete_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            rendered_html=rendered)
        try:
            assert result.returncode == 0, (result.stdout, result.stderr)
            _assert_commented_not_closed(
                calls, 'No checked issue is assigned to you.')
        except AssertionError as error:
            failures.append((name, str(error)))
    assert failures == [], failures


def test_heading_shaped_text_in_a_github_raw_block_stays_content(tmp):
    body = _valid_body().replace('- One change', GITHUB_MARKDOWN['kbd_block'])
    rendered = _valid_html(changes=GITHUB_HTML['kbd_block'])
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')}, rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_no_mutation(calls)


def test_render_failures_never_change_pull_request_state(tmp):
    cases = (
        ('open', {'state': 'open'}, {}),
        ('closed', {'state': 'closed'}, {
            'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT}),
    )
    for name, pull, history in cases:
        calls, result = _run_complete_workflow(
            Path(tmp) / name, _valid_body(), {'101': _issue('alice')},
            pull=pull, history=history, render_status=502)
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        _assert_no_mutation(calls)


def test_unusable_render_responses_never_change_pull_request_state(tmp):
    pulls = (
        ('open', {}, {}),
        ('owned-closed', {'state': 'closed'}, {
            'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT}),
    )
    for pull_name, pull, history in pulls:
        for render_name, rendered in _truncated_render_cases():
            _assert_unusable_render(
                Path(tmp) / f'{pull_name}-{render_name}', _valid_body(),
                rendered, pull=pull, history=history)


def test_render_sentinel_is_unpredictable_and_terminal(tmp):
    for name, body, rendered, options in _sentinel_attack_cases():
        _assert_unusable_render(
            Path(tmp) / name, body, rendered, **options)


def test_bodies_at_and_past_the_old_byte_bound_are_rendered(tmp):
    base = _valid_body()
    exact = base + ('x' * (65536 - len(base.encode('utf-8'))))
    past = exact + 'x'
    multibyte = base + ('é' * 33000)
    for name, body in (
            ('exact', exact), ('past', past), ('multibyte', multibyte)):
        calls, result = _run_complete_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')})
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        renders = [call for call in calls if 'markdown' in call]
        assert len(renders) == 1, (name, calls)
        assert any(arg.startswith(f'text={body}\n\npr-gate-sentinel-')
                   for arg in renders[0]), name
        assert len(_issue_lookups(calls)) == 1, calls
        _assert_no_mutation(calls)


def test_open_admissible_body_is_left_untouched(tmp):
    rendered = _valid_html(
        references=(
            f'Refs {_issue_html(101, "acme/widget")} and '
            f'{_issue_html(102, "acme/widget")}'),
        repo='acme/widget')
    calls, result = _run_complete_workflow(
        tmp, _valid_body('Refs #101 and #102'),
        {'101': _issue('bob'), '102': _issue('carol')},
        actor='carol', repo='acme/widget', parser_crlf=True,
        rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    lookups = _issue_lookups(calls)
    assert 'repos/acme/widget/issues/101' in lookups[0], calls
    assert 'repos/acme/widget/issues/102' in lookups[1], calls
    _assert_no_mutation(calls)


def test_claimed_literal_markdown_contexts_are_left_untouched(tmp):
    instruction = (
        '<!-- required: bullet list of concrete changes — files, modules, '
        'behavior. -->')
    issue = f'Fixes {_issue_html(101)}'
    cases = (
        (_valid_body('The token `<!--` is literal; Fixes #101'),
         _valid_html(references=issue)),
        (_valid_body().replace(
            '- One change', f'- Changed `{instruction}` to prose.'),
         _valid_html(changes=GITHUB_HTML['inline_instruction'])),
        (_valid_body().replace(
            '- One change', f'    {instruction}\n- A visible change'),
         _valid_html(changes=GITHUB_HTML['indented_instruction'])),
        (_valid_body().replace(
            '- One change', '<pre>\n## literal heading\n</pre>\n- A change'),
         _valid_html(changes=(
             '<pre>\n## literal heading\n</pre>\n'
             '<ul dir="auto">\n<li>A change</li>\n</ul>'))),
    )
    for index, (body, rendered) in enumerate(cases):
        calls, result = _run_complete_workflow(
            Path(tmp) / str(index), body, {'101': _issue('alice')},
            rendered_html=rendered)
        assert result.returncode == 0, (result.stdout, result.stderr)
        _assert_no_mutation(calls)


def test_visible_template_prose_is_not_an_instruction_comment(tmp):
    prose = ('The template says: required: claim the issue with `/claim` '
             'before you start, then name it')
    body = _valid_body().replace('One sentence.', prose)
    summary = ('<p dir="auto">The template says: required: claim the issue '
               'with <code class="notranslate">/claim</code> before you '
               'start, then name it</p>')
    rendered = _html_body(
        ('Summary', summary),
        ('Related Issues and Pull Requests', f'Fixes {_issue_html(101)}'),
        ('Changes', '<ul><li>One change</li></ul>'),
        ('Testing', _text_html('Ran the suite.')))
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')}, rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_no_mutation(calls)


def test_layout_names_retained_template_instructions_separately(tmp):
    template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
        encoding='utf-8')
    instruction = next(
        comment for comment in re.findall(
            r'<!--.*?-->', template, re.DOTALL)
        if 'bullet list of concrete changes' in comment)
    body = _valid_body().replace(
        '- One change', f'{instruction}\n- One change')
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')},
        rendered_html=_valid_html())
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(
        calls, 'Remove the template instruction comments.')


def test_retained_comment_is_detected_beside_visible_fingerprint(tmp):
    body = _valid_body().replace(
        '- One change', '- <!-- optional --> optional')
    rendered = _valid_html(
        changes='<ul dir="auto"><li>optional</li></ul>')
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')}, rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(
        calls, 'Remove the template instruction comments.')


def test_entity_spelling_does_not_hide_a_retained_comment(tmp):
    changes = ('<!-- optional -->\n'
               '- The literal template marker is '
               '&lt;!-- optional --&gt;.\n- One change.')
    body = _valid_body().replace('- One change', changes)
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')},
        rendered_html=_valid_html(
            changes=GITHUB_HTML['entity_instruction']))
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(
        calls, 'Remove the template instruction comments.')


def test_each_template_instruction_comment_is_detected(tmp):
    template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
        encoding='utf-8')
    comments = re.findall(r'<!--.*?-->', template, re.DOTALL)
    failures = []
    for index, comment in enumerate(comments):
        calls, result = _run_complete_workflow(
            Path(tmp) / str(index), f'{comment}\n{_valid_body()}',
            {'101': _issue('alice')})
        try:
            assert result.returncode == 0, (result.stdout, result.stderr)
            _assert_commented_not_closed(
                calls, 'Remove the template instruction comments.')
        except AssertionError as error:
            failures.append((index, str(error)))
    assert failures == [], failures


def test_open_body_reports_both_failed_conditions_once(tmp):
    body = _layout_body(
        ('Summary', ''),
        ('Related Issues and Pull Requests', 'Fixes #103'),
        ('Changes', '- One change'), ('Testing', 'Ran tests.'))
    rendered = _html_body(
        ('Summary', ''),
        ('Related Issues and Pull Requests',
         f'Fixes {_issue_html(103)}'),
        ('Changes', '<ul><li>One change</li></ul>'),
        ('Testing', _text_html('Ran tests.')))
    calls, result = _run_complete_workflow(
        tmp, body, {'103': _issue('alicebob')}, rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(
        calls, 'No checked issue is assigned to you.',
        'Section "Summary" is empty.')


def test_open_body_without_a_raw_reference_is_closed(tmp):
    calls, result = _run_complete_workflow(
        tmp, 'No issue reference anywhere.', {},
        rendered_html='<p dir="auto">No issue reference anywhere.</p>')
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(
        calls, 'No checked issue is assigned to you.')


def test_open_body_reports_each_single_failed_condition(tmp):
    unclaimed, first = _run_complete_workflow(
        Path(tmp) / 'claim', _valid_body(), {'101': _issue()})
    malformed, second = _run_complete_workflow(
        Path(tmp) / 'layout', _valid_body().replace('## Summary', '## Notes'),
        {'101': _issue('alice')}, rendered_html=_html_body(
            ('Notes', _text_html('One sentence.')),
            ('Related Issues and Pull Requests',
             f'Fixes {_issue_html(101)}'),
            ('Changes', '<ul><li>One change</li></ul>'),
            ('Testing', _text_html('Ran the suite.'))))
    assert first.returncode == second.returncode == 0
    _assert_commented_not_closed(
        unclaimed, 'No checked issue is assigned to you.')
    _assert_commented_not_closed(
        malformed, 'Section `Notes` is not defined by the template.')


def test_open_rendered_empty_changes_is_commented_without_closing(tmp):
    body = _valid_body().replace('- One change', '-')
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')},
        rendered_html=_valid_html(changes=GITHUB_HTML['empty_list']))
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(calls, 'Section "Changes" is empty.')


def test_open_image_only_optional_section_is_left_untouched(tmp):
    body = _valid_body() + (
        '\n## Breaking Changes\n![migration diagram](diagram.png)\n')
    image = ('<p dir="auto"><img src="diagram.png" '
             'alt="migration diagram"></p>')
    rendered = _valid_html() + _html_body(('Breaking Changes', image))
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')}, rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_no_mutation(calls)


def test_unanswerable_analysis_inputs_never_write(tmp):
    cases = (
        ('repository', 'missing-owner', _valid_html(
            references='<p>No reference.</p>')),
        ('self-closing', 'owner/repo', _valid_html(changes='<p/>')),
    )
    for name, repository, rendered in cases:
        calls, result = _run_complete_workflow(
            Path(tmp) / name, _valid_body(), {'101': _issue('alice')},
            repo=repository, rendered_html=rendered)
        assert result.returncode != 0, (
            name, result.stdout, result.stderr)
        assert 'could not analyze' in result.stderr, result.stderr
        _assert_no_mutation(calls)


def test_link_destination_does_not_satisfy_the_claim(tmp):
    calls, result = _run_complete_workflow(
        tmp, _valid_body('[documentation](#101)'),
        {'101': _issue('alice')},
        rendered_html=_valid_html(
            references=GITHUB_HTML['html_attribute']))
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(
        calls, 'No checked issue is assigned to you.')


def test_unknown_section_name_cannot_post_a_live_issue_reference(tmp):
    name = 'Notes for #255'
    body = _valid_body().replace(
        '## Testing', f'## {name}\nUnknown.\n\n## Testing')
    calls, result = _run_complete_workflow(
        tmp, body, {'101': _issue('alice')}, rendered_html=_html_body(
            ('Summary', _text_html('One sentence.')),
            ('Related Issues and Pull Requests',
             f'Fixes {_issue_html(101)}'),
            ('Changes', '<ul><li>One change</li></ul>'),
            (name, _text_html('Unknown.')),
            ('Testing', _text_html('Ran the suite.'))))
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(calls)
    endpoint = 'repos/owner/repo/issues/99/comments'
    comment = _body_from(next(call for call in calls if endpoint in call))
    quoted = PR_BODY._code_span(name)
    assert f'Section {quoted} is not defined by the template.' in comment
    assert quoted.startswith('`') and quoted.endswith('`'), quoted
    spans = [match.span() for match in re.finditer(
        re.escape(quoted), comment)]
    references = list(re.finditer(r'#[0-9]+', comment))
    assert references, comment
    assert all(any(start <= match.start() and match.end() <= end
                   for start, end in spans) for match in references), comment


def test_closed_owned_admissible_body_is_reopened(tmp):
    calls, result = _run_complete_workflow(
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
        rendered = _valid_html() if body != 'bad body' else (
            '<p dir="auto">bad body</p>')
        calls, result = _run_complete_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            pull={'state': 'closed'}, history={
                'timeline': timeline, 'comments': MARKER_COMMENT},
            rendered_html=rendered)
        assert result.returncode == 0, (name, result.stderr)
        _assert_no_mutation(calls)


def test_closed_owned_without_marker_is_not_changed(tmp):
    spoof = [{'id': 8, 'user': {'login': 'alice'}, 'body': CLOSE_MARKER}]
    for name, comments in [('absent', ()), ('spoofed', spoof)]:
        calls, result = _run_complete_workflow(
            Path(tmp) / name, _valid_body(), {'101': _issue('alice')},
            pull={'state': 'closed'}, history={
                'timeline': _closed_by(BOT), 'comments': comments})
        assert result.returncode == 0, (result.stdout, result.stderr)
        _assert_no_mutation(calls)


def test_closed_owned_inadmissible_body_stays_closed(tmp):
    calls, result = _run_complete_workflow(
        tmp, 'bad body', {}, pull={'state': 'closed'}, history={
            'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT},
        rendered_html='<p dir="auto">bad body</p>')
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_no_mutation(calls)


def test_merged_pull_request_is_never_touched(tmp):
    for name, body in [('valid', _valid_body()), ('invalid', 'bad body')]:
        rendered = _valid_html() if name == 'valid' else (
            '<p dir="auto">bad body</p>')
        calls, result = _run_complete_workflow(
            Path(tmp) / name, body, {'101': _issue('alice')},
            pull={'state': 'closed', 'merged': 'true'}, history={
                'timeline': _closed_by(BOT), 'comments': MARKER_COMMENT},
            rendered_html=rendered)
        assert result.returncode == 0, (name, result.stderr)
        assert calls == [], calls


def test_unreadable_ownership_evidence_never_reopens(tmp):
    first, one = _run_complete_workflow(
        Path(tmp) / 'timeline', _valid_body(), {}, pull={'state': 'closed'},
        history={'timeline_status': 502})
    second, two = _run_complete_workflow(
        Path(tmp) / 'marker', _valid_body(), {}, pull={'state': 'closed'},
        history={'timeline': _closed_by(BOT), 'comments_status': 502})
    assert one.returncode == two.returncode == 0
    _assert_no_mutation(first)
    _assert_no_mutation(second)


def test_reference_limit_checks_twenty_and_tells_the_truth(tmp):
    references = ' '.join(f'#{number}' for number in range(130, 151))
    issues = {str(number): _issue('alice' if number == 130 else 'bob')
              for number in range(130, 150)}
    rendered_references = ' '.join(
        _issue_html(number) for number in range(130, 151))
    calls, result = _run_complete_workflow(
        tmp, _valid_body(references), issues,
        rendered_html=_valid_html(references=rendered_references))
    assert result.returncode == 0, (result.stdout, result.stderr)
    lookups = _issue_lookups(calls)
    assert len(lookups) == 20, lookups
    reason = ('This body names more than 20 issue references, so only the '
              'first 20 were checked.')
    _assert_commented_not_closed(calls, reason)
    assert 'No checked issue is assigned to you.' not in _body_from(calls[-1])


def test_exactly_twenty_references_do_not_trigger_the_limit(tmp):
    references = ' '.join(f'#{number}' for number in range(160, 180))
    issues = {str(number): _issue(
        'alice' if number == 179 else 'bob') for number in range(160, 180)}
    rendered = _valid_html(references=' '.join(
        _issue_html(number) for number in range(160, 180)))
    calls, result = _run_complete_workflow(
        tmp, _valid_body(references), issues, rendered_html=rendered)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(_issue_lookups(calls)) == 20, calls
    _assert_no_mutation(calls)


def test_lookup_failures_keep_the_safe_direction(tmp):
    missing, one = _run_complete_workflow(
        Path(tmp) / 'missing', _valid_body('Refs #120 and #121'), {},
        rendered_html=_valid_html(
            references=(
                f'Refs {_issue_html(120)} and {_issue_html(121)}')))
    failed, two = _run_complete_workflow(
        Path(tmp) / 'failed', _valid_body('Fixes #122'),
        {'122': {'_http_status': 502}},
        rendered_html=_valid_html(
            references=f'Fixes {_issue_html(122)}'))
    assert one.returncode == 0
    assert len(_issue_lookups(missing)) == 2
    _assert_commented_not_closed(
        missing, 'No checked issue is assigned to you.')
    assert two.returncode != 0, (two.stdout, two.stderr)
    _assert_no_mutation(failed)


def test_pull_request_reference_does_not_satisfy_claim(tmp):
    calls, result = _run_complete_workflow(
        tmp, _valid_body(), {'101': _issue('alice', pull_request=True)})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_not_closed(
        calls, 'No checked issue is assigned to you.')


def test_failed_explanation_prevents_silent_state_change(tmp):
    calls, result = _run_complete_workflow(
        tmp, _valid_body(), {'101': _issue()}, comment_status=502)
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not any(any(arg.startswith('state=') for arg in call)
                   for call in calls), calls


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prgate_')


if __name__ == '__main__':
    raise SystemExit(main())
