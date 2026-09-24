#!/usr/bin/env python3
"""The rate-limit-aware `gh` client the pull-request watchers share.

Every surface a watcher watches is read through one `gh api graphql` query
per poll, so an idle pull request costs one request per watcher per tick
rather than one per surface. The query and its variables travel as one JSON
payload on stdin, which is what keeps a GraphQL `null` variable a `null`
instead of the empty string `-f` would send, and `-i` asks for the response
headers, which is where the rate-limit reset lives.

Two shapes of exhaustion are recognised, because GitHub reports both: a 403
or 429 carrying rate-limit evidence, and a 200 whose `errors[]` carries a
`RATE_LIMITED` entry. A 403 with no rate-limit evidence is an ordinary
failure and is never a pause — a permission refusal must not be answered by
sleeping. `Watcher` is the long-running half: on a refusal it says once
where it is waiting, sleeps until the reset the API reported, and resumes.
The wait is clamped, so a hostile or absent header cannot hang or hot-loop a
watcher, and it is slept in slices so a watcher whose parent has gone still
notices while it waits.

Cursor pagination is the GraphQL spelling of `--paginate`: `paginate` loops
while any named connection reports another page and feeds each `endCursor`
back as its own `after`, one `gh` invocation per page, so nothing is missed.

`DAEDALUS_GH` overrides the executable, which is how the suites put a fake
`gh` in front of a watcher on a platform where a bare `gh` name does not
resolve.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

GH_TIMEOUT = 120
PAGE_SIZE = 100
# A reset already in the past must not become a hot loop, and a reset far in
# the future must not become a hang; an absent one is a plain minute.
MIN_BACKOFF = 2
MAX_BACKOFF = 3600
DEFAULT_BACKOFF = 60
SLEEP_SLICE = 1.0
STAMP = '%Y-%m-%dT%H:%M:%SZ'
ACCEPTABLE = frozenset({'success', 'neutral', 'skipped'})

RUNS_QUERY = f'''query WatchRuns(
    $owner: String!, $name: String!, $sha: GitObjectID!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    object(oid: $sha) {{
      ... on Commit {{
        checkSuites(first: {PAGE_SIZE}, after: $after) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{
            status conclusion createdAt
            workflowRun {{
              databaseId createdAt url
              file {{ path }}
              workflow {{ databaseId name }}
            }}
          }}
        }}
      }}
    }}
  }}
}}'''


class QueryError(RuntimeError):
    """One failed API read; the caller decides what a failure means."""


class WaitExpired(RuntimeError):
    """The bound a Watcher was given passed while it was still waiting.

    The pause is a wait, and a wait needs a liveness escape: without one a
    refusal that outlives the bound sleeps to the bound, retries, and spins
    on the API that is refusing it. This is what the bound turns into, so
    the caller reaches its own timed-out path rather than looping here.
    """


class RateLimited(RuntimeError):
    """A refusal that carries the instant to resume at, when it carries one.

    A sibling of `QueryError`, never a subclass: a pause must be handled
    before the failure path, and an `except QueryError` that also caught
    this would turn a known wait back into the loud immediate failure it is
    not.
    """

    def __init__(self, message, resume_at=None):
        super().__init__(message)
        self.resume_at = resume_at


def _executable():
    """The `gh` to run: the fake in the suites, the real one elsewhere."""
    return os.environ.get('DAEDALUS_GH') or 'gh'


def _parse(text):
    """(status, headers, body) from a `-i` response."""
    parts = re.split(r'\r?\n\r?\n', text, maxsplit=1)
    if len(parts) != 2:
        raise QueryError('no header block in the gh response')
    head, body = parts
    lines = head.replace('\r\n', '\n').split('\n')
    match = re.search(r'\s(\d{3})\s', lines[0])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(':')
        if name and value:
            headers[name.strip().lower()] = value.strip()
    return int(match.group(1)) if match else 0, headers, body


def _resume_at(headers, now):
    """The instant a refusal reported, preferring `Retry-After`."""
    retry = headers.get('retry-after')
    if retry and retry.lstrip('-').isdigit():
        return now + int(retry)
    reset = headers.get('x-ratelimit-reset')
    if reset and reset.lstrip('-').isdigit():
        return float(reset)
    return None


def _refused(status, headers, body):
    """Whether an HTTP answer is a rate-limit refusal, and when to resume."""
    if status not in (403, 429):
        return False, None
    resume = _resume_at(headers, time.time())
    if resume is not None or 'rate limit' in body.lower():
        return True, resume
    return False, None


def _graphql_refusal(payload):
    """Whether a 200 body reports exhaustion in `errors[]`, and when to resume.

    This is how GraphQL reports a throttled query: the transport succeeded,
    so the evidence is the error's `type` and its `rateLimit` extension.
    """
    for error in payload.get('errors') or []:
        if str(error.get('type') or '').upper() != 'RATE_LIMITED':
            continue
        extensions = error.get('extensions') or {}
        rate = extensions.get('rateLimit') or {}
        now = time.time()
        reset = rate.get('resetAt') or extensions.get('resetAt')
        if reset:
            try:
                iso = str(reset).replace('Z', '+00:00')
                stamp = datetime.fromisoformat(iso)
                return True, stamp.timestamp()
            except ValueError:
                pass
        retry = rate.get('retryAfter') or extensions.get('retryAfter')
        if isinstance(retry, (int, float)):
            return True, now + retry
        return True, None
    return False, None


def _call(query, variables):
    """One `gh api graphql`, payload on stdin, headers asked for."""
    payload = json.dumps({'query': query, 'variables': variables or {}})
    try:
        proc = subprocess.run(
            [_executable(), 'api', '-i', 'graphql',
             '-H', 'Cache-Control: no-cache', '--input', '-'],
            input=payload, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=GH_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as exc:
        raise QueryError(f'gh failed: {exc}') from exc
    if not proc.stdout.strip():
        detail = proc.stderr.strip()[:400] or f'gh exited {proc.returncode}'
        raise QueryError(detail)
    return proc


def graphql(query, variables=None):
    """One page of a GraphQL query, fresh, no-cache, and header-carrying."""
    proc = _call(query, variables)
    status, headers, body = _parse(proc.stdout)
    refused, resume = _refused(status, headers, body)
    if refused:
        raise RateLimited(f'HTTP {status}: {body.strip()[:200]}', resume)
    if proc.returncode != 0 or status >= 400:
        detail = (proc.stderr or body).strip()[:400] or f'HTTP {status}'
        raise QueryError(detail)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise QueryError(f'unparseable gh output: {exc}') from exc
    if not isinstance(payload, dict):
        raise QueryError('the gh response is not a JSON object')
    refused, resume = _graphql_refusal(payload)
    if refused:
        raise RateLimited('GraphQL rate limit exceeded', resume)
    data = payload.get('data')
    if not isinstance(data, dict):
        raise QueryError(f'no data in the gh response: '
                         f'{json.dumps(payload.get("errors"))[:300]}')
    return data


def nodes(page, path):
    """The nodes of the connection at `path`, empty when it is absent."""
    return at(page, path).get('nodes') or []


def page_info(page, path):
    """The pageInfo of the connection at `path`, empty when it is absent."""
    return at(page, path).get('pageInfo') or {}


def at(page, path):
    """The object at `path` in one page, empty when any step is absent."""
    node = page
    for key in path:
        node = (node or {}).get(key)
    return node or {}


def paginate(query, variables, connections):
    """Every page a set of connections names, one `gh` call per page.

    `connections` is a sequence of (path, cursor variable) pairs. The loop
    ends when none of them reports another page, so a list is as complete
    here as it was under `--paginate`: no page is skipped and none is read
    twice.
    """
    variables = dict(variables or {})
    pages = []
    while True:
        page = graphql(query, variables)
        pages.append(page)
        more = False
        for path, cursor in connections:
            info = page_info(page, path)
            if info.get('hasNextPage'):
                variables[cursor] = info.get('endCursor')
                more = True
        if not more:
            return pages


def _suite_run(suite):
    """The workflow run a check suite belongs to, or None without one."""
    return (suite or {}).get('workflowRun')


def _run_status(states):
    """`completed` only when every suite of the run is."""
    if all(state == 'COMPLETED' for state in states):
        return 'completed'
    return next(state.lower() for state in states if state != 'COMPLETED')


def _run_from_suites(suites):
    """One workflow run, from the check suites it created.

    A workflow run has no status or conclusion of its own in the GraphQL
    schema — the state lives on the suites, one per job — so the run's
    state is read off its suites: completed only when every one of them is,
    and the first conclusion that is not an acceptable one otherwise. The
    fields are the ones the verdict logic already reads, so it is unchanged
    by where they came from.
    """
    runs = [run for run in (_suite_run(suite) for suite in suites) if run]
    if not runs:
        return None
    first = min(runs, key=lambda run: run.get('createdAt') or '')
    workflow = first.get('workflow') or {}
    states = [(suite.get('status') or '').upper() for suite in suites]
    conclusions = [(suite.get('conclusion') or '').lower()
                   for suite in suites]
    return {
        'id': first.get('databaseId'),
        'name': workflow.get('name'),
        'status': _run_status(states),
        'conclusion': next((value for value in conclusions
                            if value not in ACCEPTABLE), 'success'),
        'run_started_at': first.get('createdAt'),
        'created_at': first.get('createdAt'),
        'workflow_id': (workflow.get('databaseId')
                        or (first.get('file') or {}).get('path')),
        'html_url': first.get('url'),
    }


def workflow_runs(owner, name, sha):
    """Every workflow run GitHub reports against one SHA.

    Runs are read through the commit's check suites rather than the
    check-runs list, for the reason `ci_wait.py` documents: that list is
    appended to while a matrix fills. A run whose jobs have not started has
    no suite yet, and reads as no runs at all — which is a wait, never a
    pass. The suites of one run collapse to that run, so a run is one entry
    here exactly as the REST list returned it.
    """
    pages = paginate(
        RUNS_QUERY,
        {'owner': owner, 'name': name, 'sha': sha, 'after': None},
        [(('repository', 'object', 'checkSuites'), 'after')])
    by_suite = {}
    for page in pages:
        for suite in nodes(page, ('repository', 'object', 'checkSuites')):
            run = _suite_run(suite)
            if run:
                by_suite.setdefault(run.get('databaseId'), []).append(suite)
    runs = [_run_from_suites(suites) for suites in by_suite.values()]
    return [run for run in runs if run]


def check_parent(parent_pid):
    """Exit the process when the parent that armed it is gone.

    `os.getppid()` is re-parented on POSIX and resolved per call on Windows,
    so one comparison covers every platform these suites run on; a
    process-group kill would cover only the POSIX ones.
    """
    if parent_pid and os.getppid() != parent_pid:
        raise SystemExit(0)


class Watcher:
    """The pause a long-running watcher applies to a rate-limit refusal.

    A refusal is a known wait, not a failure: one line says where the wait
    is until, and the poll resumes when the reset passes rather than at the
    next tick. The wait is bounded, and slept in slices, so neither a
    hostile header nor a gone parent can hang or hot-loop the watcher.
    """

    def __init__(self, label, out=None, parent_pid=None, deadline=None):
        self.label = label
        self.out = sys.stdout if out is None else out
        self.parent_pid = parent_pid
        self.deadline = deadline

    def poll(self, call):
        """Run one poll, pausing and resuming across a refusal.

        A bound, when one was given, is a liveness escape and not only a
        cap on the sleep: once it has passed, the pause ends the wait
        instead of buying one more request.
        """
        while True:
            self.check_parent()
            if (self.deadline is not None
                    and time.monotonic() >= self.deadline):
                raise WaitExpired('the wait deadline passed')
            try:
                return call()
            except RateLimited as refusal:
                self._pause(refusal)

    def sleep(self, seconds):
        """Sleep, noticing a gone parent while it waits."""
        left = max(0.0, float(seconds))
        while left > 0:
            self.check_parent()
            time.sleep(min(SLEEP_SLICE, left))
            left -= SLEEP_SLICE

    def check_parent(self):
        check_parent(self.parent_pid)

    def _wait_seconds(self, refusal, now=None):
        """How long to wait for this refusal, clamped into a sane bound."""
        moment = time.time() if now is None else now
        if refusal.resume_at is None:
            delay = DEFAULT_BACKOFF
        else:
            delay = refusal.resume_at - moment
        delay = min(max(delay, MIN_BACKOFF), MAX_BACKOFF)
        if self.deadline is not None:
            delay = min(delay, max(0.0, self.deadline - time.monotonic()))
        return delay

    def _pause(self, refusal):
        now = time.time()
        delay = self._wait_seconds(refusal, now)
        stamp = datetime.fromtimestamp(now + delay,
                                       timezone.utc).strftime(STAMP)
        print(f'{self.label}: rate limit reached; waiting {delay:.0f}s '
              f'until {stamp}', file=self.out, flush=True)
        self.sleep(delay)
