#!/usr/bin/env python3
"""Judge threshold direction and removals over one base..head range."""
import argparse
import importlib
import re
import subprocess
import sys
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level,no-name-in-module
    from . import thresholds, thresholds_removed
else:
    thresholds = importlib.import_module('thresholds')
    thresholds_removed = importlib.import_module('thresholds_removed')

SCALAR = 'tests_line_baseline'
REMOVAL_REMEDY = (
    'Declare removals with Thresholds-Removed: <entry>=<bug|definition> '
    'in the judged range; use the full entry or bare baseline family. '
    'A path entry needs no declaration only when its file is no longer '
    'tracked. A breach is never a removal reason.')
RAISE_REMEDY = (
    'Record a higher tests/ budget only with a feat: or feat(scope): '
    'commit in the judged range. Non-feature changes must trim their own '
    'tests: drop redundant cases, merge parametrisations, or delete tests '
    'that repeat assertions.')


def _has_feature_commits(base_rev, head_rev):
    """Only a readable range with an eligible subject proves a feature."""
    if not base_rev or not base_rev.strip():
        raise ValueError('cannot read feature history: no judged base')
    try:
        root = str(Path(__file__).resolve().parents[2])
        history = subprocess.run(
            ['git', '-C', root, 'rev-parse', '--is-shallow-repository'],
            capture_output=True, check=False, timeout=10)
        if history.returncode or history.stdout.strip() != b'false':
            raise ValueError('cannot read feature history: shallow or '
                             'unreadable repository')
        proc = subprocess.run(
            ['git', '-C', root, 'log', '--format=%s',
             f'{base_rev}..{head_rev}'],
            capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'cannot read feature history: {error}') from None
    if proc.returncode != 0:
        raise ValueError(
            f'cannot read feature history {base_rev}..{head_rev}: '
            f'git exited {proc.returncode}')
    # Git separates subjects with LF; other separators are subject content.
    subjects = proc.stdout.decode('utf-8', 'replace').split('\n')
    return any(re.match(r'^feat:|^feat\([^)]*\):', subject)
               for subject in subjects)


def _subject_gone(path):
    """Only git's unmatched-path status proves the subject is untracked."""
    try:
        proc = subprocess.run(
            ['git', '-C', str(Path(__file__).resolve().parents[2]),
             'ls-files', '--error-unmatch', '--', path],
            capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 1


def forbidden_moves(base, head, feature_present=False,
                    removal_declarations=thresholds_removed.Declarations()):
    """Compare validated documents, with scalar and path authority separate."""
    moves = []
    before, after = base.get(SCALAR), head.get(SCALAR)
    if before is not None:
        if after is None and SCALAR not in removal_declarations:
            moves.append(f'removed without a reason: {SCALAR}')
        elif after is not None and after > before and not feature_present:
            moves.append(f'{SCALAR}: {before} -> {after}; {RAISE_REMEDY}')
    for family in sorted(thresholds_removed.BASELINE_FAMILIES):
        before, after = base.get(family, {}), head.get(family, {})
        removed = before.keys() - after.keys()
        declared_paths = removal_declarations.paths(family, removed)
        for path in sorted(removed - declared_paths):
            if not _subject_gone(path):
                moves.append(f'removed without a reason: {family}.{path}')
        for path in sorted(before.keys() & after.keys()):
            if after[path] > before[path]:
                moves.append(f'{family}.{path}: {before[path]} -> '
                             f'{after[path]}; baseline entries never rise')
    for language in ('python', 'javascript'):
        for field in ('measured', 'floor'):
            before = base['coverage'][language][field]
            after = head['coverage'][language][field]
            if after < before:
                moves.append(f'coverage.{language}.{field}: '
                             f'{before} -> {after}; coverage never lowers')
    return moves


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True,
                        help='base document read from the judged revision')
    parser.add_argument('--head', type=Path, default=thresholds.THRESHOLDS)
    parser.add_argument('--base-rev', help='exclusive judged base')
    parser.add_argument('--head-rev', default='HEAD',
                        help='inclusive judged head')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        base, head = thresholds.load(args.base), thresholds.load(args.head)
        feature = _has_feature_commits(args.base_rev, args.head_rev)
        declarations = thresholds_removed.declared(
            args.base_rev, args.head_rev, Path(__file__).resolve().parents[2])
        moves = forbidden_moves(base, head, feature, declarations)
        if not moves:
            print('no forbidden threshold moves')
            return 0
        for move in moves:
            print(f'forbidden move: {move}', file=sys.stderr)
        if any(move.startswith('removed without a reason:') for move in moves):
            print(REMOVAL_REMEDY, file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
