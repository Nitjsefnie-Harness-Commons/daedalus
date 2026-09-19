#!/usr/bin/env python3
"""Check and tighten the long-line baseline in CI threshold data.

A line is over the limit when its UTF-8 text, without the terminator, has
more than 79 characters. The mutable policy state is the
``long_line_baseline`` member of ``.github/ci-thresholds.json``: the number
of over-limit lines each tracked Python file still carries. A recorded
number is never raised by hand and no entry is ever added by hand: wrap
each over-limit line to 79 characters or fewer. A stale entry goes away
rather than being kept: --tighten drops one whose file has no over-limit
line left, and an entry naming a file that is gone is deleted by hand.

  python3 scripts/ci/line_lengths.py
  python3 scripts/ci/line_lengths.py --tighten
"""
import argparse
import importlib
import subprocess
import sys
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level,no-name-in-module
    from . import thresholds
else:
    thresholds = importlib.import_module('thresholds')


ROOT = Path(__file__).resolve().parents[2]
LINE_LIMIT = 79

WRAP_REMEDY = (
    'A recorded number is never raised by hand and no entry is ever added '
    f'by hand: wrap each over-limit line to {LINE_LIMIT} characters or '
    'fewer.')
STALE_ENTRY_REMEDY = (
    'A stale entry goes away rather than being kept: --tighten drops one '
    'whose file has no over-limit line left, and an entry naming a file '
    'that is gone is deleted by hand.')
REMEDY_FOR = {
    'grown': WRAP_REMEDY,
    'over': WRAP_REMEDY,
    'missing': STALE_ENTRY_REMEDY,
    'graduated': STALE_ENTRY_REMEDY,
}


def _without_terminator(raw):
    text = raw.decode('utf-8')
    if text.endswith('\r\n'):
        return text[:-2]
    if text.endswith('\n'):
        return text[:-1]
    return text


def _over_limit_lines(path):
    with open(path, 'rb') as handle:
        return sum(1 for raw in handle
                   if len(_without_terminator(raw)) > LINE_LIMIT)


def tracked_long_lines(root=ROOT):
    """Return the over-limit line count of every tracked Python file."""
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z', '*.py'],
        capture_output=True, check=True, timeout=30)
    counts = {}
    for raw in listed.stdout.split(b'\0'):
        if not raw:
            continue
        rel = raw.decode('utf-8', 'surrogateescape')
        path = root / rel
        if not path.is_file():
            continue
        try:
            counts[rel] = _over_limit_lines(path)
        except UnicodeDecodeError as error:
            raise ValueError(f'{rel}: {error}') from None
    return counts


def violations(counts, baseline):
    return {
        'grown': {rel: (counts[rel], recorded)
                  for rel, recorded in baseline.items()
                  if rel in counts and counts[rel] > recorded},
        'over': {rel: count for rel, count in counts.items()
                 if rel not in baseline and count > 0},
        'missing': sorted(rel for rel in baseline if rel not in counts),
        'graduated': sorted(rel for rel in baseline
                            if rel in counts and counts[rel] == 0),
    }


def tightened(baseline, counts):
    """Return a lowered baseline mapping, or ``None`` when unchanged."""
    updated = dict(baseline)
    for rel, recorded in baseline.items():
        if rel not in counts:
            continue
        current = counts[rel]
        if current == 0:
            del updated[rel]
        elif current < recorded:
            updated[rel] = current
    return updated if updated != baseline else None


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tighten', action='store_true',
                        help='follow wrapped files down instead of reporting')
    parser.add_argument(
        '--thresholds', type=Path, default=thresholds.THRESHOLDS)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        data = thresholds.load(args.thresholds)
        baseline = thresholds.long_line_baseline(data)
        counts = tracked_long_lines()
        if args.tighten:
            updated = tightened(baseline, counts)
            if updated is None:
                print('no file lost an over-limit line')
                return 0
            data['long_line_baseline'] = updated
            thresholds.write(args.thresholds, data)
            print('tightened the long-line baseline')
            return 0

        found = violations(counts, baseline)
        if not any(found.values()):
            print(f'{len(counts)} tracked modules within the line-length '
                  'policy')
            return 0
        remedies = []
        for kind, detail in found.items():
            if detail:
                print(f'{kind}: {detail}', file=sys.stderr)
                if REMEDY_FOR[kind] not in remedies:
                    remedies.append(REMEDY_FOR[kind])
        for remedy in remedies:
            print(remedy, file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
