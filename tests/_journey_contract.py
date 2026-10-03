"""What the journey-budget suites share: how to load each module, the
documents they hand it, and the profile fixture they write.

A shared module rather than one copy each, for the reason every other shared
module here exists: the loaders are the same four lines, and a duplicated
loader is one that drifts from the module it names without anything
noticing. The profile fixture is here because a callgrind out file is a
journey fact rather than a counter fact. Not a suite itself —
`run_tests.py` only loads `test_*.py`.
"""
import contextlib
import io
import json

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'journey_budget.py'
ARTIFACT = ROOT / '.github' / 'journey-budget.json'

# Fields a rendered journey must never carry, whatever the round: each is
# minted per run or per store, and a rendering holding one has no stable sha.
# The command id and the `ts` the journey posts itself are fixed inputs, so
# they are absent here and pinned by value in the test that reads them back.
PER_RUN = ('did', '_did', 'deliveryId', 'resultGeneration', 'roundtrip_ms',
           'age')


# A recorded identity: any toolchain the schema accepts, used where the
# test is about something else and needs a toolchain that MATCHES.
IDENTITY = {'python': '3.13.15 (main, Aug  6 2026, 02:15:18) [GCC 13.3.0]',
            'valgrind_version': 'valgrind-3.24.0',
            'runner_image': 'ubuntu24 20260801.1.0'}


def policy():
    return _util.load(POLICY_SOURCE, 'journey_budget_contract')


def threads():
    """The thread classifier, loaded the way the policy module loads it."""
    source = ROOT / 'scripts' / 'ci' / 'journey_threads.py'
    return _util.load(source, 'journey_threads_contract')


def counters():
    """The counter module, loaded the way the policy module loads it."""
    source = ROOT / 'scripts' / 'ci' / 'journey_counters.py'
    return _util.load(source, 'journey_counters_contract')


def summaries():
    """The step-summary module, loaded the way the policy module loads it.

    Not named `report`: another tests module already owns that name, and a
    shared helper may not re-implement a name its tree has an owner for.
    """
    source = ROOT / 'scripts' / 'ci' / 'journey_report.py'
    return _util.load(source, 'journey_report_contract')


def journeys():
    sys.path.insert(0, str(ROOT / 'tests'))
    try:
        import _journeys  # the module under test, by its own name
    finally:
        sys.path.pop(0)
    return _journeys


def callgrind_profile(directory, name, slot, thread, ir, signature=(),
                      pid=4242, cmd=None):
    """The out-file valgrind leaves for one thread, in the shape the
    reader that sums it expects.

    `signature` writes the `fn=` lines the thread's role is read from, so a
    fixture can say a background thread ran a loop or executed module
    bodies instead of only how big it was. `cmd` is the command line of the
    PROCESS the thread belongs to, because that is what the classifier reads
    first; it defaults to the harness child, which is what most of them are.
    It lives here because the suite that grew it is at the size ceiling and
    the profile is a journey fact rather than a counter fact.
    """
    path = Path(directory) / f'callgrind.{name}.{slot}'
    body = ''.join(f'fn=({index}) {symbol}\n1 12\n'
                   for index, symbol in enumerate(signature, start=1))
    cmd = cmd or f'python3 tests/_journeys.py --journey {name}'
    path.write_text(
        f'version: 1\ncreator: callgrind-3.24.0\npid: {pid}\npart: 1\n'
        f'cmd: {cmd}\n'
        f'events: Ir\nthread: {thread}\n{body}1 {ir}\n\nsummary: {ir}\n',
        encoding='utf-8')
    return path


# The two command lines a counter measures, spelled the way a real profile
# spells them: the harness child it launched, and the bridge that child
# started. The classifier reads the PROCESS before it reads any symbol, so a
# fixture that gives every row one cmd is a fixture for a different gate.
HARNESS_CMD = 'python3 tests/_journeys.py --journey mcp-exec --root .'
BRIDGE_CMD = 'python3 server.py'


def bridge_profile(main, request=0, imported=0, served=0):
    """The classifier's own rows for a two-process profile, one per role.

    The counterpart to `callgrind_profile`, which writes the FILES a real
    profile arrives as; this hands back what `journey_threads.read` would
    have read out of them, so a control can drive the real classifier
    without a profiler. The signatures come from the classifier's own
    table rather than from symbols copied here, so a control cannot agree
    with a signature the tree changed.

    `main` and `request` are the journey's own process; `imported` and
    `served` are the bridge's. A role left at zero is thread 1's neighbour
    that never ran, and is omitted rather than written as an empty thread: a
    thread below `REQUEST_FROM` is a refusal about the SHAPE of a profile,
    and a control that wanted one would be testing something else.
    """
    policy = threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': main, 'cmd': HARNESS_CMD,
             'names': frozenset()}]
    if request:
        rows.append({'pid': 1, 'thread': 2, 'ir': request,
                     'cmd': HARNESS_CMD, 'names': frozenset()})
    for thread, (role, ir) in enumerate(
            ((policy.IMPORT, imported), (policy.SERVE, served)), start=3):
        if not ir:
            continue
        # `serve` is what no signature claims, so it is the one role with no
        # symbols to write and no table entry to read.
        rows.append({'pid': 2, 'thread': thread, 'ir': ir, 'cmd': BRIDGE_CMD,
                     'names': frozenset(policy.SIGNATURES.get(role, ()))})
    return rows


def budget_document(**overrides):
    document = {
        'schema_version': 1,
        'counter': 'perf-instructions',
        'tolerance_pct': 10,
        'journeys': {name: 1000 for name in journeys().NAMES},
    }
    document.update(overrides)
    return document


# ─── the artefact ──────────────────────────────────────────────────────────


def line_endings(data):
    """`data` with CRLF folded to LF, so a checkout's endings are not content.

    The repository has no `.gitattributes`, so a Windows runner's
    `core.autocrlf` hands this file over with CRLF while `render()` writes
    LF, and a raw comparison would fail on the line endings alone. Folding
    them leaves the invariant the test is for: the artefact's CONTENT is
    what `render()` writes for it. A hand-edited or out-of-date file still
    fails, because its content differs and its endings are not the thing
    being compared.
    """
    return data.replace(b'\r\n', b'\n')


def measurements_file(path, document, **over):
    """A measurements file whose report carries the document's identity."""
    names = journeys().NAMES
    report = {'rounds': 1, 'python': sys.version, 'shas': fixture_shas(),
              'toolchain': dict(over.get('toolchain', IDENTITY)),
              'excluded_threads': {name: list(threads().excluded_for(name))
                                   for name in names},
              'counters': {'perf-instructions': {
                  'available': True, 'startup_only': 0,
                  'journeys': {name: {'min': 5000, 'max': 5000,
                                      'median': 5000, 'spread': 0,
                                      'raw': 5000}
                               for name in names}}}}
    Path(path).write_text(json.dumps(report), encoding='utf-8')
    return path


def fixture_shas():
    """One sha per journey: the shape a real measurement reports.

    A content hash of the journey's NAME, which is as good as any other for a
    test: what is under test is that a recorded sha is compared and that a
    differing one refuses, not which bytes hash to which digest. A LIST,
    because a measurement carries one per round and the shape check reads
    them across rounds.
    """
    import hashlib
    return {name: [hashlib.sha256(name.encode('utf-8')).hexdigest()]
            for name in journeys().NAMES}


def recorded_document(**over):
    """A budget whose recorded maps all MATCH a fixture measurement.

    A check that compares anything needs every gate's recorded value
    present, because an un-recorded one is a refusal. So the document a
    comparison hands the gate is built here rather than spelled out per
    test, or a test that means to exercise the budget would silently be
    exercising the refusal.
    """
    policy = threads()
    document = budget_document(
        toolchain=dict(IDENTITY),
        excluded_threads={name: list(policy.excluded_for(name))
                          for name in journeys().NAMES},
        thread_signatures={
            role: list(names) for role, names in policy.SIGNATURES.items()},
        shas={name: seen[0]
              for name, seen in fixture_shas().items()})
    document.update(over)
    return document


def recorded_maps():
    """The three maps a measurement carries so a comparison can happen.

    The fourth gate is the SIGNATURES, and the measured side of that one is
    the classifier's own table rather than anything a report carries, so it
    is not here. A check refuses on any recorded value being un-recorded, so a
    test that means to exercise the BUDGET rather than the refusal has to
    hand the gate a measurement that says the other three.
    """
    policy = threads()
    return {'shas': fixture_shas(),
            'toolchain': dict(IDENTITY),
            'excluded_threads': {name: list(policy.excluded_for(name))
                                 for name in journeys().NAMES}}


def measured_report(counts, **maps):
    """A measurement whose median per journey is `counts`, gates all matching.

    A named journey may simply be absent from `counts`, which is what a
    runner whose counter refused one journey reports - and what a control
    about a journey with no count hands the check on purpose. Every gate's
    recorded side is carried too, or the check would refuse on the first one
    instead of reaching the budget the control is about.

    THREE rounds, not one: a tighten records nothing out of a measurement
    that took a single draw, so a fixture standing for a measurement a
    comparison may act on has to be one that took more than one.
    """
    report = {'rounds': 3, 'python': sys.version, **recorded_maps(), **maps}
    report['counters'] = {'perf-instructions': {
        'available': True, 'startup_only': 0,
        'journeys': {name: {'min': seen, 'max': seen, 'median': seen,
                            'spread': 0, 'raw': seen}
                     for name, seen in counts.items()}}}
    return report


@contextlib.contextmanager
def environment(name, value):
    """`name` set to `value` for a block, and the ambient value back after.

    `None` removes it for the block, which is a state a setting has and a
    dict that only ever assigns cannot express. The name arrives as an
    argument on purpose: a helper holding the name would be the shared
    literal a control then reads its expectation out of, so each caller
    spells the name its own subject reads and the drive proves they agree.
    """
    saved = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        os.environ.pop(name, None)
        if saved is not None:
            os.environ[name] = saved


# The variable a counted child reads its compiled helper's path from. It is
# spelled HERE as well as in each of the two modules that read it, because a
# control that read the name out of either of those would pin only that the
# two agree -- which they would, by construction. A control spells it a third
# time and drives both subjects with that spelling, so a rename in either one
# is a red.
BOUNDARY_ENV = 'DAEDALUS_CALLGRIND_BOUNDARY'


def boundary_loader(*, fails=False, **symbol):
    """The journeys module's own `CDLL` name, and its two records.

    `served` is every path a load was asked for and `zeroed` every call of
    the zero symbol; they are separate records because a control pinning
    only the load passes against a module that loads the helper and then
    never asks it to zero anything. Naming the symbol makes the stub serve
    it, and `fails` makes the call raise -- one of the ways the boundary can
    be unavailable. It is its own keyword because it is not a symbol: in the
    `**symbol` dict it read as one the stub served, and a symbol the caller
    does not name raises `AttributeError` off the returned namespace, which
    is what a shared object without it does.
    """
    served, zeroed = [], []

    def load(path):
        served.append(path)

        def zero():
            zeroed.append(path)
            if fails:
                raise OSError('the client request would not issue')

        return SimpleNamespace(**{name: zero for name in symbol})

    load.served = served
    load.zeroed = zeroed
    return load


def boundary_set(path='/cgzero.so'):
    """`BOUNDARY_ENV` pointed at a helper, for a control about what the
    counter does once it has started.

    The boundary is a PRECONDITION of the callgrind counter, so a control
    about the work it does after that has to establish it rather than read
    as the refusal the boundary control pins. Nothing here loads the path:
    whether a child can is that control's question, not this one's.
    """
    return environment(BOUNDARY_ENV, path)


class _Spawned(Exception):
    """Raised by the bridge spy the instant the harness reaches for it.

    It stops `main()` AT the spawn, so a boundary that failed to refuse
    reads as this rather than as whatever the journey body does with a
    base URL nothing ever served.
    """


def _bridge_spy(spawned):
    """`_util.bridge` as a recorder: it records that it was asked to, and
    stops the run there.

    A bare `append` standing in for it would raise `TypeError` on the
    keyword the real call passes, so a boundary that failed to refuse
    would read as a crash in the double rather than as the refusal that
    did not happen — a red naming the wrong conjunct.
    """
    def bridge(*_args, **_kwargs):
        spawned.append(True)
        raise _Spawned

    return bridge


def boundary_probe(boundary, loader, establish, call=None):
    """One counted-boundary setting, driven, and everything it produced.

    `(code, said, spawned)`: the exit code and the refusal it printed, and
    the calls the bridge spawner received. All three in one record because
    a control reading one proves only that one -- an absence assertion over
    the spawner is an assertion only once the same record shows the probe
    CAN reach it, which is what separates "nothing spawned because the
    boundary refused" from "nothing spawned because nothing ran".

    `loader` is planted as the journeys module's own `CDLL`, so the double
    is bound to the subject rather than to the stdlib module every other
    holder in the process shares. A caller that wants the REAL one passes
    the journeys module's `CDLL` itself. `call` defaults to the boundary
    itself; naming `main` is how the case that has to prove WHERE the
    boundary runs drives the real call site instead.
    """
    spawned = []
    subject = sys.modules[establish.__module__]
    with contextlib.redirect_stderr(io.StringIO()) as said:
        with contextlib.ExitStack() as stack:
            stack.enter_context(environment(BOUNDARY_ENV, boundary))
            stack.enter_context(planting(subject, CDLL=loader))
            stack.enter_context(planting(_util, bridge=_bridge_spy(spawned)))
            try:
                (call or establish)()
            except _Spawned:
                pass
            except SystemExit as refusal:
                return refusal.code, said.getvalue(), spawned
    return 0, said.getvalue(), spawned


def counted_run(counters, name, root, workdir,
                boundary: str | None = '/cgzero.so'):
    """What the callgrind counter did with the boundary set to `boundary`.

    The spawn is answered rather than performed, because a real one is a
    whole journey under valgrind and the only question here is whether the
    refusal fired before it. So the record is the refusal the counter
    reported, and the argv it reached for when it did not refuse. `None` is
    the UNSET case — the one the refusal exists for — while the default is
    a counted run that has its helper.
    """
    argv = []
    with environment(BOUNDARY_ENV, boundary), planting(
            counters,
            shutil=SimpleNamespace(which=lambda tool: f'/usr/bin/{tool}'),
            _run=lambda run: (argv.append(run), (None, '', 'answered'))[1]):
        value, why = counters._callgrind(name, root, workdir)
    return value, why, argv


@contextlib.contextmanager
def summary_file(tmp, name='step-summary.md'):
    """`GITHUB_STEP_SUMMARY` aimed at a file, and the caller's value back.

    What a reader of a run meets is the FILE, so a control reads the file
    rather than a stand-in collector: the wrong call writing the right lines
    still puts them there, and a summary written nowhere reads as a run that
    said nothing.
    """
    path = Path(tmp) / name
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    os.environ['GITHUB_STEP_SUMMARY'] = str(path)
    try:
        yield path
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved


def _report_file(tmp, toolchain_over, maps):
    """A measurements file whose report carries `maps` over a valid identity.

    The first argument overrides the identity so a test can hand the gate a
    measurement taken somewhere else; the second is the recorded maps the
    report should carry, verbatim.
    """
    report = {'rounds': 1, 'python': sys.version, 'counters': {}}
    report.update(maps)
    for field, value in toolchain_over.items():
        report[field] = value
    target = Path(tmp) / f'counts-{len(list(Path(tmp).glob("counts*")))}.json'
    target.write_text(json.dumps(report), encoding='utf-8')
    return target


def artifact():
    """The artefact's document module, loaded the way the others are."""
    source = ROOT / 'scripts' / 'ci' / 'journey_artifact.py'
    return _util.load(source, 'journey_artifact_contract')


def fixture_report():
    """A measurement shaped like a real one: a counter, a toolchain, shas."""
    shas = fixture_shas()
    names = journeys().NAMES
    return {'selected_counter': 'valgrind-callgrind',
            'toolchain': dict(IDENTITY),
            'excluded_threads': {name: list(threads().excluded_for(name))
                                 for name in names},
            'thread_signatures': {
                role: list(names)
                for role, names in threads().SIGNATURES.items()},
            'shas': shas,
            'counters': {'valgrind-callgrind': {
                'available': True, 'startup_only': 0,
                'journeys': {name: {'min': 900, 'max': 1000,
                                    'median': 950, 'spread': 100,
                                    'raw': 950}
                             for name in names}}}}


def never_recorded_gates():
    """Each recorded field, and the subject its refusal is printed under."""
    return (('toolchain', 'toolchain'),
            ('excluded_threads', 'excluded threads'),
            ('thread_signatures', 'thread signatures'),
            ('shas', 'journey shas'))


def probe():
    return {'python': '3.13.0 (main)', 'perf_event_paranoid': 4,
            'perf_path': '/usr/bin/perf',
            'perf_stat': {'event': 'instructions:u', 'returncode': 0,
                          'counts': True, 'stderr': ''},
            'valgrind_path': '/usr/bin/valgrind',
            'valgrind_version': 'valgrind-3.22',
            'strace_path': '/usr/bin/strace', 'strace_usable': True,
            'selected': 'perf-instructions'}


def counter_facts():
    """What `measure` reads off the probe, with a counter it can use."""
    found = dict(probe())
    found['perf_stat'] = {'event': 'instructions:u', 'returncode': 0,
                          'counts': False, 'stderr': 'not permitted'}
    return found


def launch_refusal(argv):
    """`str()` of the `OSError` this platform raises for an `argv` that
    cannot start, or None where the `argv` started.

    The expectation for a refusal that is the OS's own words rather than a
    sentence this repository wrote, computed by asking the platform the same
    question the module under test asks: POSIX spells a missing program
    `No such file or directory: '<program>'`, while Windows answers
    `[WinError 2] The system cannot find the file specified` and names no
    program at all -- `subprocess` reports the winerror there, never the
    `argv`, so a control that asserts the basename it launched is asserting a
    spelling only a POSIX refusal carries.

    None rather than an assertion, so the caller that launches successfully
    reads its own failure: a control with nothing to compare against must not
    pass because both sides are None.
    """
    try:
        subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as error:
        return str(error)
    return None


@contextlib.contextmanager
def planting(module, **attributes):
    """Set a loaded module's attributes for a block, and put them back.

    `setattr` rather than `module.name = ...`: the module came out of
    `_util.load`, so a type checker knows nothing about its attributes and
    the assignment is the one shape it cannot see. The restore matters as
    much as the set, because the module object outlives the test that loaded
    it.
    """
    missing = object()
    saved = {name: getattr(module, name, missing)
             for name in attributes}
    try:
        for name, value in attributes.items():
            setattr(module, name, value)
        yield module
    finally:
        for name, value in saved.items():
            if value is missing:
                delattr(module, name)
            else:
                setattr(module, name, value)


def artifact_shapes():
    """Every artefact shape that must be refused, and the words it says.

    A table rather than a test body, and beside the suites rather than in
    one: the rows are one thing with one owner, and the suite that reads
    them is at its size ceiling. Each entry is `(document, fragment)`, and
    the fragment is the exact refusal — naming it is what makes a plausible
    simplification of the validator die here instead of in a later run.
    """
    name = journeys().NAMES[0]

    def over(field, value):
        shaped = budget_document()
        shaped[field] = value
        return shaped

    def dropped(name):
        """A document that HOLDS no count for `name`, and bounds it anyway.

        `budget_of` answers None for such a journey before it reads a
        tolerance, so an entry naming one decides nothing — and a committed
        document is where a rule that decides nothing is a rule nobody
        enforces.
        """
        shaped = budget_document()
        shaped['journeys'][name] = None
        shaped['tolerances'] = {name: 1.0}
        return shaped

    return (
        ('not an object', 'must be an object'),
        (over('toolchain', []), 'toolchain must be an object'),
        (over('toolchain', {'': 'x'}), 'unknown toolchain field'),
        (over('toolchain', {'python': '  '}), 'non-empty string or null'),
        (over('excluded_threads', 'none'),
         'excluded_threads must be an object'),
        (over('excluded_threads', {'no-such-journey': ['front-end-import']}),
         'names a journey with no count'),
        (over('excluded_threads', {name: 'front-end-import'}),
         'excluded_threads names no thread'),
        (over('excluded_threads', {name: ['front-end-import',
                                          'front-end-import']}),
         'repeats a role'),
        (over('excluded_threads', {name: ['no-such-role']}),
         'unknown excluded thread role'),
        # `main` and `request` are what a journey's OWN process produces, so a
        # list naming either drops every thread of the work the count exists
        # to measure. The refusal names the journey beside the role, because
        # one list per journey is seven lists in one document.
        (over('excluded_threads', {name: ['front-end-import',
                                          'uvicorn-serve', 'request']}),
         f'a journey may not exclude its own work: request for {name}'),
        (over('excluded_threads', {name: ['main']}),
         f'a journey may not exclude its own work: main for {name}'),
        (over('thread_bands', 'big'), 'thread_bands must be an object'),
        (over('thread_bands', {'no-such-band': 1}), 'unknown thread band'),
        (over('thread_bands', {'front-end-import': 0}),
         'a thread band must be a positive integer'),
        # `main` is read from a thread's POSITION, so a threshold for it is
        # a claim about a role no total can put a thread in.
        (over('thread_bands', {'main': 1_000}),
         'a thread band cannot be a role read from position: main'),
        (over('thread_signatures', 'big'),
         'thread_signatures must be an object'),
        (over('thread_signatures', {'request': ['a_symbol']}),
         'unknown thread signature'),
        (over('thread_signatures', {'front-end-import': []}),
         'names no symbol for front-end-import'),
        (over('thread_signatures', {'front-end-import': ['a', 'a']}),
         'repeats a symbol'),
        (over('thread_signatures', {'front-end-import': ['  ']}),
         'a signature symbol is a non-empty string'),
        (over('shas', 'one'), 'shas must be an object'),
        (over('shas', {'no-such-journey': 'a' * 64}),
         'shas names a journey with no count'),
        (over('shas', {name: 'abc'}), '64 lowercase hex characters'),
        (over('shas', {name: '  '}), 'a recorded sha is a non-empty string'),
        # A PADDED sha is the one a length check that strips and a hex
        # check that does not lets through: it validates, then reads as a
        # change rather than a format refusal.
        (over('shas', {name: f'  {"a" * 64}  '}),
         '64 lowercase hex characters'),
        (over('counter', 'wall-clock'), 'unknown counter'),
        (over('tolerance_pct', -1), 'must be a nonnegative number'),
        # The three shapes `tolerance_pct` refuses, and each refusal has to
        # name the JOURNEY: a per-journey bound is seven numbers in one
        # document, so "must be a nonnegative number" without the name is a
        # question a reader cannot answer from the document.
        (over('tolerances', 'wide'), 'tolerances must be an object'),
        (over('tolerances', {'no-such-journey': 1.0}),
         'tolerances names a journey with no count'),
        (over('tolerances', {name: -0.5}),
         f'a journey tolerance must be a nonnegative number: {name} = -0.5'),
        (over('tolerances', {name: 'wide'}),
         f'a journey tolerance must be a nonnegative number: {name} = '),
        (over('tolerances', {name: True}),
         f'a journey tolerance must be a nonnegative number: {name} = True'),
        # A bound for a journey the budget does not hold reads nothing:
        # `budget_of` answers None before it reaches the tolerance.
        (dropped(name),
         'a tolerance for a journey the budget does not hold decides nothing'),
        (over('journeys', []), 'journeys must be an object'),
        (over('journeys', {name: 'many'}),
         'a recorded count must be a nonnegative integer'),
        (over('schema_version', 2), 'unsupported schema_version'),
    )


def unreadable_artifacts(tmp):
    """The two ways a file cannot be read at all, and the words for each."""
    broken = Path(tmp) / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    return ((Path(tmp) / 'absent.json', 'cannot read'),
            (broken, 'invalid journey budget JSON'))
