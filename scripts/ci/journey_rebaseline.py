"""Write the journey budget from the measurements given, whole.

The document this writes is a document, not a verdict: `journey_budget.py`
decides whether a count may be compared and `journey_artifact.py` owns the
shape one is recorded in, while the mapping from a measurement to that
shape is neither of those jobs.

A re-baseline stays a reviewed commit — the maintainer's ruling is
"autocommit tighten only, where a PR can increase its own cap if
necessary" — so what this removes is the transcription, not the decision.
One command reads the `measure --out` files it is given and writes the
artefact from them, so what lands is the runs' own measurements rather
than a reader's copy of a table. Several files are one recording: the
recorded count is the median of the files' own medians, every file must
agree on what was rendered and on what it ran on, and a `--draws` pool
re-binds a journey to the span of its own draws.
"""
import json
import sys
from pathlib import Path

# The counter, artefact and thread modules sit beside this one and are
# imported by their own names, which is what a run from the repository
# root, a run from anywhere else and `python3 -m scripts.ci.journey_budget`
# all do. A relative import would only work for the last of the three.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_artifact  # noqa: E402  pylint: disable=wrong-import-position
import journey_counters  # noqa: E402  pylint: disable=wrong-import-position
import journey_recording  # noqa: E402  pylint: disable=wrong-import-position
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position


def document_from(reports, recorded, restore=(), drop=(), draws=()):
    """The artefact these measurements justify, as a validated document.

    Counts, shas, toolchain and excluded threads all come from `reports`
    and none of them from `recorded`: they describe the runs being
    recorded, and taking any of them from the document being replaced
    describes two — and a check compares all of them or none, so such a
    document can never match anything and every later run refuses it.

    The tolerance is the one field that stays. It is the bound a person
    set, and a re-baseline moves the counts under it rather than moving the
    bound with them. A journey's OWN tolerance is the same bound for one
    journey, and stays for the same reason — narrowed to the journeys this
    document carries, because a tolerance for a journey the set no longer
    has is a rule nothing enforces, which is the same fate a stale count
    has. A `--draws` pool moves it instead of carrying it: each file
    contributes every round's count, and a journey the pool names is
    re-bound to the span of its own draws.

    A journey recorded at `null` is a journey the budget deliberately does
    not hold, and this measurement SEPARATES it. That is a change of fact,
    so the command refuses and names the journey and the residual that makes
    it a question: a `null` nothing can undo is a policy hole with a green
    face — the journey reports `unrecorded`, passes, and stays that way
    forever. Restoring it is a decision, so it takes `--restore` naming the
    journey; a routine re-baseline never makes that decision on its own, and
    a name the artefact does not hold at `null` is refused rather than
    ignored, because an ignored flag and a misspelled one look identical
    from the command line.
    """
    reports = list(reports)
    counter = journey_recording.agreed_counter(reports)
    counts = [journey_counters.counts_of(report, counter)
              for report in reports]
    names = journey_counters.journey_names()
    journeys = {}
    for name in names:
        measured = [seen.get(name) for seen in counts]
        if any(value is None for value in measured):
            # A journey `--drop` names is exempt: its own absence of a count
            # is what it is being dropped for, and refusing here would make
            # the ruling's second clause unreachable — a run that cannot
            # separate a journey produces no measurement to drop it out of.
            if name not in set(drop):
                raise ValueError(
                    f'the measurement carries no count for {name}, so there '
                    'is nothing to record and a budget without it compares '
                    'nothing')
            journeys[name] = None
            continue
        journeys[name] = journey_recording.recorded_median(measured)
    # Less whatever `--drop` names: that journey's recorded null is the
    # one decision this command has been asked to make again, and the
    # refusal below would otherwise answer it with a measurement that
    # cannot separate it — naming a residual of `None` and offering a
    # `--restore` that cannot succeed, on the one journey the flag
    # exists for.
    held = _dropped(recorded, names)
    _restored([name for name in held if name not in set(drop)],
              held, restore, reports, counter)
    _dropped_now(drop, reports, counter)
    shas = {name: journey_recording.agreed_sha(reports, name)
            for name in names}
    toolchain = journey_recording.agreed_toolchain(reports)
    if not journey_artifact.recorded_toolchain({'toolchain': toolchain}):
        raise ValueError(
            'the measurement carries no toolchain identity, and a recorded '
            'count without one compares against nothing')
    exclusions = journey_recording.agreed_exclusions(reports)
    unaccounted = [name for name in names if not exclusions.get(name)]
    if unaccounted:
        raise ValueError(
            'the measurement says which threads it excluded for every '
            f'journey but these: {unaccounted}')
    measured = {'schema_version': journey_artifact.SCHEMA_VERSION,
                'counter': counter,
                'tolerance_pct': recorded.get('tolerance_pct'),
                'tolerances': journey_recording.carried_tolerances(
                    recorded,
                    {name for name, count in journeys.items()
                     if count is not None},
                    draws=draws, counter=counter,
                    toolchain=toolchain, exclusions=exclusions),
                'toolchain': toolchain,
                'excluded_threads': exclusions,
                # `None` rather than absent: every schema field is spelled
                # here so a document that omitted one would be refused, and
                # a null band table is what drops the field from the
                # rendered document on the next re-baseline. The signatures
                # replace it as the table a count is classified under.
                'thread_bands': None,
                'thread_signatures': dict(journey_threads.SIGNATURES),
                'shas': shas, 'journeys': journeys}
    # The field list is the SCHEMA'S, not a literal beside it: a document
    # that omitted a field would be one the next run refuses, with a
    # remedy pointing at this very command.
    # pylint: disable-next=protected-access
    return journey_artifact._validated(
        {field: measured[field] for field in journey_artifact.FIELDS})


def _restored(dropped, held, restore, reports, counter):
    """Settle the journeys the recorded budget holds no count for.

    TWO lists, and each means only what its name says. `held` is every
    journey the artefact records at `null`; `dropped` is those the caller is
    not already dropping, which is why the two differ. A one-line subtraction
    used to hand `dropped` to both, and the second reader rendered it as
    "journeys held at null" — so a journey being dropped and held at null at
    once was reported as holding a COUNT, and `--restore X --drop X` together
    was refused on the one invocation that works.

    A measurement that could not separate one of these carries no count for
    it, and the refusal in the counts loop would have fired first — unless
    `--drop` named it, which is the case the subtraction exists for. Every
    one that IS here is therefore separable, and every one needs a decision.

    Nothing here writes: restoring is the counts loop above having already
    assigned every name, so this decides only whether to object.

    The residual is named because it is the evidence: "unrecorded" says the
    budget holds no count and nothing about whether that is still true, and
    the number is what says it is no longer.
    """
    _named_journeys(restore, '--restore')
    _restorable(restore, held)
    for name in dropped:
        if name in set(restore):
            continue
        row = _separating_row(reports, counter, name)
        raise ValueError(
            f'the budget holds no count for {name} and this measurement '
            f'separates it by {row.get("median")} instructions (spread '
            f'{row.get("spread")}), so the recorded null is a decision the '
            f'measurement no longer supports: pass --restore {name} to '
            'record it again')


def _named_journeys(named, flag):
    """Every name on `flag` that no journey is called.

    The likeliest thing that goes wrong with a per-journey flag is a
    misspelling, and it used to be reported as a fact about the budget
    instead. One failure, one sentence, and it is this one.
    """
    names = journey_counters.journey_names()
    for name in sorted(set(named)):
        if name not in names:
            raise ValueError(
                f'{flag} names {name}, which no journey is called, so there '
                'is nothing there to act on: a journey set is '
                f'{sorted(names)}')


def _restorable(named, held):
    """Refuse a `--restore` the artefact is not already holding at null.

    `held` is the ARTEFACT's own set, never a caller's subset of it: the
    question is what the document records, and a list something else has
    been subtracted from answers a different one.

    `--drop` has no counterpart: every journey in the set can be dropped, so
    a name that reached it is already a valid one and a second refusal there
    could only describe a case that cannot occur.
    """
    unknown = sorted(set(named) - set(held))
    if unknown:
        raise ValueError(
            f'--restore names {unknown[0]}, which the budget already holds a '
            'count for, so there is nothing there to restore')


def _measured_row(report, counter, name):
    """One journey's measured row, or `{}` where the run refused one."""
    entry = (report.get('counters') or {}).get(counter) or {}
    return (entry.get('journeys') or {}).get(name) or {}


def _separating_row(reports, counter, name):
    """The first file's measured row for `name`, or `{}` where none does.

    A drop or a restore refusal names the residual the run measured, and
    with several files the first one that has it is as good a witness as
    any: the sentence names the residual, not the file.
    """
    for report in reports:
        row = _measured_row(report, counter, name)
        if row:
            return row
    return {}


def _dropped_now(drop, reports, counter):
    """Settle every journey `--drop` names, and write nothing.

    The mirror of `--restore`, and the ruling is symmetric: a `null` is the
    one value in the artefact that says the budget does not hold a journey,
    so it is written only when a person names that journey and never as a
    side effect of re-recording the rest.

    It REFUSES a journey any file can separate. A positive residual
    is a journey whose own work the run measured above the background it
    shares — the opposite of the case a drop exists for — and dropping it
    would remove the one journey a person most wants the gate to hold. The
    run is the evidence, so the refusal names what the run measured.
    """
    _named_journeys(drop, '--drop')
    for name in sorted(set(drop)):
        row = _separating_row(reports, counter, name)
        if row:
            raise ValueError(
                f'--drop names {name} and this measurement separates it by '
                f'{row.get("median")} instructions (spread '
                f'{row.get("spread")}), so a journey the run CAN resolve '
                'cannot be dropped: the flag is for one whose own work is '
                'smaller than the background it shares, and this one is not '
                'it')
    # The null is already written: the counts loop above left it at whatever
    # a journey with no measured row reads as, which is `None`, because a
    # journey `--drop` names is exempt from its refusal. So this function
    # decides only whether to object — which is what `_restored` does, and
    # for the same reason: two writers for one value is one writer too many,
    # and the second is the one nothing exercises.


def _dropped(recorded, names):
    """The journeys this document deliberately does not hold a count for.

    A name the artefact records at `null` is a journey the budget does not
    compare, and the measurement in hand counts it anyway — that is the
    whole of the difference between a dropped journey and a journey the
    measurement failed on, which the refusal above already covers. So the
    null is carried forward rather than filled from a number the person who
    wrote it declined.
    """
    journeys = recorded.get('journeys') or {}
    return [name for name in names
            if name in journeys and journeys[name] is None]


def _paths(value):
    """One path or many, as a list: one file is the commonest case."""
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [value]
    return list(value)


def _read_report(source, remedy):
    """One parsed `measure --out` file, or None after printing the refusal.

    The three states a file `run` refuses before any document is shaped: a
    path with no file, a file the decoder rejects, and a measurement whose
    own shape failed. Each prints and lets `run` stop without writing.
    """
    source = Path(source)
    if not source.is_file():
        print(f'no measurements file to read at {source}', file=sys.stderr)
        return None
    try:
        report = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return None
    if report.get('shape_failure'):
        print(f'shape: {report["shape_failure"]}', file=sys.stderr)
        if remedy:
            print(remedy, file=sys.stderr)
        return None
    return report


def run(measurements, artifact, remedy=None, restore=(), drop=(), draws=()):
    """Write the artefact from the measurements, and the exit is the verdict.

    Every refusal writes nothing, so a failed re-baseline leaves the
    recorded budget exactly as it was.
    """
    sources = _paths(measurements)
    pools = _paths(draws)
    reports = []
    for source in sources:
        report = _read_report(source, remedy)
        if report is None:
            return 1
        reports.append(report)
    pool_reports = []
    for source in pools:
        report = _read_report(source, remedy)
        if report is None:
            return 1
        pool_reports.append(report)
    try:
        recorded = journey_artifact.load(artifact)
        document = document_from(reports, recorded,
                                 restore=restore, drop=drop,
                                 draws=pool_reports)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    Path(artifact).write_bytes(journey_artifact.render(document))
    print(f'wrote {len(document["journeys"])} journeys to {artifact} from '
          f'{", ".join(str(path) for path in sources)}, denominated in '
          f'{document["counter"]}')
    # Which bounds the pool moved and which it left — the two groups the
    # pull-request body has to carry for a pool re-baseline, printed where
    # the person who ran the command is looking: a pool that derives
    # nothing must be visible, not a silent success.
    own = recorded.get('tolerances') or {}
    written = document.get('tolerances') or {}
    derived = {name: written[name] for name in written if name not in own}
    carried = sorted(name for name in written if name in own)
    for name, value in sorted(derived.items()):
        print(f'derived the tolerance for {name}: {value}')
    if carried:
        print(f'carried the tolerance for {", ".join(carried)}')
    if pools and not derived:
        print('the pool derived no bound; every tolerance carried')
    # What it stopped holding, and why — the two numbers the pull-request
    # body has to carry for a dropped journey, printed where the person who
    # ran the command is looking rather than left to be reconstructed from
    # the measurement file.
    for name, row in sorted(_refused_rows(reports).items()):
        print(f'dropped {name}: {row or "this run could not separate it"}')
    return 0


def _refused_rows(reports):
    """The journeys the measurements refused, and the sentence for each.

    Whatever counter the run selected is the one the artefact is
    denominated in, so its refusals are the ones that decided what the
    artefact holds. Several files are one recording, so the union is what
    the run refused: a journey one file could not separate is one the
    command stopped on before it wrote anything.
    """
    refused = {}
    for report in reports:
        entry = (report.get('counters') or {}).get(
            report.get('selected_counter'))
        for name, row in ((entry or {}).get('refused') or {}).items():
            refused.setdefault(name, row)
    return refused
