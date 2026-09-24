#!/usr/bin/env python3
"""Emit one stdout line per check conclusion on a branch's current head.

    python3 -u ci_watch.py <branch>

Armed as a Monitor beside `pr_comment_watch.py`: a green local run on one
platform says nothing about the other three, so the runner's verdict is the
event worth being interrupted for. The head SHA and its check runs travel in
ONE GraphQL query per poll, re-resolved every time because a push moves the
head; a conclusion is announced once per (sha, check) pair, so a re-run of
the same check is announced again. **Failure and success both announce**: a
watcher that only reports green is silent through exactly the run you needed
to hear about, and silence is indistinguishable from a queue that has not
started.

Conclusions are held for DEBOUNCE_SECONDS and flushed together: a twelve-cell
matrix finishing over a couple of minutes is one thing happening, not nine,
and Monitor turns each line into its own interruption. The window opens on
the first held conclusion and closes a minute later - a batching window,
not a true debounce, which would restart on every arrival and hold a steady
trickle indefinitely. `coverage`, `diff-coverage` and `speed` skip the window
entirely: the slow jobs everything waits on arrive alone rather than in a
burst, so batching them only delays the line that says the wait is over, and
the blind-watcher escalation is immediate for the same reason.

The channels, the rate-limit pause, the escalation, the parent's pipe and
the --once trial are the same contract for both children and are stated in
SKILL.md beside this script.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_client  # noqa: E402

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
FAIL_ESCALATE = 5
DEBOUNCE_SECONDS = 60

TARGET = ('repository', 'ref', 'target')
CONTEXTS = TARGET + ('statusCheckRollup', 'contexts')

CI_QUERY = f'''query WatchChecks($owner: String!, $name: String!,
    $ref: String!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    ref(qualifiedName: $ref) {{
      target {{
        ... on Commit {{
          oid
          statusCheckRollup {{
            contexts(first: {gh_client.PAGE_SIZE}, after: $after) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                __typename
                ... on CheckRun {{
                  databaseId name conclusion detailsUrl
                }}
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}'''


def is_immediate(name):
    """Whether this check bypasses the batching window."""
    lowered = (name or '').lower()
    return lowered == 'speed' or 'coverage' in lowered


def head_and_checks(repo, branch, seen=None):
    """The branch's current head and every check run against it.

    Returns (sha, every check on it, the concluded ones). `seen` is the set
    of (sha, check, conclusion) keys already announced, and narrows the
    third to what is new since. The head is re-resolved because a push
    moves it, and a branch that is gone raises rather than answering with
    an empty surface a quiet matrix could pass for. The counts are kept
    apart because a check that has not concluded is exactly what a trial
    is asked about.
    """
    owner, name = repo.split('/', 1)
    pages = gh_client.paginate(
        CI_QUERY,
        {'owner': owner, 'name': name, 'ref': f'refs/heads/{branch}',
         'after': None},
        [(CONTEXTS, 'after')])
    commit = gh_client.at(pages[0], TARGET)
    sha = (commit or {}).get('oid')
    if not sha:
        raise RuntimeError(f'no head sha for {branch}')
    checks = [node for page in pages
              for node in gh_client.nodes(page, CONTEXTS)]
    concluded = [node for node in checks if node.get('conclusion')]
    if seen is None:
        return sha, checks, concluded
    fresh = []
    for node in concluded:
        key = (sha, node.get('name'), node.get('databaseId'),
               node['conclusion'].lower())
        if key in seen:
            continue
        seen.add(key)
        fresh.append(node)
    return sha, checks, fresh


def poll(repo, branch, seen):
    """One pass. Returns (immediate_lines, held_lines) for what is new."""
    sha, _, fresh = head_and_checks(repo, branch, seen)
    immediate = []
    held = []
    for node in fresh:
        name = node.get('name')
        conclusion = node['conclusion'].lower()
        line = (f'CI {branch} {sha[:7]} {name}: {conclusion} '
                f'{node.get("detailsUrl") or ""}')
        (immediate if is_immediate(name) else held).append(line)
    return immediate, held


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('branch')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL)
    parser.add_argument('--debounce', type=int, default=DEBOUNCE_SECONDS,
                        help='seconds to batch conclusions before emitting; '
                             'coverage and speed always emit at once')
    parser.add_argument('--once', action='store_true',
                        help='one trial cycle to stderr, then exit')
    args = parser.parse_args()
    gh_client.watch_parent()

    if args.once:
        sha, checks, concluded = head_and_checks(args.repo, args.branch)
        print(f'ok head {sha}', file=sys.stderr)
        print(f'ok {len(checks)} check run(s), {len(concluded)} concluded',
              file=sys.stderr)
        for node in concluded:
            print(f'  {node.get("name")}: {node["conclusion"].lower()}',
                  file=sys.stderr)
        return 0

    seen = set()
    failures = 0
    pending = []
    window_opened = None
    watcher = gh_client.Watcher(f'CI {args.branch} watcher')
    while True:
        try:
            immediate, held = watcher.poll(
                lambda: poll(args.repo, args.branch, seen))
            failures = 0
            for line in immediate:
                print(line, flush=True)
            if held and window_opened is None:
                window_opened = time.monotonic()
            pending.extend(held)
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f'poll failed ({failures}): {exc}', file=sys.stderr,
                  flush=True)
            if failures == FAIL_ESCALATE:
                print(f'CI {args.branch} watcher cannot read checks after '
                      f'{failures} consecutive failures: {exc}', flush=True)
        if (pending and window_opened is not None
                and time.monotonic() - window_opened >= args.debounce):
            for line in pending:
                print(line, flush=True)
            pending = []
            window_opened = None
        watcher.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
