#!/usr/bin/env python3
"""Compare test durations between two checkouts of this repository.

Total wall time is the wrong metric, which is why this does not use it. A
suite total moves whenever a test is added, removed or skipped, so a release
that grew three tests would read as a regression and one that deleted three
would read as an improvement. What is compared here is the set of tests
present and passing on BOTH sides, summed — adding or removing a test cannot
move that number at all.

Every number reported here comes from a run that actually happened. Each
side is measured over several rounds, and the rounds are compared IN PAIRS:
round 1 of the baseline against round 1 of the head, round 2 against round 2.
The workflow interleaves them — base, head, base, head — so a paired ratio
divides two totals measured minutes apart on one machine, and whatever the
runner was doing at the time is in both halves of it.

Taking each test's minimum across rounds and summing those minima was the
previous shape, and it constructs a total no complete run ever achieved: the
minima come from different rounds for different tests, so the two sides have
different amounts of noise removed. On one recorded artifact it took 2.4s off
the baseline and 22.4s off the head, and reported 0.999 where every complete
pair of rounds was at least 1.07.

The verdict is the MEDIAN of the paired ratios rather than one of them. A
single pair can be spoiled outright by a noisy neighbour; the median needs
most of the pairs to agree before it moves.

Durations come from `scripts/ci/time_tests.py`, which times a checkout from
the outside and records only passing tests -- so a test that failed on one side
is simply absent and drops out of the intersection rather than contributing the
time it took to give up. That instrument belongs to the comparison, not to
either tree, which is what lets a release predating it be measured at all.

A baseline label is whichever point the run measured against: a release tag
on a push, and on a pull request the merge base of the branch and its base
branch, never that branch's tip.

An accepted speed change is an explicit in-tree record for one bare test
function name. It authorizes ONE transition — from any of the recorded
`through_baseline` labels to the next baseline. While the comparison's base
label is one of those recorded baselines, the named test is removed from the
shared-set budget and its own MEDIAN of paired-round ratios is checked against
the positive `max_ratio`. The permission is relative (runner-proof, like the
paired budget) while active, and inert (fail-closed) everywhere else; it never
allows another test to regress or a later baseline to hide a new regression.
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .zeroed_suites import zeroed_on_both_sides
else:
    from zeroed_suites import zeroed_on_both_sides


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


def side_rounds(directories):
    """One dict of durations per round, in the order the rounds are given."""
    return [round_durations(directory) for directory in directories]


def shared_tests(rounds):
    """Tests present in EVERY round given, on either side.

    The set is fixed once, across all rounds of both sides, because a set
    recomputed per round would let a test that dropped out of one round change
    what that round's total even covers — and the point of a paired ratio is
    that the two totals it divides cover the same work.
    """
    everywhere = None
    for durations in rounds:
        names = set(durations)
        everywhere = names if everywhere is None else everywhere & names
    return sorted(everywhere or ())


def compare(base_rounds, head_rounds, excluded=()):
    """Paired totals and movements, optionally excluding named tests."""
    excluded = set(excluded)
    shared = [name for name in shared_tests([*base_rounds, *head_rounds])
              if name not in excluded]
    pairs = []
    for base, head in zip(base_rounds, head_rounds):
        base_total = sum(base[name] for name in shared)
        head_total = sum(head[name] for name in shared)
        pairs.append((base_total, head_total,
                      head_total / base_total if base_total else 1.0))
    movements = []
    for name in shared:
        was = statistics.median([durations[name] for durations in base_rounds])
        now = statistics.median([durations[name] for durations in head_rounds])
        movements.append((name, was, now, now - was))
    # Ordering by |delta| lets the limit's cut tail hide a large mover of
    # the minority sign -- a table of speedups can drop the one regression.
    movements.sort(key=lambda row: abs(row[3]), reverse=True)
    return shared, pairs, movements


def _unique_object(pairs):
    """Build a JSON object while rejecting ambiguous duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate key in JSON object: {key!r}')
        result[key] = value
    return result


def _load_acceptances(path):
    """Read and validate an optional accepted-speed manifest."""
    if path is None:
        return []
    manifest = Path(path)
    if not manifest.exists():
        return []
    try:
        document = manifest.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise ValueError(f'{manifest}: unreadable ({exc})') from exc
    try:
        data = json.loads(document, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError, UnicodeError,
            RecursionError) as exc:
        raise ValueError(f'{manifest}: invalid JSON ({exc})') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{manifest}: root must be an object')
    unknown = set(data) - {'acceptances'}
    if unknown:
        raise ValueError(
            f'{manifest}: unknown key(s): {", ".join(sorted(unknown))}')
    acceptances = data.get('acceptances')
    if not isinstance(acceptances, list):
        raise ValueError(f'{manifest}: acceptances must be a list')

    parsed = []
    seen = set()
    for index, acceptance in enumerate(acceptances, start=1):
        where = f'{manifest}: acceptance {index}'
        if not isinstance(acceptance, dict):
            raise ValueError(f'{where} must be an object')
        unknown = (set(acceptance)
                   - {'test', 'max_ratio', 'reason', 'through_baseline'})
        if unknown:
            raise ValueError(
                f'{where}: unknown key(s): {", ".join(sorted(unknown))}')
        missing = ({'test', 'max_ratio', 'reason', 'through_baseline'}
                   - set(acceptance))
        if missing:
            raise ValueError(
                f'{where}: missing key(s): {", ".join(sorted(missing))}')
        name = acceptance['test']
        if (not isinstance(name, str) or not name.strip()
                or name != name.strip()):
            raise ValueError(
                f'{where}: test name must be a bare non-empty string')
        if name in seen:
            raise ValueError(f'{where}: duplicate test name {name!r}')
        seen.add(name)
        bound = acceptance['max_ratio']
        numeric = (isinstance(bound, (int, float))
                   and not isinstance(bound, bool))
        if isinstance(bound, float) and not math.isfinite(bound):
            numeric = False
        if not numeric or bound <= 0:
            raise ValueError(
                f'{where}: max_ratio must be a positive number')
        reason = acceptance['reason']
        if not isinstance(reason, str):
            raise ValueError(f'{where}: reason must be a string')
        through_baseline = acceptance['through_baseline']
        if (not isinstance(through_baseline, list)
                or not through_baseline):
            raise ValueError(
                f'{where}: through_baseline must be a non-empty list')
        if any(not isinstance(label, str) or not label.strip()
               for label in through_baseline):
            raise ValueError(
                f'{where}: through_baseline entries must be non-empty '
                'strings')
        if len(through_baseline) != len(set(through_baseline)):
            raise ValueError(
                f'{where}: through_baseline entries must be unique')
        parsed.append({'test': name, 'max_ratio': bound,
                       'reason': reason,
                       'through_baseline': through_baseline})
    return parsed


def _acceptance_checks(acceptances, shared, base_rounds, head_rounds,
                       base_label):
    """Return acceptance rows and whether active bounds are respected."""
    rows = []
    all_ok = True
    for acceptance in acceptances:
        name = acceptance['test']
        bound = acceptance['max_ratio']
        active = base_label in acceptance['through_baseline']
        expired = f'expired at baseline {base_label}'
        if name not in shared:
            status = ('not measured this run' if active else
                      f'{expired}; not measured this run')
            rows.append((name, None, None, None, bound,
                         status))
            continue
        was = statistics.median([round_[name] for round_ in base_rounds])
        now = statistics.median([round_[name] for round_ in head_rounds])
        paired_ratios = []
        for base, head in zip(base_rounds, head_rounds):
            if base[name] == 0.0:
                pair_ratio = 1.0 if head[name] == 0.0 else math.inf
            else:
                pair_ratio = head[name] / base[name]
            paired_ratios.append(pair_ratio)
        ratio = statistics.median(paired_ratios)
        if not active:
            rows.append((name, was, now, ratio, bound, expired))
            continue
        status = 'PASS' if ratio <= bound else 'FAIL'
        all_ok = all_ok and status == 'PASS'
        rows.append((name, was, now, ratio, bound, status))
    return rows, all_ok


def _unmeasured_acceptance_rows(acceptances, base_label):
    """Rows for a comparison that produced no shared measurements."""
    rows = []
    for item in acceptances:
        active = base_label in item['through_baseline']
        status = ('not measured this run' if active else
                  f'expired at baseline {base_label}; not measured this run')
        rows.append((item['test'], None, None, None, item['max_ratio'],
                     status))
    return rows


def _render_acceptances(lines, rows):
    """Append the accepted-speed table, including stale entries."""
    if not rows:
        return
    lines.append('')
    lines.append('### Accepted speed changes')
    lines.append('')
    lines.append('| test | base median | head median | ratio | '
                 'bound | status |')
    lines.append('|---|---:|---:|---:|---:|---|')
    for name, was, now, ratio, bound, status in rows:
        if was is None:
            lines.append(f'| `{name}` | — | — | — | {bound:.3f} | '
                         f'{status} |')
            continue
        shown_ratio = 'inf' if math.isinf(ratio) else f'{ratio:.3f}'
        lines.append(f'| `{name}` | {was:.2f}s | {now:.2f}s | '
                     f'{shown_ratio} | {bound:.3f} | {status} |')


def render(lines, base_label, shared, pairs, movements, limit=10):
    """The step summary: the verdict first, then what moved most."""
    lines.append('### Test speed')
    lines.append('')
    lines.append(f'Baseline `{base_label}`, over {len(shared)} tests present '
                 'and passing in every round on both sides.')
    lines.append('')
    if not shared:
        lines.append('No non-accepted shared tests; only acceptance bounds '
                     'apply.')
        return None
    ratio = statistics.median([pair[2] for pair in pairs])
    lines.append('Each row is one interleaved pair of rounds, so every total '
                 'below is a run that happened:')
    lines.append('')
    lines.append('| round | baseline | this commit | ratio |')
    lines.append('|---|---:|---:|---:|')
    for index, (base_total, head_total, pair_ratio) in enumerate(pairs, 1):
        lines.append(f'| {index} | {base_total:.2f}s | {head_total:.2f}s '
                     f'| {pair_ratio:.3f} |')
    lines.append('')
    lines.append(f'- median paired ratio: {ratio:.3f}')
    if len(pairs) > 1:
        spread = [pair[2] for pair in pairs]
        lines.append(f'- spread across pairs: {min(spread):.3f} to '
                     f'{max(spread):.3f}')
    lines.append('')
    # Individual numbers are not trustworthy enough to gate on -- a single
    # test can move by multiples between two runs of identical code -- but
    # they are what tells a reader WHERE the total went, so they are shown
    # and never asserted on.
    lines.append('Largest individual movements '
                 '(median across rounds, indicative only):')
    lines.append('')
    lines.append('| test | baseline | head | delta |')
    lines.append('|---|---:|---:|---:|')
    for name, was, now, delta in movements[:limit]:
        lines.append(f'| `{name}` | {was:.2f}s | {now:.2f}s | {delta:+.2f}s |')
    head_total = sum(now for _name, _was, now, _delta in movements)
    lines.append('')
    lines.append('Longest-running tests (head median, covered-set share):')
    lines.append('')
    lines.append('Shares sum head medians over the same covered set used by '
                 'the paired ratio.')
    lines.append('')
    lines.append('| test | head median | share of covered set |')
    lines.append('|---|---:|---:|')
    for name, _was, now, _delta in sorted(
            movements, key=lambda row: row[2], reverse=True)[:limit]:
        share = now / head_total if head_total else 0.0
        lines.append(f'| `{name}` | {now:.2f}s | {share:.3f} |')
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
    parser.add_argument('--accept',
                        help='optional accepted speed-change manifest')
    parser.add_argument(
        '--require-measurements', action='store_true',
        help='treat an unmeasurable comparison as a failure rather than a '
             'skip; CI passes this, because there the absence of data means '
             'a step broke rather than that there is nothing to compare')
    args = parser.parse_args(argv)

    try:
        acceptances = _load_acceptances(args.accept)
    except ValueError as exc:
        print(f'Invalid accepted speed changes file: {exc}', file=sys.stderr)
        return 1

    base_rounds = side_rounds(args.base)
    head_rounds = side_rounds(args.head)
    lines = []
    # Checked before any verdict can be rendered, because this is the one
    # failure the intersection cannot surface on its own: the lost suites are
    # gone from the covered set before the first total is summed.
    zeroed = zeroed_on_both_sides(args.base, args.head)
    if zeroed:
        names = ', '.join(f'`{name}.py`' for name in zeroed)
        lines.append('### Test speed')
        lines.append('')
        lines.append(f'Failed: {names} timed zero tests on both sides, so '
                     'the covered set is smaller than the suites the cell '
                     'selected. A missing dependency in the timed '
                     'virtualenvs fails every suite of a coverage tool the '
                     'same way.')
        _emit(lines, args.summary_file)
        return 1
    # A baseline with no durations is a measurement that did not happen, not
    # a fast baseline. The timing instrument belongs to the head checkout and
    # asks nothing of the tree it measures, so an empty side means the timing
    # step failed rather than that the release was too old -- say so instead of
    # dividing by it.
    if not any(base_rounds):
        lines.append('### Test speed')
        lines.append('')
        lines.append(f'Skipped: the baseline `{args.base_label}` produced no '
                     'per-test durations, so nothing was measured to compare '
                     'against.')
        _render_acceptances(
            lines, _unmeasured_acceptance_rows(acceptances, args.base_label))
        _emit(lines, args.summary_file)
        return 1 if args.require_measurements else 0

    # Rounds are compared in pairs, so an unequal count means one side lost a
    # round — there is no honest way to pair what is left, and pairing the
    # first N would silently drop the rest.
    if len(base_rounds) != len(head_rounds):
        lines.append('### Test speed')
        lines.append('')
        lines.append(f'Skipped: the baseline ran {len(base_rounds)} rounds and '
                     f'this commit ran {len(head_rounds)}, so the rounds '
                     'cannot be paired.')
        _render_acceptances(
            lines, _unmeasured_acceptance_rows(acceptances, args.base_label))
        _emit(lines, args.summary_file)
        return 1 if args.require_measurements else 0

    all_shared = shared_tests([*base_rounds, *head_rounds])
    if not all_shared:
        lines.append('### Test speed')
        lines.append('')
        lines.append('Skipped: no test passed in every round on both sides, '
                     'so the comparison has no shared set to sum.')
        _render_acceptances(
            lines, _unmeasured_acceptance_rows(acceptances, args.base_label))
        _emit(lines, args.summary_file)
        return 1 if args.require_measurements else 0

    accepted_names = [item['test'] for item in acceptances
                      if args.base_label in item['through_baseline']]
    shared, pairs, movements = compare(
        base_rounds, head_rounds, excluded=accepted_names)
    acceptance_rows, acceptances_ok = _acceptance_checks(
        acceptances, all_shared, base_rounds, head_rounds, args.base_label)
    ratio = render(lines, args.base_label, shared, pairs, movements)
    _render_acceptances(lines, acceptance_rows)
    budget = 1.0 + args.max_regression
    budget_ok = ratio is None or ratio <= budget
    if acceptances:
        lines.append('')
        budget_text = ('no non-accepted shared tests; only acceptance bounds '
                       'apply'
                       if ratio is None else
                       f'covered-set median paired ratio {ratio:.3f} is '
                       f'within the {budget:.2f} budget'
                       if budget_ok else
                       f'covered-set median paired ratio {ratio:.3f} '
                       f'exceeds the {budget:.2f} budget')
        active_count = len(accepted_names)
        acceptance_text = ('every acceptance bound holds'
                           if acceptances_ok and active_count else
                           'no active acceptance bounds apply'
                           if acceptances_ok else
                           'one or more acceptance bounds were breached')
        verdict = 'OK' if budget_ok and acceptances_ok else 'FAIL'
        lines.append(f'**{verdict}**: {budget_text}; {acceptance_text}.')
        _emit(lines, args.summary_file)
        return 0 if verdict == 'OK' else 1
    if not budget_ok:
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
            # The summary is the step's rendered note, not its verdict: the
            # exit status is what gates, and it has already been decided. A
            # run that cannot write the note still gates correctly.
            pass


if __name__ == '__main__':
    sys.exit(main())
