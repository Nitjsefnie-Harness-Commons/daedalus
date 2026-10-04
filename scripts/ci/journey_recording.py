"""How several measurements become one recording.

`journey_rebaseline.py` decides what a recording may replace and writes
the artefact; the arithmetic over the files themselves lives here — the
agreement the files must hold, the median their medians record, and the
tolerance a `--draws` pool derives — because shaping numbers from a set
of `measure --out` files is neither of those jobs and both of them read
it.
"""
import statistics


def agreed_counter(reports):
    """The one counter every file selected, and the refusal for the rest.

    A count denominated in one counter is a different quantity from a count
    denominated in another, so files that selected differently have nothing
    to record together.
    """
    selected = [report.get('selected_counter') for report in reports]
    if len(set(selected)) != 1:
        raise ValueError(
            'the files do not agree which counter they measured under: '
            f'{sorted(set(selected), key=str)}')
    return selected[0]


def agreed_sha(reports, name):
    """The one sha every round of every file saw `name` render.

    A measurement whose rounds disagree about what a journey rendered is
    not one to record counts from: the rounds measured different things and
    a set's pick among them is a coin toss wearing a determinism's clothes.
    The same rule reaches across files, because two files whose rounds saw
    different renderings are two runs of different journeys and a median
    over them is a number no run measured.
    """
    seen = sorted({sha for report in reports
                   for sha in (report.get('shas') or {}).get(name) or ()})
    if len(seen) != 1:
        raise ValueError(
            f'the rounds did not agree what {name} rendered, so no sha can '
            f'be recorded for it: {seen}')
    return seen[0]


def agreed_toolchain(reports):
    """The one toolchain identity every file was measured on.

    A count is comparable only within the toolchain it was taken on, and a
    median over files from two of them is a number no machine measured.
    """
    seen = []
    for report in reports:
        identity = report.get('toolchain') or {}
        if identity not in seen:
            seen.append(identity)
    if len(seen) != 1:
        raise ValueError(
            'the files were measured on different toolchains, and a count '
            f'recorded across them compares against nothing: {seen}')
    return seen[0]


def agreed_exclusions(reports):
    """The one exclusion map every file counted under.

    A count taken under one exclusion map is a different quantity under
    another, so files that disagree there have nothing to record together.
    """
    exclusions = reports[0].get('excluded_threads') or {}
    if any((report.get('excluded_threads') or {}) != exclusions
           for report in reports[1:]):
        raise ValueError(
            'the files do not agree which threads each journey excluded, '
            'so no count is recorded from them together')
    return exclusions


def recorded_median(measured):
    """The count across files: their median, integral when the median is.

    An even file count averages its two middle files, and an average of two
    equal integers arrives as a float; the schema records an integer, so an
    integral median is written as one. A median that lands between two
    counts is left a float, which the schema's own validator refuses — no
    run measured that count.
    """
    value = statistics.median(measured)
    return int(value) if value == int(value) else value


def carried_tolerances(recorded, counted, draws=(), counter=None):
    """The recorded per-journey tolerances, for the journeys this one holds.

    Absent stays absent, as everywhere else in the document: a re-baseline
    of an artefact that names no per-journey tolerance writes no block, so
    the canonical rendering of every artefact recorded before the field
    still round-trips.

    `counted` is the journeys this measurement records a COUNT for, not the
    journeys the set carries. A journey recorded at `null` -- the manager's
    own case, written only by `--drop` -- is in the set and carries no
    count, so a bound kept beside it is a bound no arithmetic reads and
    `_validated_tolerances` refuses the document for: `--drop` would be the
    one flag that cannot be used on a budget with a per-journey tolerance,
    and the remedy for that is the hand-edit this command replaces.

    A `--draws` pool moves the bound instead of carrying it: a journey the
    pool names is re-bound to the span of every round of every pool file,
    in the percent the artefact denominates tolerances in; a journey the
    pool does not name keeps the bound it carried.
    """
    own = recorded.get('tolerances')
    if own is None:
        carried = None
    else:
        carried = {name: value for name, value in own.items()
                   if name in counted}
    for name, value in sorted(derived_tolerances(
            draws, counter, counted).items()):
        if carried is None:
            carried = {}
        carried[name] = value
    return carried


def derived_tolerances(draws, counter, counted):
    """The bound each pool-named journey is re-derived to, and none beside.

    Each pool file contributes every ROUND's count — the row's `net`, the
    per-round residual the recorded median is the median of — and a journey
    the pool names is held to (max - min) / min over all of them. A journey
    the pool does not name keeps its carried bound, which is why only named
    journeys come back.

    A file that selected another counter measured a different quantity, and
    a count from it would re-bind a journey to a span its recorded counts
    are not denominated in.
    """
    pool = {}
    for report in draws:
        selected = report.get('selected_counter')
        if selected != counter:
            raise ValueError(
                'a draws file selected '
                f'{selected or "no counter"} while the budget is '
                f'denominated in {counter}, so its counts are a different '
                'quantity')
        rows = ((report.get('counters') or {}).get(counter)
                or {}).get('journeys') or {}
        for name, row in rows.items():
            pool.setdefault(name, []).extend(row.get('net') or ())
    derived = {}
    for name, values in sorted(pool.items()):
        if not values or name not in counted:
            continue
        low = min(values)
        if low <= 0:
            raise ValueError(
                f'the draws for {name} reach {low}, and a span from a '
                'nonpositive floor is not a bound any run can be held to')
        derived[name] = round((max(values) - low) / low * 100, 6)
    return derived
