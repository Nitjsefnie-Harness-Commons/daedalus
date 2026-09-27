#!/usr/bin/env python3
"""Wait for every workflow run on one commit SHA to conclude.

    python3 -u ci_wait.py <sha> [--repo R] [--interval S] [--timeout S]
                             [--grace S]

The exit code is the verdict, so a caller cannot conflate the outcomes the
hand-rolled loops this replaces conflate:

  0  every run on the SHA has `status: completed`, every conclusion is
     `success`, `neutral` or `skipped`, and every workflow in
     REQUIRED_WORKFLOWS has at least one run of its own on the SHA
  1  every run concluded and at least one conclusion is none of those; the
     offending runs are named on stdout with their URLs
  2  the wait exceeded --timeout without every run concluding, or with
     every run concluded and a required workflow still absent
  3  the invocation was rejected, or a query failed - a malformed SHA, a
     refused argument, or the first failed query, with the reason on stderr.
     Never wrap this tool in a retry: retrying a failed query behind a
     message that reads like waiting is the failure it exists to remove
  4  every run concluded acceptably and none of them is a REQUIRED_WORKFLOWS
     run, so the gate that decides the merge was never dispatched

The fourth outcome is the one this tool got wrong (issue 1217). "Every run
that happened to exist concluded" is not "every run that should exist did",
and on a head that conflicts with its base the two are indistinguishable:
the merge ref cannot be built, no pull_request workflow is dispatched, and
the `tests` matrix - the workflow that gates the merge - has no run at all.
Two unrelated short workflows conclude, the tool says acceptable and exits
0, and every session that reads that exit code reads a false green. The
expectation is a constant rather than a flag, so it cannot be switched off
by whoever is waiting.

An incomplete set is answered, never waited on forever. If the open pull
request for this head reports itself CONFLICTING or DIRTY the run is never
going to be dispatched, so the wait refuses at once (4). Otherwise the set
is merely not built yet, which on a mergeable head is the normal state for
a minute or two: the wait allows --grace seconds from the FIRST observation
of an incomplete set and refuses (4) if the gate has still not appeared,
naming the workflow, the grace and the runs that do exist. The pull request
lookup is a disambiguation, not this tool's subject: a failed lookup is said
once on stderr and the wait continues, so it degrades to the slower grace
refusal and never to a green.

Two distinctions the loops got wrong are deliberate. Zero runs on the SHA is
a waiting state, never success, and an exit 2 says which wait it was: no run
ever appeared, or named runs were still open, or the bound fell inside a
rate-limit pause. The SHA is PINNED, unlike the sibling watcher re-resolving
the head each poll: a push landing mid-wait must not turn the answer into one
about a commit nobody asked about.

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
so an older failure beside a newer success fails as before, and a superseded
run's name never satisfies the required-workflow check, because that check
reads the set the filter left. The runs are read through the commit's check
suites rather than the check-runs list because that list is appended to while
a matrix fills; how is `gh_client`'s subject.

Run --once before a long wait; --once prints the matrix to stderr and exits 0
when the query succeeded, `state: incomplete` included, because a trial call
is not a verdict. A rejected argument or a failed query exits 3, and the
argument refusals are read before this branch, so they answer --once too.
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_client  # noqa: E402
import gh_head_prs  # noqa: E402

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
DEFAULT_TIMEOUT = 5400
DEFAULT_GRACE = 300
ACCEPTABLE = frozenset({'success', 'neutral', 'skipped'})
# The workflows whose absence is a refusal rather than a wait. A constant,
# not a flag: this is the expectation, and a caller who may switch it off
# is the reader this tool exists to protect.
REQUIRED_WORKFLOWS = frozenset({'tests'})
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


def prs_on(repo, sha):
    """The open pull requests whose head is exactly the pinned SHA."""
    owner, name = repo.split('/', 1)
    return gh_head_prs.head_pull_requests(owner, name, sha)


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


def _judged(runs):
    """The runs the verdict reads: a superseded cancelled run gates nothing."""
    return [run for run in runs if not _superseded_cancelled(run, runs)]


def _missing(runs, required=REQUIRED_WORKFLOWS):
    """The required workflow names no surviving run carries."""
    return sorted(required - {run.get('name') for run in _judged(runs)})


def verdict(runs, *, required=REQUIRED_WORKFLOWS):
    """Classify runs as a state, with the offending runs for the bad one.

    States: acceptable (exit 0), unacceptable (exit 1), waiting, incomplete
    (exit 4). Zero runs is waiting - "no run yet" must not read as "all
    concluded", and must not read as an incomplete set either. A superseded
    cancelled run is ignored: it gates nothing, and its name cannot satisfy
    the required-workflow check that runs after the filter.

    The order is load-bearing. A conclusion is judged before the set is:
    a required workflow that is present and red is a failure (1), never an
    incomplete set (4), so the refusal a missing gate earns can never
    swallow a real failure. Only a set whose every conclusion is acceptable
    can be incomplete, and a run satisfies the requirement by its `name`
    alone, because its conclusion was already judged. An empty `required`
    is satisfied by any set, which is what makes the argument a no-op
    rather than a rule that refuses every head.
    """
    runs = _judged(runs)
    if not runs:
        return 'waiting', []
    if any(run.get('status') != 'completed' for run in runs):
        return 'waiting', []
    offenders = [run for run in runs
                 if run.get('conclusion') not in ACCEPTABLE]
    if offenders:
        return 'unacceptable', offenders
    if required and not any(run.get('name') in required for run in runs):
        return 'incomplete', []
    return 'acceptable', []


def print_matrix(runs, sha, out):
    print(f'{sha[:12]} {len(runs)} run(s)', file=out, flush=True)
    for run in runs:
        state = run.get('status')
        conclusion = run.get('conclusion')
        suffix = f'/{conclusion}' if conclusion else ''
        print(f'  {run.get("name")}: {state}{suffix}', file=out, flush=True)


def _timeout_report(runs, timeout, sha, out, missing=None, grace=None):
    """The exit-2 line about the runs the bound was reached with.

    `missing` is the required workflow no run carried, which is a wait the
    bound can end before the grace does. It is its own line because every
    run in that state HAS concluded, so the "still open:" report would name
    nothing and leave the caller with a reason the runs do not carry.
    """
    if not runs:
        print(f'wait exceeded {timeout}s on {sha[:12]}: no workflow '
              'run ever appeared', file=out, flush=True)
        return
    if missing is not None:
        print(f'wait exceeded {timeout}s on {sha[:12]}: no {missing} run and '
              f'the {grace}s grace has not elapsed, so this head is not '
              'certified', file=out, flush=True)
        return
    open_runs = ', '.join(
        f'{run.get("name")} ({run.get("status")})'
        for run in runs if run.get('status') != 'completed')
    print(f'wait exceeded {timeout}s on {sha[:12]}: still open: '
          f'{open_runs}', file=out, flush=True)


def _blocked(pull_requests):
    """The open pull request whose merge state keeps a run from dispatching."""
    for pull in pull_requests:
        if (pull.get('mergeable') == 'CONFLICTING'
                or pull.get('mergeStateStatus') == 'DIRTY'):
            return pull
    return None


def _conflict_report(missing, pull, sha, out):
    """The exit-4 line about the pull request that will never dispatch."""
    print(f'no {missing} run on {sha[:12]}: pull request '
          f'#{pull.get("number")} is {pull.get("mergeStateStatus")} '
          f'(mergeable: {pull.get("mergeable")}), so the workflow is never '
          'dispatched', file=out, flush=True)


def _grace_report(missing, runs, grace, sha, out):
    """The exit-4 line about a gate that has not been dispatched in time.

    Nothing here says "merge": this path is reached with an open pull
    request, without one, and on a branch of its own, and the only claim
    the data supports on all three is that a required workflow has no run
    for this SHA.
    """
    present = ', '.join(str(run.get('name')) for run in runs)
    print(f'no {missing} run on {sha[:12]} after the {grace}s grace, so this '
          f'head is not certified: the {len(runs)} run(s) on this SHA are '
          f'{present}', file=out, flush=True)


def wait(repo, sha, interval, timeout, out, *, grace=DEFAULT_GRACE):
    """Poll until a verdict or the bound; returns the exit code.

    A rate-limit refusal does not end the wait: the watcher says once where
    it is waiting and resumes at the reset, bounded by this wait's own
    deadline, and the next poll is a poll like any other. Only a bound
    reached on the wake from a pause is the rate limit's; one reached
    between two polls reports the runs, which is the state the caller has
    to act on.

    An incomplete set is the one state the wait can answer without the
    runs saying so: a conflicting pull request refuses it at once, and
    anything else is given `grace` seconds from the first observation. The
    pull request is re-read every observation rather than once, because
    `mergeable` is UNKNOWN while GitHub computes it and can still come
    back CONFLICTING minutes later.
    """
    deadline = time.monotonic() + timeout
    watcher = gh_client.Watcher('ci_wait', out=sys.stderr,
                                deadline=deadline)
    runs = []
    incomplete_since = None
    lookup_reported = False
    missing = None
    while True:
        try:
            runs = watcher.poll(lambda: runs_on(repo, sha))
        except gh_client.WaitExpired as expired:
            if expired.rate_limited:
                print(f'wait exceeded {timeout}s on {sha[:12]}: still rate '
                      'limited, no verdict to report', file=out, flush=True)
            else:
                _timeout_report(runs, timeout, sha, out, missing, grace)
            return 2
        state, offenders = verdict(runs)
        missing = None
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
        if state == 'incomplete':
            if incomplete_since is None:
                incomplete_since = time.monotonic()
            missing = ' or '.join(_missing(runs))
            try:
                blocked = _blocked(prs_on(repo, sha))
            except gh_client.QueryError as exc:
                # Secondary query: said once, and the wait carries on to the
                # grace below, which refuses anyway.
                blocked = None
                if not lookup_reported:
                    lookup_reported = True
                    print('ci_wait: the head_pull_requests query failed, so a '
                          f'conflicting head cannot be told from a slow one: '
                          f'{exc}', file=sys.stderr, flush=True)
            if blocked is not None:
                _conflict_report(missing, blocked, sha, out)
                return 4
            if time.monotonic() - incomplete_since >= grace:
                _grace_report(missing, runs, grace, sha, out)
                return 4
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _timeout_report(runs, timeout, sha, out, missing, grace)
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
    parser.add_argument('--grace', type=int, default=DEFAULT_GRACE,
                        help='seconds an incomplete set is waited out '
                             'before it refuses with exit 4')
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
    if args.timeout <= 0:
        print(f'--timeout must be positive, got {args.timeout}',
              file=sys.stderr)
        return 3
    if args.grace <= 0:
        print(f'--grace must be positive, got {args.grace}',
              file=sys.stderr)
        return 3
    try:
        if not args.once:
            return wait(args.repo, args.sha, args.interval, args.timeout,
                        sys.stdout, grace=args.grace)
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
