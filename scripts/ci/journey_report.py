"""Every line of markdown the journey harness writes to a step summary.

Split out of the two modules that decide and measure, because both had
grown past the production size ceiling and this is the responsibility
neither of them owns: `journey_counters.py` counts and
`journey_budget.py` decides, and what a run SAYS about either is a third
thing. Each function here takes data and returns lines; writing them is
the callers' business.

Nothing here writes the artefact, and nothing here prints it either. A
re-baseline used to be pasted out of a block of JSON, which made the
numbers a person reviewed a transcription of a table rather than the run's
own measurement. It is now one command, over the measurement the job
uploaded, so the reading survives and the copying does not.
"""
import os
import sys
from pathlib import Path

# The counter module sits beside this one and is imported by its own name.
# The path insert is this module's own, so it resolves whichever of the three
# was imported first rather than only when `journey_budget` went before it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_artifact  # noqa: E402  pylint: disable=wrong-import-position
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
              f"- strace on PATH: `{found['strace_path']}`",
              f"- counter selected here: `{found['selected']}`", '']
    return lines


def verdict_lines(document, counts, found):
    """One row per recorded journey, on a pass and on a fail alike.

    The same table either way is the point: a reader deciding whether a red
    is a regression needs the recorded number and this run's number on one
    line, and a reader deciding whether a green can be trusted needs the
    same line — so a run whose verdict changed does not also change what
    there is to read. The budget is the gate's own arithmetic, not a
    second copy of it.
    """
    over = found['over']
    unmeasured = found['unmeasured']
    lines = ['### Journey budget', '',
             '| journey | count | budget | delta | verdict |',
             '|---|---|---|---|---|']
    for name in sorted(document['journeys']):
        limit = journey_artifact.budget_of(document, name)
        budget = f'{limit:.0f}' if limit is not None else '—'
        measured = counts.get(name)
        if limit is None:
            verdict = 'not recorded yet'
            measured = None
        elif name in unmeasured:
            verdict = f'no count for `{unmeasured[name]}`'
            measured = None
        elif name in over:
            verdict = 'OVER BUDGET'
        else:
            verdict = 'within budget'
        if measured is None:
            lines.append(f'| {name} | not measured | {budget} | — | '
                         f'{verdict} |')
            continue
        delta = measured - limit
        sign = '+' if delta > 0 else ''
        lines.append(f'| {name} | {measured} | {budget} | {sign}{delta:.0f} '
                     f'| {verdict} |')
    return lines


def rebaseline_lines(run_id=None):
    """The one line a rise is answered with, runnable as printed.

    The run id is the workflow's own substitution, so the line a reader
    copies is the line that works: `gh run download` fetches the
    measurement this job measured, and the command writes the artefact from
    it — this run's counts, shas, toolchain, exclusions and bands
    together, with the recorded tolerance left where a person put it.
    """
    run = run_id or os.environ.get('GITHUB_RUN_ID') or '<run-id>'
    return ['A journey is over its budget, so raising the recorded counts is '
            'a decision this repository does not make for you. This run\'s '
            'own measurement is uploaded as `journey-counts`; run this in a '
            'checkout and review the diff it leaves.', '',
            '```bash',
            f'gh run download {run} -n journey-counts -D journey-rebaseline '
            '&& python3 scripts/ci/journey_budget.py rebaseline '
            '--measurements journey-rebaseline/journey-counts.json',
            '```', '']


def toolchain_lines(document, report, changed, remedy,
                    subject='toolchain'):
    """The toolchain outcome as a step summary, in those words.

    Not a regression and not a silent pass: the reader is told in the
    summary that no count was compared and why, and the exit status is
    success only because the tree did not regress.
    """
    counter = document.get('counter')
    counts = (journey_counters.counts_of(report, counter) if counter else {})
    if changed:
        # The same block serves a toolchain that moved and a set of excluded
        # threads that changed: both say the recorded number and this run's
        # are different quantities, and the table reads the same either way.
        lines = ['### Journey budget', '',
                 f'**{subject} changed, re-baseline.**', '',
                 'No count was compared. The recorded counts were taken on '
                 'a different '
                 f'{subject}, so comparing them against this run\'s numbers '
                 f'would measure the {subject} rather than the code. The '
                 'step succeeds because the tree did not regress, not '
                 'because anything was within budget.', '',
                 '| field | recorded | measured |', '|---|---|---|']
        for field, (was, now) in sorted(changed.items()):
            lines.append(f'| {field} | `{was}` | `{now}` |')
    else:
        lines = ['### Journey budget', '',
                 f'**the journey budget records no {subject} yet.**', '',
                 f'No count was compared. There is no recorded {subject} to '
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
