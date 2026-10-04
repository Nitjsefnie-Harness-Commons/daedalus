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
import time
from datetime import datetime, timezone
from urllib.parse import quote

# Both selectors are FILE NAMES. The actions API's workflow selector
# takes `secrets.yml` or a numeric id and answers `Not Found` to the
# path — which a gate that fails closed turns into a required check that
# never once passes, or into a supersession query that never reaches the
# API at all.
WORKFLOW = 'tests.yml'
SECRETS_WORKFLOW = 'secrets.yml'
SECRETS_JOB = 'gitleaks'
POLL_BOUND_ENV = 'SECRETS_POLL_BOUND_S'
# The gitleaks job's own 10-minute backstop plus queueing; the job this
# runs in carries timeout-minutes 20, leaving 8 minutes to report.
DEFAULT_POLL_BOUND_S = 720.0
POLL_INTERVAL_S = 20.0
# One `gh` read, bounded, because a hung query must not outlive the bound
# it is being counted against.
READ_TIMEOUT_S = 120
# A bound at or over the job's own 20-minute ceiling is a wait the runner
# cuts off mid-verdict, so the module refuses it rather than accept a
# number that can no longer report. 900 s is where the stop sits: the
# bound plus two READ_TIMEOUT_S reads still lands under the 1200 s
# ceiling the job carries.
MAX_POLL_BOUND_S = 900.0
STRICT = frozenset(
    {'changes', 'pycodestyle', 'pylint', 'pyright', 'eslint'})
ALLOWED = frozenset({'success', 'skipped'})
CANCELLED = 'cancelled'
OLDEST = datetime.min.replace(tzinfo=timezone.utc)
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
HEAD_SHA = re.compile(r'[0-9a-f]{40}\Z')

PASSED = 'passed'
FAILED = 'failed'
STRICT_SKIPPED = 'strict-skipped'
CANCEL_SUPERSEDED = 'cancelled-superseded'
CANCEL_DELIBERATE = 'cancelled-deliberate'
QUERY_FAILED = 'query-failed'
SCAN_MISSING = 'secrets-missing'
SCAN_FAILED = 'secrets-failed'
SCAN_QUERY_FAILED = 'secrets-query-failed'
SCAN_UNREPORTED = 'secrets-unreported'
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
    if not (REPOSITORY.fullmatch(repository or '') and _is_run_id(run_id)):
        return None
    try:
        chunks = _decode(read([
            'gh', 'api', '-H', 'Cache-Control: no-cache',
            f'repos/{repository}/actions/runs/{run_id}']))
    except QueryError:
        return None
    return (chunks[0] if len(chunks) == 1 and isinstance(chunks[0], dict)
            else None)


def _is_run_id(run_id):
    return (str(run_id or '').isascii()
            and str(run_id or '').isdigit())


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


def scan_runs(repository, head_sha, read):
    """The `secrets` workflow's runs on one head SHA; None when unanswerable.

    Workflow-scoped by construction: the path is the query's own subject,
    so no other workflow's run can answer this gate whatever it is named.
    """
    if not (REPOSITORY.fullmatch(repository or '')
            and HEAD_SHA.fullmatch(str(head_sha or ''))):
        return None
    return _runs_page(read, [
        'gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
        f'repos/{repository}/actions/workflows/{SECRETS_WORKFLOW}/runs'
        f'?head_sha={quote(str(head_sha), safe="")}&per_page=100'],
        'workflow_runs')


def scan_jobs(repository, run_id, read):
    """The jobs of one workflow run; None when the read is unanswerable."""
    if not (REPOSITORY.fullmatch(repository or '') and _is_run_id(run_id)):
        return None
    return _runs_page(read, [
        'gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
        f'repos/{repository}/actions/runs/{run_id}/jobs?per_page=100'],
        'jobs')


def _runs_page(read, argv, field):
    # None here means the transport refused, which is what keeps an
    # unreadable query from reading as a repository with no runs. Entries
    # the API did not identify are dropped before anything sorts them,
    # because `_started_key` reads the id as an integer and a non-numeric
    # one escapes as a traceback rather than a verdict; a page that
    # carried only those is unanswerable rather than empty.
    items = []
    try:
        for chunk in _decode(read(argv)):
            if isinstance(chunk, dict):
                items.extend(chunk.get(field) or [])
    except QueryError:
        return None
    usable = [item for item in items
              if isinstance(item, dict) and _is_run_id(item.get('id'))]
    return usable or (None if items else [])


def _judge_scan(repository, run, read):
    """Rule on one completed run's gitleaks JOB, which is what the verdict
    is: a run's own conclusion summarises its jobs and is not one of them.
    """
    where = (f'run {run.get("id")} of {SECRETS_WORKFLOW} '
             f'({run.get("html_url") or "?"})')
    jobs = scan_jobs(repository, run.get('id'), read)
    if jobs is None:
        return SCAN_QUERY_FAILED, (
            f'the jobs of {where} could not be read, so the secret scan '
            'could not be judged')
    found = [job for job in jobs if job.get('name') == SECRETS_JOB]
    if not found:
        return SCAN_MISSING, (
            f'{where} has no job named {SECRETS_JOB}; its jobs were: '
            + (', '.join(sorted(str(job.get('name')) for job in jobs))
               or 'none at all'))
    conclusion = found[0].get('conclusion')
    if conclusion == 'success':
        return PASSED, f'the {SECRETS_JOB} job of {where} concluded success'
    return SCAN_FAILED, (
        f'the {SECRETS_JOB} job of {where} concluded '
        f'{conclusion or "nothing at all"}, and a scan that did not run '
        'cleanly is not a clean scan')


def require_secret_scan(repository, head_sha, bound, read, clock, sleep):
    """Wait for the `secrets` run on this SHA, then rule on its scan job.

    Newest run on the SHA wins, and the comparison never leaves the SHA: a
    re-push cancels the older run through secrets.yml's own concurrency
    group, so the cross-branch rule the cancelled-dependency case needs
    would excuse exactly that deliberate cancel.
    """
    deadline = clock() + bound
    while True:
        runs = scan_runs(repository, head_sha, read)
        if runs is None:
            return SCAN_QUERY_FAILED, (
                f'the {SECRETS_WORKFLOW} runs on {head_sha or "(no head sha)"}'
                ' could not be read, so the secret scan could not be judged')
        run = max(runs, key=_started_key, default=None)
        if run is not None and run.get('status') == 'completed':
            return _judge_scan(repository, run, read)
        if clock() >= deadline:
            return SCAN_UNREPORTED, (
                f'no completed {SECRETS_WORKFLOW} run reported on {head_sha} '
                f'within {bound:g} s, so the secret scan never concluded')
        sleep(POLL_INTERVAL_S)


def poll_bound():
    """The seconds the scan wait may take, from the environment or not.

    A value that is not a finite number inside the range is refused rather
    than clamped: a wait that never expires is the failure it exists to
    prevent, and a bound past the job's ceiling is a wait the runner ends
    before it can report.
    """
    raw = os.environ.get(POLL_BOUND_ENV)
    if raw is None:
        return DEFAULT_POLL_BOUND_S
    try:
        bound = float(raw)
    except ValueError as exc:
        raise QueryError(f'{POLL_BOUND_ENV} is not a number: {raw!r}') from exc
    if not 0 < bound <= MAX_POLL_BOUND_S:
        raise QueryError(f'{POLL_BOUND_ENV} is out of range: {raw!r}')
    return bound


def gh_read(argv):
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=READ_TIMEOUT_S)
    except (subprocess.SubprocessError, UnicodeDecodeError,
            OSError) as exc:
        raise QueryError(f'gh failed: {exc}') from exc
    if proc.returncode != 0:
        raise QueryError(proc.stderr.strip()[:400])
    return proc.stdout


def _secret_scan():
    """The scan verdict, and every way refusing to know one fails closed."""
    try:
        bound = poll_bound()
    except QueryError as exc:
        return SCAN_QUERY_FAILED, str(exc)
    return require_secret_scan(
        os.environ.get('REPOSITORY', ''),
        os.environ.get('SECRETS_HEAD_SHA', ''), bound, gh_read,
        time.monotonic, time.sleep)


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
    if verdict in GREEN:
        # Only once the dependencies are green: the aggregate is red
        # either way, and a failing suites job must not also spend the
        # scan's whole bound waiting for a scan.
        scan, detail = _secret_scan()
        message = f'{message}; {detail}'
        verdict = PASSED if scan in GREEN else scan
    print(message, file=sys.stdout if verdict in GREEN else sys.stderr)
    return 0 if verdict in GREEN else 1


if __name__ == '__main__':
    sys.exit(main())
