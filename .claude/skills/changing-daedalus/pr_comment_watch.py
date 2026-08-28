#!/usr/bin/env python3
"""Emit one stdout line per new OR EDITED comment or review on a pull request.

Intended to be armed as a persistent watcher, normally through the
`watch_all.py` aggregator beside it rather than on its own:

    python3 pr_comment_watch.py <pr-number>

A pull request has three comment surfaces and a review is not a comment, so
all three are polled. Every fetch is no-cache and paginated, and read/unread
state is never consulted -- it is delivery bookkeeping, not evidence about
whether a thread has been dealt with.

The pull request's own lifecycle state is polled as a fourth surface, because
a transition is a thing that happened to the work: leaving draft opens it to
reviewers, and a close or a merge decides it. It is announced on the same
terms as a comment -- the state found on the first pass is announced, since a
session that did not perform the transition has not handled it either.

An edit counts as an event, not as something already handled. A comment that
rewrites itself in place is the case that motivates this: a bot that posts one
comment per pull request and edits it on every push carries its real content in
the edits, so a watcher keyed only on arrival goes silent exactly when the
number it reports changes. Each item is fingerprinted by its update timestamp
AND a digest of its body, because the two fail in different directions -- a
review carries no `updated_at` at all, and a timestamp can move without the
text changing.

stdout is the event channel (Monitor turns each line into a notification);
everything else goes to stderr, which Monitor keeps in a silent file. Nothing
is seeded away on the first pass: an item that already exists when the watcher
is armed is still something this session has not handled, so it is announced.

A failure is never silent. Poll errors are reported to stderr, and a run of
them escalates to a stdout line, because a watcher that has stopped being able
to see the pull request must not look the same as a quiet pull request.

Run with --once before arming the Monitor. A polling loop is never armed
without one trial cycle: an unsupported flag or a renamed endpoint makes every
fetch fail, and because the failures go to stderr the watcher then sits silent
forever and looks exactly like a pull request nobody has commented on.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
FAIL_ESCALATE = 5
STATE_KEY = ('state', 'pull-request')


def surfaces(repo, pr):
    """The three surfaces a pull request carries, as (kind, api path)."""
    return (
        ('review', f'repos/{repo}/pulls/{pr}/reviews'),
        ('inline', f'repos/{repo}/pulls/{pr}/comments'),
        ('conversation', f'repos/{repo}/issues/{pr}/comments'),
    )


def _decode(payload):
    """Parse one JSON array, or several concatenated by --paginate."""
    payload = payload.strip()
    if not payload:
        return []
    decoder = json.JSONDecoder()
    items = []
    index = 0
    while index < len(payload):
        chunk, index = decoder.raw_decode(payload, index)
        items.extend(chunk if isinstance(chunk, list) else [chunk])
        while index < len(payload) and payload[index].isspace():
            index += 1
    return items


def fetch(path):
    """Every item on one surface, fresh and fully paginated."""
    proc = subprocess.run(
        ['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate', path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:400])
    return _decode(proc.stdout)


def pr_state(item):
    """The lifecycle state, as one word.

    Ordered by which fact outranks which: a merged pull request is also
    closed, and a closed one keeps whatever draft flag it carried, so reading
    `draft` first would report a pull request closed months ago as a draft.
    """
    if item.get('merged_at'):
        return 'merged'
    if item.get('state') == 'closed':
        return 'closed'
    return 'draft' if item.get('draft') else 'open'


def read_state(repo, pr):
    """The pull request's current state word, fetched fresh."""
    found = fetch(f'repos/{repo}/pulls/{pr}')
    if not found:
        raise RuntimeError(f'no pull request body for {pr}')
    return pr_state(found[0])


def fingerprint(item):
    """What must change for an item to count as edited.

    Both halves are load-bearing. `updated_at` is absent on a review, so a
    digest is the only thing that notices an edited review body; and a
    timestamp can be touched without the text moving, which the digest filters
    out. Taken together they catch an edit either one alone would miss.
    """
    body = item.get('body') or ''
    stamp = item.get('updated_at') or item.get('submitted_at') or ''
    return stamp, hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def describe(pr, kind, item, edited=False):
    """One line naming who said what, trimmed to stay readable as an event."""
    who = (item.get('user') or {}).get('login', '?')
    body = (item.get('body') or '').replace('\n', ' ').strip()
    state = item.get('state', '')
    if len(body) > 240:
        body = body[:240] + '...'
    tail = f' state={state}' if state else ''
    where = ''
    if kind == 'inline' and item.get('path'):
        where = f" on {item['path']}:{item.get('line') or 0}"
    what = 'EDITED ' if edited else ''
    return (f'PR {pr} {what}{kind}{where} from {who}{tail}: '
            f'{body or "(no body)"}')


def poll(repo, pr, seen, announce):
    """One pass over the state and all three surfaces, counting announced.

    `seen` maps each item to its last fingerprint rather than merely recording
    that it existed, so an edit to an item already announced is announced
    again and marked as an edit.
    """
    announced = 0
    state_now = read_state(repo, pr)
    state_before = seen.get(STATE_KEY)
    if state_before != state_now:
        seen[STATE_KEY] = state_now
        if announce:
            shown = (f'{state_before} -> {state_now}' if state_before
                     else state_now)
            print(f'PR {pr} state: {shown}', flush=True)
        announced += 1
    for kind, path in surfaces(repo, pr):
        for item in fetch(path):
            key = (kind, item.get('id'))
            current = fingerprint(item)
            previous = seen.get(key)
            if previous == current:
                continue
            seen[key] = current
            if announce:
                print(describe(pr, kind, item, edited=previous is not None),
                      flush=True)
            announced += 1
    return announced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pr', help='pull request number')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL)
    parser.add_argument('--once', action='store_true',
                        help='one trial cycle to stderr, then exit')
    args = parser.parse_args()

    if args.once:
        seen = {}
        found = poll(args.repo, args.pr, seen, announce=False)
        for kind, path in surfaces(args.repo, args.pr):
            print(f'ok {kind}: {path}', file=sys.stderr)
        print(f'ok state: {seen.get(STATE_KEY)}', file=sys.stderr)
        print(f'ok {found} existing item(s) readable on '
              f'{args.repo} PR {args.pr}', file=sys.stderr)
        return 0

    seen = {}
    failures = 0
    while True:
        try:
            poll(args.repo, args.pr, seen, announce=True)
            failures = 0
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f'poll failed ({failures}): {exc}', file=sys.stderr,
                  flush=True)
            if failures == FAIL_ESCALATE:
                print(f'PR {args.pr} watcher cannot read the pull request '
                      f'after {failures} consecutive failures: {exc}',
                      flush=True)
        time.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
