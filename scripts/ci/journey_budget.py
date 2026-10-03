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
import journey_gates  # noqa: E402  pylint: disable=wrong-import-position
import journey_rebaseline  # noqa: E402  pylint: disable=wrong-import-position
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
budget_for = journey_artifact.budget_for
tolerance_of = journey_artifact.tolerance_of
exclusion_diff = journey_artifact.exclusion_diff
map_diff = journey_artifact.map_diff
sha_diff = journey_artifact.sha_diff
# pylint: disable-next=protected-access
_validated = journey_artifact._validated  # noqa: SLF001

# What each gate says when it refuses lives in the module that renders a
# run's prose, bound back here because this is the policy surface the suites
# reach them through — `test_journey_budget_gates.py` reads a recorded
# gate's remedy from HERE rather than loading a second module to find it.
#
# `SIGNATURES_REMEDY` is deliberately absent: nothing read it, in this
# module or in any suite. Its gate prints the remedy out of
# `journey_gates`, where the gate itself is built, and a binding here was a
# second name for a string that could move without anything noticing.
OVER_REMEDY = journey_report.OVER_REMEDY
SHAPE_REMEDY = journey_report.SHAPE_REMEDY
UNMEASURED_REMEDY = journey_report.UNMEASURED_REMEDY
UNRESOLVED_REMEDY = journey_report.UNRESOLVED_REMEDY
TOOLCHAIN_REMEDY = journey_report.TOOLCHAIN_REMEDY
SHA_REMEDY = journey_report.SHA_REMEDY
THREADS_REMEDY = journey_report.THREADS_REMEDY
REMEDY_FOR = journey_report.REMEDY_FOR

# What DECIDES lives in `journey_gates.py` — the recorded comparisons, the
# budget's own violations, and the state a run reports — and is bound back
# here by name. The suites read the policy through THIS module, so one name
# here is one name for one function; a suite that wants the gate itself
# loads that module.
toolchain_diff = journey_gates.toolchain_diff
unrecorded = journey_gates.unrecorded
stale = journey_gates.stale
refused_of = journey_gates.refused_of
unavailable_reason = journey_gates.unavailable_reason
violations = journey_gates.violations
tightened = journey_gates.tightened
_recorded_gates = journey_gates._recorded_gates  # noqa: SLF001
_recorded_outcome = journey_gates._recorded_outcome  # noqa: SLF001
_report_state = journey_gates._report_state  # noqa: SLF001


def journey_names():
    """The journey set, which the counters module reads from one place."""
    return journey_counters.journey_names()


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
    # Per-journey and repeatable, because restoring a count the budget
    # deliberately dropped is a decision about that journey and nothing
    # else. A flag that restored whatever it found separable would make the
    # decision on every re-baseline of anything.
    rebase.add_argument('--restore', action='append', default=[],
                        metavar='JOURNEY',
                        help='record a count for a journey the budget holds '
                             'none for; repeatable')
    # The mirror of `--restore`, for the other one of the two values a
    # journey's row can hold.
    rebase.add_argument('--drop', action='append', default=[],
                        metavar='JOURNEY',
                        help='write no count for a journey this run cannot '
                             'separate; repeatable')
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
                args.measurements, args.artifact, remedy=SHAPE_REMEDY,
                restore=args.restore, drop=args.drop)

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
        found = violations(counts, document, names,
                           journey_gates.refused_of(report, counter))

        if args.tighten:
            # A shape failure refuses a tighten as firmly as it refuses a
            # check: a count taken from a journey that no longer renders the
            # same way is not a cheaper journey, it is a different one.
            _report_state(document, names, counter, report)
            # The same argument settles both, and neither is a rise: a
            # budget the NEXT check cannot accept is not one this command
            # may write and report success on. `over` is the obvious case;
            # `unresolved` is the one that needed probing — the arithmetic
            # is right to skip an unmeasured journey's row, and the WRITE is
            # not, so `check` and `check --tighten` read the same measurement
            # to opposite answers until this.
            if found['over']:
                print('a journey is over its budget, so nothing is tightened: '
                      'lowering the journeys that fell would land a smaller '
                      'budget with the rise still in the tree and nothing '
                      'recording it', file=sys.stderr)
                print(OVER_REMEDY, file=sys.stderr)
                return 1
            # BOTH kinds a journey can be missing for, because the
            # argument is the same and only one of them was guarded. A
            # journey the run REFUSED and a journey the counter never
            # counted are different findings with different remedies — so
            # each keeps its own sentence — and neither may be the reason a
            # write lands a budget the next check refuses.
            #
            # What each arm NAMES is not the same thing either, and joining
            # one shape for both dropped half of it: `violations` keys both
            # mappings by journey, and only `unmeasured`'s value repeats
            # what `because` has already said — `unresolved` holds the run's
            # own SENTENCE, so joining its keys printed the journey's name
            # and nothing of why the run could not resolve it. That is
            # #1502 on its second reader path; the first is the row this
            # same refusal reaches in the step summary.
            for kind, because, remedy, named in (
                    ('unresolved',
                     'the run could not resolve', UNRESOLVED_REMEDY,
                     dict.values),
                    ('unmeasured',
                     f'the run counted no journey under '
                     f'{counter or "no counter"} for', UNMEASURED_REMEDY,
                     dict.keys)):
                if not found.get(kind):
                    continue
                refused = ', '.join(sorted(named(found[kind])))
                print(f'{because} {refused}, so nothing is tightened: '
                      'lowering the journeys it did measure would land a '
                      'budget the next check refuses over the ones it could '
                      'not', file=sys.stderr)
                print(remedy, file=sys.stderr)
                return 1
            recorded = document['journeys']
            updated = tightened(counts, document, names)
            if updated is None:
                print('no journey measured a drop wider than its own '
                      'tolerance')
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
            journey_counters.write_summary(journey_report.verdict_lines(
                document, counts, found,
                unavailable_reason(report, counter)))
        if not any(found.values()):
            print(f'{len(names)} journeys measured against '
                  f'{counter or "no counter"}; none over budget')
            return 0
        if args.summary:
            # Each kind's remedy reaches the summary beside the row that
            # reports it; stderr is collapsed by default, and the summary
            # is the only place a reader of an unmeasured journey can act
            # from.
            #
            # In the order `found` iterates, which is the order the report
            # below prints them in. Three hand-written `if`s each naming a
            # kind were free to disagree with it, and a reader comparing the
            # summary against the log had two orders to hold in their head.
            for kind, detail in found.items():
                if not detail:
                    continue
                if kind == 'over':
                    journey_counters.write_summary(
                        journey_report.rebaseline_lines())
                elif kind in REMEDY_FOR:
                    journey_counters.write_summary([REMEDY_FOR[kind]])
        for kind, detail in found.items():
            if detail:
                print(f'{kind}: {detail}', file=sys.stderr)
                print(REMEDY_FOR[kind], file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
