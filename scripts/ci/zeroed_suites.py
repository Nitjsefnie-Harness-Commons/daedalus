#!/usr/bin/env python3
"""Suites that produced zero timed tests on both sides of a comparison.

The speed comparison intersects on test name, so a suite that produced
nothing on both sides drops out of every total without anything reporting
it, and the verdict describes a narrower measurement than the cell selected
-- the shape a missing dependency in the timed virtualenvs produces. Zero on
one side alone is not that: a suite testing a feature the other side lacks
fails there legitimately, and a report only one side has is a suite that
side does not ship.
"""
import json
from pathlib import Path


def counts_by_suite(directories):
    """Timed-test count per suite report, keyed by the suite's name."""
    counts = {}
    for directory in directories:
        for path in sorted(Path(directory).glob('*.json')):
            try:
                summary = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if not isinstance(summary, dict):
                continue
            tests = summary.get('tests')
            if not isinstance(tests, dict):
                continue
            counts[path.stem] = max(counts.get(path.stem, 0), len(tests))
    return counts


def zeroed_on_both_sides(base_dirs, head_dirs):
    """Suites whose every report, on both sides, timed zero tests."""
    base = counts_by_suite(base_dirs)
    head = counts_by_suite(head_dirs)
    return sorted(name for name in set(base) & set(head)
                  if base[name] == 0 and head[name] == 0)
