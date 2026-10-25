"""Declared threshold removals from one bounded judged-range history read."""
from __future__ import annotations

import importlib
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

BASELINE_FAMILIES = frozenset({
    'long_line_baseline', 'type_error_baseline', 'js_coverage_baseline',
})


class Declarations(frozenset[str]):
    """Exact entry tokens or bare baseline-family declarations."""

    def paths(self, family: str, removed_entries: Iterable[str]) -> set[str]:
        """Select declared paths from a supplied set of removed entries."""
        if family not in BASELINE_FAMILIES:
            return set()
        return {path for path in removed_entries
                if family in self or f'{family}.{path}' in self}


def declared(base_rev: str | None, head_rev: str, root: Path) -> Declarations:
    """Read declarations; malformed lines admit nothing, failed reads raise."""
    if not base_rev or not base_rev.strip():
        raise ValueError('cannot read removal history: no judged base')
    try:
        proc = subprocess.run(
            ['git', '-C', str(root), 'log', '--format=%B',
             f'{base_rev}..{head_rev}'],
            capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'cannot read removal history: {error}') from None
    if proc.returncode != 0:
        raise ValueError(
            f'cannot read removal history {base_rev}..{head_rev}: '
            f'git exited {proc.returncode}')
    declarations = set()
    for line in proc.stdout.decode('utf-8', 'replace').split('\n'):
        match = re.fullmatch(r'Thresholds-Removed:[ \t]*(.*)', line)
        if not match:
            continue
        entries = set()
        for token in match[1].split(','):
            entry, separator, kind = (
                part.strip() for part in token.partition('='))
            if not separator or not entry or kind not in ('bug', 'definition'):
                break
            entries.add(entry)
        else:
            declarations.update(entries)
    return Declarations(declarations)


def exempt_paths(base_rev, head_rev, root, family, head_entries, candidates):
    """Intersect declarations, actual removals, and measured complaints."""
    if not base_rev or not base_rev.strip():
        return set()
    try:
        declarations = declared(base_rev, head_rev, root)
        if not declarations or family not in BASELINE_FAMILIES:
            return set()
        proc = subprocess.run(
            ['git', '-C', str(root), 'show',
             f'{base_rev}:.github/ci-thresholds.json'],
            capture_output=True, check=False, timeout=10)
        if proc.returncode != 0:
            return set()
        thresholds = importlib.import_module(
            'scripts.ci.thresholds' if __package__ else 'thresholds')
        base = thresholds.normalise(
            thresholds._drop_retired(thresholds._decode(proc.stdout)))
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    removed = set(base.get(family, {})) - set(head_entries)
    return declarations.paths(family, removed) & set(candidates)
