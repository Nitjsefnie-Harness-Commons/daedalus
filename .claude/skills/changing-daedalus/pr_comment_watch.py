#!/usr/bin/env python3
"""Emit one stdout line per new OR EDITED comment or review on a pull request.

Intended to be armed as a persistent watcher, normally through the
`watch_all.py` aggregator beside it rather than on its own:

    python3 pr_comment_watch.py <pr-number>

A pull request has three comment surfaces and a review is not a comment, so
all three are polled, together with the pull request's own lifecycle state.
They travel in ONE GraphQL query per poll — state, reviews, the inline
comments on each review, and the conversation — because four unconditional
REST reads a minute is what exhausted the account's primary rate limit with
several watchers running. Read or unread state is still never consulted: it
is delivery bookkeeping, not evidence about whether a thread has been dealt
with. Every query is no-cache, and every connection is followed to its last
page.

The lifecycle state is watched because a transition is a thing that happened
to the work: leaving draft opens it to reviewers, and a close or a merge
decides it. It is announced on the same terms as a comment — the state found
on the first pass is announced, since a session that did not perform the
transition has not handled it either.

An edit counts as an event, not as something already handled. A comment that
rewrites itself in place is the case that motivates this: a bot that posts
one comment per pull request and edits it on every push carries its real
content in the edits, so a watcher keyed only on arrival goes silent exactly
when the number it reports changes. Each item is fingerprinted by its update
timestamp AND a digest of its body, because the two fail in different
directions -- a review carries no update timestamp at all, and a timestamp
can move without the text changing.

stdout is the event channel (Monitor turns each line into a notification);
everything else goes to stderr, which Monitor keeps in a silent file. A
rate-limit refusal is announced once, on stdout, with the instant the wait
ends: it is a known wait, not a poll failure, and retrying it every interval
is what kept the primary limit at zero. Nothing is seeded away on the first
pass: an item that already exists when the watcher is armed is still
something this session has not handled, so it is announced.

A failure is never silent. Poll errors are reported to stderr, and a run of
them escalates to a stdout line, because a watcher that has stopped being
able to see the pull request must not look the same as a quiet pull request.
Started by `watch_all.py`, the watcher holds the read end of a pipe whose
only write end the aggregator holds, and exits when that goes - so a
restarted aggregator never leaves the old pair polling beside the new one.

Run with --once before arming the Monitor. A polling loop is never armed
without one trial cycle: an unsupported flag or a renamed endpoint makes every
fetch fail, and because the failures go to stderr the watcher then sits silent
forever and looks exactly like a pull request nobody has commented on.
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_client  # noqa: E402

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
FAIL_ESCALATE = 5
STATE_KEY = ('state', 'pull-request')
KINDS = ('review', 'inline', 'conversation')
PULL = ('repository', 'pullRequest')

PR_QUERY = f'''query WatchPull($owner: String!, $name: String!, $number: Int!,
    $reviewCursor: String, $talkCursor: String) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      state isDraft mergedAt
      reviews(first: {gh_client.PAGE_SIZE}, after: $reviewCursor) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id databaseId body state submittedAt
          author {{ login }}
          comments(first: {gh_client.PAGE_SIZE}) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              databaseId body createdAt updatedAt path line
              author {{ login }}
            }}
          }}
        }}
      }}
      comments(first: {gh_client.PAGE_SIZE}, after: $talkCursor) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ databaseId body createdAt updatedAt author {{ login }} }}
      }}
    }}
  }}
}}'''

CONNECTIONS = (
    (PULL + ('reviews',), 'reviewCursor'),
    (PULL + ('comments',), 'talkCursor'),
)

REVIEW_COMMENTS_QUERY = f'''query WatchReviewComments(
    $id: ID!, $after: String) {{
  node(id: $id) {{
    ... on PullRequestReview {{
      comments(first: {gh_client.PAGE_SIZE}, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ databaseId body createdAt updatedAt path line
                 author {{ login }} }}
      }}
    }}
  }}
}}'''


def _user(node):
    return {'login': ((node or {}).get('author') or {}).get('login') or '?'}


def _review_item(node):
    """One review, in the field shape the fingerprint and the line expect.

    The REST shape is kept rather than renamed, so what counts as an edit and
    what the announcement says are decided by the same code as before. A
    review carries no update timestamp, so its stamp is when it was
    submitted.
    """
    return {'id': node.get('databaseId'), 'body': node.get('body'),
            'state': node.get('state') or '',
            'submitted_at': node.get('submittedAt'), 'user': _user(node)}


def _comment_item(node):
    item = {'id': node.get('databaseId'), 'body': node.get('body'),
            'created_at': node.get('createdAt'),
            'updated_at': node.get('updatedAt'), 'user': _user(node)}
    if node.get('path'):
        item['path'] = node['path']
        item['line'] = node.get('line')
    return item


def pr_state(node):
    """The lifecycle state, as one word.

    Ordered by which fact outranks which: a merged pull request is also
    closed, and a closed one keeps whatever draft flag it carried, so
    reading the draft flag first would report a pull request closed months
    ago as a draft.
    """
    if node.get('mergedAt'):
        return 'merged'
    if (node.get('state') or '').upper() == 'CLOSED':
        return 'closed'
    return 'draft' if node.get('isDraft') else 'open'


def fingerprint(item):
    """What must change for an item to count as edited.

    A change in either half counts as an edit and neither half filters the
    other: a timestamp-only bump is an edit too. The digest is what notices
    an edited review body, since a review carries no update timestamp.
    """
    body = item.get('body') or ''
    stamp = item.get('updated_at') or item.get('submitted_at') or ''
    return stamp, hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def describe(pr, kind, item, edited=False):
    """One line naming who said what, trimmed to stay readable as an event."""
    who = item.get('user', {}).get('login', '?')
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


def _announce(pr, seen, announce, kind, item):
    """Record one item's fingerprint and announce it when it moved."""
    key = (kind, item.get('id'))
    previous = seen.get(key)
    current = fingerprint(item)
    if previous == current:
        return 0
    seen[key] = current
    if announce:
        print(describe(pr, kind, item, edited=previous is not None),
              flush=True)
    return 1


def _inline_comments(pr, seen, announce, review):
    """Every inline comment on one review, past the first hundred if any.

    A review's comments are a connection of their own, so a review with more
    than a page of them is followed with one further query rather than
    silently truncated — the no-item-is-missed guarantee the paginated REST
    list gave.
    """
    announced = 0
    connection = review.get('comments') or {}
    while True:
        for node in connection.get('nodes') or []:
            announced += _announce(pr, seen, announce, 'inline',
                                   _comment_item(node))
        info = connection.get('pageInfo') or {}
        if not info.get('hasNextPage'):
            return announced
        page = gh_client.graphql(REVIEW_COMMENTS_QUERY,
                                 {'id': review.get('id'),
                                  'after': info.get('endCursor')})
        connection = gh_client.at(page, ('node', 'comments'))


def poll(repo, pr, seen, announce):
    """One pass over the state and all three surfaces, counting announced.

    `seen` maps each item to its last fingerprint rather than merely recording
    that it existed, so an edit to an item already announced is announced
    again and marked as an edit.
    """
    owner, name = repo.split('/', 1)
    pages = gh_client.paginate(
        PR_QUERY,
        {'owner': owner, 'name': name, 'number': int(pr),
         'reviewCursor': None, 'talkCursor': None},
        CONNECTIONS)
    found = gh_client.at(pages[0], PULL)
    if not found:
        raise RuntimeError(f'no pull request {pr} in {repo}')

    announced = 0
    processed = set()
    state_now = pr_state(found)
    state_before = seen.get(STATE_KEY)
    if state_before != state_now:
        seen[STATE_KEY] = state_now
        if announce:
            shown = (f'{state_before} -> {state_now}' if state_before
                     else state_now)
            print(f'PR {pr} state: {shown}', flush=True)
        announced += 1
    for page in pages:
        for node in gh_client.nodes(page, PULL + ('reviews',)):
            # A page list that repeats a review it has already finished is
            # one review: following its inline comments twice would spend a
            # second request on the quota this watcher exists to protect.
            if node.get('databaseId') in processed:
                continue
            processed.add(node.get('databaseId'))
            announced += _announce(pr, seen, announce, 'review',
                                   _review_item(node))
            announced += _inline_comments(pr, seen, announce, node)
        for node in gh_client.nodes(page, PULL + ('comments',)):
            announced += _announce(pr, seen, announce, 'conversation',
                                   _comment_item(node))
    return announced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pr', help='pull request number')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL)
    parser.add_argument('--once', action='store_true',
                        help='one trial cycle to stderr, then exit')
    args = parser.parse_args()
    gh_client.watch_parent()

    if args.once:
        seen = {}
        found = poll(args.repo, args.pr, seen, announce=False)
        for kind in KINDS:
            count = sum(1 for key in seen if key[0] == kind)
            print(f'ok {kind}: {count} item(s)', file=sys.stderr)
        print(f'ok state: {seen.get(STATE_KEY)}', file=sys.stderr)
        print(f'ok {found} existing item(s) readable on '
              f'{args.repo} PR {args.pr}', file=sys.stderr)
        return 0

    seen = {}
    failures = 0
    watcher = gh_client.Watcher(f'PR {args.pr} watcher')
    while True:
        try:
            watcher.poll(
                lambda: poll(args.repo, args.pr, seen, announce=True))
            failures = 0
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f'poll failed ({failures}): {exc}', file=sys.stderr,
                  flush=True)
            if failures == FAIL_ESCALATE:
                print(f'PR {args.pr} watcher cannot read the pull request '
                      f'after {failures} consecutive failures: {exc}',
                      flush=True)
        watcher.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
