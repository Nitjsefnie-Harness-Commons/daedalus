#!/usr/bin/env python3
"""The rate-limit-aware `gh` client the pull-request waiter reads through.

Every surface a wait watches is read through one `gh api graphql` query
per poll, so an idle pull request costs one request per poll rather than
one per surface. The query and its variables travel as one JSON payload
on stdin, which is what keeps a GraphQL `null` variable a `null` instead
of the empty string `-f` would send, and `-i` asks for the response
headers, which is where the rate-limit reset lives.

Whether an answer is a rate-limit refusal is `gh_rate_limit`'s question
and its rule, stated once there; this module asks it and obeys it. An
answer with no evidence is an ordinary failure and is never a pause - a
permission refusal must not be answered by sleeping. `Watcher` is the
long-running half: on a refusal it says once where it is waiting, sleeps
until the reset the API reported - bounded so a hostile or absent header
cannot hang or hot-loop a watcher - and resumes. The parent-death
guarantee is the pipe's, below; the in-flight gh child ends with the
process, under `_INFLIGHT_LOCK`, so EOF cannot orphan it.

`paginate` is the GraphQL spelling of `--paginate`: one `gh` invocation
per page, following every `endCursor`, so nothing is missed. `checkRuns`
nests inside the paginated `checkSuites`, which `paginate` pages at the
OUTER level only, so over `PAGE_SIZE` per suite is truncated silently;
one per job here. This file is AT the 500 ceiling. SEAM: transport, then
the CI read, then process lifetime; a split is measured twice by the
tree harness, so not now.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_rate_limit import RateLimited  # noqa: E402
from gh_rate_limit import bare_complaint  # noqa: E402
from gh_rate_limit import delivered  # noqa: E402
from gh_rate_limit import exhausted  # noqa: E402
from gh_rate_limit import refusal_text  # noqa: E402

GH_TIMEOUT = 120
PAGE_SIZE = 100
# The floor stops a reset already in the past from becoming a hot loop; the
# ceiling bounds ONE pause's total wait - past it the next refusal is a new
# pause with its own line. An absent reset is a plain minute.
MIN_BACKOFF = 2
MAX_BACKOFF = 6 * 3600
DEFAULT_BACKOFF = 60
SLEEP_SLICE = 1.0
STAMP = '%Y-%m-%dT%H:%M:%SZ'
# The pipe's own name, not the bridge's: a process may run both children.
PARENT_WATCH_ENV = 'DAEDALUS_WATCH_PARENT_FD'
# What `ci_gate.ACCEPTABLE` judges published CHECK runs by; a suite
# extracts this module without its siblings.
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
            checkRuns(first: {PAGE_SIZE}) {{
              nodes {{ databaseId name status conclusion completedAt
                      detailsUrl }}
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
    the bound sleeps to it, retries and spins on the API refusing it. The
    `rate_limited` flag says the bound was reached on the wake from a
    pause, not between two polls.
    """

    def __init__(self, message, rate_limited=False):
        super().__init__(message)
        self.rate_limited = rate_limited


def _executable():
    """The `gh` to run: the fake in the suites, the real one elsewhere."""
    return os.environ.get('DAEDALUS_GH') or 'gh'


# The one `gh` in flight, and the lock its lifetime shares: the spawn
# registers under it, and the EOF ending holds it across kill, reap and
# exit. The reap bound keeps a child the kill cannot free from hanging it.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT = None
REAP_LIMIT = 5


def _end_inflight():
    """Kill and reap the in-flight gh child, bounded so this cannot hang."""
    global _INFLIGHT
    child, _INFLIGHT = _INFLIGHT, None
    if child is None:
        return
    child.kill()
    try:
        child.wait(timeout=REAP_LIMIT)
    except subprocess.TimeoutExpired:
        pass


def _parse(text):
    """(status, headers, body) from a `-i` response, line by line: a
    byte-pair split at the first blank line would cut inside a re-
    translated ending and put the header lines in the body.
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


def _call(query, variables):
    """One `gh api graphql`, payload on stdin, headers asked for.

    What the run leaves behind is returned whole - the evidence can be on
    either stream. The answer is read as bytes and decoded here: a
    text-mode read translates line endings a second time on a Windows
    relay, ending the header block early.
    """
    global _INFLIGHT
    payload = json.dumps({'query': query,
                          'variables': variables or {}}).encode('utf-8')
    try:
        with _INFLIGHT_LOCK:
            proc = subprocess.Popen(
                [_executable(), 'api', '-i', 'graphql',
                 '-H', 'Cache-Control: no-cache', '--input', '-'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
            _INFLIGHT = proc
    except (subprocess.SubprocessError, OSError) as exc:
        raise QueryError(f'gh failed: {exc}') from exc
    try:
        out, complained = proc.communicate(payload, timeout=GH_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise QueryError(f'gh failed: {exc}') from exc
    finally:
        with _INFLIGHT_LOCK:
            if _INFLIGHT is proc:
                _INFLIGHT = None
    return (proc.returncode, out.decode('utf-8', 'replace'),
            complained.decode('utf-8', 'replace'))


def graphql(query, variables=None):
    """One page of a GraphQL query, fresh, no-cache, headers included."""
    code, answered, complained = _call(query, variables)
    if not answered.strip():
        # No transport response, so the complaint is the only carrier; one
        # naming no limit is the plain failure an empty stdout always was.
        found = bare_complaint(complained)
        if found is not None:
            raise found
        raise QueryError(complained.strip()[:400] or f'gh exited {code}')
    status, headers, body = _parse(answered)
    # The body is read before the code: on a throttled query it says all.
    try:
        payload = json.loads(body)
    except ValueError as exc:
        payload, unparseable = None, exc
    else:
        unparseable = None
    ok = delivered(code, payload)
    refused, resume = exhausted(status, headers, body, complained, payload,
                                ok)
    if refused:
        raise RateLimited(refusal_text(status, body, complained), resume)
    if code != 0 or status >= 400:
        detail = (complained or body).strip()[:400] or f'HTTP {status}'
        raise QueryError(detail)
    if unparseable is not None:
        raise QueryError(f'unparseable gh output: {unparseable}')
    if not isinstance(payload, dict):
        raise QueryError('the gh response is not a JSON object')
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
    under `--paginate`.
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

    A run has no status of its own in the schema - the state lives on the
    suites - so it is read off them, completed only when every suite is.
    """
    runs = [run for run in (_suite_run(suite) for suite in suites) if run]
    # `runs` is never empty: `ci_state`, this function's only caller,
    # groups a suite only where it had one.
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


def _check_run(node):
    """One check run, in the key names a workflow run already uses.

    Lowercased because the API spells the enum in caps and both callers
    compare against `ACCEPTABLE`.
    """
    conclusion = node.get('conclusion')
    return {
        'id': node.get('databaseId'),
        'name': node.get('name'),
        'status': (node.get('status') or '').lower(),
        'conclusion': conclusion.lower() if conclusion else None,
        'html_url': node.get('detailsUrl'),
        'completed_at': node.get('completedAt'),
    }


def ci_state(owner, name, sha):
    """(workflow runs, check runs) GitHub reports against one SHA.

    Through the commit's check suites rather than the check-runs list, for
    the reason `ci_wait.py` documents; a run with no suite yet reads as no
    runs at all, a wait, never a pass. The check runs are read off EVERY
    suite: dropping those with no workflow run is what hid a red gate
    (issue #1360).
    """
    pages = paginate(
        RUNS_QUERY,
        {'owner': owner, 'name': name, 'sha': sha, 'after': None},
        [(('repository', 'object', 'checkSuites'), 'after')])
    by_suite = {}
    checks = []
    for page in pages:
        for suite in nodes(page, ('repository', 'object', 'checkSuites')):
            run = _suite_run(suite)
            if run:
                by_suite.setdefault(run.get('databaseId'), []).append(suite)
            checks.extend(_check_run(node)
                          for node in nodes(suite, ('checkRuns',)))
    runs = [_run_from_suites(suites) for suites in by_suite.values()]
    return [run for run in runs if run], checks


def workflow_runs(owner, name, sha):
    """Every run against one SHA: the first of `ci_state`'s two answers."""
    return ci_state(owner, name, sha)[0]


def _exit_at_eof(descriptor):
    """Exit at end of file - the parent's death - taking the in-flight
    gh child along: the lock is held across kill, reap and exit, so no
    child is ever between spawn and registration here.
    """
    try:
        while os.read(descriptor, 1):
            pass
    finally:
        with _INFLIGHT_LOCK:
            _end_inflight()
            os._exit(0)


def spawn_watched(argv, env=None, **popen):
    """Start a child that exits when this process disappears.

    One pipe per child: the child holds the read end, this process the
    only write end, and this process dying closes the last copy, which the
    child reads as end of file - no parent-id check, which is historical
    on Windows. The handle travels as a number in the environment, a
    `pass_fds` descriptor on POSIX and the inherited HANDLE's value on
    Windows, wrapped back by `msvcrt`. Returns the child and the write
    end to hold until it is done with.
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
    is not a tick: a daemon thread sits on the pipe and ends the process
    at end of file. Started by hand there is no pipe and no thread.
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

    A refusal is a known wait, not a failure: the poll resumes when the
    reset passes rather than at the next tick, and the wait is bounded,
    so neither a hostile header nor an absurd reset can hang it.
    """

    def __init__(self, label, out=None, deadline=None):
        self.label = label
        self.out = sys.stdout if out is None else out
        self.deadline = deadline

    def poll(self, call):
        """Run one poll, pausing and resuming across a refusal.

        A bound is a liveness escape, not only a cap on the sleep: once it
        has passed, the pause ends the wait instead of buying one more
        request. The `WaitExpired` then carries whether a pause was the
        step that reached it; later polls are ordinary polls.
        """
        paused = False
        while True:
            if (self.deadline is not None
                    and time.monotonic() >= self.deadline):
                raise WaitExpired('the wait deadline passed', paused)
            try:
                return call()
            except RateLimited as refusal:
                self._pause(refusal)
                paused = True

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
