#!/usr/bin/env python3
"""Wait for every workflow run on one commit SHA to conclude.

    python3 -u ci_wait.py <sha> [--repo R] [--interval S] [--timeout S]

The exit code is the verdict, so a caller cannot conflate the four outcomes
the hand-rolled loops this replaces conflate:

  0  every run on the SHA has `status: completed` and every conclusion is
     `success`, `neutral` or `skipped`
  1  every run concluded and at least one conclusion is none of those; the
     offending runs are named on stdout with their URLs
  2  the wait exceeded --timeout without every run concluding
  3  the invocation was rejected, or a query failed - a malformed SHA, a
     refused argument, or the first failed query, with the reason on stderr.
     Never wrap this tool in a retry: retrying a failed query behind a
     message that reads like waiting is the failure it exists to remove

Two distinctions the loops got wrong are deliberate. Zero runs on the SHA is
a waiting state, never success, and an exit 2 says which wait it was: no run
ever appeared, or named runs were still open. The SHA is PINNED, unlike the
sibling watcher re-resolving the head each poll: a push landing mid-wait
must not turn the answer into one about a commit nobody asked about.

A rate-limit refusal is the one exception to the exit-3 rule, and
deliberate: a refusal is a known wait, not a failed query, so the wait says
once where it is waiting, sleeps until the reset the API reported (bounded
by its own --timeout, which then ends the wait rather than buying another
request) and polls again. Every other failure exits 3 at once, which keeps a
403 that is really a permission refusal loud.

A cancelled run whose workflow has a strictly newer run against the same SHA
is ignored: it is the remnant of a re-run, which says nothing about the
commit and gates nothing; with no newer sibling it is a deliberate cancel and
still fails. The grouping is by workflow, the path standing in when the id
is absent; "newer" is by run_started_at, created_at standing in when that is
missing, ties broken by numeric id. Only a cancelled run is ever superseded,
so an older failure beside a newer success fails as before. The runs are read
through the commit's check suites rather than the check-runs list because
that list is appended to while a matrix fills; how is `gh_client`'s subject.

Run --once before a long wait; --once prints the matrix to stderr and exits 0
when the query succeeded, and only a failed query exits 3.
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_client  # noqa: E402

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
DEFAULT_TIMEOUT = 5400
ACCEPTABLE = frozenset({'success', 'neutral', 'skipped'})
SHA_RE = re.compile(r'[0-9a-fA-F]{40}\Z')
OLDEST = datetime.min.replace(tzinfo=timezone.utc)


class RefusingParser(argparse.ArgumentParser):
    """Exit 3 on usage errors, never argparse's 2 - the timeout verdict."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f'{self.prog}: error: {message}\n')


def runs_on(repo, sha):
    """Every workflow run GitHub reports against the pinned SHA."""
    owner, name = repo.split('/', 1)
    return gh_client.workflow_runs(owner, name, sha)


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


def _superseded(run, runs):
    """True when a strictly newer run of the same workflow exists."""
    mine = _workflow_of(run)
    started = _started_key(run)
    return any(_workflow_of(other) == mine and _started_key(other) > started
               for other in runs)


def _superseded_cancelled(run, runs):
    """A cancelled run a newer run of the same workflow has replaced."""
    return (run.get('conclusion') == 'cancelled' and _superseded(run, runs))


def verdict(runs):
    """Classify runs as a state, with the offending runs for the bad one.

    States: acceptable (exit 0), unacceptable (exit 1), waiting. Zero runs
    is waiting - "no run yet" must not read as "all concluded". A
    superseded cancelled run is ignored: it gates nothing.
    """
    runs = [run for run in runs if not _superseded_cancelled(run, runs)]
    if not runs:
        return 'waiting', []
    if any(run.get('status') != 'completed' for run in runs):
        return 'waiting', []
    offenders = [run for run in runs
                 if run.get('conclusion') not in ACCEPTABLE]
    return ('unacceptable', offenders) if offenders else ('acceptable', [])


def print_matrix(runs, sha, out):
    print(f'{sha[:12]} {len(runs)} run(s)', file=out, flush=True)
    for run in runs:
        state = run.get('status')
        conclusion = run.get('conclusion')
        suffix = f'/{conclusion}' if conclusion else ''
        print(f'  {run.get("name")}: {state}{suffix}', file=out, flush=True)


def wait(repo, sha, interval, timeout, out):
    """Poll until a verdict or the bound; returns the exit code.

    A rate-limit refusal does not end the wait: the watcher says once where
    it is waiting and resumes at the reset, bounded by this wait's own
    deadline, and the next poll is a poll like any other.
    """
    deadline = time.monotonic() + timeout
    watcher = gh_client.Watcher('ci_wait', out=sys.stderr,
                                deadline=deadline)
    while True:
        try:
            runs = watcher.poll(lambda: runs_on(repo, sha))
        except gh_client.WaitExpired:
            print(f'wait exceeded {timeout}s on {sha[:12]}: still rate '
                  'limited, no verdict to report', file=out, flush=True)
            return 2
        state, offenders = verdict(runs)
        print_matrix(runs, sha, out)
        if state == 'acceptable':
            ignored = sum(1 for run in runs
                          if _superseded_cancelled(run, runs))
            note = (f' ({ignored} superseded cancelled ignored)'
                    if ignored else '')
            print(f'all {len(runs) - ignored} run(s) on {sha[:12]}'
                  f' acceptable{note}', file=out, flush=True)
            return 0
        if state == 'unacceptable':
            print(f'run matrix on {sha[:12]} UNACCEPTABLE:', file=out,
                  flush=True)
            for run in offenders:
                print(f'  {run.get("name")}: {run.get("conclusion")}'
                      f' {run.get("html_url") or ""}', file=out, flush=True)
            return 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if not runs:
                print(f'wait exceeded {timeout}s on {sha[:12]}: no workflow '
                      'run ever appeared', file=out, flush=True)
            else:
                open_runs = ', '.join(
                    f'{run.get("name")} ({run.get("status")})'
                    for run in runs if run.get('status') != 'completed')
                print(f'wait exceeded {timeout}s on {sha[:12]}: still open: '
                      f'{open_runs}', file=out, flush=True)
            return 2
        watcher.sleep(max(0, min(interval, remaining)))


def main(argv=None):
    parser = RefusingParser(description=__doc__)
    parser.add_argument('sha')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL,
                        help='seconds between polls')
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                        help='seconds before the wait gives up with exit 2')
    parser.add_argument('--once', action='store_true',
                        help='one trial evaluation: print the matrix to '
                             'stderr, exit 0 unless the query failed')
    args = parser.parse_args(argv)
    if not SHA_RE.fullmatch(args.sha):
        print(f'not a 40-character commit SHA: {args.sha!r}', file=sys.stderr)
        return 3
    if args.interval <= 0:
        print(f'--interval must be positive, got {args.interval}',
              file=sys.stderr)
        return 3
    try:
        if not args.once:
            return wait(args.repo, args.sha, args.interval, args.timeout,
                        sys.stdout)
        watcher = gh_client.Watcher('ci_wait', out=sys.stderr)
        runs = watcher.poll(lambda: runs_on(args.repo, args.sha))
        print_matrix(runs, args.sha, sys.stderr)
        state, _ = verdict(runs)
        print(f'state: {state}', file=sys.stderr)
        return 0
    except gh_client.QueryError as exc:
        print(f'query failed: {exc}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
