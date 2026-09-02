#!/usr/bin/env python3
"""The speed summary the workflow attaches to a run."""
import math
import statistics

NOISE_FLOOR = 0.10


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


def render(lines, base_label, shared, pairs, movements, total, limit=10):
    """The step summary: the verdict first, then what moved most."""
    lines.append('### Test speed')
    lines.append('')
    lines.append(f'Baseline `{base_label}`, over {len(shared)} of {total} '
                 'tests present and passing in every round on both sides.')
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
    movers = [row for row in movements if abs(row[3]) >= NOISE_FLOOR]
    movers.sort(key=lambda row: (row[2] / row[1]) if row[1] else math.inf,
                reverse=True)
    lines.append('')
    if movers:
        lines.append('Largest relative changes '
                     '(median across rounds, indicative only):')
        lines.append('')
        lines.append('Rows that moved less than the 0.10s noise floor are '
                     'omitted, so a shown ratio is a shown movement.')
        lines.append('')
        lines.append('| test | baseline | head | delta | ratio |')
        lines.append('|---|---:|---:|---:|---:|')
        for name, was, now, delta in movers[:limit]:
            test_ratio = now / was if was else math.inf
            shown = 'inf' if math.isinf(test_ratio) else f'{test_ratio:.3f}'
            lines.append(f'| `{name}` | {was:.2f}s | {now:.2f}s | '
                         f'{delta:+.2f}s | {shown} |')
    else:
        lines.append('No test moved beyond the 0.10s noise floor.')
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
