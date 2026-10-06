#!/usr/bin/env python3
"""The documented tests-line re-seed marker (the ``tests_line_baseline`` row).

A recorded line budget is never raised by hand, so a re-seed is the
delete-then-seed sequence: one commit carries the document WITHOUT the
row, the next records the seed at the count the gate measures on its own
tree. The loader refuses a document without the row, so the delete needs
a sanction this module reads: a commit whose MESSAGE carries

    [tests-line-re-seed]

may carry the document without the row. The marker buys the ABSENCE
only — never a value, which is always the next commit's seed.
"""
from __future__ import annotations

import functools
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Spelled here because importing thresholds for it would put the
# git-reading module on every loader consumer's import graph — the same
# reason thresholds reaches this module only lazily, inside verdict().
# Mirrors thresholds._SCALAR_FIELDS; a test pins the two together.
_SCALAR_FIELD = 'tests_line_baseline'
_THRESHOLDS_AT = '.github/ci-thresholds.json'
_TIMEOUT = 10
# The re-seed series is short by construction; a longer window than the
# cap fails closed and needs the cap widened, not silently tolerated.
_MAX_VISITS = 10

MARKER = '[tests-line-re-seed]'


def _run(root: Path, *args: str) -> subprocess.CompletedProcess | None:
    """One bounded git read, or None when git cannot answer."""
    try:
        proc = subprocess.run(
            ['git', '-C', str(root), *args],
            capture_output=True, check=False, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc


def _revision_message(root: Path, revision: str) -> str | None:
    proc = _run(root, 'log', '-1', '--format=%B', revision)
    if proc is None:
        return None
    return proc.stdout.decode('utf-8', 'replace')


def _parents(root: Path, revision: str) -> list[str]:
    proc = _run(root, 'show', '-s', '--format=%P', revision)
    if proc is None:
        return []
    return proc.stdout.decode('ascii', 'replace').split()


def _field_present(root: Path, revision: str) -> bool:
    """Whether the document committed at ``revision`` carries the row.

    Presence only, judged on the committed bytes — an unparseable
    document is not field-present. A commit whose row is present closes
    the window whatever its message says.
    """
    proc = _run(root, 'show', f'{revision}:{_THRESHOLDS_AT}')
    if proc is None:
        return False
    try:
        document = json.loads(proc.stdout)
    except ValueError:
        return False
    return isinstance(document, dict) and _SCALAR_FIELD in document


def _declares(root: Path) -> bool:
    """Whether any commit the bounded walk reads carries the marker.

    A walk, never a single read of HEAD: a tolerance scoped to HEAD
    lasts exactly one commit, and a publisher commit can land between
    the delete and the seed. Every parent is queued with ``reversed()``
    so at a GitHub merge commit the last-listed parent — the pull
    request's head, the lineage that declared — is visited first.
    """
    seen: set[str] = set()
    queue: list[str] = ['HEAD']
    while queue and len(seen) < _MAX_VISITS:
        revision = queue.pop(0)
        if revision in seen:
            continue
        seen.add(revision)
        message = _revision_message(root, revision)
        if message is None:
            continue
        if _field_present(root, revision):
            continue
        if MARKER in message:
            return True
        queue.extend(reversed(_parents(root, revision)))
    return False


@functools.lru_cache(maxsize=8)
def in_flight(root: Path | None = None) -> bool:
    """Whether this tree declares a tests-line re-seed in flight."""
    return _declares(ROOT if root is None else root)


def clear_cache() -> None:
    """Drop the cached probe so a caller may read another tree."""
    in_flight.cache_clear()
