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
import io
import sys
from pathlib import Path
from types import SimpleNamespace

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
    """A counter that answers every name the way the callgrind leaf does.

    Every name, `startup-only` included, gets rows the real
    `journey_threads.total_for` sorts. A double that special-cased the
    startup run to answer a bare number hid the shape the real leaf has
    always returned for that name, which is the whole of what the control
    below exists to pin. The startup child is one thread that started and
    left — it has no bridge, so there is nothing for it to exclude.
    """
    def answering(name, root, workdir):
        del root, workdir
        if name == counters.STARTUP_NAME:
            return {'rows': _journey_contract.bridge_profile(main=startup),
                    'unread': None}, None
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
    """A negative residual refuses THAT JOURNEY, and names its four numbers.

    Clamping to zero would report a journey as costing nothing when what it
    measured is that its own work is smaller than the run-to-run wobble of
    the threads it shares — the exact condition the baseline exists to
    surface. So the four numbers the sentence depends on are the journey's
    kept total, the startup, the bridge total and the residual, and it
    carries each of them.

    And it refuses ONE journey, which is the other half of this: the six
    beside it separated fine, and a counter that marked itself unavailable
    over the one that did not threw away every count in the run. The counter
    stays available, the separable journeys keep their rows, and only the
    refused one is absent from them.
    """
    del tmp
    counters = _journey_contract.counters()
    names = _journey_contract.journeys().NAMES
    # One journey under the background it shares; the rest well clear of it,
    # so the run has both a refusal and a count to show it lost neither.
    tiny = names[0]

    def answering(name, root, workdir):
        del root, workdir
        if name == counters.STARTUP_NAME:
            return {'rows': _journey_contract.bridge_profile(main=7_000),
                    'unread': None}, None
        return {'rows': _journey_contract.bridge_profile(
            main=1_000 if name == counters.BRIDGE_NAME else (
                2_000 if name == tiny else 100_000),
            request=1_000, imported=3_000, served=3_000),
            'unread': None}, None

    _report, row = _measured(counters, answering)
    assert row['available'] is True, row
    assert row['refused'].keys() == {tiny}, row['refused']
    assert tiny not in row['journeys'], row['journeys']
    assert sorted(row['journeys']) == sorted(
        name for name in names if name != tiny), row['journeys']
    assert all(value is not None
               for value in row['journeys'].values()), row['journeys']
    why = row['refused'][tiny]
    assert tiny in why, why
    # The kept total, the startup, the bridge total this journey's own
    # exclusion list leaves, and the negative residual between them.
    for number in ('3000', '7000', '2000', '6000'):
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
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            assert journeys.main(['--journey', journeys.BRIDGE_ONLY,
                                  '--root', str(ROOT)]) == 0
    assert rendering == {'journey': 'bridge-only'}, rendering
    assert entered == [({'DAEDALUS_TOKEN': journeys.BRIDGE_TOKEN,
                         'TOKEN': ''}, True)] * 2, entered
    # Nothing printed, the way `startup-only` prints nothing: this run is a
    # constant to subtract, not a journey whose rendering anyone compares.
    assert journeys.RECORD_MARKER not in printed.getvalue(), printed.getvalue()
    assert journeys.BRIDGE_ONLY not in journeys.JOURNEYS
    assert journeys.BRIDGE_ONLY not in journeys.NAMES
    gate = _journey_contract.policy()
    document = gate.load(_journey_contract.ARTIFACT)
    names = gate.journey_names()
    assert gate.unrecorded(document, names) == [], (
        'a journey the artefact records nothing about is reported by every '
        f'check, and the artefact records nothing about this one: {names}')
    assert gate.stale(document, names) == [], names


def test_the_startup_baseline_is_read_through_the_same_reader(tmp):
    """`startup-only` is measured by the REAL leaf, so its row is a number.

    The callgrind leaf answers rows for every name it is given, the startup
    baseline is measured by that same leaf, and a leaf whose answer was
    taken raw put a mapping where a count belongs. The crash that reached
    was unhandled where it landed — `journey_budget` catches no `TypeError`
    — so `measure` aborted and the gate produced no counts at all, on the
    one counter this artefact is denominated in.

    Only the launcher is planted. The leaf, `journey_threads.read`, the
    classification and the whole subtraction are the shipped code.
    """
    del tmp
    counters = _journey_contract.counters()
    asked = []

    def answering(argv):
        directory = Path(next(part for part in argv if part.startswith(
            '--callgrind-out-file=')).split('=', 1)[1]).parent
        name = argv[argv.index('--journey') + 1]
        asked.append(name)
        # Every role is present whatever the name, because a journey that
        # excludes a role no thread carries is a refusal. Only the main
        # thread's total differs: the startup child starts and leaves, and
        # the bridge child has no journey's work on top of its background.
        profile = _journey_contract.bridge_profile(
            main={counters.STARTUP_NAME: 7_000,
                  counters.BRIDGE_NAME: 30_000}.get(name, 100_000),
            request=2_000, imported=3_000, served=5_000)
        for slot, row in enumerate(profile):
            _journey_contract.callgrind_profile(
                str(directory), name, slot, row['thread'], row['ir'],
                sorted(row['names']))
        return 0, '', ''

    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      {name: ['s'] for name in names}, None),
                  shutil=SimpleNamespace(
                      which=lambda _program: '/usr/bin/valgrind'),
                  _run=answering, COUNTERS=('valgrind-callgrind',),
                  COUNTERS_BY_NAME={'valgrind-callgrind': (
                      counters._callgrind, True)}):
        report = counters.measure(root=ROOT, rounds=1, found=counter_facts())
    row = report['counters']['valgrind-callgrind']
    assert row['available'] is True, row
    assert asked[:2] == [counters.STARTUP_NAME, counters.BRIDGE_NAME], asked
    # The WHOLE profile, 7,000 on the main thread and 10,000 on the three
    # beside it: `startup-only` excludes no role, so every thread that
    # child ran is its own cost. A count taken raw from the leaf would be
    # the mapping, and a reader that kept only the main thread would say
    # 7,000.
    assert row['startup_only'] == 17_000, row
    assert row['bridge_only']['mcp-exec'] == 37_000, row
    assert row['journeys']['mcp-exec']['net'] == [53_000], row


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybaseline_')


if __name__ == '__main__':
    raise SystemExit(main())
