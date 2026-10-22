#!/usr/bin/env python3
"""Claim a release on main, wait for its gates, tag, and await publishing."""
import argparse
import importlib
import json
import math
import os
import re
import subprocess
import sys
import time

if __package__:
    # pylint: disable-next=relative-beyond-top-level,no-name-in-module
    from . import ratchet_push
else:
    ratchet_push = importlib.import_module('ratchet_push')


ATTEMPTS = 3


def _run(*argv):
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    print(done.stdout + done.stderr, end='', file=sys.stderr)
    done.check_returncode()
    return done.stdout.strip()


def _git(*argv):
    return _run('git', *argv)


def _credentials():
    key = os.environ.get('MASTER_PUSH_DEPLOY_KEY')
    repository = os.environ.get('REPO')
    if not key or not repository:
        raise ValueError('MASTER_PUSH_DEPLOY_KEY and REPO must be set')
    ratchet_push.prepare_ssh(key)
    return repository


def _commit(message):
    _git('-c', f'user.name={ratchet_push.AUTHOR}',
         '-c', f'user.email={ratchet_push.EMAIL}', 'commit', '-m', message)


def bump(version, successor=False):
    """Rebuild the claim on current main when a fast-forward push races."""
    if not successor and len(version.split('.')) > 3:
        raise ValueError('release version must leave room for its .1 '
                         'successor within Chrome\'s four-segment limit')
    repository = _credentials()
    released = version
    if successor:
        version += '.1'
    message = (f'Stop main claiming the version release {released} published'
               if successor else f'Claim {version} across every version site')
    for attempt in range(1, ATTEMPTS + 1):
        _git('fetch', 'origin', 'main')
        _git('reset', '--hard', 'origin/main')
        base = _git('rev-parse', 'HEAD')
        _run(sys.executable, 'scripts/check_versions.py')
        _run(sys.executable, 'scripts/check_versions.py', '--set', version)
        _git('add', '-u')
        if not _git('diff', '--cached', '--name-only'):
            raise ValueError(f'main already claims {version}; nothing to bump')
        _commit(message)
        sha = _git('rev-parse', 'HEAD')
        try:
            ratchet_push.push(repository, 'HEAD:main')
        except subprocess.CalledProcessError as error:
            _git('fetch', 'origin', 'main')
            if _git('rev-parse', 'origin/main') == base:
                raise ValueError('release push rejected while main stood '
                                 'still; see git refusal above') from error
            if attempt == ATTEMPTS:
                raise ValueError(f'release push rejected after {ATTEMPTS} '
                                 'attempts; see git refusal above') from error
            print('main moved; retry on its current tip', file=sys.stderr)
        else:
            output = os.environ.get('GITHUB_OUTPUT')
            if output:
                with open(output, 'a', encoding='utf-8') as handle:
                    handle.write(f'sha={sha}\n')
            print(sha)
            return


def tag(version, sha):
    """Publish an annotated release tag without replacing an existing tag."""
    repository = _credentials()
    name = f'v{version}'
    _git('-c', f'user.name={ratchet_push.AUTHOR}',
         '-c', f'user.email={ratchet_push.EMAIL}',
         'tag', '-a', name, sha, '-m', f'Release {name}')
    ratchet_push.push(repository, f'refs/tags/{name}:refs/tags/{name}')


def _read(argv):
    argv = [os.environ.get('DAEDALUS_GH', argv[0]), *argv[1:]]
    return subprocess.run(argv, check=True, capture_output=True,
                          text=True, timeout=60).stdout


def _runs(read, repository, sha):
    raw = read(['gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
                f'repos/{repository}/actions/runs'
                f'?head_sha={sha}'])
    decoder = json.JSONDecoder()
    rows = []
    # gh emits one JSON document per page; consume every page.
    while raw.strip():
        page, end = decoder.raw_decode(raw.lstrip())
        rows.extend(page['workflow_runs'])
        raw = raw.lstrip()[end:]
    return rows


def wait(read, repository, sha, *, release=False, timeout=2700, grace=300,
         interval=20):
    """Use the release waiter's gate predicate, or require release success."""
    started = time.monotonic()
    kind = 'release' if release else 'gates'
    state = ''
    while time.monotonic() - started < timeout:
        rows = _runs(read, repository, sha)
        rows = ([row for row in rows if row['name'] == 'release'] if release
                else [row for row in rows
                      if row['name'] not in ('release', 'pr gate')
                      and row['event'] != 'issue_comment'])
        pending = [row for row in rows if row['status'] != 'completed']
        accepted = ('success',) if release else ('success', 'skipped')
        bad = [row for row in rows if row['status'] == 'completed'
               and row['conclusion'] not in accepted]
        state = '\n'.join(f'{row["name"]} ({row["conclusion"]}) '
                          f'{row["status"]}' for row in rows)
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            break
        if bad:
            raise ValueError(f'{kind} on {sha} did not pass:\n{state}')
        if rows and not pending:
            print(f'{len(rows)} {kind} on {sha}, all green:\n{state}')
            return
        if not rows and elapsed >= grace:
            raise ValueError(f'no workflow for {kind} ran on {sha} '
                             f'within the {grace:g}s registration grace')
        print(f'waiting: {len(rows)} {kind}, {len(pending)} still running',
              flush=True)
        remaining = min(timeout - elapsed,
                        grace - elapsed if not rows else timeout)
        time.sleep(min(interval, remaining))
    raise ValueError(f'{kind} on {sha} reached the '
                     f'{timeout:g}s deadline:\n{state}')


def _duration(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('duration must be finite and >= 0')
    return number


def _sha(value):
    if not re.fullmatch('[0-9a-f]{40}', value):
        raise argparse.ArgumentTypeError('commit must be a 40-hex SHA')
    return value


def main(argv=None, read=_read):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    claim = commands.add_parser('bump')
    claim.add_argument('version')
    claim.add_argument('--successor', action='store_true')
    annotate = commands.add_parser('tag')
    annotate.add_argument('version')
    annotate.add_argument('sha', type=_sha)
    for name, deadline in (('wait-gates', 45 * 60), ('wait-release', 30 * 60)):
        waiting = commands.add_parser(name)
        waiting.add_argument('sha', type=_sha)
        waiting.add_argument('--timeout', type=_duration, default=deadline)
        waiting.add_argument('--grace', type=_duration, default=5 * 60)
        waiting.add_argument('--interval', type=_duration, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == 'bump':
            bump(args.version, args.successor)
        elif args.command == 'tag':
            tag(args.version, args.sha)
        else:
            repository = os.environ.get('REPO')
            if not repository:
                raise ValueError('REPO must be set')
            wait(read, repository, args.sha,
                 release=args.command == 'wait-release', timeout=args.timeout,
                 grace=args.grace, interval=args.interval)
    except (OSError, ValueError, KeyError,
            subprocess.SubprocessError) as error:
        print(f'release dispatch: {error}', file=sys.stderr)
        if detail := getattr(error, 'stderr', None):
            print(detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
