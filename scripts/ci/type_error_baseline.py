#!/usr/bin/env python3
"""Check and tighten the test-tree type-error baseline in threshold data.

The mutable policy state is the ``type_error_baseline`` member of
``.github/ci-thresholds.json``: the number of type errors each tracked test
module still carries. A recorded number is never raised by hand and no
entry is ever added by hand: fix the type error in the named test module.
A stale entry goes away rather than being kept: --tighten drops one whose
file has no type error left, and an entry naming a file that is gone is
deleted by hand. A run that analyses no test module, or fewer than the
tracked count, is a broken scope rather than a clean one: check that
pyrightconfig.tests.json includes the tests directory.

  python3 scripts/ci/type_error_baseline.py
  python3 scripts/ci/type_error_baseline.py --tighten
"""
import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level,no-name-in-module
    from . import thresholds
else:
    thresholds = importlib.import_module('thresholds')


ROOT = Path(__file__).resolve().parents[2]
CONFIG_NAME = 'pyrightconfig.tests.json'
TEST_GLOB = 'tests/*.py'

FIX_REMEDY = (
    'A recorded number is never raised by hand and no entry is ever added '
    'by hand: fix the type error in the named test module.')
STALE_ENTRY_REMEDY = (
    'A stale entry goes away rather than being kept: --tighten drops one '
    'whose file has no type error left, and an entry naming a file that '
    'is gone is deleted by hand.')
SCOPE_REMEDY = (
    'A run that analyses no test module, or fewer than the tracked count, '
    'is a broken scope rather than a clean one: check that '
    f'{CONFIG_NAME} includes the tests directory.')
REMEDY_FOR = {
    'unanalysed': SCOPE_REMEDY,
    'grown': FIX_REMEDY,
    'over': FIX_REMEDY,
    'missing': STALE_ENTRY_REMEDY,
    'graduated': STALE_ENTRY_REMEDY,
}


def tracked_test_modules(root=ROOT):
    """Return the relative path of every tracked test module."""
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z', '--', TEST_GLOB],
        capture_output=True, check=True, timeout=30)
    return {raw.decode('utf-8', 'surrogateescape')
            for raw in listed.stdout.split(b'\0') if raw}


def _pyright_report(root):
    result = subprocess.run(
        ['pyright', '-p', str(root / CONFIG_NAME), '--outputjson'],
        cwd=str(root), capture_output=True, text=True, timeout=600)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError(
            f'pyright produced no report: {result.stderr.strip()}') from None


def _errors_by_file(report, root):
    """Count error-severity diagnostics per tracked test module."""
    counts = {}
    for diagnostic in report.get('generalDiagnostics', []):
        if diagnostic.get('severity') != 'error':
            continue
        try:
            rel = str(Path(diagnostic['file']).relative_to(root))
        except (KeyError, ValueError):
            continue
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def analyse(root=ROOT):
    """Return pyright's analysed count and every tracked module's errors."""
    report = _pyright_report(root)
    errored = _errors_by_file(report, root)
    tracked = tracked_test_modules(root)
    counts = {rel: errored.get(rel, 0) for rel in tracked}
    return report['summary']['filesAnalyzed'], counts


def violations(counts, analysed, expected, baseline):
    return {
        'unanalysed': () if analysed == expected else (analysed, expected),
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
                        help='follow fixed test modules down instead of '
                             'reporting')
    parser.add_argument(
        '--thresholds', type=Path, default=thresholds.THRESHOLDS)
    parser.add_argument(
        '--root', type=Path, default=ROOT,
        help='the repository whose tests and checker config are checked')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        data = thresholds.load(args.thresholds)
        baseline = thresholds.type_error_baseline(data)
        analysed, counts = analyse(args.root)
        if args.tighten:
            updated = tightened(baseline, counts)
            if updated is None:
                print('no test module lost a type error')
                return 0
            data['type_error_baseline'] = updated
            thresholds.write(args.thresholds, data)
            print('tightened the type-error baseline')
            return 0

        found = violations(counts, analysed, len(counts), baseline)
        if not any(found.values()):
            print(f'{analysed} test modules analysed, within the '
                  'type-error policy')
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
