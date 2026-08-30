#!/usr/bin/env python3
"""One debounced watcher over the PR-comment and CI watchers.

Both children are read continuously and nothing is dropped. Output is held
until neither child has produced a line for the debounce window, then the
whole batch is emitted as a single block — so a burst of twenty CI verdicts
arrives as one notification instead of twenty.

stdout carries the batches, which is what a Monitor turns into notifications.
stderr carries this script's own diagnostics and stays off that stream --
both children route events to stdout and diagnostics to stderr, so that
split is preserved rather than invented here.

A batch of nothing but settled, actionless conclusions (success, skipped,
neutral, cancelled) is held past the debounce window as well, because a filling
matrix goes quiet between cells and every partial tally is superseded by the
next. Such a batch is released when something worth reading arrives — which
makes it no longer quiet, so the ordinary debounce applies — or when every
check on that head has concluded, `speed` included. An unanswerable completion
query keeps it holding: a failed query must never look like a settled matrix.

This is a true debounce: the window restarts on every arrival, so nothing
is emitted while either watcher is still producing. `ci_watch.py` chose a
fixed batching window instead, on the grounds that a true debounce can hold
a steady trickle indefinitely. That is the accepted trade here: during a
live CI matrix this stays quiet by design and reports once it settles.
`ci_watch.py` is therefore run with `--debounce 0`, so the batching happens
once, here, rather than twice.

  python3 -u watch_all.py --once 195 my-branch     # trial both, print, exit
  python3 -u watch_all.py 195 my-branch            # persistent, debounced
"""
import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _repo_root():
    """The checkout this script sits in, so logs land beside it.

    The script lives inside the tracked skill directory; its logs must
    not, or every run drops an untracked file into a directory git is
    watching.
    """
    found = subprocess.run(
        ['git', '-C', str(HERE), 'rev-parse', '--show-toplevel'],
        capture_output=True, text=True, timeout=30)
    root = found.stdout.strip()
    return Path(root) if found.returncode == 0 and root else HERE


LOG_ROOT = _repo_root()


def default_log(pr, branch):
    """A log path no sibling session shares.

    Several sessions run this script at once, each on its own pull request
    and branch. One fixed filename would interleave their batches, leave the
    `full batch in ...` pointer ambiguous, and can mangle a batch outright:
    a long one exceeds the buffer and splits into several writes, so two
    writers can interleave inside a single batch rather than between two.
    """
    slug = re.sub(r'[^A-Za-z0-9]+', '-', f'{pr}-{branch}').strip('-')
    return LOG_ROOT / f'.watch_all-{slug}.log'


# `CI <branch> <sha> <name>: <conclusion> <url>` as ci_watch.py emits it.
CI_LINE = re.compile(
    r'^\[ci\] CI \S+ (?P<sha>\S+) (?P<name>.+): (?P<concl>\S+)'
    r'(?: (?P<url>\S+))?$')

# Conclusions that carry no action. Counted, never listed: a settled matrix
# is one fact, and the superseded runs a force-push leaves behind are noise
# that would otherwise crowd out the line worth reading.
TALLIED = frozenset({'success', 'skipped', 'neutral', 'cancelled'})

MAX_LISTED = 10


def _condense(batch, limit, log_path):
    """A batch small enough to survive a notification, losing no failure."""
    counts = {}
    listed = []
    rest = []
    sha = None
    for line in batch:
        match = CI_LINE.match(line)
        if not match:
            rest.append(line)
            continue
        sha = match.group('sha')
        conclusion = match.group('concl')
        counts[conclusion] = counts.get(conclusion, 0) + 1
        if conclusion not in TALLIED:
            listed.append(
                f"  {match.group('name')}: {conclusion} "
                f"{match.group('url') or ''}".rstrip())

    out = []
    if counts:
        tally = ', '.join(f'{n} {c}' for c, n in
                          sorted(counts.items(), key=lambda kv: -kv[1]))
        out.append(f'CI {sha}: {tally}')
        out.extend(listed[:MAX_LISTED])
        if len(listed) > MAX_LISTED:
            out.append(f'  ... {len(listed) - MAX_LISTED} more non-success')
    for line in rest[:MAX_LISTED]:
        out.append(line[:200])
    if len(rest) > MAX_LISTED:
        out.append(f'... {len(rest) - MAX_LISTED} more comment events')

    text = '\n'.join(out)
    if len(text) > limit:
        text = text[:limit].rstrip()
        out_of = f'\n... condensed; full batch appended to {log_path}'
        text += out_of
    elif counts or rest:
        text += f'\n(full batch in {log_path})'
    return text


def _batch_is_only_quiet_ci(batch):
    """Whether a batch holds nothing but settled, actionless CI conclusions.

    A success-only batch is worth holding: the matrix is mid-flight and every
    partial tally it would emit is superseded by the next one. Anything else —
    a failure, a comment, a watcher diagnostic — is the line somebody is
    waiting for, so it falls back to the ordinary debounce.
    """
    for line in batch:
        match = CI_LINE.match(line)
        if not match or match.group('concl') not in TALLIED:
            return False
    return bool(batch)


def _latest_sha(batch):
    """The newest head SHA a batch mentions, or None."""
    sha = None
    for line in batch:
        match = CI_LINE.match(line)
        if match:
            sha = match.group('sha')
    return sha


def _repo_slug():
    """`owner/name` for the checkout this script sits in, or None."""
    try:
        url = subprocess.run(
            ['git', '-C', str(HERE), 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'[:/]([^/:]+/[^/]+?)(?:\.git)?\s*$', url)
    return match.group(1) if match else None


def _all_concluded(sha):
    """Whether every check on `sha` has finished.

    Returns None when the answer cannot be established, and the caller keeps
    holding on None: a failed query must never look like a settled matrix.
    `speed` counts like any other check; the max-hold cap, not this function,
    bounds how long the slowest check may hold a batch.
    """
    slug = _repo_slug()
    if not (slug and sha):
        return None
    try:
        done = subprocess.run(
            ['gh', 'api', '--paginate', '-H', 'Cache-Control: no-cache',
             f'repos/{slug}/commits/{sha}/check-runs?per_page=100'],
            capture_output=True, text=True, timeout=120, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    runs = []
    for chunk in done.split('\n'):
        if not chunk.strip():
            continue
        try:
            runs.extend(json.loads(chunk).get('check_runs', []))
        except (ValueError, AttributeError):
            return None
    named = runs
    if not named:
        return None
    return all(run.get('status') == 'completed' for run in named)


def _pump(name, stream, sink, kind):
    """Feed every line of one child stream into the shared queue."""
    try:
        for raw in stream:
            line = raw.rstrip('\n')
            if line:
                sink.put((name, kind, line))
    finally:
        stream.close()


def _spawn(argv):
    child = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace')
    return child


def _watchers(pr, branch):
    return (
        ('comments', [sys.executable, '-u',
                      str(HERE / 'pr_comment_watch.py'), str(pr)]),
        ('ci', [sys.executable, '-u',
                str(HERE / 'ci_watch.py'), branch, '--debounce', '0']),
    )


def run_once(pr, branch):
    """Trial both watchers once and print what they report, together."""
    lines = []
    for name, argv in _watchers(pr, branch):
        result = subprocess.run(
            argv + ['--once'], capture_output=True,
            text=True, encoding='utf-8', errors='replace')
        body = (result.stdout or '') + (result.stderr or '')
        reported = [ln for ln in body.splitlines() if ln.strip()]
        if not reported:
            reported = ['(no output)']
        lines.append(f'--- {name} (rc={result.returncode}) ---')
        lines.extend(reported)
    print('\n'.join(lines), flush=True)
    return 0


def _emit(batch, limit, log_path):
    """Log the batch in full, notify with a version that fits."""
    try:
        with open(log_path, 'a', encoding='utf-8') as handle:
            handle.write('\n'.join(batch) + '\n')
    except OSError as exc:                            # noqa: BLE001
        print(f'log write failed: {exc}', file=sys.stderr, flush=True)
    print(_condense(batch, limit, log_path), flush=True)


def run(pr, branch, debounce, limit, log_path, max_hold):
    sink = queue.Queue()
    children = {}
    for name, argv in _watchers(pr, branch):
        child = _spawn(argv)
        children[name] = child
        for kind, stream in (('out', child.stdout), ('err', child.stderr)):
            thread = threading.Thread(
                target=_pump, args=(name, stream, sink, kind), daemon=True)
            thread.start()

    print(f'watching pr {pr} and branch {branch}; '
          f'batching until {debounce}s of silence', file=sys.stderr,
          flush=True)

    batch = []
    last = None
    held_since = None
    while True:
        timeout = debounce if batch else 1.0
        try:
            name, kind, line = sink.get(timeout=timeout)
        except queue.Empty:
            pass
        else:
            # A watcher's stderr is its own diagnostic channel; keep it off
            # the event stream unless the watcher has actually died, which
            # must never look the same as a quiet surface.
            if kind == 'out':
                batch.append(f'[{name}] {line}')
            else:
                print(f'[{name}:err] {line}', file=sys.stderr, flush=True)
            last = time.monotonic()
            continue

        if batch and last is not None and time.monotonic() - last >= debounce:
            # A batch of nothing but settled, actionless conclusions is a
            # matrix still filling in. Emitting it now spends a notification
            # on a tally the next arrival supersedes, so hold it until either
            # something worth reading lands — which makes the batch no longer
            # quiet, and the debounce above takes over — or every check on
            # that head has concluded and the tally is final.
            held_for = time.monotonic() - (held_since or time.monotonic())
            if _batch_is_only_quiet_ci(batch) and held_for < max_hold:
                settled = _all_concluded(_latest_sha(batch))
                if settled is not True:
                    if held_since is None:
                        held_since = time.monotonic()
                    last = time.monotonic()
                    continue
            _emit(batch, limit, log_path)
            batch = []
            last = None
            held_since = None

        dead = [n for n, c in children.items() if c.poll() is not None]
        if dead:
            for name in dead:
                batch.append(
                    f'[{name}] WATCHER EXITED rc={children[name].returncode}')
                del children[name]
            if not children:
                _emit(batch, limit, log_path)
                return 1
            last = time.monotonic()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pr', help='pull request number for the comment watch')
    parser.add_argument('branch', help='branch name for the CI watch')
    parser.add_argument('--once', action='store_true',
                        help='trial both watchers once and exit')
    parser.add_argument('--max-hold', type=float, default=600.0,
                        help='cap on holding a success-only batch, in '
                             'seconds. A held batch keys on the SHA it '
                             'names, and a push supersedes that SHA, so '
                             'without a cap a batch held across a force-push '
                             'would wait forever on runs nobody will finish '
                             '— silence indistinguishable from a clean '
                             'matrix.')
    parser.add_argument('--debounce', type=float, default=60.0,
                        help='seconds of silence before a batch is emitted')
    parser.add_argument('--max-chars', type=int, default=1000,
                        help='cap on the emitted batch; a Monitor truncates '
                             'a longer event, so it is condensed instead')
    parser.add_argument('--log', default=None,
                        help='every batch is appended here in full; defaults '
                             'to a path unique to this pr and branch')
    args = parser.parse_args()
    if args.once:
        return run_once(args.pr, args.branch)
    log_path = args.log or default_log(args.pr, args.branch)
    return run(args.pr, args.branch, args.debounce, args.max_chars,
               log_path, args.max_hold)


if __name__ == '__main__':
    sys.exit(main())
