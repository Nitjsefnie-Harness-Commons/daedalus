#!/usr/bin/env python3
"""The aggregate job's verdict: which `needs` results gate this run.

Branch protection names one required check, the aggregate. The job runs
with `if: always()` and used to fail a `cancelled` dependency blindly, so
with `cancel-in-progress` on (concurrency group `tests-` + ref) every push
superseding an in-flight run left a red aggregate on the old SHA. The rule
ci_wait.py settled ports here: a cancelled run with a strictly newer run of
the same workflow is the remnant of that supersession and gates nothing;
with no newer sibling the cancel is deliberate and stays a failure.

`github.head_branch` names the population the query reads: the pushed
branch on `push`, the pull request's head branch on `pull_request` — the
branch a newer run of the same workflow appears on in both events, which a
head-SHA-scoped query could never see. One known edge: a fork pull request
reusing another pull request's branch name shares that branch's run
population without sharing its concurrency group, so one run can be proven
superseded by the other's newer run. Accepted: rare, and the older run's
cancel was then deliberate while a newer run of that branch name was
running anyway.
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
    """One failed API read; the caller answers conservatively."""


def allowed_results(name):
    """The results that pass for one dependency, strict or not."""
    return frozenset({'success'}) if name in STRICT else ALLOWED


def classify_needs(needs):
    """Split the refused dependencies into hard and cancelled ones."""
    refused = [name for name, details in needs.items()
               if details['result'] not in allowed_results(name)]
    hard = [name for name in refused
            if needs[name]['result'] != CANCELLED]
    cancelled = [name for name in refused
                 if needs[name]['result'] == CANCELLED]
    return hard, cancelled


def _workflow_of(run):
    """The workflow a run belongs to: its id, or its path when id is absent."""
    return run.get('workflow_id') or run.get('path')


def _started_key(run):
    """(start, id): the instant the run began, tie-broken by numeric id."""
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
    """The strictly newest same-workflow run, or None when there is none."""
    newer = [run for run in runs
             if _workflow_of(run) == _workflow_of(mine)
             and _started_key(run) > _started_key(mine)]
    return max(newer, key=_started_key, default=None)


def superseded(mine, runs):
    """Whether a strictly newer run of the same workflow exists."""
    return superseding_run(mine, runs) is not None


def decide(needs, mine=None, runs=None):
    """Return (verdict, message) for one needs mapping and query evidence.

    `mine` and `runs` carry the supersession query's answer; either None
    means the query failed and a cancelled dependency cannot be proven
    superseded.
    """
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
    """Parse one JSON value, or several concatenated by --paginate."""
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
    """This run's record, or None when the query or its shape fails."""
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
    """Every run of this workflow on one branch, or None on failure."""
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
    """The verdict, reading the supersession evidence only when needed."""
    if not classify_needs(needs)[1]:
        return decide(needs)
    mine = own_run(repository, run_id, read)
    runs = branch_runs(repository, branch, read)
    return decide(needs, mine, runs)


def gh_read(argv):
    """One fresh gh read; any failure raises QueryError."""
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
