"""Write the journey budget from one measurement, whole.

Its own module because `journey_budget.py` sits at its size ceiling and the
document this writes is a document, not a verdict: `journey_budget.py`
decides whether a count may be compared and `journey_artifact.py` owns the
shape one is recorded in, while the mapping from a measurement to that
shape is neither of those jobs.

A re-baseline stays a reviewed commit — the maintainer's ruling is
"autocommit tighten only, where a PR can increase its own cap if
necessary" — so what this removes is the transcription, not the decision.
One command reads a `measure --out` file and writes the artefact from it,
so what lands is the run's own measurement rather than a reader's copy of
a table.
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
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position


def document_from(report, recorded, restore=(), drop=()):
    """The artefact this measurement justifies, as a validated document.

    Counts, shas, toolchain and excluded threads all come from `report`
    and none of them from `recorded`: they describe ONE run, and
    taking any of them from the document being replaced describes two —
    and a check compares all of them or none, so such a document can never
    match anything and every later run refuses it.

    The tolerance is the one field that stays. It is the bound a person
    set, and a re-baseline moves the counts under it rather than moving the
    bound with them. A journey's OWN tolerance is the same bound for one
    journey, and stays for the same reason — narrowed to the journeys this
    document carries, because a tolerance for a journey the set no longer
    has is a rule nothing enforces, which is the same fate a stale count
    has.

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
    counter = report.get('selected_counter')
    counts = journey_counters.counts_of(report, counter)
    names = journey_counters.journey_names()
    journeys = {}
    for name in names:
        measured = counts.get(name)
        if measured is None and name not in set(drop):
            # A journey `--drop` names is exempt: its own absence of a count
            # is what it is being dropped for, and refusing here would make
            # the ruling's second clause unreachable — a run that cannot
            # separate a journey produces no measurement to drop it out of.
            raise ValueError(
                f'the measurement carries no count for {name}, so there is '
                'nothing to record and a budget without it compares '
                'nothing')
        journeys[name] = measured
    _restored(_dropped(recorded, names), restore, report, counter)
    _dropped_now(drop, names, report, counter, journeys)
    shas = {name: _agreed_sha(report, name) for name in names}
    toolchain = report.get('toolchain') or {}
    if not journey_artifact.recorded_toolchain({'toolchain': toolchain}):
        raise ValueError(
            'the measurement carries no toolchain identity, and a recorded '
            'count without one compares against nothing')
    exclusions = report.get('excluded_threads') or {}
    unaccounted = [name for name in names if not exclusions.get(name)]
    if unaccounted:
        raise ValueError(
            'the measurement says which threads it excluded for every '
            f'journey but these: {unaccounted}')
    measured = {'schema_version': journey_artifact.SCHEMA_VERSION,
                'counter': counter,
                'tolerance_pct': recorded.get('tolerance_pct'),
                'tolerances': _carried_tolerances(recorded, names),
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


def _restored(dropped, restore, report, counter):
    """Settle the journeys the recorded budget holds no count for.

    `dropped` is every journey recorded at `null`; a measurement that could
    not separate one of them carries no count for it, and the refusal above
    would have fired first. So every one that IS here is separable, every one
    needs a decision, and this is where it is refused rather than taken.

    Nothing here writes: restoring is the counts loop above having already
    assigned every name, so this decides only whether to object.

    The residual is named because it is the evidence: "unrecorded" says the
    budget holds no count and nothing about whether that is still true, and
    the number is what says it is no longer.
    """
    _named_journeys(restore, dropped, '--restore')
    for name in dropped:
        if name in set(restore):
            continue
        row = _measured_row(report, counter, name)
        raise ValueError(
            f'the budget holds no count for {name} and this measurement '
            f'separates it by {row.get("median")} instructions (spread '
            f'{row.get("spread")}), so the recorded null is a decision the '
            f'measurement no longer supports: pass --restore {name} to '
            'record it again')


def _named_journeys(named, allowed, flag):
    """Every name on `flag` that the artefact's state does not allow.

    Two failures, two sentences, because they are told apart by nothing
    else and the likelier one was getting the rarer one's explanation: a
    name this journey set does not have at all, against a name it has and
    already holds a count for. A misspelling is the first.
    """
    names = journey_counters.journey_names()
    for name in sorted(set(named)):
        if name not in names:
            raise ValueError(
                f'{flag} names {name}, which no journey is called, so there '
                'is nothing there to act on: a journey set is '
                f'{sorted(names)}')
    unknown = sorted(set(named) - set(allowed))
    if unknown:
        raise ValueError(
            f'{flag} names {unknown[0]}, which the budget already holds a '
            'count for, so there is nothing there to restore or drop')


def _measured_row(report, counter, name):
    """One journey's measured row, or `{}` where the run refused one."""
    entry = (report.get('counters') or {}).get(counter) or {}
    return (entry.get('journeys') or {}).get(name) or {}


def _dropped_now(drop, names, report, counter, journeys):
    """Write the `null` for every journey `--drop` names, and nothing else.

    The mirror of `--restore`, and the ruling is symmetric: a `null` is the
    one value in the artefact that says the budget does not hold a journey,
    so it is written only when a person names that journey and never as a
    side effect of re-recording the rest.

    It REFUSES a journey this measurement can separate. A positive residual
    is a journey whose own work the run measured above the background it
    shares — the opposite of the case a drop exists for — and dropping it
    would remove the one journey a person most wants the gate to hold. The
    run is the evidence, so the refusal names what the run measured.
    """
    _named_journeys(drop, names, '--drop')
    for name in sorted(set(drop)):
        row = _measured_row(report, counter, name)
        if row:
            raise ValueError(
                f'--drop names {name} and this measurement separates it by '
                f'{row.get("median")} instructions (spread '
                f'{row.get("spread")}), so a journey the run CAN resolve '
                'cannot be dropped: the flag is for one whose own work is '
                'smaller than the background it shares, and this one is not '
                'it')
        journeys[name] = None


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


def _carried_tolerances(recorded, names):
    """The recorded per-journey tolerances, for the journeys that are here.

    Absent stays absent, as everywhere else in the document: a re-baseline
    of an artefact that names no per-journey tolerance writes no block, so
    the canonical rendering of every artefact recorded before the field
    still round-trips.
    """
    own = recorded.get('tolerances')
    if own is None:
        return None
    return {name: value for name, value in own.items() if name in names}


def _agreed_sha(report, name):
    """The one sha the rounds of this measurement agree on.

    A measurement whose rounds disagree about what a journey rendered is
    not one to record counts from: the rounds measured different things and
    a set's pick among them is a coin toss wearing a determinism's clothes.
    """
    seen = sorted(set(report.get('shas', {}).get(name) or ()))
    if len(seen) != 1:
        raise ValueError(
            f'the rounds did not agree what {name} rendered, so no sha can '
            f'be recorded for it: {seen}')
    return seen[0]


def run(measurements, artifact, remedy=None, restore=(), drop=()):
    """Write the artefact from one measurement file; the exit is the verdict.

    Every refusal writes nothing, so a failed re-baseline leaves the
    recorded budget exactly as it was.
    """
    source = Path(measurements)
    if not source.is_file():
        print(f'no measurements file to read at {source}', file=sys.stderr)
        return 1
    try:
        report = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if report.get('shape_failure'):
        print(f'shape: {report["shape_failure"]}', file=sys.stderr)
        if remedy:
            print(remedy, file=sys.stderr)
        return 1
    try:
        document = document_from(report, journey_artifact.load(artifact),
                                 restore=restore, drop=drop)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    Path(artifact).write_bytes(journey_artifact.render(document))
    print(f'wrote {len(document["journeys"])} journeys to {artifact} from '
          f'{source}, denominated in {document["counter"]}')
    # What it stopped holding, and why — the two numbers the pull-request
    # body has to carry for a dropped journey, printed where the person who
    # ran the command is looking rather than left to be reconstructed from
    # the measurement file.
    for name, row in sorted(_refused_rows(report).items()):
        print(f'dropped {name}: {row or "this run could not separate it"}')
    return 0


def _refused_rows(report):
    """The journeys the measurement refused, and the sentence for each.

    Whatever counter the run selected is the one the artefact is
    denominated in, so its refusals are the ones that decided what the
    artefact holds.
    """
    entry = (report.get('counters') or {}).get(report.get('selected_counter'))
    return dict((entry or {}).get('refused') or {})
