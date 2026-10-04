#!/usr/bin/env python3
"""Contracts for the per-thread rows a journey measurement carries.

The journey budget records ONE instruction count per journey, and two runs
of unchanged code have produced counts about seven percent apart. The
measurement already reads every thread's own total out of the callgrind
out-files and then sums them, so the data exists and is thrown away; these
rows are what it read, kept so a later run can say WHICH THREAD carries the
difference.

They are instrumentation, so the contract that matters most is the last one
here: the rows are assembled in the parent, after valgrind has exited, from
files on disk, and nothing they add moves a recorded count. Nothing here
runs valgrind either — every measurement below is driven through a stubbed
counter, because a journey measurement is CI's work and never this box's.
"""
import copy
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
    _util,
    counter_facts,
    environment,
    journeys,
    planting,
)

# The name is spelled HERE rather than read out of the module that owns it,
# because a control that read the name would pin only that the two agree —
# which they would by construction. This is the third spelling: the reader
# owns one, the workflow sets one, and this drives both subjects with this
# one, so a rename in either of them is a red.
FLAG = 'DAEDALUS_JOURNEY_THREAD_ROWS'

ROWS_SOURCE = ROOT / 'scripts' / 'ci' / 'journey_thread_rows.py'
TESTS_YML = ROOT / '.github' / 'workflows' / 'tests.yml'


def rows_module():
    """The rows module, loaded by path like every other CI module here."""
    return _util.load(ROWS_SOURCE, 'journey_thread_rows_contract')


def _profile(main, imported=3_000, served=5_000, request=2_000):
    """One profile's rows, through the shared two-process fixture."""
    return _journey_contract.bridge_profile(
        main=main, request=request, imported=imported, served=served)


def _measured(rows):
    return {'rows': rows, 'unread': None}


def _stubbed(main_by_journey, startup=7_000, bridge=100_000):
    """A callgrind stand-in that hands back a profile for every journey.

    The counts are the fixture's, not a journey's: `journey_kept` is a
    number this shape makes positive for every journey in the set, so a
    refused residual in a test below is a number that test chose rather than
    one the profile happened to produce.
    """
    def counting(name, root, workdir):
        del root, workdir
        if name == 'startup-only':
            return _measured(_profile(startup, imported=0, served=0,
                                      request=0)), None
        if name == 'bridge-only':
            return _measured(_profile(bridge)), None
        return _measured(_profile(main_by_journey[name])), None
    return counting


def _even_counts(value=200_000):
    return {name: value for name in journeys().NAMES}


def _run_measure(value, counting, rounds=1, shapes=None):
    """One `measure()` over a stubbed counter, with the flag as `value`.

    `value` of `None` removes the variable, which is a state a setting has
    and a dict that only ever assigns cannot express.
    """
    counters = _journey_contract.counters()
    shape = shapes or (lambda names, root, r: (
        {name: ['sha'] * r for name in names}, None))
    with environment(FLAG, value), planting(
            counters,
            shapes=shape,
            COUNTERS=('valgrind-callgrind',),
            COUNTERS_BY_NAME={'valgrind-callgrind': (counting, True)}):
        return counters.measure(root=ROOT, rounds=rounds,
                                found=counter_facts())


def test_the_rows_are_recorded_only_while_the_flag_is_on(tmp):
    """The key is ABSENT when the flag is off — not null, not empty.

    Absent is the state every existing consumer of a counter row already
    ignores, and `null` is not: `counts_of` reads every entry's `median`,
    so a reader reaching `.get` through a null is a shape this report has
    never had.
    """
    del tmp
    counting = _stubbed(_even_counts())
    off = _run_measure(None, counting)
    on = _run_measure('1', counting)
    assert 'thread_rows' not in off['counters']['valgrind-callgrind'], (
        'the rows key must be absent while the flag is unset: '
        f"{sorted(off['counters']['valgrind-callgrind'])}")
    assert 'thread_rows' in on['counters']['valgrind-callgrind'], (
        'the rows are what this flag exists for, and a run with it on that '
        'recorded none is a run that measured and threw the data away: '
        f"{sorted(on['counters']['valgrind-callgrind'])}")


def test_the_samples_are_not_retained_while_the_flag_is_off(tmp):
    """A run told to throw the rows away must not gather them first.

    Every round's own profile is retained in the parent to build the rows,
    so an ungated `samples` holds a profile for every thread of every
    journey of every round for a key this run then never emits. What that
    structure held at the moment the report consulted it is what a spy
    sees, so this reads the retention rather than the output: a report with
    no `thread_rows` key cannot tell a builder that never filled one from
    one that filled it and dropped it.

    The gate is `journey_thread_rows.enabled()` — the same reader that
    decides whether the key is emitted — so there is one owner of the
    switch's name and its meaning and no second spelling of either.
    """
    del tmp
    counters = _journey_contract.counters()
    rows = counters.journey_thread_rows
    real = rows.for_counter
    retained = []

    def watching(bridge, samples):
        retained.append(samples)
        return real(bridge, samples)

    counting = _stubbed(_even_counts())
    with planting(rows, for_counter=watching):
        _run_measure(None, counting)
        _run_measure('1', counting)
    assert len(retained) == 2, (
        'both runs must reach the rows gate for this to mean anything: '
        f'{retained}')
    off = retained[0]
    on = retained[1]
    assert off is None, (
        'every round of every journey was retained for a key this run '
        f'never emits: {sorted(off or ())}')
    assert sorted(on) == sorted(journeys().NAMES), sorted(on)
    assert all(len(taken) == 1 for taken in on.values()), on


def test_the_flag_is_the_exact_string_one(tmp):
    """`== '1'` and nothing else, which is the whole of the convention.

    A truthy reader turns the flag ON by a workflow that set it to `0` to
    turn it off, and the recorded artefact grows by every thread of every
    profile — silently, and in the one place a reader is not looking for it.
    """
    del tmp
    rows = rows_module()
    for value, expected in (('1', True), ('0', False), ('', False),
                            ('true', False), ('yes', False), ('on', False),
                            (' 1', False), ('1 ', False), ('01', False)):
        with environment(FLAG, value):
            assert rows.enabled() is expected, (value, rows.enabled())
    with environment(FLAG, None):
        assert rows.enabled() is False


def test_a_row_names_its_thread_its_role_and_its_total_and_nothing_else(tmp):
    """Four keys, and a row that JSON can carry.

    `read()` hands back a `frozenset` of declared symbols per thread, and
    `cmd` is a whole command line. Neither belongs in an uploaded artefact:
    the first is not a JSON type and the second is a page per thread of a
    command nobody reads twice.
    """
    del tmp
    rows = rows_module()
    kept, why = rows.kept_rows(
        _measured(_profile(100_000)), 'mcp-exec')
    assert why is None, why
    assert kept, kept
    for row in kept:
        assert set(row) == {'pid', 'thread', 'role', 'ir'}, row
    json.dumps(kept)


def test_rows_are_sorted_by_pid_then_thread_and_carry_no_command_line(tmp):
    """A stable order, so two artefacts can be diffed by a reader.

    The profile is handed over with the LATER process first, which is the
    order a reader that kept whatever `read` returned would carry. `cmd` is
    asserted absent by name: it is in `read()`'s row, so a builder that
    copied the row and edited it would have carried it.
    """
    del tmp
    rows = rows_module()
    # No import thread, so this journey's exclusion list drops nothing and
    # the only thing the order assertion can be about is the order. The
    # SECOND process is declared first, which is what a builder that kept
    # whatever order it was handed would carry.
    profile = _profile(100_000, imported=0)
    out_of_order = [dict(row, pid=9) for row in profile] + profile
    kept, why = rows.kept_rows(_measured(out_of_order), 'mcp-exec')
    assert why is None, why
    assert len(kept) == len(out_of_order), kept
    assert [(row['pid'], row['thread']) for row in kept] == sorted(
        (row['pid'], row['thread']) for row in kept), kept
    assert 'cmd' not in json.dumps(kept), kept


def test_a_measurement_the_classifier_refuses_carries_no_rows(tmp):
    """No rows rather than rows with a guessed role.

    `classify` refuses a thread below the floor an interpreter thread
    starts at, and that refusal is load-bearing for the COUNT — the sum is
    not taken either. The rows are read through the same classifier, so the
    same refusal has to reach them: a row whose role was guessed is a
    number a reader would compare against the next run's as though it were
    measured.
    """
    del tmp
    rows = rows_module()
    floor = _journey_contract.threads().REQUEST_FROM
    thin = _profile(100_000)
    thin[0] = dict(thin[0], ir=floor - 1)
    kept, why = rows.kept_rows(_measured(thin), 'mcp-exec')
    assert kept is None, kept
    assert why is not None and str(floor - 1) in why, why

    # And a journey whose samples cannot all be read carries NO entry
    # rather than one of them: a `rounds` list shorter than the run is a
    # reader comparing the wrong two samples.
    whole, round_why = rows.journey_rows(
        'mcp-exec', _measured(_profile(100_000)),
        [_measured(_profile(200_000)), _measured(thin)])
    assert whole is None, whole
    assert round_why is not None, round_why


def test_a_baseline_the_classifier_refuses_carries_no_entry(tmp):
    """The BASELINE's refusal drops the journey, exactly as a round's does.

    `kept_rows` is asked about the baseline and about every round, and only
    the round refusal had a case. A guard that fires for one of the two and
    not the other hands back an entry whose `baseline` is null -- a
    half-measured journey presented as a whole one, which is what
    `journey_rows`'s own docstring forbids: a reader comparing it with the
    next run's is comparing a sample against nothing.

    The refusal is built the way the round case builds it -- one thread of
    the profile dropped below the interpreter's floor -- because a profile
    is a profile and the classifier does not know which of the two callers
    is asking.
    """
    del tmp
    rows = rows_module()
    names = journeys().NAMES
    floor = _journey_contract.threads().REQUEST_FROM
    sample = _measured(_profile(200_000))
    thin = _profile(100_000)
    thin[0] = dict(thin[0], ir=floor - 1)

    whole, why = rows.journey_rows(names[0], _measured(thin), [sample])
    assert whole is None, whole
    assert why is not None and str(floor - 1) in why, why

    # One baseline serves the whole counter, so every journey in this
    # mapping is refused with it and the mapping itself is the assertion:
    # an entry per journey with a null `baseline` is what a guard that
    # fired for rounds only would hand back here.
    samples = {name: [sample] for name in names[:2]}
    with environment(FLAG, '1'):
        readable = rows.for_counter(_measured(_profile(100_000)), samples)
        entries = rows.for_counter(_measured(thin), samples)
    assert readable is not None and set(readable) == set(samples), (
        'the fixture must reach the rows for the refusal to mean anything: '
        f'{readable}')
    assert entries is None, (
        f'a baseline the classifier refused drops the entry whole: {entries}')


def test_a_journey_whose_count_was_refused_still_carries_its_rows(tmp):
    """A refused count is exactly where a reader wants the rows.

    The refusal is a negative residual, not a failed measurement: the
    profile is on disk and every thread in it was read. The rows are
    diagnostic, so the journey stays in the mapping — the journey missing
    is the reader concluding there is nothing to look at.
    """
    del tmp
    counts = _even_counts()
    counts['segment-relay'] = 50_000
    report = _run_measure('1', _stubbed(counts))
    row = report['counters']['valgrind-callgrind']
    assert 'segment-relay' in row['refused'], sorted(row['refused'])
    assert 'segment-relay' in row['thread_rows'], sorted(row['thread_rows'])


def test_a_journey_whose_rounds_disagree_still_carries_its_rows(tmp):
    """One row set per round, whatever the rounds agreed on.

    The rendering sha is what `shapes` settled, and a journey whose three
    rounds rendered differently is a measurement whose three samples are
    not comparable — which is the second thing a reader comes here for.
    Dropping the journey because its shas disagree would drop exactly the
    measurement that needs reading.
    """
    del tmp

    def disagreeing(names, root, rounds):
        del root
        return ({name: [f'{name}-{index}' for index in range(rounds)]
                 for name in names}, None)
    report = _run_measure('1', _stubbed(_even_counts()), rounds=2,
                          shapes=disagreeing)
    assert report['shas']['segment-relay'] == ['segment-relay-0',
                                               'segment-relay-1'], (
        f"the fixture must disagree for this to mean anything: "
        f"{report['shas']['segment-relay']}")
    row = report['counters']['valgrind-callgrind']
    entry = row['thread_rows']['segment-relay']
    assert len(entry['rounds']) == 2, entry
    assert entry['baseline'] is not None, entry


def test_a_journey_that_never_ran_carries_no_entry(tmp):
    """Only a journey the run measured gets a key.

    A journey absent from the samples has no profile on disk, and an entry
    built for it would be an empty list a reader reads as a measurement of
    nothing — which is the one thing these rows must never be.
    """
    del tmp
    rows = rows_module()
    names = journeys().NAMES
    bridge = _measured(_profile(100_000))
    sample = _measured(_profile(200_000))
    with environment(FLAG, '1'):
        entries = rows.for_counter(bridge, {names[0]: [sample],
                                            names[1]: [sample]})
    assert set(entries) == {names[0], names[1]}, sorted(entries)


def test_a_counter_that_hands_back_a_number_separates_no_threads(tmp):
    """`syscalls` (and perf) report one number and get no rows.

    The distinction is the measurement's own shape rather than a name in a
    list: a counter that counts a process tree whole hands back a number,
    and there is nothing under it to break into threads. The row it
    produces therefore carries no key at all — and that number IS the count
    the row carries, so the arm that hands it back reads it rather than
    building one out of it.
    """
    del tmp
    counters = _journey_contract.counters()
    ran = {}

    def answering(name, root, workdir):
        del root, workdir
        # A count that rises, because both fixed backgrounds come off the
        # top of it, so every journey nets a positive residual.
        ran[name] = 1000 * len(ran) + 1000
        return ran[name], None

    def shape(names, root, rounds):
        del root
        return {name: ['sha'] * rounds for name in names}, None

    with environment(FLAG, '1'), planting(
            counters,
            shapes=shape,
            COUNTERS=('syscalls',),
            COUNTERS_BY_NAME={'syscalls': (answering, True)}):
        report = counters.measure(root=ROOT, rounds=1,
                                  found=counter_facts())
    assert ran, ran
    row = report['counters']['syscalls']
    assert row['available'] is True, row
    assert 'thread_rows' not in row, row
    # What the counter handed back is what the report carries, read rather
    # than rebuilt: `perf` counts a tree whole too, so this arm is reached
    # by a GATED counter and a total invented here is a recorded count that
    # is wrong, not a diagnostic number that is wrong. The two assertions
    # above survive a doubled return untouched, because a doubled total
    # only reaches `startup_only` and `bridge_only`.
    assert row['startup_only'] == ran['startup-only'], row
    assert set(row['bridge_only'].values()) == {ran['bridge-only']}, row


def test_the_rows_change_no_recorded_count(tmp):
    """The assertion that says the instrumentation measured nothing.

    Both runs are driven through the same stub, so the only thing that can
    differ is the flag; strip the rows off the run that recorded them and
    the two reports have to be the same document, byte for byte. A builder
    that subtracted, re-summed or re-ordered a round is caught here and
    nowhere else.

    The CHILD ENVIRONMENT is not caught here, and this docstring used to
    say it was: `_stubbed` replaces `COUNTERS_BY_NAME` wholesale and never
    calls `_run`, so it spawns no child and reads no child's environment.
    A builder that reached into one would be green across this control. The
    allowlist a child IS built from is pinned where that list is owned,
    `tests/test_journey_counters.py`, not here.
    """
    del tmp
    counting = _stubbed(_even_counts())
    without = copy.deepcopy(_run_measure(None, counting))
    with_rows = copy.deepcopy(_run_measure('1', counting))
    recorded = with_rows['counters']['valgrind-callgrind']
    assert recorded.pop('thread_rows'), 'this run recorded no rows'
    assert json.dumps(with_rows, sort_keys=True) == json.dumps(
        without, sort_keys=True), 'the rows moved a recorded number'


def test_the_measuring_step_sets_the_flag_and_nothing_else_does(tmp):
    """Step-level, because the step that reads it is the one that sets it.

    The switch is read only in the parent that assembles the report, from
    this step's own process environment, and no counted child consults it —
    so it is inert to a journey child whether the entry is step-level or
    job-level, and the placement is here so the value's scope sits beside
    the step that consumes it rather than beside seven steps it does not.
    """
    del tmp
    document = yaml.safe_load(TESTS_YML.read_text(encoding='utf-8'))
    job = document['jobs']['journey-budget']
    steps = [step for step in job['steps'] if step.get('id') == 'measure']
    assert len(steps) == 1, [step.get('name') for step in job['steps']]
    assert steps[0]['env'][FLAG] == '1', steps[0].get('env')
    assert FLAG not in (job.get('env') or {}), (
        f'the flag belongs to the step that reads it: {job.get("env")}')
    elsewhere = [step.get('id') or step.get('name')
                 for step in job['steps']
                 if FLAG in (step.get('env') or {})]
    assert elsewhere == ['measure'], elsewhere


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeythreadrows_')


if __name__ == '__main__':
    raise SystemExit(main())
