"""Every line of markdown the journey harness writes to a step summary.

Split out of the two modules that decide and measure, because both had
grown past the production size ceiling and this is the responsibility
neither of them owns: `journey_counters.py` counts and
`journey_budget.py` decides, and what a run SAYS about either is a third
thing. Each function here takes data and returns lines; writing them is
the callers' business, and the gate over them is none — none of these
numbers is asserted anywhere.

The re-baseline block is machine-readable on purpose. A re-baseline is a
reviewed commit, so what lands in it has to be this run's own numbers
rather than a reader's transcription of a table.
"""
import json
import sys
from pathlib import Path

# The counter module sits beside this one and is imported by its own name.
# The path insert is this module's own, so it resolves whichever of the three
# was imported first rather than only when `journey_budget` went before it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_counters  # noqa: E402  pylint: disable=wrong-import-position


def probe_lines(found):
    """The probe as a step summary, mirroring the JSON it printed.

    The booleans are lowercased so a value a reader copies out of the
    summary is the value the JSON carries, not Python's spelling of it.
    """
    stat = found.get('perf_stat')
    lines = ['### Journey counter probe', '',
             'What this runner actually allows, measured rather than assumed.',
             '',
             f"- python: `{found['python'].splitlines()[0]}`",
             f"- perf_event_paranoid: `{found['perf_event_paranoid']}`",
             f"- perf on PATH: `{found['perf_path']}`"]
    if stat is None:
        lines.append('- `perf stat -e instructions:u -- true`: not run, perf '
                     'is not on PATH')
    else:
        lines.append(f"- `perf stat -e {stat['event']} -- true`: returncode "
                     f"`{stat['returncode']}`, counts: "
                     f"`{str(stat['counts']).lower()}`")
        if stat['stderr']:
            lines += ['', '```', stat['stderr'], '```']
    lines += [f"- valgrind on PATH: `{found['valgrind_path']}`",
              f"- valgrind --version: `{found['valgrind_version']}`",
              f"- callgrind_control on PATH: "
              f"`{found['callgrind_control_path']}`",
              f"- callgrind_control --version: "
              f"`{found['callgrind_control_version']}`",
              f"- callgrind_control usable: "
              f"`{str(found['callgrind_control_usable']).lower()}`",
              f"- strace on PATH: `{found['strace_path']}`",
              f"- counter selected here: `{found['selected']}`", '']
    return lines


def summary_lines(report):
    """The measurement table, as markdown for a step summary."""
    lines = ['### Journey counts', '',
             f"Counter selected here: `{report.get('selected_counter')}`.",
             '',
             '| counter | gated | journey | min | median | max | spread '
             '| sha |', '|---|---|---|---|---|---|---|---|']
    shas = report.get('shas') or {}
    for counter in journey_counters.COUNTERS:
        entry = report['counters'].get(counter) or {}
        if not entry.get('available'):
            lines.append(f"| {counter} | — | — | — | — | — | — | not "
                         f"usable here: {entry.get('why')} |")
            continue
        gated = 'yes' if entry.get('gated') else 'no'
        for name in journey_counters.journey_names():
            row = (entry.get('journeys') or {}).get(name)
            if row is None:
                continue
            seen = sorted(set(shas.get(name) or ()))
            sha = seen[0][:12] if len(seen) == 1 else 'MISMATCH'
            lines.append(
                f"| {counter} | {gated} | {name} | {row['min']} | "
                f"{row['median']} | {row['max']} | {row['spread']} | "
                f"{sha} |")
    return lines


def rebaseline_lines(report, document=None):
    """The block a re-baseline is pasted from, as this run measured it.

    Machine-readable and unambiguous on purpose: the re-baseline is a
    reviewed commit, so what lands in it has to be the numbers this run
    measured rather than a reader's transcription of a table above.
    Nothing in CI writes the artefact — that is what makes a re-baseline
    a decision rather than a side effect.
    """
    selected = report.get('selected_counter')
    entry = (report.get('counters') or {}).get(selected) or {}
    journeys = entry.get('journeys') or {}
    block = {'counter': selected,
             'toolchain': report.get('toolchain') or {},
             'journeys': {}, 'spread': {}}
    for name in journey_counters.journey_names():
        row = journeys.get(name) or {}
        block['journeys'][name] = row.get('median')
        block['spread'][name] = row.get('spread')
    if document is not None:
        block['recorded'] = {
            'counter': document.get('counter'),
            'tolerance_pct': document.get('tolerance_pct'),
            'toolchain': document.get('toolchain') or {},
            'journeys': document.get('journeys') or {}}
    return ['### Re-baseline block', '',
            'What a re-baseline is pasted from, as this run measured it. '
            'Nothing in CI writes `.github/journey-budget.json`; the '
            're-baseline is a reviewed commit carrying these numbers, '
            'their spread, and the toolchain they were taken on.', '',
            '```json',
            json.dumps(block, indent=2, sort_keys=True),
            '```', '']


def _seconds(value):
    return 'not recorded' if value is None else f'{value:.1f}s'


def accounting_lines(report, cost=None):
    """What this job cost on this runner, measured rather than estimated.

    Accounting only. Nothing here is bounded or asserted, because a
    wall-clock margin is a quantity this repository refuses to assert and
    a number measured off any other machine is not this job's.
    """
    cost = cost or {}
    lines = ['### Journey budget cost', '',
             'What the measurement cost on the runner that produced it. '
             'Accounting, not a gate: none of these numbers is bounded or '
             'asserted anywhere.', '',
             f"- valgrind install step: {_seconds(cost.get('install'))}",
             f"- valgrind installed files: "
             f"{cost.get('installed_kib') or 'not recorded'} KiB",
             f"- measure step: {_seconds(cost.get('measure'))}", '',
             '| journey | counter | seconds |', '|---|---|---|']
    for counter in journey_counters.COUNTERS:
        entry = report.get('counters', {}).get(counter) or {}
        for name, row in sorted((entry.get('journeys') or {}).items()):
            lines.append(f"| {name} | {counter} | "
                         f"{row.get('seconds')} |")
    lines.append('')
    return lines


def toolchain_lines(document, report, changed, remedy):
    """The toolchain outcome as a step summary, in those words.

    Not a regression and not a silent pass: the reader is told in the
    summary that no count was compared and why, and the exit status is
    success only because the tree did not regress.
    """
    counter = document.get('counter')
    counts = (journey_counters.counts_of(report, counter) if counter else {})
    if changed:
        lines = ['### Journey budget', '',
                 '**toolchain changed, re-baseline.**', '',
                 'No count was compared. The recorded counts were taken on '
                 'a different toolchain, so comparing them against this '
                 "run's numbers would measure the toolchain rather than "
                 'the code. The step succeeds because the tree did not '
                 'regress, not because anything was within budget.', '',
                 '| field | recorded | measured |', '|---|---|---|']
        for field, (was, now) in sorted(changed.items()):
            lines.append(f'| {field} | `{was}` | `{now}` |')
    else:
        lines = ['### Journey budget', '',
                 '**the journey budget records no toolchain yet.**', '',
                 'No count was compared. There is no recorded identity to '
                 'say this run still matches, so the measurement is '
                 'reported and nothing is compared until a re-baseline '
                 'records one.']
    lines += ['', 'What would have been compared:', '',
              '| journey | recorded | measured |', '|---|---|---|']
    for name in journey_counters.journey_names():
        lines.append(f"| {name} | {document['journeys'].get(name)} | "
                     f"{counts.get(name)} |")
    lines += ['', remedy, '']
    return lines
