#!/usr/bin/env python3
"""The aggregate job's verdict: which `needs` results gate this run.

A `cancelled` dependency used to fail the aggregate blindly: with
`cancel-in-progress` on, every superseding push left a red aggregate on
the old SHA. The rule ci_wait.py settled ports here — a cancelled run
with a strictly newer run of the same workflow gates nothing, while a
deliberate cancel stays a failure.

The supersession query reads the head branch from
`github.event.pull_request.head.ref || github.ref_name` — the pushed
branch on `push`, the pull request's head branch on `pull_request` —
where a newer run of the same workflow appears in both events. One
accepted edge: runs group by branch name, so a fork pull request
reusing a branch name from another fork or the base repository is
superseded by whatever newer run shares that name, even one testing
different commits — a rare, accepted false green.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import quote

WORKFLOW = '.github/workflows/tests.yml'
STRICT = frozenset(
    {'changes', 'pycodestyle', 'pylint', 'pyright', 'eslint'})
ALLOWED = frozenset({'success', 'skipped'})
CANCELLED = 'cancelled'
OLDEST = datetime.min.replace(tzinfo=timezone.utc)
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')

PASSED = 'passed'
FAILED = 'failed'
STRICT_SKIPPED = 'strict-skipped'
CANCEL_SUPERSEDED = 'cancelled-superseded'
CANCEL_DELIBERATE = 'cancelled-deliberate'
QUERY_FAILED = 'query-failed'
GREEN = frozenset({PASSED, CANCEL_SUPERSEDED})


class QueryError(RuntimeError):
    pass


def allowed_results(name):
    return frozenset({'success'}) if name in STRICT else ALLOWED


def classify_needs(needs):
    refused = [name for name, details in needs.items()
               if details['result'] not in allowed_results(name)]
    hard = [name for name in refused
            if needs[name]['result'] != CANCELLED]
    cancelled = [name for name in refused
                 if needs[name]['result'] == CANCELLED]
    return hard, cancelled


def _workflow_of(run):
    return run.get('workflow_id') or run.get('path')


def _started_key(run):
    text = run.get('run_started_at') or run.get('created_at')
    stamp = OLDEST
    if text:
        try:
            stamp = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
        except ValueError:
            stamp = OLDEST
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp, int(run.get('id') or 0)


def superseding_run(mine, runs):
    newer = [run for run in runs
             if _workflow_of(run) == _workflow_of(mine)
             and _started_key(run) > _started_key(mine)]
    return max(newer, key=_started_key, default=None)


def superseded(mine, runs):
    return superseding_run(mine, runs) is not None


def decide(needs, mine=None, runs=None):
    """Return (verdict, message); None evidence means the query failed."""
    hard, cancelled = classify_needs(needs)
    if not hard and not cancelled:
        return PASSED, ('All dependencies succeeded: '
                        + ', '.join(sorted(needs)))
    named = ', '.join(f"{name}={needs[name]['result']}"
                      for name in hard + cancelled)
    if hard:
        verdict = (STRICT_SKIPPED
                   if all(needs[name]['result'] == 'skipped'
                          for name in hard) else FAILED)
        return verdict, f'Dependencies not successful: {named}'
    if mine is None or runs is None:
        return QUERY_FAILED, ('the supersession query failed, so the '
                              'cancel could not be proven superseded: '
                              f'{named}')
    newer = superseding_run(mine, runs)
    if newer is None:
        return CANCEL_DELIBERATE, ('no newer run of this workflow exists, '
                                   'so the cancel is deliberate: '
                                   f'{named}')
    return CANCEL_SUPERSEDED, (
        'the cancelled dependencies were superseded by run '
        f'{newer.get("id")} ({newer.get("html_url") or "?"}): {named}')


def _decode(payload):
    payload = payload.strip()
    if not payload:
        return []
    decoder = json.JSONDecoder()
    out = []
    index = 0
    while index < len(payload):
        try:
            chunk, index = decoder.raw_decode(payload, index)
        except json.JSONDecodeError as exc:
            raise QueryError(f'unparseable gh output: {exc}') from exc
        out.append(chunk)
        while index < len(payload) and payload[index].isspace():
            index += 1
    return out


def own_run(repository, run_id, read):
    if not (REPOSITORY.fullmatch(repository or '')
            and str(run_id or '').isascii()
            and str(run_id or '').isdigit()):
        return None
    try:
        chunks = _decode(read([
            'gh', 'api', '-H', 'Cache-Control: no-cache',
            f'repos/{repository}/actions/runs/{run_id}']))
    except QueryError:
        return None
    return (chunks[0] if len(chunks) == 1 and isinstance(chunks[0], dict)
            else None)


def branch_runs(repository, branch, read):
    if not (REPOSITORY.fullmatch(repository or '') and branch):
        return None
    runs = []
    try:
        for chunk in _decode(read([
                'gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
                f'repos/{repository}/actions/workflows/{WORKFLOW}/runs'
                f'?branch={quote(branch, safe="")}&per_page=100'])):
            if isinstance(chunk, dict):
                runs.extend(chunk.get('workflow_runs') or [])
    except QueryError:
        return None
    return runs


def evaluate(needs, repository, run_id, branch, read):
    if not classify_needs(needs)[1]:
        return decide(needs)
    mine = own_run(repository, run_id, read)
    runs = branch_runs(repository, branch, read)
    return decide(needs, mine, runs)


def gh_read(argv):
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, UnicodeDecodeError,
            OSError) as exc:
        raise QueryError(f'gh failed: {exc}') from exc
    if proc.returncode != 0:
        raise QueryError(proc.stderr.strip()[:400])
    return proc.stdout


def main():
    try:
        needs = json.loads(os.environ['NEEDS_JSON'])
    except (KeyError, ValueError):
        print('NEEDS_JSON is missing or not JSON', file=sys.stderr)
        return 1
    verdict, message = evaluate(
        needs, os.environ.get('REPOSITORY', ''),
        os.environ.get('RUN_ID', ''), os.environ.get('HEAD_BRANCH', ''),
        gh_read)
    print(message, file=sys.stdout if verdict in GREEN else sys.stderr)
    return 0 if verdict in GREEN else 1


if __name__ == '__main__':
    sys.exit(main())
