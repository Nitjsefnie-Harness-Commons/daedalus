#!/usr/bin/env python3
"""The rate-limit-aware `gh` client the pull-request watchers share.

Every surface a watcher watches is read through one `gh api graphql` query
per poll, so an idle pull request costs one request per watcher per tick
rather than one per surface. The query and its variables travel as one JSON
payload on stdin, which is what keeps a GraphQL `null` variable a `null`
instead of the empty string `-f` would send, and `-i` asks for the response
headers, which is where the rate-limit reset lives.

Two shapes of exhaustion are recognised because GitHub reports both: a 403
or 429 carrying rate-limit evidence, and a 200 whose `errors[]` carries a
`RATE_LIMITED` entry. A 403 with no rate-limit evidence is an ordinary
failure and is never a pause - a permission refusal must not be answered by
sleeping. `Watcher` is the long-running half: on a refusal it says once
where it is waiting, sleeps until the reset the API reported - bounded so a
hostile or absent header cannot hang or hot-loop a watcher - and resumes. The
parent-death guarantee is the pipe's, below, and a thread rather than the
poll loop's business.

`paginate` is the GraphQL spelling of `--paginate`: it loops while any named
connection reports another page and feeds each `endCursor` back as its own
`after`, one `gh` invocation per page, so nothing is missed.

`DAEDALUS_GH` overrides the executable, which is how the suites put a fake
`gh` in front of a watcher where a bare `gh` name does not resolve.
"""

import importlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

GH_TIMEOUT = 120
PAGE_SIZE = 100
# The floor stops a reset already in the past from becoming a hot loop; the
# ceiling bounds ONE pause's total wait, six hours being far past any reset
# the API reports, so it bounds an absurd header rather than cutting a real
# one short - past it the next refusal is a new pause with its own line. An
# absent reset is a plain minute.
MIN_BACKOFF = 2
MAX_BACKOFF = 6 * 3600
DEFAULT_BACKOFF = 60
SLEEP_SLICE = 1.0
STAMP = '%Y-%m-%dT%H:%M:%SZ'
# The pipe's own name, not the bridge's: a process may run a bridge child
# and a watcher child at once without the two watching each other.
PARENT_WATCH_ENV = 'DAEDALUS_WATCH_PARENT_FD'
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

    A wait needs a liveness escape: without one, a refusal that outlives
    the bound sleeps to it, retries and spins on the API refusing it. This
    is what the bound becomes, so the caller reaches its own timed-out path
    rather than looping here.
    """


class RateLimited(RuntimeError):
    """A refusal carrying the instant to resume at, when it carries one.

    A sibling of `QueryError`, never a subclass: a pause must be handled
    before the failure path, and an `except QueryError` that caught this
    too would turn a known wait back into the loud failure it is not.
    """

    def __init__(self, message, resume_at=None):
        super().__init__(message)
        self.resume_at = resume_at


def _executable():
    """The `gh` to run: the fake in the suites, the real one elsewhere."""
    return os.environ.get('DAEDALUS_GH') or 'gh'


def _parse(text):
    """(status, headers, body) from a `-i` response.

    Read line by line rather than split at the first blank-line byte pair:
    a Windows text stream re-translates the `\r\n` the answer already
    carried, so each ending arrives as `\r\r\n` and the blank line is two
    near-empty lines. A byte-pair split cuts inside the block, the header
    lines fall into the body, and a reported reset arrives as no reset at
    all - a fixed default instead of the wait the API asked for.
    """
    lines = text.split('\n')
    headers = {}
    status = 0
    for index, line in enumerate(lines):
        line = line.rstrip('\r')
        if index == 0:
            match = re.search(r'\s(\d{3})\s', line)
            status = int(match.group(1)) if match else 0
        elif not line.strip():
            body = '\n'.join(part.rstrip('\r') for part in lines[index + 1:])
            return status, headers, body
        else:
            name, _, value = line.partition(':')
            if name and value:
                headers[name.strip().lower()] = value.strip()
    raise QueryError('no header block in the gh response')


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

    How GraphQL reports a throttled query: the transport succeeded, so the
    evidence is the error's `type` and its `rateLimit` extension.
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
    """One `gh api graphql`, payload on stdin, headers asked for.

    The answer is read as bytes and decoded here rather than through a
    text-mode read. A text-mode read translates line endings again on a
    Windows relay: the `\r\n` the producer already spelled arrives as
    `\r\r\n`, and the universal-newline reader turns each `\r` into a
    line of its own - a blank line after every real line, which cuts the
    header block short and takes the reported rate-limit reset with it. The
    parse reads the endings as they came, so the block survives whatever
    the relay did to them.
    """
    payload = json.dumps({'query': query,
                          'variables': variables or {}}).encode('utf-8')
    try:
        proc = subprocess.run(
            [_executable(), 'api', '-i', 'graphql',
             '-H', 'Cache-Control: no-cache', '--input', '-'],
            input=payload, capture_output=True, timeout=GH_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as exc:
        raise QueryError(f'gh failed: {exc}') from exc
    answered = proc.stdout.decode('utf-8', 'replace')
    complained = proc.stderr.decode('utf-8', 'replace')
    if not answered.strip():
        detail = complained.strip()[:400] or f'gh exited {proc.returncode}'
        raise QueryError(detail)
    return proc.returncode, answered, complained


def graphql(query, variables=None):
    """One page of a GraphQL query, fresh, no-cache, headers included."""
    code, answered, complained = _call(query, variables)
    status, headers, body = _parse(answered)
    refused, resume = _refused(status, headers, body)
    if refused:
        raise RateLimited(f'HTTP {status}: {body.strip()[:200]}', resume)
    if code != 0 or status >= 400:
        detail = (complained or body).strip()[:400] or f'HTTP {status}'
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
    """The nodes of the connection at `path`, empty when absent."""
    return at(page, path).get('nodes') or []


def page_info(page, path):
    """The pageInfo of the connection at `path`, empty when absent."""
    return at(page, path).get('pageInfo') or {}


def at(page, path):
    """The object at `path` in one page, empty when any step is absent."""
    node = page
    for key in path:
        node = (node or {}).get(key)
    return node or {}


def paginate(query, variables, connections):
    """Every page a set of connections names, one `gh` call per page.

    `connections` is a sequence of (path, cursor variable) pairs; the loop
    ends when none reports another page, so a list is as complete here as
    under `--paginate`: no page skipped, none read twice.
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

    A workflow run has no status or conclusion of its own in the schema -
    the state lives on the suites, one per job - so it is read off them:
    completed only when every suite is, and otherwise the first conclusion
    that is not an acceptable one. The fields are the ones the verdict
    logic already reads.
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

    Through the commit's check suites rather than the check-runs list, for
    the reason `ci_wait.py` documents. A run whose jobs have not started has
    no suite yet and reads as no runs at all, which is a wait, never a pass.
    The suites of one run collapse to that run, so a run is one entry here
    exactly as the REST list returned it.
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


def _exit_at_eof(descriptor):
    """Exit when the pipe reports end of file, which is the parent's death."""
    try:
        while os.read(descriptor, 1):
            pass
    finally:
        os._exit(0)


def spawn_watched(argv, env=None, **popen):
    """Start a child that exits when this process disappears.

    One pipe per child: the child holds the read end, this process the
    only write end, and this process dying closes the last copy, which the
    child reads as end of file. The check depends on no platform's idea of
    a parent id, which is the point - a process-group kill, and a pid
    compared with `os.getppid()`, both fail on Windows, where the parent
    id is historical and does not change when the parent dies. The handle
    travels as a number in the environment: a descriptor passed with
    `pass_fds` on POSIX, the inherited HANDLE's own value on Windows, which
    the child wraps back into a descriptor with `msvcrt`.

    Returns the child and the write end to hold until it is done with.
    """
    read_fd, write_fd = os.pipe()
    child_env = dict(os.environ if env is None else env)
    if os.name == 'nt':
        msvcrt = importlib.import_module('msvcrt')
        read_handle = msvcrt.get_osfhandle(read_fd)
        inheritable = getattr(os, 'set_handle_inheritable')
        inheritable(read_handle, True)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {'handle_list': [read_handle]}
        child_env[PARENT_WATCH_ENV] = str(read_handle)
    else:
        child_env[PARENT_WATCH_ENV] = str(read_fd)
    try:
        try:
            if os.name == 'nt':
                child = subprocess.Popen(argv, env=child_env,
                                         startupinfo=startup, **popen)
            else:
                child = subprocess.Popen(argv, env=child_env,
                                         pass_fds=(read_fd,), **popen)
        finally:
            try:
                if os.name == 'nt':
                    inheritable(read_handle, False)
            finally:
                os.close(read_fd)
    except BaseException:
        os.close(write_fd)
        raise
    return child, write_fd


def watch_parent():
    """Exit this process when the one that started it disappears.

    A watcher asleep for a poll interval cannot notice anything, so this
    is not a tick: a daemon thread sits on the pipe and ends the process at
    end of file. Started by hand there is no pipe, and the watcher is left
    alone.
    """
    raw = os.environ.get(PARENT_WATCH_ENV)
    if raw is None:
        return
    try:
        descriptor = int(raw)
        if os.name == 'nt':
            msvcrt = importlib.import_module('msvcrt')
            descriptor = msvcrt.open_osfhandle(descriptor, os.O_RDONLY)
        if not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise ValueError('descriptor is not a pipe')
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f'{PARENT_WATCH_ENV} must name an inherited pipe') from exc
    threading.Thread(target=_exit_at_eof, args=(descriptor,),
                     name='parent-watch', daemon=True).start()


class Watcher:
    """The pause a long-running watcher applies to a rate-limit refusal.

    A refusal is a known wait, not a failure: one line says where the wait
    is until, and the poll resumes when the reset passes rather than at the
    next tick. The wait is bounded, so neither a hostile header nor an
    absurd reset can hang or hot-loop the watcher.
    """

    def __init__(self, label, out=None, deadline=None):
        self.label = label
        self.out = sys.stdout if out is None else out
        self.deadline = deadline

    def poll(self, call):
        """Run one poll, pausing and resuming across a refusal.

        A bound, when one was given, is a liveness escape and not only a
        cap on the sleep: once it has passed, the pause ends the wait
        instead of buying one more request.
        """
        while True:
            if (self.deadline is not None
                    and time.monotonic() >= self.deadline):
                raise WaitExpired('the wait deadline passed')
            try:
                return call()
            except RateLimited as refusal:
                self._pause(refusal)

    def sleep(self, seconds):
        """Sleep the whole wait in slices, so it stays a sequence of steps."""
        left = max(0.0, float(seconds))
        while left > 0:
            time.sleep(min(SLEEP_SLICE, left))
            left -= SLEEP_SLICE

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
