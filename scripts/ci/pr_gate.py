#!/usr/bin/env python3
"""Apply the pull-request body admission gate through GitHub's API."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .pr_body import (
        layout_errors, parse_rendered, referenced_issues,
        related_may_reference, retains_instruction_comment,
    )
else:
    from pr_body import (
        layout_errors, parse_rendered, referenced_issues,
        related_may_reference, retains_instruction_comment,
    )


ROOT = Path(__file__).resolve().parents[2]
BOT = 'github-actions[bot]'
MARKER = '<!-- pr-gate -->'
CLOSED_MARKER = '<!-- pr-gate: closed -->'
OVERFLOW_REASON = (
    'This body names more than 20 issue references, so only the first 20 '
    'were checked.')
UNCLAIMED_REASON = 'No checked issue is assigned to you.'
INSTRUCTION_REASON = 'Remove the template instruction comments.'
_STATUS_LINE = re.compile(r'^HTTP/\S+ ([0-9]{3})(?: |$)')


class Response(NamedTuple):
    status: int
    data: object


class GhApi:
    """Runs `gh api`. request() never raises for an HTTP status; it raises
    RuntimeError only when gh could not be run or its output is unparsable.
    """

    def __init__(self):
        self.gh = shutil.which('gh')

    def request(self, method: str, endpoint: str,
                payload: dict | None = None) -> Response:
        return self._request(method, endpoint, payload)

    def _request(self, method, endpoint, payload=None, fields=()):
        if self.gh is None:
            raise RuntimeError('gh was not found on PATH')
        arguments = [
            self.gh, 'api', '--include', '-X', method, endpoint]
        for field in fields:
            arguments.extend(('-f', field))
        with tempfile.TemporaryDirectory(prefix='pr-gate-') as directory:
            if payload:
                path = Path(directory) / 'payload.json'
                path.write_text(json.dumps(payload), encoding='utf-8')
                arguments.extend(('--input', str(path)))
            try:
                completed = subprocess.run(
                    arguments, capture_output=True, text=True,
                    check=False)
            except OSError as error:
                raise RuntimeError(f'could not run gh: {error}') from error
        lines = completed.stdout.splitlines()
        match = _STATUS_LINE.match(lines[0]) if lines else None
        if match is None:
            detail = (' '.join(completed.stderr.split())
                      or 'no HTTP status in output')
            raise RuntimeError(f'could not read gh response: {detail}')
        try:
            separator = lines.index('')
        except ValueError as error:
            raise RuntimeError('could not read gh response headers') from error
        body = '\n'.join(lines[separator + 1:])
        try:
            data = json.loads(body) if body else None
        except json.JSONDecodeError as error:
            raise RuntimeError('could not parse gh response body') from error
        return Response(int(match.group(1)), data)

    def paginate(self, endpoint: str) -> Response:
        items = []
        for page in range(1, 51):
            response = self._request(
                'GET', endpoint,
                fields=('per_page=100', f'page={page}'))
            if response.status != 200:
                return response
            if not isinstance(response.data, list):
                raise RuntimeError('paginated gh response is not a list')
            items.extend(response.data)
            if len(response.data) < 100:
                return Response(200, items)
        raise RuntimeError('gh pagination exceeded 50 pages')


class _GateError(RuntimeError):
    pass


def _response(api, method, endpoint, payload=None):
    try:
        response = api.request(method, endpoint, payload)
    except RuntimeError as error:
        raise _GateError(str(error)) from error
    return response


def _page(api, endpoint):
    try:
        response = api.paginate(endpoint)
    except RuntimeError as error:
        raise _GateError(str(error)) from error
    if response.status != 200 or not isinstance(response.data, list):
        raise _GateError(f'GitHub returned {response.status} for {endpoint}')
    return response.data


def _read(api, method, endpoint, payload=None):
    response = _response(api, method, endpoint, payload)
    if response.status != 200:
        raise _GateError(f'GitHub returned {response.status} for {endpoint}')
    return response.data


def _write(api, method, endpoint, payload):
    response = _response(api, method, endpoint, payload)
    if not 200 <= response.status < 300:
        raise _GateError(f'GitHub returned {response.status} for {endpoint}')


def _gate_comment(comments):
    for comment in comments:
        user = comment.get('user') or {}
        body = comment.get('body') or ''
        lines = (line.rstrip('\r') for line in body.splitlines())
        if user.get('login') == BOT and MARKER in lines:
            return comment
    return None


def _closed_by_gate(timeline, comment):
    closed = [event for event in timeline if event.get('event') == 'closed']
    actor = (closed[-1].get('actor') or {}).get('login') if closed else None
    body = (comment or {}).get('body') or ''
    lines = (line.rstrip('\r') for line in body.splitlines())
    return actor == BOT and CLOSED_MARKER in lines


def _claim(api, repo, issues, actor):
    claimed = None
    for number in issues[:20]:
        endpoint = f'repos/{repo}/issues/{number}'
        response = _response(api, 'GET', endpoint)
        if response.status == 404:
            continue
        if response.status != 200 or not isinstance(response.data, dict):
            raise _GateError(
                f'GitHub returned {response.status} for {endpoint}')
        if 'pull_request' in response.data:
            continue
        assignees = response.data.get('assignees') or []
        if any(item.get('login') == actor for item in assignees):
            claimed = claimed or number
    return claimed


def _reasons_block(reasons):
    return '\n'.join(f'- {reason}' for reason in reasons)


def _inadmissible_text(actor, reasons, closed):
    if closed:
        opening = (
            f'@{actor} — closing this automatically; it is recoverable, '
            'read on.\n'
            f'{MARKER}\n{CLOSED_MARKER}')
        ending = (
            'The gate re-checks every edit of this closed pull request and '
            'reopens it\nautomatically once every condition passes. Nothing '
            'here is lost.')
    else:
        opening = (
            f'@{actor} — this pull request needs changes before it can be '
            f'reviewed.\n{MARKER}')
        ending = (
            'The gate re-checks every edit; there is no need to open a second '
            'pull\nrequest.')
    return f"""{opening}

{_reasons_block(reasons)}

Fix every item above, including these two repository requirements:

1. Comment `/claim` on the issue you are fixing. That assigns it to you,
   no write access needed — see CONTRIBUTING.md.
2. Edit this same pull request so its sections match the pull request
   template. Its **Related Issues and Pull Requests** section must name
   the claimed issue as `Fixes #<issue>`, with the real number.

{ending}
"""


def _reopen_text(actor):
    return (
        f'@{actor} — the body now names a claimed issue and matches '
        'the pull request\ntemplate, so I am reopening it automatically.\n'
        f'{MARKER}\n')


def _resolved_text(actor):
    return (
        f'@{actor} — every condition now passes; nothing further is needed '
        f'from you.\n{MARKER}\n')


def _write_comment(api, repo, pr, comment, body):
    if comment is None:
        endpoint = f'repos/{repo}/issues/{pr}/comments'
        _write(api, 'POST', endpoint, {'body': body})
    else:
        endpoint = f"repos/{repo}/issues/comments/{comment['id']}"
        _write(api, 'PATCH', endpoint, {'body': body})


def _run(api, repo, pr, actor, template):
    pull_endpoint = f'repos/{repo}/pulls/{pr}'
    pull = _read(api, 'GET', pull_endpoint)
    if not isinstance(pull, dict):
        raise _GateError('pull request response is not an object')
    body = pull.get('body') or ''
    state = pull.get('state')
    if pull.get('merged'):
        print('nothing to do')
        return 0

    comments = _page(
        api, f'repos/{repo}/issues/{pr}/comments')
    comment = _gate_comment(comments)
    if state == 'closed':
        timeline = _page(
            api, f'repos/{repo}/issues/{pr}/timeline')
        if not _closed_by_gate(timeline, comment):
            print(f'pull request {pr} was not closed by the gate')
            return 0

    rendered = _read(
        api, 'POST', 'markdown',
        {'text': body, 'mode': 'gfm', 'context': repo})
    if not isinstance(rendered, str):
        raise _GateError('markdown response is not text')
    try:
        sections = parse_rendered(rendered, repo)
    except ValueError as error:
        raise _GateError(
            f'could not analyze rendered body: {error}') from error

    layout = layout_errors(sections, template)
    if retains_instruction_comment(body, template):
        layout.append(INSTRUCTION_REASON)
    issues = referenced_issues(sections)
    claimed = _claim(api, repo, issues, actor)
    reasons = list(layout)
    if len(issues) > 20:
        reasons.append(OVERFLOW_REASON)
    elif claimed is None:
        reasons.append(UNCLAIMED_REASON)
    closable = bool(layout) or not related_may_reference(
        body, sections, template)

    if not reasons:
        if state == 'closed':
            _write_comment(api, repo, pr, comment, _reopen_text(actor))
            _write(api, 'PATCH', pull_endpoint, {'state': 'open'})
            print('reopened')
        elif comment is not None:
            _write_comment(api, repo, pr, comment, _resolved_text(actor))
            print(f'covered by claimed issue {claimed}')
        else:
            print(f'covered by claimed issue {claimed}')
        return 0

    if state == 'closed':
        _write_comment(
            api, repo, pr, comment,
            _inadmissible_text(actor, reasons, True))
        print('commented')
        return 0

    _write_comment(
        api, repo, pr, comment,
        _inadmissible_text(actor, reasons, closable))
    if closable:
        _write(api, 'PATCH', pull_endpoint, {'state': 'closed'})
        print('closed')
    else:
        print('commented')
    return 0


def run(api, repo: str, pr: str, actor: str, template: str) -> int:
    try:
        return _run(api, repo, pr, actor, template)
    except _GateError as error:
        print(f'pr gate failed: {error}', file=sys.stderr)
        return 1


def main() -> int:
    try:
        repo = os.environ['REPO']
        pr = os.environ['PR']
        actor = os.environ['ACTOR']
        template = (ROOT / '.github' / 'PULL_REQUEST_TEMPLATE.md').read_text(
            encoding='utf-8')
    except (KeyError, OSError) as error:
        print(f'pr gate failed: {error}', file=sys.stderr)
        return 1
    return run(GhApi(), repo, pr, actor, template)


if __name__ == '__main__':
    raise SystemExit(main())
