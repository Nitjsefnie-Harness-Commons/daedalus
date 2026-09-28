#!/usr/bin/env python3
"""Check and tighten the per-module JavaScript coverage baseline.

The mutable policy state is the ``js_coverage_baseline`` member of
``.github/ci-thresholds.json``: how many uncovered executable lines each
tracked JavaScript file still carries, counted from the same V8 dumps and
the same physical code-line detection the tree-wide total uses. A recorded
number is never raised by hand and no entry is ever added by hand: cover the
uncovered lines. A stale entry goes away rather than being kept: --tighten
drops one whose file is fully covered, and an entry naming a file that is
gone is deleted by hand.

  python3 scripts/ci/js_module_coverage.py "$NODE_V8_COVERAGE"
  python3 scripts/ci/js_module_coverage.py --tighten \
    "$NODE_V8_COVERAGE" --thresholds .github/ci-thresholds.json
"""
import argparse
import importlib
import subprocess
import sys
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level,no-name-in-module
    from . import js_coverage, thresholds
else:
    js_coverage = importlib.import_module('js_coverage')
    thresholds = importlib.import_module('thresholds')


ROOT = Path(__file__).resolve().parents[2]

UNCOVERED_REMEDY = (
    'A recorded number is never raised by hand and no entry is ever added '
    'by hand: cover the uncovered lines.')
STALE_ENTRY_REMEDY = (
    'A stale entry goes away rather than being kept: --tighten drops one '
    'whose file is fully covered, and an entry naming a file that is gone '
    'is deleted by hand.')
REMEDY_FOR = {
    'grown': UNCOVERED_REMEDY,
    'unrecorded': UNCOVERED_REMEDY,
    'missing': STALE_ENTRY_REMEDY,
    'graduated': STALE_ENTRY_REMEDY,
}


def uncovered_counts(coverage_dir, root=ROOT):
    """Return the uncovered executable line count of every tracked file."""
    report = js_coverage.collect_coverage(coverage_dir, root)
    return {rel: len(item.executable_lines) - len(item.covered_lines)
            for rel, item in report.files.items()}


def violations(counts, baseline):
    return {
        'grown': {rel: (counts[rel], recorded)
                  for rel, recorded in baseline.items()
                  if rel in counts and counts[rel] > recorded},
        'unrecorded': {rel: count for rel, count in counts.items()
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
    parser.add_argument('coverage_dir', type=Path,
                        help='directory containing NODE_V8_COVERAGE dumps')
    parser.add_argument('--tighten', action='store_true',
                        help='follow fully covered files down instead of '
                             'reporting')
    parser.add_argument('--root', type=Path, default=ROOT,
                        help='repository root (default: this checkout)')
    parser.add_argument(
        '--thresholds', type=Path, default=thresholds.THRESHOLDS)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        data = thresholds.load(args.thresholds)
        baseline = thresholds.js_coverage_baseline(data)
        counts = uncovered_counts(args.coverage_dir, args.root)
        if args.tighten:
            updated = tightened(baseline, counts)
            if updated is None:
                print('no file lost an uncovered line')
                return 0
            data['js_coverage_baseline'] = updated
            thresholds.write(args.thresholds, data)
            print('tightened the per-module coverage baseline')
            return 0

        found = violations(counts, baseline)
        if not any(found.values()):
            print(f'{len(counts)} tracked modules within the per-module '
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
    except (OSError, subprocess.SubprocessError, ValueError) \
            as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
