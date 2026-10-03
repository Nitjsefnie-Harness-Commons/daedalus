"""Every line of markdown the journey harness writes to a step summary.

The third thing neither module that counts nor the module that decides owns:
what a run SAYS about either. Each function here takes data and returns
lines; writing them is the callers' business.

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


# The prose each gate speaks when it refuses. These live here, beside the
# module that renders a run's own words, and `journey_budget.py` binds them
# back by name: the gates that print them and the renderer that shows them
# are the same responsibility split the rest of this module already follows.
# The command, not a pointer at where it is printed: these remedies are read
# on stderr, where a `check` run without `--summary` has printed nothing at
# all. Named here in full, so the sentence is true wherever it is read.
REBASELINE_COMMAND = (
    '`python3 scripts/ci/journey_budget.py rebaseline --measurements '
    '<counts.json>` writes the whole artefact from one measurement: the '
    'counts, their shas, the toolchain, the threads excluded and the '
    'signature table that classified them all from that same run, with the '
    'recorded tolerance left where you put it.')
OVER_REMEDY = (
    'A journey over its budget is a regression in what a user waits for: '
    'the recorded count is never raised by hand, and no entry is ever added '
    'by hand. Find what the journey now does that it did not, and make it '
    'not do it; if the journey genuinely costs more now, the measurement '
    'this run took is uploaded as the `journey-counts` artifact and '
    + REBASELINE_COMMAND + ' The commit it leaves is yours to review.')
SHAPE_REMEDY = (
    'Rounds of one measurement disagreed about what the journey looks like, '
    'so their counts are not comparable and none of them is a baseline. The '
    'rendering is in the journeys module; a field that legitimately varies '
    'between runs belongs in its exclusion list, and anything else is a '
    'shape change.')
UNMEASURED_REMEDY = (
    'The budget names a counter this runner produced no count in, so no '
    'journey was compared and a green here would be a run that measured '
    'nothing. Each unmeasured row carries the sentence the counter refused '
    'with, and that sentence is what says which step to look at. If it '
    'names `DAEDALUS_CALLGRIND_BOUNDARY`, the `Build the counted-boundary '
    'helper` step is the one that failed: a counted child refuses rather '
    'than record its own interpreter startup and the compile of its import '
    'closure, and it does so when that variable names no compiled helper. '
    'Otherwise the `Probe the counters` step says what this runner allows: '
    '`instructions:u` needs less kernel access than an unqualified event, '
    'and callgrind is the fallback when perf is refused.')
TOOLCHAIN_REMEDY = (
    'A recorded count is only comparable against a measurement taken on the '
    'toolchain it was recorded on. Re-baseline from a measured run: '
    + REBASELINE_COMMAND + ' It is a reviewed commit, and so is every '
    'other change to the artefact.')
SHA_REMEDY = (
    'A recorded count describes the journey that rendered when it was '
    'recorded, so a journey that renders differently cannot be compared '
    'against it. Re-baseline from a measured run: '
    + REBASELINE_COMMAND + ' It is a reviewed commit, and so is every '
    'other change to the artefact.')
SIGNATURES_REMEDY = (
    'The function names that put a thread in an excluded role are the table '
    'this run classified by, so a run whose signatures differ from the '
    'recorded ones is measuring a different quantity whatever it reads. '
    'Re-baseline from a measured run: '
    + REBASELINE_COMMAND + ' It is a reviewed commit, and so is every '
    'other change to the artefact.')
THREADS_REMEDY = (
    'A recorded count is only comparable against a measurement that '
    'excluded the same threads. Re-baseline from a measured run: '
    + REBASELINE_COMMAND + ' It is a reviewed commit, and so is every '
    'other change to the artefact.')
UNRESOLVED_REMEDY = (
    'A journey the run could not resolve is a journey the budget holds a '
    'count for and this run could not produce one, which is never a pass. '
    'The residual was negative: the journey\'s own work is smaller than the '
    'fixed background every child shares, so the two cannot be told apart '
    'in this measurement. If the journey is representatively sized and still '
    'inseparable, `python3 scripts/ci/journey_budget.py rebaseline '
    '--measurements <counts.json> --drop <journey>` records no count for it, '
    'and the decision is then visible in the artefact rather than in a red '
    'nobody can act from.')
REMEDY_FOR = {'over': OVER_REMEDY, 'unmeasured': UNMEASURED_REMEDY,
              'unresolved': UNRESOLVED_REMEDY}


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


def _reason(why):
    """A counter's refusal as one table cell's worth of prose.

    A counter reports its reason in whichever of two shapes it has: a
    sentence of its own when the refusal is one, and a returncode with the
    tool's own stderr when the tool is what would not run. Neither is
    rendered with `str()` — a cell holding `{...}` is a cell naming nothing.
    """
    if not why:
        return ''
    if isinstance(why, str):
        return why.strip()
    return (f'returncode {why.get("returncode")}: '
            f'{(why.get("stderr") or "").strip()}').strip()


def verdict_lines(document, counts, found, why=None):
    """One row per recorded journey, on a pass and on a fail alike.

    The same table either way is the point: a reader deciding whether a red
    is a regression needs the recorded number and this run's number on one
    line, and a reader deciding whether a green can be trusted needs the
    same line — so a run whose verdict changed does not also change what
    there is to read. The budget is the gate's own arithmetic, not a
    second copy of it.

    `why` is the counter's own refusal, and it is a parameter rather than
    something read out of `report` here because this module renders data it
    is handed: the sentence a counter refused with is the only thing that
    tells a reader WHICH step to go and look at, and it used to survive only
    inside the uploaded measurement.
    """
    over = found['over']
    unmeasured = found['unmeasured']
    unresolved = found.get('unresolved') or {}
    reason = _reason(why)
    lines = ['### Journey budget', '',
             '| journey | count | budget | delta | verdict |',
             '|---|---|---|---|---|']
    for name in sorted(document['journeys']):
        # `None` here is a journey the artefact NAMES but has no count for
        # yet: `_validated` admits a null recorded count rather than
        # refusing it, and `violations()` and `tightened()` both skip that
        # shape, so the rest of the check supports it and this row has to as
        # well. Iterating this same mapping rules out an absent NAME, not a
        # null VALUE.
        limit = journey_artifact.budget_of(document, name)
        budget = f'{limit:.0f}' if limit is not None else '—'
        measured = counts.get(name)
        if limit is None:
            verdict = 'not recorded yet'
            measured = None
        elif name in unresolved:
            verdict = 'this run could not resolve it'
            measured = None
        elif name in unmeasured:
            verdict = f'no count for `{unmeasured[name]}`'
            if reason:
                verdict = f'{verdict}: {reason}'
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


def tighten_skipped_lines(subject):
    """The one line a run that compared nothing leaves for a reader.

    Not a pass: no count was measured against a recorded one, so nothing
    was tightened and nothing was committed. A green journey-budget with no
    commit is this case, and without it a reader has to infer it from the
    absence of a diff.
    """
    return [f'**The {subject} moved, so no count was compared and nothing '
            f'was tightened.**', '',
            'The recorded baseline was measured against something this run '
            'was not, so no journey was compared and no commit was made. '
            'The `rebaseline` command above re-records the budget from a '
            'measured run; until one is run, every run reports this and '
            'the budget stays as it is.', '']


def rebaseline_lines(run_id=None):
    """The one line a rise is answered with, runnable as printed.

    The run id is the workflow's own substitution, so the line a reader
    copies is the line that works: `gh run download` fetches the
    measurement this job measured, and the command writes the artefact from
    it — this run's counts, shas, toolchain, exclusions and signature
    table together, with the recorded tolerance left where a person put
    it.
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
