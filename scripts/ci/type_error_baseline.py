#!/usr/bin/env python3
"""Check and tighten the test-tree type-error baseline in threshold data.

The mutable policy state is the ``type_error_baseline`` member of
``.github/ci-thresholds.json``: the number of type errors each tracked test
module still carries. A recorded number is never raised by hand and no
entry is ever added by hand: fix the type error in the named test module.
A stale entry goes away rather than being kept: --tighten drops one whose
file has no type error left, and an entry naming a file that is gone is
deleted by hand. A run that analyses no test module, or a count that
differs from the tracked one in either direction, is a broken scope
rather than a clean one: fewer means pyrightconfig.tests.json reaches
fewer test modules than the tree tracks, more means it reaches a test
module the tree has not tracked yet, and a config that could not be
read falls back to a default scope; check the config is present,
parses, includes the tests directory, and that every test file is
tracked.

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
    'A run that analyses no test module, or a count that differs from the '
    'tracked one in either direction, is a broken scope rather than a clean '
    f'one: fewer means {CONFIG_NAME} reaches fewer test modules than the '
    'tree tracks, more means it reaches a test module the tree has not '
    'tracked yet, and a config that could not be read falls back to a '
    'default scope; check the config is present, parses, includes the tests '
    'directory, and that every test file is tracked.')
REMEDY_FOR = {
    'unanalysed': SCOPE_REMEDY,
    'grown': FIX_REMEDY,
    'over': FIX_REMEDY,
    'missing': STALE_ENTRY_REMEDY,
    'graduated': STALE_ENTRY_REMEDY,
}


def tracked_test_modules(root=ROOT):
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
        return json.loads(result.stdout), result.stderr.strip()
    except json.JSONDecodeError:
        raise ValueError(
            f'pyright produced no report: {result.stderr.strip()}') from None


def _rel_key(raw, root):
    """Return the repo-relative key `raw` names, raising ValueError outside.

    ``git ls-files`` spells every tracked path with forward slashes on every
    host, so the diagnostic set must agree with that spelling. Normalise the
    separators first, then relativise: this yields the same forward-slash key
    for the same file whichever separator the producer used, on every host,
    rather than letting a ``str(Path)`` rendering pick the host's own.
    """
    posix = str(raw).replace('\\', '/')
    return Path(posix).relative_to(root).as_posix()


def _errors_by_file(report, root):
    counts = {}
    for diagnostic in report.get('generalDiagnostics', []):
        if diagnostic.get('severity') != 'error':
            continue
        try:
            rel = _rel_key(diagnostic['file'], root)
        except (KeyError, ValueError):
            continue
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def analyse(root=ROOT):
    """Return pyright's analysed count, tracked errors, and its stderr."""
    report, stderr = _pyright_report(root)
    errored = _errors_by_file(report, root)
    tracked = tracked_test_modules(root)
    counts = {rel: errored.get(rel, 0) for rel in tracked}
    try:
        analysed = report['summary']['filesAnalyzed']
    except (KeyError, TypeError):
        raise ValueError(
            'pyright report carries no analysed-count summary') from None
    return analysed, counts, stderr


def violations(counts, analysed, expected, baseline):
    """Classify the analysed scope and every counted module's standing."""
    found = {'unanalysed': (), 'grown': {}, 'over': {}, 'missing': [],
             'graduated': []}
    if analysed != expected or expected == 0:
        found['unanalysed'] = (analysed, expected)
    for rel, count in sorted(counts.items()):
        recorded = baseline.get(rel)
        if recorded is None:
            if count > 0:
                found['over'][rel] = count
        elif count > recorded:
            found['grown'][rel] = (count, recorded)
        elif count == 0:
            found['graduated'].append(rel)
    found['missing'] = sorted(set(baseline) - set(counts))
    return found


def tightened(baseline, counts):
    """Return a lowered baseline mapping, or ``None`` when unchanged."""
    lowers = {rel: counts[rel]
              for rel, recorded in baseline.items()
              if rel in counts and 0 < counts[rel] < recorded}
    drops = {rel for rel, recorded in baseline.items()
             if rel in counts and counts[rel] == 0}
    if not lowers and not drops:
        return None
    updated = {rel: recorded for rel, recorded in baseline.items()
               if rel not in drops}
    updated.update(lowers)
    return updated


def refuse(found, diagnostic=''):
    remedies = []
    for kind, detail in found.items():
        if detail:
            print(f'{kind}: {detail}', file=sys.stderr)
            if REMEDY_FOR[kind] not in remedies:
                remedies.append(REMEDY_FOR[kind])
    if diagnostic:
        print(f'pyright: {diagnostic}', file=sys.stderr)
    for remedy in remedies:
        print(remedy, file=sys.stderr)
    return 1


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
        analysed, counts, stderr = analyse(args.root)
        found = violations(counts, analysed, len(counts), baseline)
        if args.tighten:
            if found['unanalysed']:
                return refuse({'unanalysed': found['unanalysed']}, stderr)
            updated = tightened(baseline, counts)
            if updated is None:
                print('no test module lost a type error')
                return 0
            data['type_error_baseline'] = updated
            thresholds.write(args.thresholds, data)
            print('tightened the type-error baseline')
            return 0

        if not any(found.values()):
            print(f'{analysed} test modules analysed, within the '
                  'type-error policy')
            return 0
        return refuse(found, stderr)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
