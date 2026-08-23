#!/usr/bin/env python3
"""Compare test durations between two checkouts of this repository.

Total wall time is the wrong metric, which is why this does not use it. A
suite total moves whenever a test is added, removed or skipped, so a release
that grew three tests would read as a regression and one that deleted three
would read as an improvement. What is compared here is the set of tests
present and passing on BOTH sides, summed — adding or removing a test cannot
move that number at all.

Each side is measured over several rounds and a test's duration is the
MINIMUM across them. A minimum estimates the floor, which is the quantity that
changes when code gets slower; a mean mostly reports how noisy the runner was.

Durations come from the per-suite summaries `run_tests.py` writes when
DAEDALUS_TEST_SUMMARY_DIR names a directory. Only passing tests are recorded
there, so a test that failed on one side is simply absent and drops out of the
intersection rather than contributing the time it took to give up.
"""
import argparse
import json
import sys
from pathlib import Path


def round_durations(directory):
    """Every test duration from one round, merged across its suites."""
    merged = {}
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
        for name, seconds in tests.items():
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                merged[name] = float(seconds)
    return merged


def side_durations(directories):
    """One duration per test for a side: the minimum across its rounds."""
    best = {}
    for directory in directories:
        for name, seconds in round_durations(directory).items():
            if name not in best or seconds < best[name]:
                best[name] = seconds
    return best


def compare(base, head):
    """Totals over the tests both sides ran, and the per-test movements."""
    shared = sorted(set(base) & set(head))
    base_total = sum(base[name] for name in shared)
    head_total = sum(head[name] for name in shared)
    movements = [
        (name, base[name], head[name], head[name] - base[name])
        for name in shared
    ]
    movements.sort(key=lambda row: row[3], reverse=True)
    return shared, base_total, head_total, movements


def render(lines, base_label, shared, base_total, head_total, movements, limit=10):
    """The step summary: the verdict first, then what moved most."""
    ratio = head_total / base_total if base_total else 1.0
    lines.append('### Test speed')
    lines.append('')
    lines.append(f'Baseline `{base_label}`, over {len(shared)} tests present '
                 'and passing on both sides.')
    lines.append('')
    lines.append(f'- baseline total: {base_total:.2f}s')
    lines.append(f'- this commit: {head_total:.2f}s')
    lines.append(f'- ratio: {ratio:.3f}')
    lines.append('')
    # Individual numbers are not trustworthy enough to gate on -- a single
    # test can move by multiples between two runs of identical code -- but
    # they are what tells a reader WHERE the total went, so they are shown
    # and never asserted on.
    lines.append('Largest individual movements (indicative only):')
    lines.append('')
    lines.append('| test | baseline | head | delta |')
    lines.append('|---|---:|---:|---:|')
    for name, was, now, delta in movements[:limit]:
        lines.append(f'| `{name}` | {was:.2f}s | {now:.2f}s | {delta:+.2f}s |')
    return ratio


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', nargs='+', required=True,
                        help='one directory of summaries per baseline round')
    parser.add_argument('--head', nargs='+', required=True,
                        help='one directory of summaries per head round')
    parser.add_argument('--max-regression', type=float, default=0.30,
                        help='fractional slowdown that fails the comparison')
    parser.add_argument('--base-label', default='baseline')
    parser.add_argument('--summary-file')
    args = parser.parse_args(argv)

    base = side_durations(args.base)
    head = side_durations(args.head)
    lines = []
    # A baseline that recorded no durations is not a fast baseline -- it is a
    # release from before the runner reported them. Saying so and passing is
    # the same answer this gate gives before the first release exists.
    if not base:
        lines.append('### Test speed')
        lines.append('')
        lines.append(f'Skipped: the baseline `{args.base_label}` reports no '
                     'per-test durations, so there is nothing to compare '
                     'against. This becomes a real gate at the next release.')
        _emit(lines, args.summary_file)
        return 0

    shared, base_total, head_total, movements = compare(base, head)
    if not shared:
        lines.append('### Test speed')
        lines.append('')
        lines.append('Skipped: no test passed on both sides, so the '
                     'comparison has no shared set to sum.')
        _emit(lines, args.summary_file)
        return 0

    ratio = render(lines, args.base_label, shared, base_total, head_total,
                   movements)
    budget = 1.0 + args.max_regression
    if ratio > budget:
        lines.append('')
        lines.append(f'**FAIL**: {ratio:.3f} exceeds the {budget:.2f} budget.')
        _emit(lines, args.summary_file)
        return 1
    lines.append('')
    lines.append(f'**OK**: {ratio:.3f} is within the {budget:.2f} budget.')
    _emit(lines, args.summary_file)
    return 0


def _emit(lines, summary_file):
    text = '\n'.join(lines)
    print(text)
    if summary_file:
        try:
            with open(summary_file, 'a', encoding='utf-8') as handle:
                handle.write(text + '\n')
        except OSError:
            pass


if __name__ == '__main__':
    sys.exit(main())
