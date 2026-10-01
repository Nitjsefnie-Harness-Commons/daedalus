#!/usr/bin/env python3
"""Ratchet the work three real user journeys cost, in an instruction count.

The mutable policy state is `.github/journey-budget.json`: which counter the
budget is denominated in, how far over it a journey may run, and one
recorded count per journey. It is its own file rather than a member of
`.github/ci-thresholds.json` because that document has a closed schema, and
a new baseline family belongs to a shared owner this change does not edit.
Nothing here raises a recorded number and nothing adds an entry:
`--tighten` only follows a journey down, and only drops one the journey set
no longer has.

  python3 scripts/ci/journey_budget.py probe
  python3 scripts/ci/journey_budget.py measure --rounds 1 --out counts.json
  python3 scripts/ci/journey_budget.py check --measurements counts.json
  python3 scripts/ci/journey_budget.py check --measurements counts.json
  python3 scripts/ci/journey_budget.py check --measurements c.json --tighten

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
fact and compares nothing rather than calling it a regression. A
re-baseline is a reviewed commit: nothing here writes the artefact, and
the harness prints the block one is pasted from.
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
import journey_counters  # noqa: E402  pylint: disable=wrong-import-position
import journey_report  # noqa: E402  pylint: disable=wrong-import-position

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / '.github' / 'journey-budget.json'
JOURNEYS = journey_counters.JOURNEYS
ROUNDS_DEFAULT = journey_counters.ROUNDS_DEFAULT
# `counter` may only name a candidate the counters module will gate on. A
# counter it merely reports — the syscall secondary signal — is refused here
# by name, so a recorded count can never be denominated in a quantity the
# gate does not defend.
COUNTERS = journey_counters.GATE_CANDIDATES
_SCHEMA_VERSION = 1

OVER_REMEDY = (
    'A journey over its budget is a regression in what a user waits for: '
    'the recorded count is never raised by hand, and no entry is ever added '
    'by hand. Find what the journey now does that it did not, and make it '
    'not do it.')
SHAPE_REMEDY = (
    'Rounds of one measurement disagreed about what the journey looks like, '
    'so their counts are not comparable and none of them is a baseline. The '
    'rendering is in the journeys module; a field that legitimately varies '
    'between runs belongs in its exclusion list, and anything else is a '
    'shape change.')
UNMEASURED_REMEDY = (
    'The budget names a counter this runner produced no count in, so no '
    'journey was compared and a green here would be a run that measured '
    'nothing. The probe step says what this runner allows: `instructions:u` '
    'needs less kernel access than an unqualified event, and callgrind is '
    'the fallback when perf is refused.')
TOOLCHAIN_REMEDY = (
    'A recorded count is only comparable against a measurement taken on the '
    'toolchain it was recorded on. Re-baseline from a measured run: the '
    're-baseline block in the step summary carries the counts, their '
    'spread, and the identity they were taken on, and nothing in CI writes '
    'the artefact.')
REMEDY_FOR = {'over': OVER_REMEDY, 'shape': SHAPE_REMEDY,
              'unmeasured': UNMEASURED_REMEDY}


def journey_names():
    """The journey set, which the counters module reads from one place."""
    return journey_counters.journey_names()


# ─── the committed artefact ────────────────────────────────────────────────

def _validated(value):
    if not isinstance(value, dict):
        raise ValueError('the journey budget must be an object')
    unknown = sorted(set(value) - {'schema_version', 'counter',
                                   'tolerance_pct', 'toolchain',
                                   'journeys'})
    if unknown:
        raise ValueError(f'unknown field: {unknown[0]}')
    if value.get('schema_version') != _SCHEMA_VERSION:
        raise ValueError(
            f'unsupported schema_version: {value.get("schema_version")}')
    counter = value.get('counter')
    if counter is not None and counter not in COUNTERS:
        raise ValueError(f'unknown counter: {counter}')
    tolerance = value.get('tolerance_pct')
    if tolerance is not None and (not isinstance(tolerance, (int, float))
                                  or isinstance(tolerance, bool)
                                  or tolerance < 0):
        raise ValueError('tolerance_pct must be a nonnegative number: '
                         f'{tolerance}')
    toolchain = value.get('toolchain')
    if toolchain is not None and not isinstance(toolchain, dict):
        raise ValueError('toolchain must be an object')
    for field, seen in (toolchain or {}).items():
        if field not in journey_counters.TOOLCHAIN_FIELDS:
            raise ValueError(f'unknown toolchain field: {field}')
        if seen is not None and not isinstance(seen, str):
            raise ValueError(
                f'a toolchain identity is a string or null: {field}')
    journeys = value.get('journeys')
    if not isinstance(journeys, dict):
        raise ValueError('journeys must be an object')
    for name, recorded in journeys.items():
        if recorded is None:
            continue
        if not isinstance(recorded, int) or isinstance(recorded, bool) \
                or recorded < 0:
            raise ValueError(
                f'a recorded count must be a nonnegative integer: {name}')
    return value


def load(path=ARTIFACT):
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as error:
        raise ValueError(
            f'cannot read the journey budget: {error}') from None
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'invalid journey budget JSON: {error}') from None
    return _validated(value)


def render(document):
    """The canonical bytes: one journey per line, so a diff reads as a set."""
    _validated(document)
    body = ',\n'.join(
        f'    {json.dumps(name)}: {json.dumps(document["journeys"][name])}'
        for name in sorted(document['journeys']))
    identity = recorded_toolchain(document) or {}
    toolchain = ',\n'.join(
        f'    {json.dumps(field)}: {json.dumps(identity.get(field))}'
        for field in journey_counters.TOOLCHAIN_FIELDS)
    return ('{\n'
            f'  "schema_version": {document["schema_version"]},\n'
            f'  "counter": {json.dumps(document.get("counter"))},\n'
            f'  "tolerance_pct": {json.dumps(document.get("tolerance_pct"))},'
            '\n'
            '  "toolchain": {\n'
            f'{toolchain}\n'
            '  },\n'
            '  "journeys": {\n'
            f'{body}\n'
            '  }\n'
            '}\n').encode('utf-8')


def recorded_toolchain(document):
    """The recorded identity, or None while no field of it is recorded.

    Every field null is the state before the first baseline, and it is
    reported rather than compared: there is nothing yet to say the
    toolchain still matches.
    """
    recorded = document.get('toolchain') or {}
    return recorded if any(recorded.values()) else None


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


def budget_of(document, name):
    """The count `name` may reach: its record plus the tolerance."""
    recorded = document['journeys'].get(name)
    if recorded is None:
        return None
    tolerance = document.get('tolerance_pct') or 0.0
    return recorded * (1 + tolerance / 100.0)


def unrecorded(document, names):
    """Journeys the artefact says nothing about: reported, never hidden."""
    return sorted(name for name in names
                  if document['journeys'].get(name) is None)


def stale(document, names):
    """Recorded journeys the journey set no longer has."""
    return sorted(name for name in document['journeys'] if name not in names)


def violations(counts, shapes, document, names):
    """The fixed set of kinds a check can refuse on.

    `counts` is a measured count per journey and `shapes` the sha each round
    rendered. A journey the artefact does not record is not here:
    `unrecorded` reports it and the check passes, which is what an artefact
    before its first recording looks like.

    `unmeasured` is the false green this whole design exists against: a
    runner that cannot produce the counter the budget names would otherwise
    report every journey within budget having measured none of them.
    """
    over = {}
    shape = {}
    unmeasured = {}
    for name in names:
        seen = sorted(set(shapes.get(name) or ()))
        if len(seen) > 1:
            shape[name] = seen
            continue
        limit = budget_of(document, name)
        if limit is None:
            continue
        measured = counts.get(name)
        if measured is None:
            unmeasured[name] = document['counter']
            continue
        if measured > limit:
            over[name] = (measured, limit)
    return {'over': over, 'shape': shape, 'unmeasured': unmeasured}


def tightened(counts, document, names):
    """Return a lowered journey mapping, or ``None`` when unchanged.

    Only ever downward: a journey that measured cheaper is recorded at what
    it cost, and one whose journey name is gone is dropped rather than kept
    as a rule nothing enforces.
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


def refusal_lines(found):
    lines = ['### Journey budget', '',
             '| journey | measured | budget |', '|---|---|---|']
    for name, (measured, limit) in sorted(found['over'].items()):
        lines.append(f'| {name} | {measured} | {limit:.0f} |')
    for name, seen in sorted(found['shape'].items()):
        lines.append(f'| {name} | shape mismatch: {", ".join(seen)} | — |')
    for name, counter in sorted(found['unmeasured'].items()):
        lines.append(f'| {name} | not measured: `{counter}` gave no count '
                     f'on this runner | — |')
    lines.append('')
    for kind, detail in found.items():
        if detail:
            lines.append(REMEDY_FOR[kind])
    return lines


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
    count.add_argument('--summary', action='store_true',
                       help='also write the table to the step summary')

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
    check.add_argument('--seconds', type=Path,
                       help='what this job cost, one JSON object per line, '
                            'each appended by the workflow step that '
                            'measured it')
    check.add_argument('--summary', action='store_true')
    return parser


def _cost(path):
    """What the workflow's own steps recorded, read as JSON lines.

    One object per line because the steps that measure are not one step:
    the valgrind install and the measurement each append what they timed,
    and neither of them knows the other's keys.
    """
    cost = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            cost.update(json.loads(line))
    return cost


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
            if args.summary:
                journey_counters.write_summary(
                    journey_report.probe_lines(found))
            return 0

        if args.command == 'measure':
            report = journey_counters.measure(args.root, args.rounds)
            payload = json.dumps(report, indent=2, sort_keys=True)
            print(payload)
            if args.out:
                Path(args.out).write_text(payload, encoding='utf-8')
            if args.summary:
                journey_counters.write_summary(
                    journey_report.summary_lines(report))
                journey_counters.write_summary(
                    journey_report.rebaseline_lines(report))
            return 0

        document = load(args.artifact)
        names = journey_names()
        counter = document.get('counter')
        report = _measurements(args)

        if args.seconds and Path(args.seconds).is_file():
            journey_counters.write_summary(journey_report.accounting_lines(
                report, _cost(args.seconds)))

        # Three states, not two. No identity recorded yet is not a change:
        # there is nothing to have changed from, and saying so is what tells
        # a reader this green measured nothing rather than finding a
        # regression.
        recorded = recorded_toolchain(document)
        changed = (toolchain_diff(recorded, report.get('toolchain'))
                   if recorded else {})
        if changed:
            return _toolchain_outcome(args, document, report, changed)
        if recorded is None and args.summary:
            journey_counters.write_summary(journey_report.toolchain_lines(
                document, report, {}, TOOLCHAIN_REMEDY))

        counts = (journey_counters.counts_of(report, counter)
                  if counter else {})
        found = violations(counts, report.get('shas') or {}, document, names)

        if args.tighten:
            # A shape failure refuses a tighten as firmly as it refuses a
            # check: a count taken from a journey that no longer renders the
            # same way is not a cheaper journey, it is a different one.
            if _report_state(document, names, counter, report):
                return 1
            updated = tightened(counts, document, names)
            if updated is None:
                print('no journey measured below its recorded count')
                return 0
            document['journeys'] = updated
            Path(args.artifact).write_bytes(render(document))
            print('tightened the journey budget')
            return 0

        if _report_state(document, names, counter, report):
            return 1
        if not any(found.values()):
            print(f'{len(names)} journeys measured against '
                  f'{counter or "no counter"}; none over budget')
            return 0
        if args.summary:
            journey_counters.write_summary(refusal_lines(found))
        for kind, detail in found.items():
            if detail:
                print(f'{kind}: {detail}', file=sys.stderr)
                print(REMEDY_FOR[kind], file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _toolchain_outcome(args, document, report, changed):
    """A toolchain difference, which is not a regression and not a pass.

    Success, because the tree did not regress — but the summary says in
    those words that no count was compared, so a green here can never be
    read as a journey having been measured and found within budget. A
    tighten refuses instead: a count taken on a toolchain the recorded
    baseline was not measured on is not a cheaper journey, and writing it
    would corrupt the baseline this whole check exists to keep honest.
    """
    if args.tighten:
        print('toolchain changed, re-baseline: a count measured on a '
              'different toolchain is not a cheaper journey',
              file=sys.stderr)
        print(TOOLCHAIN_REMEDY, file=sys.stderr)
        return 1
    if args.summary:
        journey_counters.write_summary(journey_report.toolchain_lines(
            document, report, changed, TOOLCHAIN_REMEDY))
    print('toolchain changed, re-baseline')
    for field, (was, now) in sorted(changed.items()):
        print(f'  {field}: recorded {was!r}, measured {now!r}')
    print('no count was compared, and this step succeeds because the tree '
          'did not regress rather than because anything was within budget')
    return 0


def _report_state(document, names, counter, report):
    """Everything a check knows but is not refusing on, said out loud.

    True when the measurement itself could not be taken, which is the one
    state no count can be compared in and the one refusal the counts cannot
    express.
    """
    if report.get('shape_failure'):
        print(f'shape: {report["shape_failure"]}', file=sys.stderr)
        print(SHAPE_REMEDY, file=sys.stderr)
        return True
    if recorded_toolchain(document) is None:
        print('the journey budget records no toolchain yet, so this run has '
              'no recorded identity to be compared against')
    if counter is None:
        print('the journey budget names no counter yet, so no count is '
              'compared')
    elif document.get('tolerance_pct') is None:
        print(f'the journey budget names {counter} but no tolerance yet, so '
              'no count is compared')
    missing = unrecorded(document, names)
    if missing:
        print(f'unrecorded, reported and passing: {", ".join(missing)}')
    gone = stale(document, names)
    if gone:
        print('recorded but the journey set no longer has them: '
              f'{", ".join(gone)}')
    return False


if __name__ == '__main__':
    raise SystemExit(main())
