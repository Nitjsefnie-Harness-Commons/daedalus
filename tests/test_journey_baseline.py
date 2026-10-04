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
import json
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


def _measured_file(path, medians, nets=None, shas=None, toolchain=None,
                   selected=None, exclusions=None, planted=None):
    """One `measure --out` file, spelled for what one arm of the run varies.

    Every journey's row carries the `median` a recording reads, and the
    journeys `nets` names carry the per-round `net` lists the draws pool
    reads — rows without one are the shape a pool file has for a journey
    it could not separate, and they name no draws. `shas` and `toolchain`
    move the identity the files must agree on; `selected` and `exclusions`
    split an identity two files must share; `planted` sets a journey's row
    verbatim, which is how a pool file carries the row the residual
    builder wrote.
    """
    report = _journey_contract.fixture_report()
    rows = report['counters']['valgrind-callgrind']['journeys']
    for name, row in rows.items():
        if planted and name in planted:
            continue
        count = medians.get(name, 950)
        row.update({'min': count, 'max': count, 'median': count,
                    'spread': 0, 'raw': [count]})
        if nets and name in nets:
            row['net'] = list(nets[name])
    rows.update(planted or {})
    if selected:
        report['selected_counter'] = selected
    report['shas'].update(shas or {})
    report['toolchain'].update(toolchain or {})
    report['excluded_threads'].update(exclusions or {})
    Path(path).write_text(json.dumps(report), encoding='utf-8')
    return path


def _rebaseline(artifact, measurements, draws):
    """The real command over several files, and both of its streams.

    Every refusal `run` prints goes to stderr, so a control that quoted
    only stdout reported an empty reason for a refusal it had just caused.
    """
    argv = ['rebaseline', '--artifact', str(artifact)]
    for source in measurements:
        argv += ['--measurements', str(source)]
    for source in draws:
        argv += ['--draws', str(source)]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = _journey_contract.policy().main(argv)
    return code, out.getvalue(), err.getvalue()


def test_a_rebaseline_records_the_median_of_files_and_the_span_of_draws(tmp):
    """Recording from several files, and what a `--draws` pool re-binds.

    However many files the command line hands it, the recorded count is
    the median of the files' own medians — not the last file's, which is
    what reading `--measurements` once records. The identity the files
    must share is refused the moment it splits: a median over files whose
    rounds saw different renderings or ran on different toolchains is a
    number no run measured, and every refusal leaves the recorded budget
    exactly as it was. The pool re-binds a journey the pool names to the
    span of all its draws, in the percent the artefact denominates
    tolerances in, and leaves the journeys it does not name — and
    `tolerance_pct` — where they were.
    """
    policy = _journey_contract.policy()
    first, second = _journey_contract.journeys().NAMES[:2]
    artifact = Path(tmp) / 'journey-budget.json'
    document = _journey_contract.recorded_document(tolerances={second: 25.0})
    artifact.write_bytes(policy.render(document))

    counts = [Path(tmp) / f'counts-{index}.json' for index in (1, 2, 3)]
    _measured_file(counts[0], {first: 900})
    _measured_file(counts[1], {first: 1000})
    _measured_file(counts[2], {first: 1100})
    draws = [Path(tmp) / f'draws-{index}.json' for index in (1, 2)]
    _measured_file(draws[0], {}, nets={first: [100, 110]})
    _measured_file(draws[1], {}, nets={first: [120]})

    code, out, err = _rebaseline(artifact, counts, draws)
    assert code == 0, err
    # The run says what the pool derived and what carried: a no-op pool is
    # visible in the output instead of succeeding silently.
    assert f'derived the tolerance for {first}: 20.0' in out, out
    written = policy.load(artifact)
    assert written['journeys'][first] == 1000, written['journeys']
    assert written['journeys'][second] == 950, written['journeys']
    # (120 - 100) / 100 over the pool, in the units tolerances are
    # denominated in. The pool does not name `second`, which carries 25.0.
    assert written['tolerances'] == {first: 20.0, second: 25.0}, (
        written.get('tolerances'))
    assert written['tolerance_pct'] == 10, written['tolerance_pct']

    # What the refusals must leave: the success arm's own bytes, not the
    # artefact this run started from.
    before = artifact.read_bytes()

    elsewhere = Path(tmp) / 'sha-off.json'
    _measured_file(elsewhere, {first: 900}, shas={first: ['b' * 64]})
    code, _out, err = _rebaseline(artifact, [counts[0], elsewhere], draws)
    assert code != 0, (
        f'files whose rounds saw different renderings recorded anyway: {err}')
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')

    elsewhere = Path(tmp) / 'toolchain-off.json'
    _measured_file(elsewhere, {first: 900},
                   toolchain={'python': '3.12.0 (other) [GCC 1.0]'})
    code, _out, err = _rebaseline(artifact, [counts[0], elsewhere], draws)
    assert code != 0, (
        f'files measured on two toolchains recorded anyway: {err}')
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')


def test_files_that_disagree_on_identity_are_refused_before_any_write(tmp):
    """A counter or an exclusion map the files do not share stops the run.

    A count denominated in another counter, or taken under another
    exclusion map, is a different quantity, and a median over two of them
    is a number nothing measured.
    """
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_journey_contract.recorded_document()))
    before = artifact.read_bytes()
    agreeing = Path(tmp) / 'agreeing.json'
    _measured_file(agreeing, {})
    other = Path(tmp) / 'counter-off.json'
    _measured_file(other, {}, selected='perf-instructions')

    code, _out, err = _rebaseline(artifact, [agreeing, other], [])
    assert code != 0, err
    assert 'counter' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')

    mapped = Path(tmp) / 'exclusions-off.json'
    _measured_file(mapped, {}, exclusions={'mcp-exec': ['uvicorn-serve']})

    code, _out, err = _rebaseline(artifact, [agreeing, mapped], [])
    assert code != 0, err
    assert 'excluded' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')


def test_a_pool_of_another_counter_or_a_nonpositive_floor_is_refused(tmp):
    """The pool reads counts of the denomination the budget records.

    A draws file that selected another counter measured a different
    quantity, and a span whose floor is zero divides nothing: both refuse.
    """
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_journey_contract.recorded_document()))
    before = artifact.read_bytes()
    counts = Path(tmp) / 'counts.json'
    _measured_file(counts, {})
    elsewhere = Path(tmp) / 'draws-other-counter.json'
    _measured_file(elsewhere, {}, selected='perf-instructions')

    code, _out, err = _rebaseline(artifact, [counts], [elsewhere])
    assert code != 0, err
    assert 'draws file' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')

    floor = Path(tmp) / 'draws-zero-floor.json'
    _measured_file(floor, {}, nets={'mcp-exec': [0, 100]})

    code, _out, err = _rebaseline(artifact, [counts], [floor])
    assert code != 0, err
    assert 'mcp-exec' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')


def test_a_median_across_two_files_lands_on_the_count_it_reaches(tmp):
    """An even file count records the count its median lands on.

    Two files that measured the same journey at the same cost arrive at a
    float the schema refuses — statistics.median([950, 950]) is `950.0` —
    so an integral median is written as the integer it is. Two files whose
    medians straddle a half-instruction refuse instead, because no run
    measured the count between them.
    """
    policy = _journey_contract.policy()
    first = _journey_contract.journeys().NAMES[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_journey_contract.recorded_document()))
    counts = [Path(tmp) / f'counts-{index}.json' for index in (1, 2)]
    _measured_file(counts[0], {first: 950})
    _measured_file(counts[1], {first: 950})

    code, _out, err = _rebaseline(artifact, counts, [])
    assert code == 0, err
    written = policy.load(artifact)
    assert written['journeys'][first] == 950, written['journeys']
    assert isinstance(written['journeys'][first], int), written['journeys']

    straddling = Path(tmp) / 'counts-3.json'
    _measured_file(straddling, {first: 951})
    after_success = artifact.read_bytes()

    code, _out, err = _rebaseline(artifact, [counts[0], straddling], [])
    assert code != 0, err
    assert 'nonnegative integer' in err, err
    assert artifact.read_bytes() == after_success, (
        'a refused re-baseline wrote the artefact anyway')


def test_the_pool_reads_the_row_the_residual_builder_wrote(tmp):
    """The draws reader and the row builder share one spelling of a round.

    `journey_residual.row` builds the row a measurement carries, `net`
    included, and the pool reads `net` back out of it — so this control
    builds its pool files through the builder itself, and a rename on
    either side lands here as a refusal or a wrong bound instead of as a
    silent pool of nothing. `bridge` is nonzero on purpose, so `raw` and
    `net` differ and a reader of the wrong list computes another bound.
    """
    residual = _journey_contract.residual()
    policy = _journey_contract.policy()
    first = _journey_contract.journeys().NAMES[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_journey_contract.recorded_document()))
    counts = Path(tmp) / 'counts.json'
    _measured_file(counts, {})
    row, why = residual.row(first, [110, 120], 10)
    assert why is None, why
    another, why = residual.row(first, [130], 10)
    assert why is None, why
    assert row['net'] == [100, 110], row
    assert another['net'] == [120], another
    draws = [Path(tmp) / 'draws-1.json', Path(tmp) / 'draws-2.json']
    _measured_file(draws[0], {}, planted={first: row})
    _measured_file(draws[1], {}, planted={first: another})

    code, _out, err = _rebaseline(artifact, [counts], draws)
    assert code == 0, err
    written = policy.load(artifact)
    # (120 - 100) / 100 over the pool the builder's own `net` lists compose.
    assert written['tolerances'][first] == 20.0, written.get('tolerances')


def test_a_pool_file_must_match_the_measurements_quantity_identity(tmp):
    """Pool files measure the same quantity, not the same rendering.

    Toolchain and exclusion map decide what a count IS, so a pool file
    from another machine or under another exclusion map is refused and
    nothing is written. The render sha is deliberately not required: a
    tolerance pool spans heads by design — it is a distribution over the
    gate's draws across ordinary tree movement — so a sha check would
    empty the pool it exists to fill. A journey already carrying a bound
    the pool re-names is reported as derived, and the artefact holds the
    new value; a pool that derives nothing says so in the output instead
    of succeeding silently. A pool file that measured nothing is refused
    like a measurement file is, and the in-process caller passes real
    lists — the same call the CLI spells as repeated flags.
    """
    policy = _journey_contract.policy()
    first = _journey_contract.journeys().NAMES[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_journey_contract.recorded_document(
        tolerances={first: 25.0})))
    counts = Path(tmp) / 'counts.json'
    _measured_file(counts, {})
    draws = Path(tmp) / 'draws.json'
    _measured_file(draws, {}, nets={first: [100, 110]})

    code, out, err = _rebaseline(artifact, [counts], [draws])
    assert code == 0, err
    before = artifact.read_bytes()
    assert f'derived the tolerance for {first}: 10.0' in out, out
    written = policy.load(artifact)
    assert written['tolerances'][first] == 10.0, written.get('tolerances')

    other_machine = Path(tmp) / 'draws-other-toolchain.json'
    _measured_file(other_machine, {},
                   toolchain={'python': '3.12.0 (other) [GCC 1.0]'})
    code, _out, err = _rebaseline(artifact, [counts], [other_machine])
    assert code != 0, err
    assert 'toolchain' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')

    other_map = Path(tmp) / 'draws-other-exclusions.json'
    _measured_file(other_map, {}, exclusions={'mcp-exec': ['uvicorn-serve']})
    code, _out, err = _rebaseline(artifact, [counts], [other_map])
    assert code != 0, err
    assert 'exclusion' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')

    no_draws = Path(tmp) / 'draws-empty.json'
    _measured_file(no_draws, {})
    code, out, err = _rebaseline(artifact, [counts], [no_draws])
    assert code == 0, err
    assert 'derived no bound' in out, out

    broken = Path(tmp) / 'draws-broken.json'
    broken.write_text(json.dumps(
        {'rounds': 1, 'shape_failure': 'the segment-relay journey '
         'printed no record'}), encoding='utf-8')
    code, _out, err = _rebaseline(artifact, [counts], [broken])
    assert code == 1, err
    assert 'the segment-relay journey printed no record' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')

    rebaseline = policy.journey_rebaseline
    lists_artifact = Path(tmp) / 'journey-budget-lists.json'
    lists_artifact.write_bytes(
        policy.render(_journey_contract.recorded_document()))
    more = Path(tmp) / 'counts-more.json'
    _measured_file(more, {})
    pool = Path(tmp) / 'draws-more.json'
    _measured_file(pool, {}, nets={first: [200, 220]})
    with contextlib.redirect_stdout(io.StringIO()):
        assert rebaseline.run([str(more)], str(lists_artifact),
                              draws=[str(pool)]) == 0
    written = policy.load(lists_artifact)
    assert written['tolerances'][first] == 10.0, written.get('tolerances')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybaseline_')


if __name__ == '__main__':
    raise SystemExit(main())
