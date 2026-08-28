#!/usr/bin/env python3
"""Emit one stdout line per check conclusion on a branch's current head.

    python3 -u ci_watch.py <branch>

Armed as a Monitor beside `pr_comment_watch.py`. Pushing early only buys
anything if the result is read, and a green local run on one platform says
nothing about the other three, so the runner's verdict is the event worth
being interrupted for.

The head SHA is re-resolved every poll, because a push moves it and the
checks that matter are the ones on what is there now. A conclusion is
announced once per (sha, check) pair: a re-run of the same check on the same
SHA is a new conclusion and is announced again.

**Failure and success both announce.** A watcher that only reports green is
silent through exactly the run you needed to hear about, and silence is
indistinguishable from a queue that has not started.

stdout is the event channel; everything else is stderr, which Monitor keeps
in a silent file. Consecutive poll failures escalate to a stdout line,
because a watcher that has gone blind must not look like a quiet branch.

Conclusions are held for DEBOUNCE_SECONDS and flushed together, because a
twelve-cell matrix finishing over a couple of minutes is one thing happening,
not nine, and Monitor turns each line into its own interruption. The window
opens when the first held conclusion arrives and closes a minute later — it is
a batching window rather than a true debounce, which would restart on every
arrival and could hold a steady trickle indefinitely.

`coverage`, `diff-coverage` and `speed` skip the window entirely. They are the
slow jobs everything else waits on, they arrive alone rather than in a burst,
so batching them buys nothing and only delays the line that says the wait is
over. The blind-watcher escalation is immediate for the same reason.

Run with --once before arming it. A polling loop is never armed without one
trial cycle: an unsupported flag or a renamed endpoint makes every fetch
fail, the failures go to stderr where they are silent, and the watcher then
sits quiet forever looking exactly like CI nobody has started.
"""
import argparse
import json
import subprocess
import sys
import time

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
DEFAULT_INTERVAL = 60
FAIL_ESCALATE = 5
DEBOUNCE_SECONDS = 60


def is_immediate(name):
    """Whether this check bypasses the batching window."""
    lowered = (name or '').lower()
    return lowered == 'speed' or 'coverage' in lowered


def _gh(path):
    """One fresh, fully paginated API read."""
    proc = subprocess.run(
        ['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate', path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:400])
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
        chunk, index = decoder.raw_decode(payload, index)
        out.append(chunk)
        while index < len(payload) and payload[index].isspace():
            index += 1
    return out


def head_sha(repo, branch):
    """The branch's current head, re-resolved because a push moves it."""
    for chunk in _decode(_gh(f'repos/{repo}/branches/{branch}')):
        commit = chunk.get('commit') if isinstance(chunk, dict) else None
        if isinstance(commit, dict) and commit.get('sha'):
            return commit['sha']
    raise RuntimeError(f'no head sha for {branch}')


def check_runs(repo, sha):
    """Every check run reported against one commit."""
    runs = []
    for chunk in _decode(_gh(f'repos/{repo}/commits/{sha}/check-runs')):
        if isinstance(chunk, dict):
            runs.extend(chunk.get('check_runs') or [])
    return runs


def poll(repo, branch, seen):
    """One pass. Returns (immediate_lines, held_lines) for what is new."""
    sha = head_sha(repo, branch)
    immediate = []
    held = []
    for run in check_runs(repo, sha):
        conclusion = run.get('conclusion')
        if not conclusion:
            continue
        name = run.get('name')
        key = (sha, name, run.get('id'), conclusion)
        if key in seen:
            continue
        seen.add(key)
        line = (f'CI {branch} {sha[:7]} {name}: {conclusion}'
                f' {run.get("html_url") or ""}')
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

    if args.once:
        sha = head_sha(args.repo, args.branch)
        runs = check_runs(args.repo, sha)
        concluded = [r for r in runs if r.get('conclusion')]
        print(f'ok head {sha}', file=sys.stderr)
        print(f'ok {len(runs)} check run(s), {len(concluded)} concluded',
              file=sys.stderr)
        for run in concluded:
            print(f'  {run.get("name")}: {run.get("conclusion")}',
                  file=sys.stderr)
        return 0

    seen = set()
    failures = 0
    pending = []
    window_opened = None
    while True:
        try:
            immediate, held = poll(args.repo, args.branch, seen)
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
        time.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
