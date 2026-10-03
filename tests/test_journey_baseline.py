#!/usr/bin/env python3
"""What the FIXED BACKGROUND costs, and that each journey's own work is
what is left of its count.

One bridge measured once, read once per journey through that journey's own
exclusion list, and subtracted from every raw total — with a journey whose
own work is smaller than the constant it shares refusing rather than
reporting a clamped zero.
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
                     imported=3_000, served=5_000, startup=None):
    """A counter that answers every name the way the callgrind leaf does.

    Every name, `startup-only` included, gets rows the real
    `journey_threads.total_for` sorts. A double that special-cased the
    startup run to answer a bare number hid the shape the real leaf has
    always returned for that name, which is the whole of what the control
    below exists to pin. The startup child is one thread that started and
    left — it has no bridge, so there is nothing for it to exclude.

    `startup` defaults to HALF the bridge-only child's own main thread, and
    that default is the point: `startup-only` is the same interpreter
    running the same module with no bridge, so it cannot cost more than a
    child that went on to start one. A fixture that gave it more is a
    profile no counter can produce — measured on the preserved runs it kept
    1,028,521,011 instructions against the bridge-only child's
    1,176,058,614 — and an impossible profile is a subtraction nothing
    constrains, which is how subtracting the startup twice survived.
    """
    startup = bridge_main // 2 if startup is None else startup

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
    is the journey's too: the journeys that drop both background roles, the
    ones that keep the serve role beside the bootstrap, and `net-capture`,
    which keeps the bridge's own per-connection work and so drops only the
    bootstrap, read one profile to three different answers. Reading the
    baseline once with one journey's list and handing that number to the rest
    would net a journey a thread it excluded, which is the defect this whole
    measurement is for.
    """
    del tmp
    counters = _journey_contract.counters()
    report, row = _measured(
        counters, _profile_counter(counters, 1_000_000, 90_000))
    assert row['bridge_only'] == {'command-round-trip': 92_000,
                                  'dashboard-fanout': 92_000,
                                  'mcp-exec': 97_000,
                                  'screenshot': 97_000,
                                  'segment-relay': 97_000,
                                  'cdp-result': 97_000,
                                  'net-capture': 97_000}, row
    assert row['startup_only'] == 45_000, row
    # 107,000 is mcp-exec's own KEPT total — its own two threads, the
    # bridge's serve threads still in it — and the 8,000 the bridge-only
    # child keeps through mcp-exec's own list comes off it ONCE. The
    # 4,500 of the startup-only child is not subtracted as well: that child
    # is inside the 8,000's own baseline, so subtracting it again would
    # take the interpreter's start off twice.
    assert row['journeys']['mcp-exec']['raw'] == [1_007_000], row
    assert row['journeys']['mcp-exec']['net'] == [910_000], row
    # The baseline is not a journey, so it is in neither recorded map: a
    # report naming it there is a check that refuses on every run.
    assert 'bridge-only' not in report['shas'], report['shas']
    assert 'bridge-only' not in report['excluded_threads']


def test_a_baselines_refusal_is_not_swallowed_by_the_next_journey(tmp):
    """One journey's refusal is the counter's, and the next journey's
    answer must not overwrite it.

    The baseline is read once per journey through that journey's OWN
    exclusion list, so a bridge-only profile that never ran the server loop
    refuses every journey whose own list excludes one, and answers the rest.
    `command-round-trip` is first in the real set, so the loop that reads
    them stops at its refusal. Carrying on lets the next journey's `None`
    erase it, and a half-filled baseline is then subtracted from counts
    that cannot be read: a journey's measurement lost, nothing saying so.
    """
    del tmp
    counters = _journey_contract.counters()
    policy = _journey_contract.threads()
    names = _journey_contract.journeys().NAMES
    # A bridge whose front end initialised and whose server loop never ran:
    # `served` left at zero omits that thread, which is what a profile that
    # did not start one looks.
    bridge = {'rows': _journey_contract.bridge_profile(
        main=30_000, imported=3_000), 'unread': None}

    def answering(name, root, workdir):
        del root, workdir
        if name == counters.STARTUP_NAME:
            return {'rows': _journey_contract.bridge_profile(main=7_000),
                    'unread': None}, None
        if name == counters.BRIDGE_NAME:
            return bridge, None
        return {'rows': _journey_contract.bridge_profile(
            main=100_000, request=2_000, imported=3_000, served=5_000),
            'unread': None}, None

    # ONE profile answers both ways, which is what makes overwriting the
    # refusal possible at all.
    refusing = names[0]
    kept, why = counters.kept_for(bridge, refusing)
    assert kept is None and why is not None, (kept, why)
    answering_name = next(name for name in names
                          if policy.SERVE not in policy.excluded_for(name))
    _kept, why = counters.kept_for(bridge, answering_name)
    assert why is None, why

    _report, row = _measured(counters, answering)
    # Nothing measured is reported beside the refusal: a partial set of
    # counts is the shape a gate cannot compare in.
    assert row.keys() == {'available', 'why'}, row
    assert row['available'] is False, row
    assert refusing in row['why'] and policy.SERVE in row['why'], row


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
    # One journey whose own work is smaller than the bridge-only child's;
    # the rest well clear of it, so the run has both a refusal and a count
    # to show it lost neither. The shape is the one measured on real
    # profiles: `command-round-trip`'s harness child cost 1,125,085,258
    # instructions and `bridge-only`'s cost 1,176,058,614, so the baseline
    # was the larger of the two and the residual came out negative.
    tiny = names[0]

    def answering(name, root, workdir):
        del root, workdir
        if name == counters.STARTUP_NAME:
            return {'rows': _journey_contract.bridge_profile(main=7_000),
                    'unread': None}, None
        if name == counters.BRIDGE_NAME:
            main, request = 100_000, 1_000
        elif name == tiny:
            main, request = 1_000, 0
        else:
            main, request = 200_000, 1_000
        return {'rows': _journey_contract.bridge_profile(
            main=main, request=request, imported=3_000, served=3_000),
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
    # The kept total, the bridge total this journey's own exclusion list
    # leaves, and the negative residual between them. The startup-only
    # child is NOT among them: it is inside the baseline, and naming it
    # beside the total it is not subtracted from would be a sentence
    # describing the arithmetic this change removed.
    for number in ('1000', '101000', '-100000'):
        assert number in why, (number, why)
    assert 'startup-only' not in why, why


def test_a_counter_that_cannot_count_a_child_subtracts_neither_baseline(tmp):
    """`childed` decides what is subtracted, exactly as it did for the
    startup baseline: a counter that cannot see the bridge child has no
    bridge-only total to take off, and reporting one would be a number
    nobody measured."""
    del tmp
    counters = _journey_contract.counters()
    _report, row = _measured(
        counters, _profile_counter(counters, 1_000_000, 90_000),
        childed=False)
    assert row['startup_only'] is None, row
    assert row['bridge_only'] is None, row
    assert row['journeys']['mcp-exec']['net'] == [1_007_000], row


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
        # Two processes, because the classifier reads the PROCESS before it
        # reads a symbol: a fixture that gave the bridge's threads the
        # harness's command line would make them the journey's own work.
        for slot, row in enumerate(profile):
            _journey_contract.callgrind_profile(
                str(directory), name, slot, row['thread'], row['ir'],
                sorted(row['names']), pid=row['pid'], cmd=row['cmd'])
        return 0, '', ''

    with _journey_contract.boundary_set(), planting(
            counters,
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
    # Once, not twice: the bridge-only child's own 37,000 already contains
    # the interpreter start the 17,000 measured.
    assert row['journeys']['mcp-exec']['net'] == [70_000], row


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybaseline_')


if __name__ == '__main__':
    raise SystemExit(main())
