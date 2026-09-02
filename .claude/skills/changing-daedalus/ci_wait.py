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
  3  the invocation was rejected, or a query failed. Both are loud and
     immediate: a malformed SHA or a refused argument exits 3 before the
     first poll, a failed query exits 3 on the first failure, and the
     reason goes to stderr. Never wrap this tool in a retry - retrying a
     failed query behind a message that reads like waiting is the exact
     failure this tool exists to remove

Two distinctions the loops got wrong are deliberate here. Zero runs on the
SHA is a waiting state, never success: "no run has started yet" and "every
run concluded" must not be answerable the same way, and an exit 2 says
which wait it was - no run ever appeared, or named runs were still open.
And the SHA is PINNED: unlike the sibling watcher ci_watch.py, which
re-resolves the branch head every poll because a watcher's subject is
whatever is there now, a wait answers about the commit it was given -
re-resolving would let a push landing mid-wait silently change the
subject, and the verdict would describe a commit the caller never asked
about.

Runs are read through actions/runs?head_sha=, not the check-runs list: the
check-runs list is appended to while a matrix fills, so "every check run
has concluded" is true early and repeatedly during a run that is still
starting jobs.

Run --once before a long wait. A polling loop is never armed without one
trial cycle: an unsupported flag or a renamed endpoint makes every fetch
fail, and the trial proves the query shape on the real repository first.
--once evaluates the current state, prints the matrix to stderr, and
exits 0 when the query itself succeeded; only a failed query exits 3.
"""
import argparse
import json
import re
import subprocess
import sys
import time

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
DEFAULT_TIMEOUT = 5400
GH_TIMEOUT = 120
ACCEPTABLE = frozenset({'success', 'neutral', 'skipped'})
SHA_RE = re.compile(r'[0-9a-fA-F]{40}\Z')


class RefusingParser(argparse.ArgumentParser):
    """Exit 3 on usage errors, never argparse's 2 - the timeout verdict."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f'{self.prog}: error: {message}\n')


class QueryError(RuntimeError):
    """One failed API read; the caller exits 3 rather than retrying."""


def _gh(path):
    """One fresh, fully paginated API read."""
    try:
        proc = subprocess.run(
            ['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
             path],
            capture_output=True, text=True, timeout=GH_TIMEOUT)
    except (subprocess.SubprocessError, UnicodeDecodeError, OSError) as exc:
        raise QueryError(f'gh failed: {exc}') from exc
    if proc.returncode != 0:
        raise QueryError(proc.stderr.strip()[:400])
    return proc.stdout


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


def runs_on(repo, sha):
    """Every workflow run GitHub reports against the pinned SHA."""
    runs = []
    for chunk in _decode(_gh(f'repos/{repo}/actions/runs?head_sha={sha}')):
        if isinstance(chunk, dict):
            runs.extend(chunk.get('workflow_runs') or [])
    return runs


def verdict(runs):
    """Classify runs as a state, with the offending runs for the bad one.

    States: acceptable (exit 0), unacceptable (exit 1), waiting. Zero runs
    is waiting - "no run yet" must not read as "all concluded".
    """
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
    """Poll until a verdict or the bound; returns the exit code."""
    deadline = time.monotonic() + timeout
    while True:
        runs = runs_on(repo, sha)
        state, offenders = verdict(runs)
        print_matrix(runs, sha, out)
        if state == 'acceptable':
            print(f'all {len(runs)} run(s) on {sha[:12]} acceptable',
                  file=out, flush=True)
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
        time.sleep(max(0, min(interval, remaining)))


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
        runs = runs_on(args.repo, args.sha)
        print_matrix(runs, args.sha, sys.stderr)
        state, _ = verdict(runs)
        print(f'state: {state}', file=sys.stderr)
        return 0
    except QueryError as exc:
        print(f'query failed: {exc}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
