#!/usr/bin/env python3
"""Resolve and export the judged threshold base once per CI job."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(*args):
    return subprocess.run(
        ['git', *args], cwd=ROOT, capture_output=True, text=True,
        check=False, timeout=10)


def resolve(event, before, repository, base_ref):
    if event == 'pull_request':
        fetched = _git('fetch', '--no-tags',
                       f'https://github.com/{repository}.git',
                       f'refs/heads/{base_ref}')
        if fetched.returncode:
            raise ValueError(
                f'cannot fetch threshold target: {fetched.stderr}')
        merged = _git('merge-base', 'HEAD', 'FETCH_HEAD')
        if merged.returncode or not merged.stdout.strip():
            raise ValueError(f'cannot resolve threshold merge base: '
                             f'{merged.stderr}')
        return merged.stdout.strip()
    for candidate in (before, 'HEAD^'):
        if not candidate.strip():
            continue
        resolved = _git('rev-parse', '--verify', f'{candidate}^{{commit}}')
        if resolved.returncode == 0 and resolved.stdout.strip():
            return resolved.stdout.strip()
    raise ValueError(f'cannot resolve threshold base: tried before '
                     f'{before!r} and HEAD^')


def main():
    try:
        base = resolve(os.environ.get('EVENT', ''),
                       os.environ.get('BEFORE', ''),
                       os.environ.get('REPO', ''),
                       os.environ.get('BASE_REF', ''))
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
            stream.write(f'DAEDALUS_THRESHOLDS_BASE_REV={base}\n')
        print(f'Threshold base: {base}')
        return 0
    except (KeyError, OSError, subprocess.SubprocessError,
            ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
