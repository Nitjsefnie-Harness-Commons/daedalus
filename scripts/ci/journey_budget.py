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
Counting them is `journey_counters.py`, which owns what this runner allows.
"""
import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

if __package__:
    # pylint: disable-next-line=relative-beyond-top-level,no-name-in-module
    from . import journey_counters
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    journey_counters = importlib.import_module('journey_counters')

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
REMEDY_FOR = {'over': OVER_REMEDY, 'shape': SHAPE_REMEDY}


def journey_names():
    """The journey set, which the counters module reads from one place."""
    return journey_counters.journey_names()


# ─── the committed artefact ────────────────────────────────────────────────

def _validated(value):
    if not isinstance(value, dict):
        raise ValueError('the journey budget must be an object')
    unknown = sorted(set(value) - {'schema_version', 'counter',
                                   'tolerance_pct', 'journeys'})
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
    return ('{\n'
            f'  "schema_version": {document["schema_version"]},\n'
            f'  "counter": {json.dumps(document.get("counter"))},\n'
            f'  "tolerance_pct": {json.dumps(document.get("tolerance_pct"))},'
            '\n'
            '  "journeys": {\n'
            f'{body}\n'
            '  }\n'
            '}\n').encode('utf-8')


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
    """
    over = {}
    shape = {}
    for name in names:
        seen = sorted(set(shapes.get(name) or ()))
        if len(seen) > 1:
            shape[name] = seen
            continue
        measured = counts.get(name)
        limit = budget_of(document, name)
        if measured is None or limit is None:
            continue
        if measured > limit:
            over[name] = (measured, limit)
    return {'over': over, 'shape': shape}


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


# ─── the probe, as a step summary ──────────────────────────────────────────

def probe_lines(found):
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
                     f"`{stat['returncode']}`, counts: `{stat['counts']}`")
        if stat['stderr']:
            lines += ['', '```', stat['stderr'], '```']
    lines += [f"- valgrind on PATH: `{found['valgrind_path']}`",
              f"- valgrind --version: `{found['valgrind_version']}`",
              f"- callgrind_control on PATH: "
              f"`{found['callgrind_control_path']}`",
              f"- callgrind_control --version: "
              f"`{found['callgrind_control_version']}`",
              f"- callgrind_control usable: "
              f"`{found['callgrind_control_usable']}`",
              f"- strace on PATH: `{found['strace_path']}`",
              f"- counter selected here: `{found['selected']}`", '']
    return lines


def refusal_lines(found):
    lines = ['### Journey budget', '',
             '| journey | measured | budget |', '|---|---|---|']
    for name, (measured, limit) in sorted(found['over'].items()):
        lines.append(f'| {name} | {measured} | {limit:.0f} |')
    for name, seen in sorted(found['shape'].items()):
        lines.append(f'| {name} | shape mismatch: {", ".join(seen)} | — |')
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
    check.add_argument('--summary', action='store_true')
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
            if args.summary:
                journey_counters.write_summary(probe_lines(found))
            return 0

        if args.command == 'measure':
            report = journey_counters.measure(args.root, args.rounds)
            payload = json.dumps(report, indent=2, sort_keys=True)
            print(payload)
            if args.out:
                Path(args.out).write_text(payload, encoding='utf-8')
            if args.summary:
                journey_counters.write_summary(
                    journey_counters.summary_lines(report))
            return 0

        document = load(args.artifact)
        names = journey_names()
        counter = document.get('counter')
        report = _measurements(args)
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