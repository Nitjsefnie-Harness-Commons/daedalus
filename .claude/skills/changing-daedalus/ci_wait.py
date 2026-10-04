#!/usr/bin/env python3
"""Wait for every workflow run on one commit SHA to conclude.

    python3 -u ci_wait.py <sha> [--repo R] [--interval S] [--timeout S]
                             [--grace S] [--required NAME]

The exit code is the verdict, so a caller cannot conflate the outcomes the
hand-rolled loops this replaces conflate:

  0  every run on the SHA has `status: completed`, every JUDGED conclusion
     is `success`, `neutral` or `skipped`, every workflow in
     REQUIRED_WORKFLOWS has at least one run of its own left after the
     newest-run-per-workflow filter, and every check in PUBLISHED_CHECKS
     has a check run of its own that concluded acceptably. A run of a
     workflow that is not about this head is never judged, and is named
     rather than counted on the acceptable line
  1  every run concluded and at least one JUDGED conclusion is none of
     those, or a required published check run concluded otherwise; the
     offenders - runs and checks alike - are named on stdout with their URLs
  2  the wait exceeded --timeout without every run concluding, or with
     every run concluded and a required workflow or check still absent
  3  the invocation was rejected, or a query failed - a malformed SHA, a
     refused argument, or the first failed query, with the reason on stderr.
     Never wrap this tool in a retry: retrying a failed query behind a
     message that reads like waiting is the failure it exists to remove
  4  every JUDGED conclusion is acceptable and one of them is not a
     REQUIRED_WORKFLOWS run, or no PUBLISHED_CHECKS check run is on the
     SHA at all, so this head is not certified. No merge is claimed: exit 4
     is reached with a pull request, without one, and on a branch of its own

The fourth outcome is the one this tool got wrong (issue 1217). "Every run
that happened to exist concluded" is not "every run that should exist did",
and on a head that conflicts with its base the two are indistinguishable:
the merge ref cannot be built, no pull_request workflow is dispatched, and
the `tests` matrix - the workflow that gates the merge - has no run at all.
Two unrelated short workflows conclude, the tool says acceptable and exits
0, and every session that reads that exit code reads a false green.

A gate is not always a run, and the same false green has the other shape
(issue 1360). The `gate freshness` workflow's own run concludes `success` on
every head, red verdict or not, because publishing that verdict is the run's
job: `scripts/ci/gate_freshness.py` writes a CHECK RUN of its own onto the
head, and the rulesets read the check. So this tool read the publisher,
missed the gate, and certified `8ddfec21` - seven green runs and a
`gate freshness` check that concluded FAILURE. Both are now read, off ONE
query, and a red verdict is exit 1 like any other conclusion.

The expectation is a constant and stays the DEFAULT: a caller who names
nothing gets exactly the exit-4 refusal, so a reader cannot switch it off by
waiting. --required NAME (repeatable, all of them required) states a gate,
and what it may DO depends on which repository this is: the tool KNOWS this
one's gates and is only GUESSING about another's. Here the names may only
make it stricter - they are unioned with the default, so the `tests`
protection is unreachable by argument. Elsewhere they REPLACE it, because
`tests` is not another repository's gate and an all-green head on a
differently-named one refused at 4 reading as "the gate never started"
(issue #1318); a caller naming another repository and no gate is told, on
the refusal itself, which name is the only one checked. The published
verdicts follow the same asymmetry and no flag at all: required here, where
nothing reaches them, and nowhere else, where that name is a guess. "This
is" is decided from the RESOLVED name, case-insensitively, and each name is
stripped before it is required, one left blank by that being refused as the
invocation it is (issue #1320).

An incomplete set is answered, never waited on forever. A pull request for
this head reporting itself CONFLICTING or DIRTY refuses at once (4), since
the run is never going to be dispatched. Otherwise the set is merely not
built yet, which on a mergeable head is normal for a minute or two: the
wait allows --grace seconds from the FIRST observation and refuses (4) if
the gate has still not appeared, naming the workflow AND the check no run
carried, the grace, and the runs that do exist. That lookup is a
disambiguation, not this tool's subject: a failed one is said on stderr and
the wait continues, so it degrades to the slower grace refusal, never to a
green.

Two distinctions the loops got wrong are deliberate. Zero runs on the SHA is
a waiting state, never success, and an exit 2 says which wait it was: no run
ever appeared, named runs were still open, or the bound fell inside a
rate-limit pause. The SHA is PINNED rather than re-resolved from the branch
head on each poll, so a push landing mid-wait cannot turn the answer into
one about a commit nobody asked about.

A rate-limit refusal is the one exception to the exit-3 rule: a refusal is a
known wait, not a failed query, so the wait says once where it is waiting,
sleeps until the reset the API reported - or a minute when it reported none -
bounded by its own --timeout, and polls again. Whether an answer IS a
refusal is `gh_rate_limit`'s question, read from the evidence it carries
whatever the status and whatever `gh`'s exit code says, because a throttled
query answers 200 and exits 1. Every other failure exits 3 at once, which
keeps a 403 that is really a permission refusal loud.

A workflow's verdict is its NEWEST run on the SHA - the run GitHub's
required-check status reports for it. Every older run of that workflow is out
of the judged set whatever it concluded, so a cancelled remnant of a re-run
has always gated nothing and a FAILED one is the intermittent failure the
re-run then cleared. Issue #1249 is a head on which the older failure
outvoted the newer green; with no newer sibling a run is judged as it
stands, so a deliberate cancel and an unretried failure both still fail.

Discarding a failure is what those rules cost, so the acceptable line
names every run either filter dropped, with its workflow, its run id, its
conclusion and its URL, and its count is the number of lines printed.
Both filters are `ci_gate`'s, so a dropped run's name cannot satisfy the
gate and no reader of it can answer differently (issue #1262). Neither is
applied to a check run: a publisher PATCHes the one it POSTed.

This file is AT the 500 ceiling, the room made by cutting prose, which
cannot be repeated. There is NO SEAM in it - one tool, one contract - so
the next change is a trim, or a relocation to a module this did not.

Run --once before a long wait; it prints the matrix AND the check runs to
stderr and exits 0 when the query succeeded, `state: incomplete` included,
because a trial call is not a verdict. A rejected argument or a failed
query exits 3, and the refusals are read before this branch.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_gate  # noqa: E402
# The newest-run filter is ci_gate's, so there is one, not one per caller.
from ci_gate import judged, superseded  # noqa: E402
import gh_client  # noqa: E402
import gh_head_prs  # noqa: E402

DEFAULT_REPO = ci_gate.DEFAULT_REPO
DEFAULT_INTERVAL = 60
DEFAULT_TIMEOUT = 5400
DEFAULT_GRACE = 300
ACCEPTABLE = ci_gate.ACCEPTABLE
# The workflows whose absence is a refusal rather than a wait, bound here
# because the name is this tool's public contract. --required REPLACES it
# for a caller watching another repository and only ADDS to it here (issue
# #1318); a caller who names no gate cannot switch it off.
REQUIRED_WORKFLOWS = ci_gate.REQUIRED_WORKFLOWS
# The gates this repository's publisher writes as check runs rather than
# as runs, and so the ones a run-shaped read could never see (issue
# #1360). No flag states them: a name this tool invented would be a
# requirement a caller cannot see or satisfy.
PUBLISHED_CHECKS = ci_gate.PUBLISHED_CHECKS
SHA_RE = re.compile(r'[0-9a-fA-F]{40}\Z')


class RefusingParser(argparse.ArgumentParser):
    """Exit 3 on usage errors, never argparse's 2 - the timeout verdict."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f'{self.prog}: error: {message}\n')


def ci_on(repo, sha):
    """(every workflow run, every check run) GitHub reports on the SHA.

    One read for both: a poll that wanted the runs and then the checks
    would spend two requests a minute for a head this tool watches for
    an hour.
    """
    owner, name = repo.split('/', 1)
    return gh_client.ci_state(owner, name, sha)


def prs_on(repo, sha):
    """The open pull requests whose head is exactly the pinned SHA."""
    owner, name = repo.split('/', 1)
    return gh_head_prs.head_pull_requests(owner, name, sha)


def _missing(runs, checks, *, required=REQUIRED_WORKFLOWS,
             required_checks=PUBLISHED_CHECKS):
    """The absent gates, each as the phrase a refusal prints for it.

    Both kinds, because a refusal naming only the workflow leaves a
    reader unable to tell which gate is missing on a head missing both.
    Each is read through the shared predicate, so a superseded run's
    name cannot satisfy the gate on its own.
    """
    return ([f'no {name} run'
             for name in ci_gate.missing_required(runs, required=required)]
            + [f'no {name} check'
               for name in ci_gate.missing_published(
                   checks, required=required_checks)])


def _print_note(note, out):
    """Append a report's note, or leave the report as it was."""
    if note:
        print(note, file=out, flush=True)


def verdict(runs, checks=(), *, required=REQUIRED_WORKFLOWS,
            required_checks=PUBLISHED_CHECKS):
    """Classify a head as a state, with the offenders for the bad one.

    States: acceptable (exit 0), unacceptable (exit 1), waiting, incomplete
    (exit 4). Zero runs is waiting - "no run yet" must not read as "all
    concluded", nor as an incomplete set. A superseded run is out of the
    judged set whatever it concluded: its name cannot satisfy the
    required-workflow check, which reads that same set.

    The order is load-bearing, over BOTH surfaces. A conclusion comes
    before the set on either: a required gate that is present and red is a
    failure (1), never an incomplete set (4), so the refusal a missing
    gate earns can never swallow a real failure - the run limb first, then
    the checks, and a red published verdict fails even when the `tests`
    run is absent (issue #1360).

    The set question is ALL-OF and is ci_gate's: a head is incomplete
    unless EVERY required workflow has a run AND every required published
    check has a check run. Being satisfied by one of two is not being
    satisfied. Asked only once every conclusion is acceptable, which is
    why both checks sit below the limbs above.

    The offenders are the runs or the checks that failed, never both - the
    run limb returns before the check limb is reached - and both carry the
    same keys, so the caller prints either through one loop.
    """
    runs = judged(runs)
    if not runs:
        return 'waiting', []
    if any(run.get('status') != 'completed' for run in runs):
        return 'waiting', []
    offenders = [run for run in runs
                 if run.get('name') not in ci_gate.NOT_ABOUT_THE_HEAD
                 and run.get('conclusion') not in ACCEPTABLE]
    if offenders:
        return 'unacceptable', offenders
    offenders = ci_gate.red_published(checks, required=required_checks)
    if offenders:
        return 'unacceptable', offenders
    # A required check that has NOT concluded is a WAIT, as a workflow
    # run that has not concluded is: a null conclusion is the absence
    # of a verdict, not a red one. It sits BELOW the limb above,
    # because a red check beside a running one of the same name is a
    # failure a guard answering first would never read.
    if any(check.get('status') != 'completed'
           for check in checks
           if check.get('name') in required_checks):
        return 'waiting', []
    if (ci_gate.missing_required(runs, required=required)
            or ci_gate.missing_published(checks, required=required_checks)):
        return 'incomplete', []
    return 'acceptable', []


def print_matrix(runs, sha, out):
    print(f'{sha[:12]} {len(runs)} run(s)', file=out, flush=True)
    _print_states('', runs, out)


def _print_states(label, entries, out):
    """A run and a check run normalise to the same keys precisely so
    one loop prints either: a reader told a verdict failed should not
    have to learn a second format to find out which one.
    """
    for entry in entries:
        conclusion = entry.get('conclusion')
        suffix = f'/{conclusion}' if conclusion else ''
        print(f'  {label}{entry.get("name")}: {entry.get("status")}'
              f'{suffix}', file=out, flush=True)


def _timeout_report(runs, checks, timeout, sha, out, missing=None,
                    grace=None, note='', required_checks=PUBLISHED_CHECKS):
    """The exit-2 line about the runs the bound was reached with.

    `missing` is a wait the bound can end before the grace does, and it
    is its own line because every run in that state HAS concluded, so the
    "still open:" report would name nothing - which is also true of a
    required check that has not concluded, so that is named too.
    """
    if not runs:
        print(f'wait exceeded {timeout}s on {sha[:12]}: no workflow '
              'run ever appeared', file=out, flush=True)
        return
    if missing is not None:
        print(f'wait exceeded {timeout}s on {sha[:12]}: '
              f'{" and ".join(missing)} and the {grace}s grace has not '
              'elapsed, so this head is not certified', file=out, flush=True)
        _print_note(note, out)
        return
    # The same data the state was read from, in the same shape, so the
    # line and the verdict cannot drift: a required CHECK that has not
    # concluded is a third thing a bound expires on, with no run id.
    open_entries = [f'{run.get("name")} ({run.get("status")})'
                    for run in runs if run.get('status') != 'completed']
    open_entries += [f'{check.get("name")} ({check.get("status")})'
                     for check in checks
                     if check.get('status') != 'completed'
                     and check.get('name') in required_checks]
    print(f'wait exceeded {timeout}s on {sha[:12]}: still open: '
          f'{", ".join(open_entries)}', file=out, flush=True)


def _blocked(pull_requests):
    """The open pull request whose merge state keeps a run from dispatching."""
    for pull in pull_requests:
        if (pull.get('mergeable') == 'CONFLICTING'
                or pull.get('mergeStateStatus') == 'DIRTY'):
            return pull
    return None


def _conflict_report(missing, pull, sha, out, note=''):
    """The exit-4 line about the pull request that will never dispatch."""
    print(f'{" and ".join(missing)} on {sha[:12]}: pull request '
          f'#{pull.get("number")} is {pull.get("mergeStateStatus")} '
          f'(mergeable: {pull.get("mergeable")}), so neither the workflow '
          'nor any check it publishes is dispatched', file=out, flush=True)
    _print_note(note, out)


def _grace_report(missing, runs, grace, sha, out, note=''):
    """The exit-4 line about a gate that has not been dispatched in time.

    Nothing here says "merge": this path is reached with an open pull
    request, without one, and on a branch of its own, and the only claim
    the data supports on all three is that a required gate has no run or no
    check run for this SHA.
    """
    present = ', '.join(str(run.get('name')) for run in runs)
    print(f'{" and ".join(missing)} on {sha[:12]} after the {grace}s grace, '
          f'so this head is not certified: the {len(runs)} run(s) on this '
          f'SHA are {present}', file=out, flush=True)
    _print_note(note, out)


def wait(repo, sha, interval, timeout, out, *, grace=DEFAULT_GRACE,
         required=REQUIRED_WORKFLOWS, required_checks=PUBLISHED_CHECKS,
         note=''):
    """Poll until a verdict or the bound; returns the exit code.

    A rate-limit refusal does not end the wait: the watcher says once where
    it is waiting and resumes at the reset, bounded by this wait's own
    deadline, and the next poll is a poll like any other. Only a bound
    reached on the wake from a pause is the rate limit's; one reached
    between two polls reports the runs, the state the caller acts on.

    An incomplete set is the one state the wait can answer without the
    runs saying so: a conflicting pull request refuses it at once, and
    anything else is given `grace` seconds from the first observation. The
    pull request is re-read every observation, because `mergeable` is
    UNKNOWN while GitHub computes it and can come back CONFLICTING later.

    `required` and `required_checks` are the gates this caller is held to
    and `note` the line a refusal naming a missing gate carries; all three
    are handed down to the questions they govern rather than re-decided.
    """
    deadline = time.monotonic() + timeout
    watcher = gh_client.Watcher('ci_wait', out=sys.stderr,
                                deadline=deadline)
    runs = []
    checks = []
    incomplete_since = None
    lookup_reported = False
    missing = None
    while True:
        try:
            runs, checks = watcher.poll(lambda: ci_on(repo, sha))
        except gh_client.WaitExpired as expired:
            if expired.rate_limited:
                print(f'wait exceeded {timeout}s on {sha[:12]}: still rate '
                      'limited, no verdict to report', file=out, flush=True)
            else:
                _timeout_report(runs, checks, timeout, sha, out, missing,
                                grace, note, required_checks)
            return 2
        state, offenders = verdict(runs, checks, required=required,
                                   required_checks=required_checks)
        missing = None
        print_matrix(runs, sha, out)
        if state == 'acceptable':
            off_head = ci_gate.NOT_ABOUT_THE_HEAD
            gone = [run for run in runs if run.get('name') in off_head]
            discarded = [run for run in runs if superseded(run, runs)
                         or run.get('name') in off_head]
            stale = len(discarded) - len(gone)
            buckets = ((stale, 'superseded run(s) ignored'),
                       (len(gone), 'not about this head'))
            parts = [f'{n} {label}' for n, label in buckets if n]
            suffix = f' ({", ".join(parts)})' if parts else ''
            print(f'all {len(runs) - len(discarded)} run(s) on {sha[:12]}'
                  f' acceptable{suffix}', file=out, flush=True)
            for run in discarded:
                print(f'  {run.get("name")} (run {run.get("id")}): '
                      f'{run.get("conclusion")} {run.get("html_url") or ""}',
                      file=out, flush=True)
            return 0
        if state == 'unacceptable':
            print(f'CI on {sha[:12]} UNACCEPTABLE:', file=out, flush=True)
            for offender in offenders:
                print(f'  {offender.get("name")}: '
                      f'{offender.get("conclusion")} '
                      f'{offender.get("html_url") or ""}',
                      file=out, flush=True)
            return 1
        if state == 'incomplete':
            if incomplete_since is None:
                incomplete_since = time.monotonic()
            missing = _missing(runs, checks, required=required,
                               required_checks=required_checks)
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
            _timeout_report(runs, checks, timeout, sha, out, missing,
                            grace, note, required_checks)
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
                        help='one trial evaluation: print the matrix and the '
                             'check runs to stderr, exit 0 unless the query '
                             'failed')
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
    required = ci_gate.required_workflows(args.repo, args.required)
    required_checks = ci_gate.required_published(args.repo)
    note = ci_gate.gate_note(args.repo, args.required)
    try:
        if not args.once:
            return wait(args.repo, args.sha, args.interval, args.timeout,
                        sys.stdout, grace=args.grace, required=required,
                        required_checks=required_checks, note=note)
        watcher = gh_client.Watcher('ci_wait', out=sys.stderr)
        runs, checks = watcher.poll(lambda: ci_on(args.repo, args.sha))
        print_matrix(runs, args.sha, sys.stderr)
        # The check runs too: a trial call that could not see them could
        # not tell a head whose publisher has not written yet from one
        # that never will, which is the false green this read removes.
        _print_states('check ', checks, sys.stderr)
        state, _ = verdict(runs, checks, required=required,
                           required_checks=required_checks)
        print(f'state: {state}', file=sys.stderr)
        return 0
    except gh_client.QueryError as exc:
        print(f'query failed: {exc}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
