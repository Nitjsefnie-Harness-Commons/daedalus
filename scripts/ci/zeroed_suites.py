#!/usr/bin/env python3
"""Suites the comparison loses: a shipped suite with zero accepted tests in
a paired round, or with no report for one of them, the suite names taken
across all rounds of both sides.
"""
import json
from pathlib import Path


def accepted_duration(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _accepted(summary):
    if not isinstance(summary, dict):
        return 0
    tests = summary.get('tests')
    if not isinstance(tests, dict):
        return 0
    return sum(1 for value in tests.values() if accepted_duration(value))


def _round_counts(path):
    try:
        return _accepted(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return 0


def _rounds(directories):
    counts = []
    for directory in directories:
        found = {}
        for path in sorted(Path(directory).glob('*.json')):
            found[path.stem] = _round_counts(path)
        counts.append(found)
    return counts


def _ships(rounds, suite):
    return any(counts.get(suite, 0) for counts in rounds)


def zeroed_on_both_sides(base_dirs, head_dirs):
    base_rounds = _rounds(base_dirs)
    head_rounds = _rounds(head_dirs)
    suites = set()
    for counts in base_rounds + head_rounds:
        suites.update(counts)
    lost = set()
    for base, head in zip(base_rounds, head_rounds):
        for suite in suites:
            if suite in base and suite in head:
                if base[suite] == 0 and head[suite] == 0:
                    lost.add(suite)
            elif _ships(base_rounds, suite) and _ships(head_rounds, suite):
                lost.add(suite)
    return sorted(lost)
