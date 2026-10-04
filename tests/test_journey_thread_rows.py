#!/usr/bin/env python3
"""Contracts for the per-thread rows a journey measurement carries.

The journey budget records ONE instruction count per journey, and two runs
of unchanged code have produced counts about seven percent apart. The
measurement already reads every thread's own total out of the callgrind
out-files and then sums them, so the data exists and is thrown away; these
rows are what it read, kept so a later run can say WHICH THREAD carries the
difference.
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
    """A callgrind stand-in that hands back a profile for every journey."""
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


def test_a_measurement_the_classifier_refuses_carries_no_rows(tmp):
    """No rows rather than rows with a guessed role."""
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

    A guard that fires for one of the two and not the other hands back an
    entry whose `baseline` is null -- a half-measured journey presented as
    a whole one.
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

    samples = {name: [sample] for name in names[:2]}
    with environment(FLAG, '1'):
        readable = rows.for_counter(_measured(_profile(100_000)), samples)
        entries = rows.for_counter(_measured(thin), samples)
    assert readable is not None and set(readable) == set(samples), (
        'the fixture must reach the rows for the refusal to mean anything: '
        f'{readable}')
    assert entries is None, (
        f'a baseline the classifier refused drops the entry whole: {entries}')


def test_a_counter_that_hands_back_a_number_separates_no_threads(tmp):
    """`syscalls` (and perf) report one number and get no rows.

    A counter that counts a process tree whole hands back a number, and
    that number IS the count the report carries: the arm that hands it back
    reads it rather than building one out of it.
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
    assert row['startup_only'] == ran['startup-only'], row
    assert set(row['bridge_only'].values()) == {ran['bridge-only']}, row


def test_the_rows_change_no_recorded_count(tmp):
    """The assertion that says the instrumentation measured nothing.

    Both runs are driven through the same stub, so the only thing that can
    differ is the flag; strip the rows off the run that recorded them and
    the two reports have to be the same document, byte for byte.
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
