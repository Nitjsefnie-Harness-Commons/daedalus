"""Every gate a recorded count has to pass, and the state a run reports.

What DECIDES, against what the workflow steps CALL: `journey_budget.py`
owns the subcommands and this file every answer they give. One module
owning both meant the policy grew into the CLI and the CLI grew into the
policy.

Nothing here reads `args`, writes the artefact, or parses a subcommand, and
`journey_budget.py` binds each of these back by name, so a suite that
reached one through the policy module still reaches the same function.
"""
import sys
from pathlib import Path

# Beside this one and imported by their own names, as
# `journey_rebaseline.py` does: a relative import would only work for
# `python3 -m scripts.ci.journey_budget`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_artifact  # noqa: E402  pylint: disable=wrong-import-position
import journey_counters  # noqa: E402  pylint: disable=wrong-import-position
import journey_report  # noqa: E402  pylint: disable=wrong-import-position
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position

# One name, one owner: the comparisons are bound from the module that
# defines them so a suite reads them through one import.
budget_of = journey_artifact.budget_of
sha_diff = journey_artifact.sha_diff
map_diff = journey_artifact.map_diff
exclusion_diff = journey_artifact.exclusion_diff

# What each gate says when it refuses, from the module that renders a run's
# prose.
OVER_REMEDY = journey_report.OVER_REMEDY
UNMEASURED_REMEDY = journey_report.UNMEASURED_REMEDY
TOOLCHAIN_REMEDY = journey_report.TOOLCHAIN_REMEDY
SHA_REMEDY = journey_report.SHA_REMEDY
SIGNATURES_REMEDY = journey_report.SIGNATURES_REMEDY
THREADS_REMEDY = journey_report.THREADS_REMEDY
REMEDY_FOR = journey_report.REMEDY_FOR


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


def refused_of(report, counter):
    """The journeys this measurement refused to resolve, per name.

    Distinct from a journey the counter never counted: a refusal carries the
    journey's own numbers and says its own work was smaller than the
    background it shares, which is a different finding from a counter that
    could not run it at all.
    """
    entry = ((report or {}).get('counters') or {}).get(counter) or {}
    return dict(entry.get('refused') or {})


def violations(counts, document, names, refused=()):
    """The kinds a check can refuse a journey on.

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
    unresolved = {}
    refusals = refused or {}
    for name in names:
        limit = budget_of(document, name)
        if limit is None:
            continue
        measured = counts.get(name)
        if measured is None:
            # The two absences are told apart here and nowhere else. A
            # journey this run REFUSED is one whose own work was smaller
            # than the background it shares, and the budget holds a count
            # for it — so a count it cannot compute is never a pass, and
            # the run's own sentence is what it is reported under.
            if name in refusals:
                unresolved[name] = refusals[name]
            else:
                unmeasured[name] = document['counter']
            continue
        if measured > limit:
            over[name] = (measured, limit)
    return {'over': over, 'unmeasured': unmeasured,
            'unresolved': unresolved}


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


def _recorded_gates(report):
    """Every way a count is only comparable against what was recorded.

    Four, on the same terms: a different toolchain, a different set of
    excluded threads, a different table of signatures the roles were read
    by, and a different journey rendering are four different quantities, and
    a count measured against any of them says nothing about the code. A
    gate whose value has never been recorded is the same refusal — there is
    nothing to have differed from, and falling through to comparing would
    compare a count against a question that was never asked.
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
        {'subject': 'thread signatures', 'remedy': SIGNATURES_REMEDY,
         'recorded': _recorded('thread_signatures'),
         'measured': lambda measured: journey_threads.SIGNATURES,
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
