#!/usr/bin/env python3
"""Ratchet the work seven real user journeys cost, in an instruction count.

The mutable policy state is `.github/journey-budget.json`: which counter the
budget is denominated in, how far over it a journey may run, and one
recorded count per journey. It is its own file rather than a member of
`.github/ci-thresholds.json` because that document has a closed schema, and
a new baseline family belongs to a shared owner this file does not edit.
Nothing here raises a recorded number and nothing adds an entry:
`--tighten` only follows a journey down, and only drops one the journey set
no longer has. A run that found a journey over budget tightens nothing at
all, and so does a run where no count was compared at all — a recorded gate
that moved, most often the runner image the toolchain records. Neither is a
regression, so neither is a red: the one thing CI can do to the file is make
it smaller, and a run that made nothing smaller says so rather than failing.

  python3 scripts/ci/journey_budget.py probe
  python3 scripts/ci/journey_budget.py measure --rounds 1 --out counts.json
  python3 scripts/ci/journey_budget.py check --measurements counts.json
  python3 scripts/ci/journey_budget.py check --measurements c.json --tighten
  python3 scripts/ci/journey_budget.py rebaseline --measurements counts.json

A COUNT IS NOT COMPARABLE UNLESS THE JOURNEY STILL IS THE SAME JOURNEY, so
every journey renders what it observed and the harness records the sha256 of
that rendering. Rounds of one measurement must agree on it, and a
disagreement is a refusal naming both shas rather than a re-baseline: the
counts of a journey whose shape moved do not describe the recorded journey.

NOR IS IT COMPARABLE ACROSS A TOOLCHAIN. A deterministic counter is
deterministic only for a fixed binary, and the interpreter's patch build,
libc and valgrind all move without a change to this repository. So
`toolchain` records the identity of everything a count depends on and no
code change controls, and a check on a different toolchain reports that
fact and compares nothing rather than calling it a regression.

A re-baseline RAISES a recorded count, so it stays a reviewed commit and
nothing here writes the artefact on a check. `rebaseline` is the one
command that does, over a measurement file a person runs deliberately: it
carries the whole document from that one measurement, so what lands is the
run's own numbers rather than a transcription of a table.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# The counter module sits beside this one and is imported by its own name,
# which is what a run from the repository root, a run from anywhere else and
# `python3 -m scripts.ci.journey_budget` all do. A relative import would
# only work for the last of the three.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_artifact  # noqa: E402  pylint: disable=wrong-import-position
import journey_counters  # noqa: E402  pylint: disable=wrong-import-position
import journey_rebaseline  # noqa: E402  pylint: disable=wrong-import-position
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position
import journey_report  # noqa: E402  pylint: disable=wrong-import-position

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / '.github' / 'journey-budget.json'
JOURNEYS = journey_counters.JOURNEYS
ROUNDS_DEFAULT = journey_counters.ROUNDS_DEFAULT
# `counter` may only name a candidate the counters module will gate on. A
# counter it merely reports — the syscall secondary signal — is
# refused here by name, so a recorded count can never be
# denominated in a quantity the gate does not defend.
COUNTERS = journey_artifact.COUNTERS

# The document shape lives in its own module, off this one's ceiling, and
# the names below are this file's own bindings of it: the suites read the
# document through THIS module, so one name here is one name for one
# function, and a suite that wants the shape itself loads that module.
load = journey_artifact.load
render = journey_artifact.render
budget_of = journey_artifact.budget_of
exclusion_diff = journey_artifact.exclusion_diff
map_diff = journey_artifact.map_diff
sha_diff = journey_artifact.sha_diff
# pylint: disable-next=protected-access
_validated = journey_artifact._validated  # noqa: SLF001

# What each gate says when it refuses lives in the module that renders a
# run's prose, bound back here because the gates below are what print it.
OVER_REMEDY = journey_report.OVER_REMEDY
SHAPE_REMEDY = journey_report.SHAPE_REMEDY
UNMEASURED_REMEDY = journey_report.UNMEASURED_REMEDY
TOOLCHAIN_REMEDY = journey_report.TOOLCHAIN_REMEDY
SHA_REMEDY = journey_report.SHA_REMEDY
BANDS_REMEDY = journey_report.BANDS_REMEDY
THREADS_REMEDY = journey_report.THREADS_REMEDY
REMEDY_FOR = journey_report.REMEDY_FOR


def journey_names():
    """The journey set, which the counters module reads from one place."""
    return journey_counters.journey_names()


# ─── the committed artefact ────────────────────────────────────────────────

def toolchain_diff(recorded, measured):
    """Every identity field the recorded and measured toolchains differ on.

    A field one side did not record differs too: `null` against a real
    value is a toolchain this repository has never measured, and comparing
    counts across it would compare two different questions.
    """
    recorded = recorded or {}
    measured = measured or {}
    return {field: (recorded.get(field), measured.get(field))
            for field in journey_counters.TOOLCHAIN_FIELDS
            if recorded.get(field) != measured.get(field)}


def unrecorded(document, names):
    """Journeys the artefact says nothing about: reported, never hidden."""
    return sorted(name for name in names
                  if document['journeys'].get(name) is None)


def stale(document, names):
    """Recorded journeys the journey set no longer has."""
    return sorted(name for name in document['journeys'] if name not in names)


def violations(counts, document, names):
    """The two kinds a check can refuse a journey on.

    `counts` is a measured count per journey. A journey the artefact does
    not record is not here: `unrecorded` reports it and the check passes,
    which is what an artefact before its first recording looks like.

    `unmeasured` is the false green this whole design exists against: a
    runner that cannot produce the counter the budget names would otherwise
    report every journey within budget having measured none of them.

    A shape disagreement between rounds is NOT here, because the sha gate
    refuses it before this is reached — a measurement whose rounds disagree
    about what a journey rendered is not one to compare counts from. The
    control for that disagreement is `sha_diff`, which is where the case
    lives.
    """
    over = {}
    unmeasured = {}
    for name in names:
        limit = budget_of(document, name)
        if limit is None:
            continue
        measured = counts.get(name)
        if measured is None:
            unmeasured[name] = document['counter']
            continue
        if measured > limit:
            over[name] = (measured, limit)
    return {'over': over, 'unmeasured': unmeasured}


def tightened(counts, document, names):
    """Return a lowered journey mapping, or ``None`` when unchanged.

    Only ever downward: a journey that measured cheaper is recorded at what
    it cost, and one whose journey name is gone is dropped rather than kept
    as a rule nothing enforces. The caller refuses a measurement with a
    rise in it, but the property is here rather than only there — this is
    what a second caller reaches without that guard in front of it.
    """
    updated = dict(document['journeys'])
    for name, recorded in document['journeys'].items():
        if name not in names:
            del updated[name]
            continue
        measured = counts.get(name)
        if measured is None or recorded is None:
            continue
        if measured < recorded:
            updated[name] = measured
    return updated if updated != document['journeys'] else None


# ─── the command line ──────────────────────────────────────────────────────

def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    # `--root` sits on each subcommand rather than above them, because a
    # top-level option is only readable BEFORE the subcommand name and a
    # workflow step that writes `measure --root .` would fail on it.
    probe = sub.add_parser(
        'probe', help='report what this runner actually counts')
    probe.add_argument('--summary', action='store_true',
                       help='also write the report to the step summary')

    count = sub.add_parser('measure', help='measure every journey')
    count.add_argument('--root', type=Path, default=ROOT,
                       help='the checkout to measure')
    count.add_argument('--rounds', type=int, default=ROUNDS_DEFAULT)
    count.add_argument('--out', type=Path,
                       help='write the measurements JSON here')

    check = sub.add_parser(
        'check', help='compare against the recorded counts')
    check.add_argument('--root', type=Path, default=ROOT,
                       help='the checkout to measure')
    check.add_argument('--measurements', type=Path,
                       help='a measure --out file; measured here without one')
    check.add_argument('--rounds', type=int, default=ROUNDS_DEFAULT)
    check.add_argument('--tighten', action='store_true',
                       help='follow journeys down instead of reporting')
    check.add_argument('--artifact', type=Path, default=ARTIFACT)
    check.add_argument('--summary', action='store_true')

    rebase = sub.add_parser(
        'rebaseline', help='write the artefact from one measurement')
    rebase.add_argument('--measurements', type=Path, required=True,
                        help='a measure --out file, read and not measured '
                             'here')
    rebase.add_argument('--artifact', type=Path, default=ARTIFACT)
    return parser


def _measurements(args):
    """The measurements a check reads, measured here when it has none."""
    if args.measurements and Path(args.measurements).is_file():
        with open(args.measurements, encoding='utf-8') as handle:
            return json.load(handle)
    report = journey_counters.measure(args.root, args.rounds)
    if args.measurements:
        Path(args.measurements).write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    return report


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == 'probe':
            found = journey_counters.facts()
            print(json.dumps(found, indent=2, sort_keys=True))
            # The block is the diagnosis of a runner that can count
            # NOTHING, so it is written only there: on a runner with a
            # usable counter it would be a page of detail about an
            # environment that turned out to be fine.
            if args.summary and found.get('selected') is None:
                journey_counters.write_summary(
                    journey_report.probe_lines(found))
            return 0

        if args.command == 'measure':
            report = journey_counters.measure(args.root, args.rounds)
            payload = json.dumps(report, indent=2, sort_keys=True)
            print(payload)
            if args.out:
                Path(args.out).write_text(payload, encoding='utf-8')
            return 0

        if args.command == 'rebaseline':
            return journey_rebaseline.run(
                args.measurements, args.artifact, remedy=SHAPE_REMEDY)

        document = load(args.artifact)
        names = journey_names()
        counter = document.get('counter')
        report = _measurements(args)

        # A measurement that could not be taken is reported before any
        # recorded map is compared: there is nothing to compare, and the
        # reason is not one the recorded maps can speak to.
        if report.get('shape_failure'):
            print(f'shape: {report["shape_failure"]}', file=sys.stderr)
            print(SHAPE_REMEDY, file=sys.stderr)
            return 1

        for gate in _recorded_gates(report):
            # Never-recorded FIRST: a diff against nothing is a difference
            # from nothing, and reading it as a change would report a red
            # whose cause is a field nobody has filled in yet.
            recorded = gate['recorded'](document)
            if recorded is None:
                return _recorded_outcome(args, document, report, {}, gate)
            differs = gate['differs'](recorded, gate['measured'](report))
            if differs:
                return _recorded_outcome(args, document, report, differs,
                                         gate)

        counts = (journey_counters.counts_of(report, counter)
                  if counter else {})
        found = violations(counts, document, names)

        if args.tighten:
            # A shape failure refuses a tighten as firmly as it refuses a
            # check: a count taken from a journey that no longer renders the
            # same way is not a cheaper journey, it is a different one.
            _report_state(document, names, counter, report)
            if found['over']:
                print('a journey is over its budget, so nothing is tightened: '
                      'lowering the journeys that fell would land a smaller '
                      'budget with the rise still in the tree and nothing '
                      'recording it', file=sys.stderr)
                print(OVER_REMEDY, file=sys.stderr)
                return 1
            recorded = document['journeys']
            updated = tightened(counts, document, names)
            if updated is None:
                print('no journey measured below its recorded count')
                return 0
            # Counted against the mapping as it was, before the rebind on the
            # next line: `document['journeys']` IS `updated` from there on,
            # so comparing the two afterwards compares a mapping with itself
            # and reports nothing lowered however much was.
            dropped = sum(1 for name, value in updated.items()
                          if recorded.get(name) is not None
                          and value < recorded[name])
            document['journeys'] = updated
            Path(args.artifact).write_bytes(render(document))
            print('tightened the journey budget')
            journey_counters.write_summary([
                f'This run tightened the journey budget on {dropped} '
                f'{"journey" if dropped == 1 else "journeys"}; nothing here '
                'raised a recorded count.'])
            return 0

        _report_state(document, names, counter, report)
        if args.summary:
            journey_counters.write_summary(
                journey_report.verdict_lines(document, counts, found))
        if not any(found.values()):
            print(f'{len(names)} journeys measured against '
                  f'{counter or "no counter"}; none over budget')
            return 0
        if args.summary:
            # Each kind's remedy reaches the summary beside the row that
            # reports it; stderr is collapsed by default, and the summary
            # is the only place a reader of an unmeasured journey can act
            # from.
            if found['unmeasured']:
                journey_counters.write_summary([UNMEASURED_REMEDY])
            if found['over']:
                journey_counters.write_summary(
                    journey_report.rebaseline_lines())
        for kind, detail in found.items():
            if detail:
                print(f'{kind}: {detail}', file=sys.stderr)
                print(REMEDY_FOR[kind], file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _recorded_gates(report):
    """Every way a count is only comparable against what was recorded.

    Four, on the same terms: a different toolchain, a different set of
    excluded threads, different band thresholds, and a different journey
    rendering are four different quantities, and a count measured against
    any of them says nothing about the code. A gate whose value has never
    been recorded is the same refusal — there is nothing to have differed
    from, and falling through to comparing would compare a count against a
    question that was never asked.
    """
    def _recorded(field):
        """The recorded map, or None when it is absent or holds nothing.

        A field that is PRESENT AND EMPTY is the never-recorded state, and
        it must read as that rather than as a value that changed: `{}` and
        `null` say the same thing about what has been measured, and a gate
        that treated the first as a change would report a difference from
        nothing.
        """
        def read(document):
            value = document.get(field)
            if isinstance(value, dict) and not any(value.values()):
                return None
            return value
        return read

    return (
        {'subject': 'toolchain', 'remedy': TOOLCHAIN_REMEDY,
         'recorded': _recorded('toolchain'),
         'measured': lambda measured: measured.get('toolchain'),
         'differs': toolchain_diff},
        {'subject': 'excluded threads', 'remedy': THREADS_REMEDY,
         'recorded': _recorded('excluded_threads'),
         'measured': lambda measured: measured.get('excluded_threads'),
         'differs': exclusion_diff},
        {'subject': 'thread bands', 'remedy': BANDS_REMEDY,
         'recorded': _recorded('thread_bands'),
         'measured': lambda measured: journey_threads.BANDS,
         'differs': map_diff},
        {'subject': 'journey shas', 'remedy': SHA_REMEDY,
         'recorded': _recorded('shas'),
         'measured': lambda measured: measured.get('shas') or {},
         'differs': sha_diff},
    )


def _recorded_outcome(args, document, report, changed, gate):
    """A recorded-and-measured difference: not a regression, and not a pass.

    Success, because the tree did not regress — but both outputs say in
    those words that no count was compared, so a green here can never be
    read as a journey having been measured and found within budget. A
    tighten takes the SAME answer and writes nothing: what it refuses is
    the write, and a count measured against something the recorded
    baseline was not measured against is not a cheaper journey. Both paths
    compare no count and write nothing, so a tighten exits 0 here too —
    agreeing with the check rather than reds a required context on a push
    to main where nothing regressed.

    The subject names the cause in the LOG as well as in the summary, and
    the remedy follows it. A green that compared nothing is exactly the case
    a reader debugs from the log, so the two must not disagree about why.
    """
    subject, remedy = gate['subject'], gate['remedy']
    if not changed:
        # A REFUSAL, and it exits 1, so the whole of it goes to stderr: a
        # reader debugging a red wants the cause beside the remedy, and a
        # report on stdout is the other half of this file's split — the
        # outcome that succeeded rather than the one that failed.
        print(f'{subject} not recorded, so no count is compared against a '
              'recorded one', file=sys.stderr)
        print(remedy, file=sys.stderr)
        if args.summary:
            journey_counters.write_summary(journey_report.toolchain_lines(
                document, report, {}, remedy, subject=subject))
        return 1
    if args.tighten:
        print(f'{subject} changed, re-baseline: no count was compared and '
              f'nothing was tightened, because a count measured against a '
              f'different {subject} is not a cheaper journey', file=sys.stderr)
        print(remedy, file=sys.stderr)
        if args.summary:
            journey_counters.write_summary(journey_report.toolchain_lines(
                document, report, changed, remedy, subject=subject))
            journey_counters.write_summary(
                journey_report.tighten_skipped_lines(subject))
        return 0
    if args.summary:
        journey_counters.write_summary(journey_report.toolchain_lines(
            document, report, changed, remedy, subject=subject))
    print(f'{subject} changed, re-baseline')
    for field, (was, now) in sorted(changed.items()):
        print(f'  {field}: recorded {was!r}, measured {now!r}')
    print('no count was compared, and this step succeeds because the tree '
          'did not regress rather than because anything was within budget')
    return 0


def _report_state(document, names, counter, report):
    """A null `counter` or `tolerance_pct`, reported without a refusal.

    Both reach here, and the run's exit then comes from the counts rather
    than from here: a null counter leaves nothing measured, and a null
    tolerance leaves a budget equal to the recorded count, so a movement in
    either direction shows in the counts instead. Both were driven on the
    CLI, which is what refuted the claim this replaces.
    """
    if counter is None:
        print('the journey budget names no counter yet, so no count is '
              'compared')
    elif document.get('tolerance_pct') is None:
        print(f'the journey budget names {counter} but no tolerance yet, so '
              'counts are compared against the recorded count itself, with '
              'zero headroom')
    missing = unrecorded(document, names)
    if missing:
        print(f'unrecorded, reported and passing: {", ".join(missing)}')
    gone = stale(document, names)
    if gone:
        print('recorded but the journey set no longer has them: '
              f'{", ".join(gone)}')


if __name__ == '__main__':
    raise SystemExit(main())
