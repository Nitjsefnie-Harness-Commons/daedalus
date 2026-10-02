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


def document_from(report, recorded):
    """The artefact this measurement justifies, as a validated document.

    Counts, shas, toolchain and excluded threads all come from `report`
    and none of them from `recorded`: they describe ONE run, and
    taking any of them from the document being replaced describes two —
    and a check compares all of them or none, so such a document can never
    match anything and every later run refuses it.

    The tolerance is the one field that stays. It is the bound a person
    set, and a re-baseline moves the counts under it rather than moving the
    bound with them.
    """
    counter = report.get('selected_counter')
    counts = journey_counters.counts_of(report, counter)
    names = journey_counters.journey_names()
    journeys = {}
    for name in names:
        measured = counts.get(name)
        if measured is None:
            raise ValueError(
                f'the measurement carries no count for {name}, so there is '
                'nothing to record and a budget without it compares '
                'nothing')
        journeys[name] = measured
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


def run(measurements, artifact, remedy=None):
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
        document = document_from(report, journey_artifact.load(artifact))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    Path(artifact).write_bytes(journey_artifact.render(document))
    print(f'wrote {len(document["journeys"])} journeys to {artifact} from '
          f'{source}, denominated in {document["counter"]}')
    return 0
