#!/usr/bin/env python3
"""Pull-request body parsing and the workflow admission gate."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import job_scalar, top_level_mapping  # noqa: E402
from _yamlsteps import step_mappings  # noqa: E402
from _workflows import _workflow_triggers  # noqa: E402


PR_BODY = _util.load(ROOT / 'scripts' / 'ci' / 'pr_body.py')
SECTION = '## Related Issues and Pull Requests\n'
TEMPLATE = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
    encoding='utf-8')
BOT = 'github-actions[bot]'
CLOSE_MARKER = '<!-- pr-gate: close -->'
MARKER_COMMENT = [{'id': 7, 'user': {'login': BOT}, 'body': CLOSE_MARKER}]

_GH_STUB = r"""#!/usr/bin/env python3
import json, os, pathlib, re, sys
fixtures = json.loads(pathlib.Path(os.environ['STUB_ISSUES']).read_text())
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
recorded = []
for arg in argv:
    if arg.startswith('body=@'):
        body = pathlib.Path(arg[6:]).read_text(encoding='utf-8')
        recorded.append('body=' + body)
    else:
        recorded.append(arg)
with calls.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(recorded) + chr(10))
endpoint = next((arg for arg in argv if arg.startswith('repos/')), '')
if '/timeline?' in endpoint:
    status = fixtures.get('_timeline_status', 200)
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    for event in fixtures.get('_timeline', []):
        if 'event == "closed"' in query and event.get('event') != 'closed':
            continue
        login = event.get('actor', {}).get('login')
        if login is None and '// "__unreadable__"' in query:
            login = '__unreadable__'
        print(login or '')
    raise SystemExit(0)
if '/comments?' in endpoint:
    status = fixtures.get('_comments_status', 200)
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    user = re.search(r'\.user\.login *== *"([^"]+)"', query)
    marker = re.search(r'contains\("([^"]+)"\)', query)
    for comment in fixtures.get('_comments', []):
        if user and comment['user']['login'] != user.group(1):
            continue
        if marker and marker.group(1) not in comment['body']:
            continue
        print(comment['id'])
    raise SystemExit(0)
parts = endpoint.split('/')
if len(parts) == 5 and parts[-2] == 'issues' and parts[-1].isdigit():
    issue = fixtures.get(parts[-1])
    status = 404 if issue is None else issue.get('_http_status', 200)
    if '--include' in argv:
        reason = {200: 'OK', 404: 'Not Found'}.get(status, 'Error')
        print(f'HTTP/2.0 {status} {reason}')
        print('cache-control: private, max-age=60')
        print('content-type: application/json; charset=utf-8')
        print('x-github-media-type: github.v3; format=json')
        print()
    if status != 200:
        print(f'gh: HTTP {status}', file=sys.stderr)
        raise SystemExit(1)
    query = argv[argv.index('--jq') + 1] if '--jq' in argv else ''
    if 'pull_request' in issue and 'has("pull_request")' in query:
        raise SystemExit(0)
    if '.assignees' in query:
        for assignee in issue['assignees']:
            prefix = 'assignee:' if 'assignee:' in query else ''
            print(prefix + assignee['login'])
    else:
        print(json.dumps(issue))
if endpoint.endswith('/comments') and os.environ.get('STUB_COMMENT_STATUS'):
    status = os.environ['STUB_COMMENT_STATUS']
    print(f'gh: HTTP {status}', file=sys.stderr)
    raise SystemExit(1)
"""

_CRLF_PYTHON_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == scripts/ci/pr_body.py ||
      "${1:-}" == */scripts/ci/pr_body.py ]]; then
  "$STUB_REAL_PYTHON" "$@" |
    "$STUB_REAL_PYTHON" -c 'import sys
data = sys.stdin.buffer.read().replace(b"\r\n", b"\n")
sys.stdout.buffer.write(data.replace(b"\n", b"\r\n"))'
else
  exec "$STUB_REAL_PYTHON" "$@"
fi
"""


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'pr-gate.yml').read_text(
        encoding='utf-8')


def _workflow_script():
    """The pr-gate.yml run block, dedented and ready for Bash."""
    _, marker, after = _workflow().partition('        run: |\n')
    assert marker, 'pr-gate.yml has no literal run block'
    first = after.splitlines()[0]
    indent = len(first) - len(first.lstrip())
    assert first.strip() and indent, 'pr-gate.yml run block has no body'
    lines = []
    for line in after.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        lines.append(line[indent:])
    return chr(10).join(lines)


def _issue(*assignees, pull_request=False):
    issue = {'assignees': [{'login': login} for login in assignees]}
    if pull_request:
        issue['pull_request'] = {'url': 'https://github.com/pulls/0'}
    return issue


def _run_workflow(
        tmp, body, issues, actor='alice', pr='99', repo='owner/repo',
        parser_crlf=False, comment_status=None, pull=None, history=None):
    """Execute the real workflow shell against the controlled gh boundary."""
    pull = pull or {}
    history = history or {}
    state = pull.get('state', 'open')
    merged = pull.get('merged', 'false')
    timeline = history.get('timeline', ())
    comments = history.get('comments', ())
    timeline_status = history.get('timeline_status')
    comments_status = history.get('comments_status')
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the pull-request body gate'
    workdir = Path(tmp) / 'workflow'
    (workdir / 'bin').mkdir(parents=True)
    stub = workdir / 'bin' / 'gh'
    stub.write_text(_GH_STUB, encoding='utf-8')
    stub.chmod(0o755)
    if parser_crlf:
        python_stub = workdir / 'bin' / 'python3'
        python_stub.write_text(_CRLF_PYTHON_STUB, encoding='utf-8')
        python_stub.chmod(0o755)
    fixture_path = workdir / 'issues.json'
    fixtures = dict(issues)
    fixtures['_timeline'] = list(timeline)
    fixtures['_comments'] = list(comments)
    if timeline_status is not None:
        fixtures['_timeline_status'] = timeline_status
    if comments_status is not None:
        fixtures['_comments_status'] = comments_status
    fixture_path.write_text(json.dumps(fixtures), encoding='utf-8')
    calls_path = workdir / 'calls.jsonl'
    calls_path.write_text('', encoding='utf-8')
    env = {
        **os.environ,
        'PATH': f'{workdir / "bin"}{os.pathsep}{os.environ["PATH"]}',
        'STUB_ISSUES': str(fixture_path),
        'STUB_CALLS': str(calls_path),
        'STUB_REAL_PYTHON': sys.executable,
        'GH_TOKEN': 'stub',
        'REPO': repo,
        'PR': pr,
        'ACTOR': actor,
        'BODY': body,
        'STATE': state,
        'MERGED': merged,
    }
    if comment_status is not None:
        env['STUB_COMMENT_STATUS'] = str(comment_status)
    result = subprocess.run(
        [bash, '-c', _workflow_script()], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    calls = [json.loads(line) for line in calls_path.read_text(
        encoding='utf-8').splitlines()]
    return calls, result


def _body_from(call):
    return next(arg[5:] for arg in call if arg.startswith('body='))


def _assert_no_mutation(calls):
    arguments = [arg for call in calls for arg in call]
    assert not any(arg.startswith(('body=', 'state='))
                   for arg in arguments), calls


def _assert_commented_then_closed(
        calls, *reasons, actor='alice', pr='99', repo='owner/repo'):
    comment_endpoint = f'repos/{repo}/issues/{pr}/comments'
    close_endpoint = f'repos/{repo}/pulls/{pr}'
    comment_calls = [call for call in calls if comment_endpoint in call]
    close_calls = [call for call in calls if close_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert len(close_calls) == 1, calls
    comment = comment_calls[0]
    close = close_calls[0]
    assert calls.index(comment) < calls.index(close), calls
    body = _body_from(comment)
    assert body.startswith(f'@{actor} — closing this automatically'), body
    assert CLOSE_MARKER in body, body
    assert '-F' in comment, comment
    assert re.search(r'#[0-9]', body) is None, body
    for reason in reasons:
        assert reason in body, body
    assert '**Related Issues and Pull Requests**' in body, body
    assert 'match the pull request template' in body, body
    assert '`Fixes #<issue>`' in body, body
    assert '/claim' in body, body
    assert 'reopen it automatically' in body, body
    assert 'Then reopen this same pull request.' not in body, body
    assert '-X' in close and close[close.index('-X') + 1] == 'PATCH', close
    assert 'state=closed' in close, close


def _assert_commented_then_reopened(
        calls, actor='alice', pr='99', repo='owner/repo'):
    comment = next(call for call in calls
                   if f'repos/{repo}/issues/{pr}/comments' in call)
    reopen = next(call for call in calls
                  if f'repos/{repo}/pulls/{pr}' in call)
    assert calls.index(comment) < calls.index(reopen), calls
    body = _body_from(comment)
    assert body.startswith(f'@{actor} —'), body
    assert 'reopening it automatically' in body, body
    assert '-X' in reopen and reopen[reopen.index('-X') + 1] == 'PATCH'
    assert 'state=open' in reopen, reopen


def test_parser_accepts_a_reference_in_the_real_template(_tmp):
    template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
        encoding='utf-8')
    body = template.replace('\nFixes #\n', '\nFixes #314\n', 1)
    assert body != template, 'the template no longer carries its Fixes marker'
    assert PR_BODY.referenced_issues(body) == [314]


def test_parser_returns_nothing_for_empty_or_missing_sections(_tmp):
    assert PR_BODY.referenced_issues(None) == []
    assert PR_BODY.referenced_issues('') == []
    assert PR_BODY.referenced_issues('## Summary\nFixes #31\n') == []
    assert PR_BODY.referenced_issues('Fixes #31\n') == []
    assert PR_BODY.referenced_issues(SECTION + '\n## Changes\n') == []


def test_parser_normalizes_windows_and_lone_carriage_returns(_tmp):
    crlf = '# Summary\r\ntext\r\n' + SECTION.replace('\n', '\r\n')
    assert PR_BODY.referenced_issues(crlf + 'Fixes #32\r\n') == [32]
    assert PR_BODY.referenced_issues(
        '# Summary\rtext\r' + SECTION.replace('\n', '\r') + '#33\r') == [33]


def test_parser_removes_complete_and_unterminated_html_comments(_tmp):
    body = (SECTION + '<!-- hidden across\nFixes #40\n-->\n'
            'Fixes #41\n')
    assert PR_BODY.referenced_issues(body) == [41]
    body = SECTION + 'Fixes #42\n<!-- #43\nFixes #44\n'
    assert PR_BODY.referenced_issues(body) == [42]
    hidden_section = '<!--\n' + SECTION + 'Fixes #45\n-->'
    assert PR_BODY.referenced_issues(hidden_section) == []


def test_parser_accepts_only_the_first_exact_atx_section_heading(_tmp):
    body = ('   ### rELATED iSSUES AND pULL rEQUESTS   ###\n'
            'Fixes #50\n# Changes\nFixes #51\n'
            + SECTION + 'Fixes #52\n')
    assert PR_BODY.referenced_issues(body) == [50]
    assert PR_BODY.referenced_issues(
        '    ' + SECTION + 'Fixes #53\n') == []
    assert PR_BODY.referenced_issues(
        '## Related Issues and Pull Requests later\nFixes #54\n') == []


def test_parser_stops_at_the_next_atx_heading(_tmp):
    body = SECTION + 'Fixes #60\n###### Testing\nFixes #61\n'
    assert PR_BODY.referenced_issues(body) == [60]


def test_parser_stops_at_a_setext_heading(_tmp):
    body = SECTION + 'Fixes #62\nSummary\n-------\nFixes #63\n'
    assert PR_BODY.referenced_issues(body) == [62]


def test_parser_does_not_treat_list_or_quote_as_setext_heading(tmp):
    del tmp
    bodies = [
        SECTION + '- Fixes #65\n---\n',
        SECTION + '> Fixes #66\n---\n',
    ]
    for number, body in zip((65, 66), bodies):
        assert PR_BODY.referenced_issues(body) == [number]


def test_parser_accepts_a_setext_section_heading(tmp):
    del tmp
    body = ('Related Issues and Pull Requests\n'
            '--------------------------------\n')
    assert PR_BODY.referenced_issues(body + 'Fixes #67\n') == [67]


def test_parser_ignores_atx_headings_inside_fenced_code(tmp):
    del tmp
    body = SECTION + '```markdown\n## Changes\n```\nFixes #64\n'
    assert PR_BODY.referenced_issues(body) == [64]


def test_parser_keeps_comment_markers_inside_fenced_code_literal(tmp):
    del tmp
    body = ('## Summary\n```html\n<!-- a template comment\n```\n'
            + SECTION + 'Fixes #68\n')
    assert PR_BODY.referenced_issues(body) == [68]
    body = ('## Summary\n<!-- a hidden fence\n```\n-->\n'
            + SECTION + 'Fixes #69\n')
    assert PR_BODY.referenced_issues(body) == [69]


def test_parser_removes_fenced_and_inline_code(tmp):
    del tmp
    body = (SECTION + '```text\nFixes #70\n```\nFixes #71\n'
            '~~~\nFixes #72\n')
    assert PR_BODY.referenced_issues(body) == [71]
    body = SECTION + 'Use `Fixes #73` or ``code ` #74``. Fixes #75\n'
    assert PR_BODY.referenced_issues(body) == [75]
    body = SECTION + 'A stray ` in prose does not hide Fixes #76\n'
    assert PR_BODY.referenced_issues(body) == [76]


def test_parser_ignores_indented_code_blocks(tmp):
    del tmp
    body = SECTION + '    Fixes #77\nFixes #78\n'
    assert PR_BODY.referenced_issues(body) == [78]


def test_parser_ignores_backslash_escaped_references(tmp):
    del tmp
    body = SECTION + r'Literal \#79, real #80.'
    assert PR_BODY.referenced_issues(body) == [80]


def test_parser_accepts_emphasized_or_colon_section_headings(tmp):
    del tmp
    headings = (
        '## **Related Issues and Pull Requests**:\n',
        '__Related Issues and Pull Requests__:\n'
        '=========================================\n',
    )
    for number, heading in zip((81, 82), headings):
        assert PR_BODY.referenced_issues(
            heading + f'Fixes #{number}\n') == [number]


def test_parser_filters_and_deduplicates_references_in_order(tmp):
    del tmp
    body = (SECTION + '#3 #1 #3 #0 abc#12 ##12 x_#13 '
            '(#42) and -#14\n')
    assert PR_BODY.referenced_issues(body) == [3, 1, 42, 14]


def test_parser_ignores_html_numeric_entities(tmp):
    del tmp
    body = SECTION + '&#8212; is an em dash. Fixes #15\n'
    assert PR_BODY.referenced_issues(body) == [15]


def test_cli_prints_one_issue_number_per_line(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py')],
        input=SECTION + 'Fixes #81 and #82 and #81\n', text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '81\n82\n', repr(result.stdout)
    body = _layout_body(
        ('Summary', 'A summary.'),
        ('Related Issues and Pull Requests', 'Fixes #81'),
        ('Changes', '- A change'),
        ('Testing', 'Ran tests.'))
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_body.py'),
         str(ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md')],
        input=body, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'issue:81\n', repr(result.stdout)


def _layout_body(*sections):
    return '\n\n'.join(
        f'## {title}\n{content}' for title, content in sections) + '\n'


def test_layout_accepts_required_sections_without_optional_footer(tmp):
    del tmp
    body = _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))
    assert PR_BODY.layout_errors(body, TEMPLATE) == []


def test_layout_reports_each_missing_or_empty_section(tmp):
    del tmp
    body = _layout_body(
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Changes', ''),
        ('Testing', 'Ran the suite.'),
        ('Breaking Changes', ''))
    errors = PR_BODY.layout_errors(body, TEMPLATE)
    assert 'Required section "Summary" is missing.' in errors
    assert 'Section "Changes" is empty.' in errors
    assert 'Section "Breaking Changes" is empty.' in errors


def test_layout_reports_unknown_duplicate_and_out_of_order_sections(tmp):
    del tmp
    body = _layout_body(
        ('Summary', 'First.'),
        ('Changes', '- Too early'),
        ('Notes', 'Unknown.'),
        ('Summary', 'Again.'),
        ('Related Issues and Pull Requests', 'Fixes #91'),
        ('Testing', 'Ran the suite.'))
    errors = PR_BODY.layout_errors(body, TEMPLATE)
    assert 'Section "Notes" is not defined by the template.' in errors
    assert 'Section "Summary" appears more than once.' in errors
    assert 'Section "Related Issues and Pull Requests" is out of order.' \
        in errors


def test_layout_names_retained_template_instructions_separately(tmp):
    del tmp
    errors = PR_BODY.layout_errors(TEMPLATE, TEMPLATE)
    assert 'Remove the template instruction comments.' in errors


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


def test_gh_stub_models_include_response_headers(tmp):
    workdir = Path(tmp) / 'gh-headers'
    workdir.mkdir()
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
    result = subprocess.run(
        [sys.executable, str(stub), 'api', '--include',
         'repos/owner/repo/issues/1', '--jq', '.assignees[].login'],
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        'cache-control: private, max-age=60',
        'content-type: application/json; charset=utf-8',
        'x-github-media-type: github.v3; format=json',
    ], result.stdout


def _valid_body(references='Fixes #101'):
    return _layout_body(
        ('Summary', 'One sentence.'),
        ('Related Issues and Pull Requests', references),
        ('Changes', '- One change'),
        ('Testing', 'Ran the suite.'))


def _closed_by(login):
    return [{'event': 'closed', 'actor': {'login': login}}]


def test_open_admissible_body_is_left_untouched(tmp):
    calls, result = _run_workflow(
        tmp, _valid_body('Refs #101 and #102'),
        {'101': _issue('bob'), '102': _issue('carol')},
        actor='carol', repo='acme/widget', parser_crlf=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'repos/acme/widget/issues/101' in calls[0], calls
    assert 'repos/acme/widget/issues/102' in calls[1], calls
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
        malformed, 'Section "Notes" is not defined by the template.')


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
    lookups = [call for call in calls if '--include' in call]
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
    assert len([call for call in missing if '--include' in call]) == 2
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
