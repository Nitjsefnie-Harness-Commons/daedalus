#!/usr/bin/env python3
"""Wait for every workflow run on one commit SHA to conclude.

    python3 -u ci_wait.py <sha> [--repo R] [--interval S] [--timeout S]
                             [--grace S] [--required NAME]

The exit code is the verdict, so a caller cannot conflate the outcomes the
hand-rolled loops this replaces conflate:

  0  every run on the SHA has `status: completed`, every conclusion is
     `success`, `neutral` or `skipped`, and every workflow in
     REQUIRED_WORKFLOWS has at least one run of its own left after the
     newest-run-per-workflow filter
  1  every run concluded and at least one conclusion is none of those; the
     offending runs are named on stdout with their URLs
  2  the wait exceeded --timeout without every run concluding, or with
     every run concluded and a required workflow still absent
  3  the invocation was rejected, or a query failed - a malformed SHA, a
     refused argument, or the first failed query, with the reason on stderr.
     Never wrap this tool in a retry: retrying a failed query behind a
     message that reads like waiting is the failure it exists to remove
  4  every run concluded acceptably and none of them is a REQUIRED_WORKFLOWS
     run, so this head is not certified. No merge is claimed: this is
     reached with a pull request, without one, and on a branch of its own

The fourth outcome is the one this tool got wrong (issue 1217). "Every run
that happened to exist concluded" is not "every run that should exist did",
and on a head that conflicts with its base the two are indistinguishable:
the merge ref cannot be built, no pull_request workflow is dispatched, and
the `tests` matrix - the workflow that gates the merge - has no run at all.
Two unrelated short workflows conclude, the tool says acceptable and exits
0, and every session that reads that exit code reads a false green.

The expectation is a constant, and stays the DEFAULT: a caller who names
nothing gets exactly the exit-4 refusal below, so a reader cannot switch
it off by waiting. --required NAME (repeatable, and all of them must be
present) states a gate, and what it may DO depends on which repository
this is: the tool KNOWS this one's gate and is only GUESSING about
another's. Here the names may only make it stricter - they are unioned
with the default, so the `tests` protection is unreachable by argument.
Elsewhere they REPLACE it, which is what a caller needs there, because
`tests` is not another repository's gate and an all-green head on a
differently-named one refused at 4 reading as "the gate never started"
(issue #1318). A caller naming another repository and no gate is told, on
the refusal itself, which name is the only one checked.

"This is" is decided from the RESOLVED name, case-insensitively, so
spelling this repository out in full names this repository. Each name is
stripped before it is required, and one left blank by that is refused as
the invocation it is (issue #1320).

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
once where it is waiting, sleeps until the reset the API reported - or a
minute when it reported none - bounded by its own --timeout, which then
ends the wait rather than buying another request, and polls again. Whether
an answer IS a refusal is `gh_rate_limit`'s question and not this tool's:
it is answered by the evidence the answer carries, whatever the status and
whatever `gh`'s exit code says, because a throttled query answers 200 and
exits 1. Every other failure exits 3 at once, which keeps a 403 that is
really a permission refusal loud.

A workflow's verdict is decided by its NEWEST run on the SHA - the run
GitHub's required-check status reports for it. Every older run of that
workflow is out of the judged set, whatever it concluded: a cancelled
remnant of a re-run has always gated nothing, and a FAILED one is the
intermittent failure the re-run then cleared, which that same status
supersedes. A close/reopen is the shape that makes it show - the head gets
a second run of the workflow against a new merge ref while the first run's
failure lingers on the same SHA - and issue #1249 is a head on which the
older failure outvoted the newer green. With no newer sibling a run is
judged as it stands, so a deliberate cancel and an unretried failure both
still fail.

Discarding a failure is what that rule costs, so the discard is never
silent: the acceptable line names every run the filter dropped, with its
workflow, its run id, its conclusion and its URL, and its count is the
number of lines printed. The filter is `ci_gate`'s and the required-workflow
check is its predicate, which applies that filter itself, so a superseded
run's name cannot satisfy the gate and this tool and `watch_all.py` cannot
answer the same question differently (issue #1262). The runs are read through
the commit's check suites rather than the check-runs list because that list
is appended to while a matrix fills; how is `gh_client`'s subject.

Run --once before a long wait; --once prints the matrix to stderr and exits 0
when the query succeeded, `state: incomplete` included, because a trial call
is not a verdict. A rejected argument or a failed query exits 3, and the
argument refusals are read before this branch, so they answer --once too.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_gate  # noqa: E402
# The newest-run-per-workflow filter is ci_gate's, beside the predicate it
# defines the set for, so there is one filter rather than one per caller.
from ci_gate import judged, superseded  # noqa: E402
import gh_client  # noqa: E402
import gh_head_prs  # noqa: E402

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
DEFAULT_TIMEOUT = 5400
DEFAULT_GRACE = 300
ACCEPTABLE = frozenset({'success', 'neutral', 'skipped'})
# The workflows whose absence is a refusal rather than a wait, and the
# default a caller who names nothing is held to. The expectation itself is
# ci_gate's, which watch_all.py reads too; the name is bound here because it
# is this tool's public contract. --required REPLACES it for a caller
# watching another repository and only ADDS to it here (issue #1318);
# either way a caller who names no gate cannot switch it off.
REQUIRED_WORKFLOWS = ci_gate.REQUIRED_WORKFLOWS
SHA_RE = re.compile(r'[0-9a-fA-F]{40}\Z')


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


def _missing(runs, *, required=REQUIRED_WORKFLOWS):
    """The required workflow names no run the filter kept carries.

    Read through the shared predicate, which applies the filter itself, so
    a superseded run's name cannot satisfy the gate on its own. The name
    is this module's own for the question the exit-4 refusal asks, so
    `wait` reads `_missing(runs, required=...)` and never the predicate,
    and a suite asserts on that name rather than on the predicate's.
    """
    return ci_gate.missing_required(runs, required=required)


def _is_default_repo(repo):
    """Whether `repo` names the repository whose gate this tool knows.

    Case-insensitive over the whole `owner/name`, and asked of the RESOLVED
    value rather than of whether `--repo` was passed: spelling this
    repository out in full names the same repository, and so does changing
    the case of either half of it. Only the comparison is normalised, so
    the query still goes out spelled as the caller spelled it.
    """
    return repo.lower() == DEFAULT_REPO.lower()


def _required_set(repo, named):
    """The workflow names this invocation is held to.

    A direction, and deliberately not a symmetric one: the flag may only
    make this tool STRICTER where it already knows the gate, and may only
    REPLACE a requirement where it was guessing for a repository it was
    not told about. So here the caller's names are UNIONED with
    `REQUIRED_WORKFLOWS` - which puts the `tests` protection out of reach
    of any argument, and with it the false green of issue #1217 - while on
    another repository they replace a default that was only ever a guess.

    Each name is stripped, so `' ci '` is the requirement `ci` rather than a
    name no run carries and no report can spell legibly (issue #1320).
    """
    if not named:
        return REQUIRED_WORKFLOWS
    names = frozenset(name.strip() for name in named)
    if _is_default_repo(repo):
        return REQUIRED_WORKFLOWS | names
    return names


def _gate_note(repo, named):
    """The note a missing-gate report carries, or nothing at all.

    Both facts it turns on are the caller's - which repository, and
    whether `--required` was - and only `main` holds them. It is empty
    whenever `--required` was given, because a caller who has stated a
    gate is not asking what this tool checks by default; and empty on this
    repository under every spelling, because there the answer is not a
    guess. `_is_default_repo` answers the repository half for this and for
    `_required_set`, so the note and the set cannot drift apart.

    The names are rendered from the constant rather than spelled here, so
    the line cannot drift from the gate the tool actually checks.
    """
    if named or _is_default_repo(repo):
        return ''
    names = ', '.join(sorted(REQUIRED_WORKFLOWS))
    return (f'  only {names} is checked by default; --required NAME states '
            'the workflow that gates another repository')


def _print_note(note, out):
    """Append a report's note, or leave the report as it was."""
    if note:
        print(note, file=out, flush=True)


def verdict(runs, *, required=REQUIRED_WORKFLOWS):
    """Classify runs as a state, with the offending runs for the bad one.

    States: acceptable (exit 0), unacceptable (exit 1), waiting, incomplete
    (exit 4). Zero runs is waiting - "no run yet" must not read as "all
    concluded", and must not read as an incomplete set either. A superseded
    run is out of the judged set whatever it concluded: its name cannot
    satisfy the required-workflow check, which reads that same set.

    The order is load-bearing. A conclusion is judged before the set is:
    a required workflow that is present and red is a failure (1), never an
    incomplete set (4), so the refusal a missing gate earns can never
    swallow a real failure. Only a set whose every conclusion is acceptable
    can be incomplete, and the set question is ci_gate's, which
    watch_all.py asks through the same call.

    That question is ALL-OF: a set is incomplete unless EVERY required
    workflow is present. An earlier form on this branch read it any-of,
    accepting a set that carried ONE of several required workflows; the base
    had no `required` argument at all, so it had neither reading. The
    default is unchanged either way, since the default names one workflow,
    and all-of is the correct one - being satisfied by one of two required
    workflows is not being satisfied. A run's conclusion is not part of the
    question, which is why the check sits after the conclusion has already
    been judged above.
    """
    runs = judged(runs)
    if not runs:
        return 'waiting', []
    if any(run.get('status') != 'completed' for run in runs):
        return 'waiting', []
    offenders = [run for run in runs
                 if run.get('conclusion') not in ACCEPTABLE]
    if offenders:
        return 'unacceptable', offenders
    if ci_gate.missing_required(runs, required=required):
        return 'incomplete', []
    return 'acceptable', []


def print_matrix(runs, sha, out):
    print(f'{sha[:12]} {len(runs)} run(s)', file=out, flush=True)
    for run in runs:
        state = run.get('status')
        conclusion = run.get('conclusion')
        suffix = f'/{conclusion}' if conclusion else ''
        print(f'  {run.get("name")}: {state}{suffix}', file=out, flush=True)


def _timeout_report(runs, timeout, sha, out, missing=None, grace=None,
                    note=''):
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
        _print_note(note, out)
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


def _conflict_report(missing, pull, sha, out, note=''):
    """The exit-4 line about the pull request that will never dispatch."""
    print(f'no {missing} run on {sha[:12]}: pull request '
          f'#{pull.get("number")} is {pull.get("mergeStateStatus")} '
          f'(mergeable: {pull.get("mergeable")}), so the workflow is never '
          'dispatched', file=out, flush=True)
    _print_note(note, out)


def _grace_report(missing, runs, grace, sha, out, note=''):
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
    _print_note(note, out)


def wait(repo, sha, interval, timeout, out, *, grace=DEFAULT_GRACE,
         required=REQUIRED_WORKFLOWS, note=''):
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

    `required` is the set this caller is held to and `note` the line a
    refusal naming a missing gate carries; both are handed down to the
    two questions they govern rather than re-decided here.
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
                _timeout_report(runs, timeout, sha, out, missing, grace, note)
            return 2
        state, offenders = verdict(runs, required=required)
        missing = None
        print_matrix(runs, sha, out)
        if state == 'acceptable':
            discarded = [run for run in runs if superseded(run, runs)]
            # Not `note`: that is the caller's gate note, below.
            suffix = (f' ({len(discarded)} superseded run(s) ignored)'
                      if discarded else '')
            print(f'all {len(runs) - len(discarded)} run(s) on {sha[:12]}'
                  f' acceptable{suffix}', file=out, flush=True)
            # The count above is this loop's length, so the two cannot
            # disagree; a dropped run never enters `judged`, so exit 1 has
            # no offender to disclose, and the run id rides on these lines.
            for run in discarded:
                print(f'  {run.get("name")} (run {run.get("id")}): '
                      f'{run.get("conclusion")} {run.get("html_url") or ""}',
                      file=out, flush=True)
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
            missing = ' or '.join(_missing(runs, required=required))
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
                _conflict_report(missing, blocked, sha, out, note)
                return 4
            if time.monotonic() - incomplete_since >= grace:
                _grace_report(missing, runs, grace, sha, out, note)
                return 4
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _timeout_report(runs, timeout, sha, out, missing, grace, note)
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
    parser.add_argument('--required', action='append', default=None,
                        metavar='NAME',
                        help='a workflow whose absence refuses; repeat for '
                             'each, and every one must be present '
                             '(default: this repository\'s own gate)')
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
    # A name with nothing in it builds a requirement no run can satisfy,
    # and the refusal then names nothing legible: `no  run on <sha>`, or
    # `no   run on <sha>` when the name was a space (issue #1320).
    if any(not name.strip() for name in args.required or ()):
        print('--required must name a workflow, got a blank value',
              file=sys.stderr)
        return 3
    required = _required_set(args.repo, args.required)
    note = _gate_note(args.repo, args.required)
    try:
        if not args.once:
            return wait(args.repo, args.sha, args.interval, args.timeout,
                        sys.stdout, grace=args.grace, required=required,
                        note=note)
        watcher = gh_client.Watcher('ci_wait', out=sys.stderr)
        runs = watcher.poll(lambda: runs_on(args.repo, args.sha))
        print_matrix(runs, args.sha, sys.stderr)
        state, _ = verdict(runs, required=required)
        print(f'state: {state}', file=sys.stderr)
        return 0
    except gh_client.QueryError as exc:
        print(f'query failed: {exc}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
