#!/usr/bin/env python3
"""Check and tighten the tests/ line budget in CI threshold data."""
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

GROWTH_REMEDY = (
    'A recorded number is never raised: delete as many lines elsewhere '
    'under tests/ as this change added, or land them after this row moves.')


def tracked_test_lines(root=ROOT):
    """Sum the text lines of every tracked file under ``tests/``.

    ``git grep -I`` skips a binary fixture, and the pathspec names the
    directory rather than an extension.
    """
    listed = subprocess.run(
        ['git', '-C', str(root), 'grep', '-I', '-c', '', '--', 'tests/'],
        capture_output=True, check=True)
    total = 0
    for raw in listed.stdout.splitlines():
        _path, _separator, count = raw.rpartition(b':')
        total += int(count)
    return total


def tightened(recorded, measured):
    """Return a lowered budget, or ``None`` when there is no drop."""
    return measured if measured < recorded else None


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tighten', action='store_true',
                        help='follow the tree down instead of reporting')
    parser.add_argument(
        '--thresholds', type=Path, default=thresholds.THRESHOLDS)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        data = thresholds.load(args.thresholds)
        recorded = thresholds.tests_line_baseline(data)
        measured = tracked_test_lines()
        if args.tighten:
            updated = tightened(recorded, measured)
            if updated is None:
                print(f'tests/ carries {measured} lines; the budget stays '
                      f'at {recorded}')
                return 0
            data['tests_line_baseline'] = updated
            thresholds.write(args.thresholds, data)
            print(f'tightened the tests/ line budget to {updated}')
            return 0

        if measured > recorded:
            print(f'tests/ carries {measured} lines against a recorded '
                  f'budget of {recorded}', file=sys.stderr)
            print(GROWTH_REMEDY, file=sys.stderr)
            return 1
        print(f'tests/ carries {measured} lines within the recorded budget '
              f'of {recorded}')
        return 0
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
