#!/usr/bin/env python3
"""Pull-request issue references and the workflow that enforces claims."""
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


PR_CLAIM = _util.load(ROOT / 'scripts' / 'ci' / 'pr_claim.py')
SECTION = '## Related Issues and Pull Requests\n'
_COMMENT_TAIL = (
    ' — closing this automatically, and it is recoverable: read on.\n\n'
    """A pull request here has to name the issue it resolves, in its
**Related Issues and Pull Requests** section, and that issue has to be
assigned to you before you start. This one does not, so nothing tells other
contributors that the work is taken.

Two steps, in this order:

1. Comment `/claim` on the issue you are fixing. That assigns it to you, no
   write access needed — see CONTRIBUTING.md.
2. Edit this pull request's body so its **Related Issues and Pull Requests**
   section names that issue: `Fixes #<issue>`, with the real number.

Then reopen this same pull request. A closed pull request still accepts edits
and comments, so there is no need to open a second one and nothing here is
lost.""")

_GH_STUB = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
fixtures = json.loads(pathlib.Path(os.environ['STUB_ISSUES']).read_text())
calls = pathlib.Path(os.environ['STUB_CALLS'])
argv = sys.argv[1:]
with calls.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(argv) + chr(10))
endpoint = next((arg for arg in argv if arg.startswith('repos/')), '')
parts = endpoint.split('/')
if len(parts) == 5 and parts[-2] == 'issues' and parts[-1].isdigit():
    issue = fixtures.get(parts[-1])
    status = 404 if issue is None else issue.get('_http_status', 200)
    if '--include' in argv:
        reason = {200: 'OK', 404: 'Not Found'}.get(status, 'Error')
        print(f'HTTP/2.0 {status} {reason}')
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
"""

_CRLF_PYTHON_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == scripts/ci/pr_claim.py ||
      "${1:-}" == */scripts/ci/pr_claim.py ]]; then
  "$STUB_REAL_PYTHON" "$@" |
    "$STUB_REAL_PYTHON" -c 'import sys
data = sys.stdin.buffer.read().replace(b"\r\n", b"\n")
sys.stdout.buffer.write(data.replace(b"\n", b"\r\n"))'
else
  exec "$STUB_REAL_PYTHON" "$@"
fi
"""


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'pr-claim.yml').read_text(
        encoding='utf-8')


def _workflow_script():
    """The pr-claim.yml run block, dedented and ready for Bash."""
    _, marker, after = _workflow().partition('        run: |\n')
    assert marker, 'pr-claim.yml has no literal run block'
    first = after.splitlines()[0]
    indent = len(first) - len(first.lstrip())
    assert first.strip() and indent, 'pr-claim.yml run block has no body'
    lines = []
    for line in after.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        lines.append(line[indent:])
    return chr(10).join(lines)


def _issue(*assignees, pull_request=False):
    issue = {
        'number': 0,
        'state': 'open',
        'assignees': [{'login': login} for login in assignees],
    }
    if pull_request:
        issue['pull_request'] = {'url': 'https://github.com/pulls/0'}
    return issue


def _run_workflow(
        tmp, body, issues, actor='alice', pr='99', repo='owner/repo',
        parser_crlf=False):
    """Execute the real workflow shell against the controlled gh boundary."""
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the pull-request claim gate'
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
    fixture_path.write_text(json.dumps(issues), encoding='utf-8')
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
    }
    result = subprocess.run(
        [bash, '-c', _workflow_script()], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    calls = [json.loads(line) for line in calls_path.read_text(
        encoding='utf-8').splitlines()]
    return calls, result


def _assert_commented_then_closed(
        calls, actor='alice', pr='99', repo='owner/repo'):
    comment_endpoint = f'repos/{repo}/issues/{pr}/comments'
    close_endpoint = f'repos/{repo}/pulls/{pr}'
    comment_calls = [call for call in calls if comment_endpoint in call]
    close_calls = [call for call in calls if close_endpoint in call]
    assert len(comment_calls) == 1, calls
    assert len(close_calls) == 1, calls
    comment = comment_calls[0]
    close = close_calls[0]
    assert calls.index(comment) < calls.index(close), calls
    body = next(arg[5:] for arg in comment if arg.startswith('body='))
    assert body == f'@{actor}{_COMMENT_TAIL}', body
    assert re.search(r'#[0-9]', body) is None, body
    assert 'has to name the issue it resolves' in body, body
    assert 'section names that issue: `Fixes #<issue>`' in body, body
    assert '/claim' in body, body
    assert 'Then reopen this same pull request.' in body, body
    assert '-X' in close and close[close.index('-X') + 1] == 'PATCH', close
    assert 'state=closed' in close, close


def test_parser_accepts_a_reference_in_the_real_template(tmp):
    del tmp
    template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
        encoding='utf-8')
    body = template.replace('\nFixes #\n', '\nFixes #314\n', 1)
    assert body != template, 'the template no longer carries its Fixes marker'
    assert PR_CLAIM.referenced_issues(body) == [314]


def test_parser_returns_nothing_for_empty_or_missing_sections(tmp):
    del tmp
    assert PR_CLAIM.referenced_issues(None) == []
    assert PR_CLAIM.referenced_issues('') == []
    assert PR_CLAIM.referenced_issues('## Summary\nFixes #31\n') == []
    assert PR_CLAIM.referenced_issues('Fixes #31\n') == []
    assert PR_CLAIM.referenced_issues(SECTION + '\n## Changes\n') == []


def test_parser_normalizes_windows_and_lone_carriage_returns(tmp):
    del tmp
    crlf = '# Summary\r\ntext\r\n' + SECTION.replace('\n', '\r\n')
    assert PR_CLAIM.referenced_issues(crlf + 'Fixes #32\r\n') == [32]
    assert PR_CLAIM.referenced_issues(
        '# Summary\rtext\r' + SECTION.replace('\n', '\r') + '#33\r') == [33]


def test_parser_removes_complete_and_unterminated_html_comments(tmp):
    del tmp
    body = (SECTION + '<!-- hidden across\nFixes #40\n-->\n'
            'Fixes #41\n')
    assert PR_CLAIM.referenced_issues(body) == [41]
    body = SECTION + 'Fixes #42\n<!-- #43\nFixes #44\n'
    assert PR_CLAIM.referenced_issues(body) == [42]
    hidden_section = '<!--\n' + SECTION + 'Fixes #45\n-->'
    assert PR_CLAIM.referenced_issues(hidden_section) == []


def test_parser_accepts_only_the_first_exact_atx_section_heading(tmp):
    del tmp
    body = ('   ### rELATED iSSUES AND pULL rEQUESTS   ###\n'
            'Fixes #50\n# Changes\nFixes #51\n'
            + SECTION + 'Fixes #52\n')
    assert PR_CLAIM.referenced_issues(body) == [50]
    assert PR_CLAIM.referenced_issues(
        '    ' + SECTION + 'Fixes #53\n') == []
    assert PR_CLAIM.referenced_issues(
        '## Related Issues and Pull Requests later\nFixes #54\n') == []


def test_parser_stops_at_the_next_atx_heading(tmp):
    del tmp
    body = SECTION + 'Fixes #60\n###### Testing\nFixes #61\n'
    assert PR_CLAIM.referenced_issues(body) == [60]


def test_parser_stops_at_a_setext_heading(tmp):
    del tmp
    body = SECTION + 'Fixes #62\nSummary\n-------\nFixes #63\n'
    assert PR_CLAIM.referenced_issues(body) == [62]


def test_parser_does_not_treat_list_or_quote_as_setext_heading(tmp):
    del tmp
    bodies = [
        SECTION + '- Fixes #65\n---\n',
        SECTION + '> Fixes #66\n---\n',
    ]
    for number, body in zip((65, 66), bodies):
        assert PR_CLAIM.referenced_issues(body) == [number]


def test_parser_accepts_a_setext_section_heading(tmp):
    del tmp
    body = ('Related Issues and Pull Requests\n'
            '--------------------------------\n')
    assert PR_CLAIM.referenced_issues(body + 'Fixes #67\n') == [67]


def test_parser_ignores_atx_headings_inside_fenced_code(tmp):
    del tmp
    body = SECTION + '```markdown\n## Changes\n```\nFixes #64\n'
    assert PR_CLAIM.referenced_issues(body) == [64]


def test_parser_keeps_comment_markers_inside_fenced_code_literal(tmp):
    del tmp
    body = ('## Summary\n```html\n<!-- a template comment\n```\n'
            + SECTION + 'Fixes #68\n')
    assert PR_CLAIM.referenced_issues(body) == [68]
    body = ('## Summary\n<!-- a hidden fence\n```\n-->\n'
            + SECTION + 'Fixes #69\n')
    assert PR_CLAIM.referenced_issues(body) == [69]


def test_parser_removes_fenced_and_inline_code(tmp):
    del tmp
    body = (SECTION + '```text\nFixes #70\n```\nFixes #71\n'
            '~~~\nFixes #72\n')
    assert PR_CLAIM.referenced_issues(body) == [71]
    body = SECTION + 'Use `Fixes #73` or ``code ` #74``. Fixes #75\n'
    assert PR_CLAIM.referenced_issues(body) == [75]
    body = SECTION + 'A stray ` in prose does not hide Fixes #76\n'
    assert PR_CLAIM.referenced_issues(body) == [76]


def test_parser_ignores_indented_code_blocks(tmp):
    del tmp
    body = SECTION + '    Fixes #77\nFixes #78\n'
    assert PR_CLAIM.referenced_issues(body) == [78]


def test_parser_ignores_backslash_escaped_references(tmp):
    del tmp
    body = SECTION + r'Literal \#79, real #80.'
    assert PR_CLAIM.referenced_issues(body) == [80]


def test_parser_accepts_emphasized_or_colon_section_headings(tmp):
    del tmp
    headings = (
        '## **Related Issues and Pull Requests**:\n',
        '__Related Issues and Pull Requests__:\n'
        '=========================================\n',
    )
    for number, heading in zip((81, 82), headings):
        assert PR_CLAIM.referenced_issues(
            heading + f'Fixes #{number}\n') == [number]


def test_parser_filters_and_deduplicates_references_in_order(tmp):
    del tmp
    body = (SECTION + '#3 #1 #3 #0 abc#12 ##12 x_#13 '
            '(#42) and -#14\n')
    assert PR_CLAIM.referenced_issues(body) == [3, 1, 42, 14]


def test_parser_ignores_html_numeric_entities(tmp):
    del tmp
    body = SECTION + '&#8212; is an em dash. Fixes #15\n'
    assert PR_CLAIM.referenced_issues(body) == [15]


def test_cli_prints_one_issue_number_per_line(tmp):
    del tmp
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'pr_claim.py')],
        input=SECTION + 'Fixes #81 and #82 and #81\n', text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '81\n82\n', repr(result.stdout)


def test_workflow_keeps_its_claim_gate_shape(tmp):
    del tmp
    workflow = _workflow()
    assert top_level_mapping(workflow, 'permissions') == {
        'pull-requests': 'write',
        'issues': 'read',
    }
    triggers = _workflow_triggers(workflow, 'pr-claim.yml')
    assert set(triggers) == {'pull_request_target'}, sorted(triggers)
    types = next(
        line.partition('types:')[2].strip()
        for line in triggers['pull_request_target']
        if line.strip().startswith('types:'))
    assert types == '[opened, edited, reopened]', types
    assert ('pull_request_target:  '
            '# zizmor: ignore[dangerous-triggers]') in workflow
    condition = job_scalar(workflow, 'claim', 'if')
    assert "github.event.pull_request.state == 'open'" in condition
    assert "github.event.pull_request.user.type != 'Bot'" in condition
    assert 'draft' not in condition.casefold(), condition
    steps = step_mappings(workflow, 'claim')
    checkout = [step for step in steps
                if str(step.get('uses', '')).startswith('actions/checkout@')]
    assert len(checkout) == 1, steps
    assert checkout[0]['uses'] == (
        'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1')
    assert checkout[0].get('with') == {'persist-credentials': 'false'}
    assert '${{' not in _workflow_script(), (
        'an expression is interpolated into contributor-controlled shell')


def test_workflow_passes_for_an_issue_assigned_to_the_author(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #101\n', {'101': _issue('alice')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(calls) == 1, calls
    assert 'repos/owner/repo/issues/101' in calls[0], calls
    assert 'issue 101' in result.stdout, result.stdout


def test_workflow_closes_when_the_issue_belongs_to_someone_else(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #102\n', {'102': _issue('bob')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(calls)


def test_workflow_requires_an_exact_assignee_login(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #108\n', {'108': _issue('alicebob')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(calls)


def test_workflow_closes_when_the_issue_is_unassigned(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #103\n', {'103': _issue()})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(calls)


def test_workflow_closes_when_the_body_references_nothing(tmp):
    calls, result = _run_workflow(tmp, '## Summary\nNo issue here.\n', {})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(calls)


def test_workflow_does_not_accept_a_pull_request_number(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #104\n',
        {'104': _issue('alice', pull_request=True)})
    assert result.returncode == 0, (result.stdout, result.stderr)
    _assert_commented_then_closed(calls)


def test_workflow_skips_a_missing_issue_without_failing(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #105\n', {})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert any('repos/owner/repo/issues/105' in call for call in calls), calls
    _assert_commented_then_closed(calls)


def test_workflow_skips_a_missing_nonfirst_issue(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Refs #109 and #110\n',
        {'109': _issue('bob')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'repos/owner/repo/issues/109' in calls[0], calls
    assert 'repos/owner/repo/issues/110' in calls[1], calls
    _assert_commented_then_closed(calls)


def test_workflow_continues_after_a_missing_first_issue(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Refs #111 and #112\n',
        {'112': _issue('alice')})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(calls) == 2, calls
    assert 'repos/owner/repo/issues/111' in calls[0], calls
    assert 'repos/owner/repo/issues/112' in calls[1], calls
    assert 'issue 112' in result.stdout, result.stdout


def test_workflow_fails_without_closing_on_a_non_404_lookup_error(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #113\n',
        {'113': {'_http_status': 502}})
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert len(calls) == 1, calls
    assert 'repos/owner/repo/issues/113' in calls[0], calls
    assert 'HTTP 502' in result.stderr, result.stderr


def test_workflow_passes_when_only_the_second_issue_matches(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Refs #106 and #107\n',
        {'106': _issue('bob'), '107': _issue('alice')},
        parser_crlf=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(calls) == 2, calls
    assert 'repos/owner/repo/issues/106' in calls[0], calls
    assert 'repos/owner/repo/issues/107' in calls[1], calls
    assert 'issue 107' in result.stdout, result.stdout


def test_workflow_uses_the_event_author_login(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #114\n', {'114': _issue('carol')},
        actor='carol')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(calls) == 1, calls
    assert 'issue 114' in result.stdout, result.stdout


def test_workflow_uses_the_event_repository(tmp):
    calls, result = _run_workflow(
        tmp, SECTION + 'Fixes #115\n', {'115': _issue('alice')},
        repo='acme/widget')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(calls) == 1, calls
    assert 'repos/acme/widget/issues/115' in calls[0], calls


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prclaim_')


if __name__ == '__main__':
    raise SystemExit(main())
