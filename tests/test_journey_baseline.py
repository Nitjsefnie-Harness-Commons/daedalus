#!/usr/bin/env python3
"""What the FIXED BACKGROUND costs, and that each journey's own work is
what is left of its count.

Split out of `test_journey_counters.py`, which owns the counting these
four drive and was at its size ceiling. They came out with the concept
they are about: one bridge measured once, read once per journey through
that journey's own exclusion list, and subtracted from every raw total —
with a journey whose own work is smaller than the constant it shares
refusing rather than reporting a clamped zero.
"""
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
    _util,
    counter_facts,
    planting,
)


def _profile_counter(counters, journey_main, bridge_main, request=2_000,
                     imported=3_000, served=5_000, startup=7_000):
    """A counter that answers every name with a real classifier profile.

    The startup-only run answers with a bare number, because a counter
    that counts a process tree whole has no threads to read; every other
    run answers with rows the real `journey_threads.total_for` sorts, so
    the journey's own profile and the baseline's profile go through one
    reader rather than two that agree today.
    """
    def answering(name, root, workdir):
        del root, workdir
        if name == counters.STARTUP_NAME:
            return startup, None
        return {'rows': _journey_contract.bridge_profile(
            main=bridge_main if name == counters.BRIDGE_NAME else journey_main,
            request=request, imported=imported, served=served),
            'unread': None}, None
    return answering


def _measured(counters, answering, childed=True):
    """`measure` over one planted counter, and that counter's row."""
    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      {name: ['s'] for name in names}, None),
                  COUNTERS=('valgrind-callgrind',),
                  COUNTERS_BY_NAME={
                      'valgrind-callgrind': (answering, childed)}):
        report = counters.measure(root=ROOT, rounds=1, found=counter_facts())
    return report, report['counters']['valgrind-callgrind']


def test_the_baseline_is_read_through_each_journeys_own_exclusions(tmp):
    """One measured profile, and a different answer per journey.

    Every journey's exclusion list is its own, so the constant subtracted
    is the journey's too: `command-round-trip` keeps neither background
    thread, `mcp-exec` keeps the serve loop, and `net-capture` keeps the
    import instead. Reading the baseline once with one journey's list and
    handing that number to the rest would net a journey a thread it
    excluded, which is the defect this whole measurement is for.
    """
    del tmp
    counters = _journey_contract.counters()
    report, row = _measured(
        counters, _profile_counter(counters, 100_000, 1_000))
    assert row['bridge_only'] == {'command-round-trip': 3_000,
                                  'dashboard-fanout': 3_000,
                                  'mcp-exec': 8_000,
                                  'screenshot': 8_000,
                                  'segment-relay': 8_000,
                                  'cdp-result': 8_000,
                                  'net-capture': 6_000}, row
    assert row['startup_only'] == 7_000, row
    # 107,000 is mcp-exec's own kept total: 7,000 of startup and the
    # 8,000 of bridge it shares with the front end's import come off.
    assert row['journeys']['mcp-exec']['net'] == [92_000], row
    # The baseline is not a journey, so it is in neither recorded map: a
    # report naming it there is a check that refuses on every run.
    assert 'bridge-only' not in report['shas'], report['shas']
    assert 'bridge-only' not in report['excluded_threads']


def test_a_journey_whose_own_work_is_under_the_bridge_refuses_and_says_so(tmp):
    """A negative residual is a refusal naming both numbers, never a clamp.

    Clamping to zero would report a journey as costing nothing when what
    it measured is that its own work is smaller than the run-to-run
    wobble of the threads it shares — the exact condition the baseline
    exists to surface. The four numbers are the journey's kept total,
    the startup, the bridge total and the residual, and the sentence
    carries each of them.
    """
    del tmp
    counters = _journey_contract.counters()
    _report, row = _measured(
        counters, _profile_counter(counters, 1_000, 3_000, request=1_000,
                                   imported=3_000, served=3_000))
    assert row['available'] is False, row
    assert 'journeys' not in row, row
    why = row['why']
    assert 'command-round-trip' in why, why
    for number in ('2000', '7000', '4000', '9000'):
        assert number in why, (number, why)


def test_a_counter_that_cannot_count_a_child_subtracts_neither_baseline(tmp):
    """`childed` decides what is subtracted, exactly as it did for the
    startup baseline: a counter that cannot see the bridge child has no
    bridge-only total to take off, and reporting one would be a number
    nobody measured."""
    del tmp
    counters = _journey_contract.counters()
    _report, row = _measured(
        counters, _profile_counter(counters, 100_000, 1_000), childed=False)
    assert row['startup_only'] is None, row
    assert row['bridge_only'] is None, row
    assert row['journeys']['mcp-exec']['net'] == [107_000], row


def test_bridge_only_spawns_the_bridge_and_is_not_a_journey(tmp):
    """The baseline is a measurement, and the journey set never sees it.

    It is spawned exactly as a journey is — the same credential, the same
    temporary data root, the same `await_mcp=True` readiness — because a
    constant taken from a bridge that started differently is a different
    constant. And it is in no journey set: `journey_budget` walks
    `journey_names()`, so a name that reached it would be carried into
    every gate, the recorded maps and the artefact alike.
    """
    del tmp
    journeys = _journey_contract.journeys()
    entered = []

    @contextlib.contextmanager
    def bridge(_directory, env=None, await_mcp=False):
        entered.append((env, await_mcp))
        yield 'http://127.0.0.1:1', None

    with planting(journeys._util, bridge=bridge):
        rendering = journeys.bridge_only_rendering()
        assert journeys.main(['--journey', journeys.BRIDGE_ONLY,
                              '--root', str(ROOT)]) == 0
    assert rendering == {'journey': 'bridge-only'}, rendering
    assert entered == [({'DAEDALUS_TOKEN': journeys.BRIDGE_TOKEN,
                         'TOKEN': ''}, True)] * 2, entered
    assert journeys.BRIDGE_ONLY not in journeys.JOURNEYS
    assert journeys.BRIDGE_ONLY not in journeys.NAMES
    gate = _journey_contract.policy()
    document = gate.load(_journey_contract.ARTIFACT)
    names = gate.journey_names()
    assert gate.unrecorded(document, names) == [], (
        'a journey the artefact records nothing about is reported by every '
        f'check, and the artefact records nothing about this one: {names}')
    assert gate.stale(document, names) == [], names


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybaseline_')


if __name__ == '__main__':
    raise SystemExit(main())
