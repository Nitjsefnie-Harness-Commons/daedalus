#!/usr/bin/env python3
"""One debounced watcher over the PR-comment and CI watchers.

Both children are read continuously and nothing is dropped. Output is held
until neither child has produced a line for the debounce window, then the
whole batch is emitted as a single block — so a burst of twenty CI verdicts
arrives as one notification instead of twenty.

Each child is told this process's pid and exits when it is gone, and they
are terminated here on the way out, so a restart never leaves the old pair
polling beside the new one. The children each spend one GraphQL query per
poll, and a rate-limit refusal pauses the child that read it until the reset
the API reported.

stdout carries the batches, which is what a Monitor turns into notifications.
stderr carries this script's own diagnostics and stays off that stream --
both children route events to stdout and diagnostics to stderr, so that
split is preserved rather than invented here.

A batch of nothing but settled, actionless conclusions (success, skipped,
neutral, cancelled) is held past the debounce window as well, because a filling
matrix goes quiet between cells and every partial tally is superseded by the
next. Such a batch is released when something worth reading arrives — which
makes it no longer quiet, so the ordinary debounce applies — or when every
workflow run on that head has concluded, `speed` included. The runs are read
through `actions/runs?head_sha=`, never the check-runs list: that list is
appended to while a matrix fills, so "every check run has concluded" is true
early and repeatedly. An unanswerable completion query keeps it holding: a
failed query must never look like a settled matrix. A batch the `--max-hold`
cap releases instead is announced as partial, so it never reads as settled.

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
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_client  # noqa: E402

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


def _runs_on(slug, sha):
    """Every workflow run on `sha`, or None when the query fails.

    A rate-limit refusal is not a failed query and is not caught here: it
    belongs to the wait the caller pauses on, and swallowing it would let a
    refused hold read as a settled matrix.
    """
    owner, name = slug.split('/', 1)
    try:
        return gh_client.workflow_runs(owner, name, sha)
    except gh_client.QueryError:
        return None


def _settled(runs):
    """None with no run yet, which is not settled.

    A conclusion is the batch's business, not the hold's: a completed
    failure settles the matrix as much as a completed success does.
    """
    if not runs:
        return None
    return all(run.get('status') == 'completed' for run in runs)


def _all_concluded(sha, watcher=None):
    """Whether every workflow run on `sha` has finished, or None.

    None keeps the batch held: a failed query must never look settled.
    Runs rather than check runs for the reason in the module docstring. A
    rate-limit refusal is the exception the watcher pauses on, so with one
    the query is retried at the reset rather than read as a failure.
    """
    slug = _repo_slug()
    if not (slug and sha):
        return None

    def ask():
        return _runs_on(slug, sha)

    runs = ask() if watcher is None else watcher.poll(ask)
    if runs is None:
        return None
    return _settled(runs)


def _cap_line(sha, max_hold):
    """The line a cap release adds, so its tally cannot pass for settled."""
    return (f'[watch_all] hold cap {max_hold:.0f}s reached on {sha}; '
            'runs still open or unknown — tally is partial')


def _hold_release(settled, held_for, max_hold, sha):
    """None to keep holding, else the lines to add before emitting."""
    if settled is True:
        return []
    if held_for < max_hold:
        return None
    return [_cap_line(sha, max_hold)]


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


def _watchers(pr, branch, parent_pid=None):
    """The two children, each told which process armed it.

    The parent pid is what makes an orphan impossible: a child compares it
    against its own on every tick and while it waits, and exits when the
    parent is gone. This is redundant with the terminate below on purpose —
    a kill of this process never runs a `finally`, so neither mechanism
    alone carries the guarantee.
    """
    tell = ['--parent-pid', str(parent_pid)] if parent_pid else []
    return (
        ('comments', [sys.executable, '-u',
                      str(HERE / 'pr_comment_watch.py'), str(pr)] + tell),
        ('ci', [sys.executable, '-u',
                str(HERE / 'ci_watch.py'), branch, '--debounce', '0']
         + tell),
    )


def _terminate(children):
    """Terminate, a short wait, then kill: the children never outlive us."""
    for child in children.values():
        if child.poll() is None:
            child.terminate()
    for child in children.values():
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


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
    watcher = gh_client.Watcher('watch_all', out=sys.stderr)
    try:
        for name, argv in _watchers(pr, branch, os.getpid()):
            child = _spawn(argv)
            children[name] = child
            print(f'started {name} watcher pid {child.pid}', file=sys.stderr,
                  flush=True)
            for kind, stream in (('out', child.stdout),
                                 ('err', child.stderr)):
                thread = threading.Thread(
                    target=_pump, args=(name, stream, sink, kind),
                    daemon=True)
                thread.start()

        print(f'watching pr {pr} and branch {branch}; '
              f'batching until {debounce}s of silence', file=sys.stderr,
              flush=True)
        return _aggregate(sink, children, watcher, pr, branch, debounce,
                          limit, log_path, max_hold)
    finally:
        _terminate(children)


def _aggregate(sink, children, watcher, pr, branch, debounce, limit,
               log_path, max_hold):
    """The batching loop, until every child is gone."""
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
            # A watcher's stderr is its own diagnostic channel; keep it
            # off the event stream unless the watcher has actually died,
            # which must never look the same as a quiet surface.
            if kind == 'out':
                batch.append(f'[{name}] {line}')
            else:
                print(f'[{name}:err] {line}', file=sys.stderr,
                      flush=True)
            last = time.monotonic()
            continue

        if (batch and last is not None
                and time.monotonic() - last >= debounce):
            held_for = time.monotonic() - (held_since or time.monotonic())
            if _batch_is_only_quiet_ci(batch):
                sha = _latest_sha(batch)
                extra = _hold_release(
                    _all_concluded(sha, watcher), held_for, max_hold,
                    sha)
                if extra is None:
                    if held_since is None:
                        held_since = time.monotonic()
                    last = time.monotonic()
                    continue
                batch.extend(extra)
            _emit(batch, limit, log_path)
            batch = []
            last = None
            held_since = None

        dead = [n for n, c in children.items() if c.poll() is not None]
        if dead:
            for name in dead:
                batch.append(
                    f'[{name}] WATCHER EXITED '
                    f'rc={children[name].returncode}')
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
                             'matrix. A batch the cap releases is emitted '
                             'with a line naming the SHA and that runs are '
                             'still open, so its tally reads as partial.')
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
