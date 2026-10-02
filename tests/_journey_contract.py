"""What the journey-budget suites share: how to load each module, and
the documents they hand it.

A shared module rather than one copy each, for the reason every other shared
module here exists: the loaders are the same four lines, and a duplicated
loader is one that drifts from the module it names without anything
noticing. Not a suite itself — `run_tests.py` only loads `test_*.py`.
"""
import contextlib
import json

import os
import subprocess
import sys
from pathlib import Path

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
        thread_bands=dict(policy.BANDS),
        shas={name: seen[0]
              for name, seen in fixture_shas().items()})
    document.update(over)
    return document


def recorded_maps():
    """The three maps a measurement carries so a comparison can happen.

    The fourth gate is the BANDS, and the measured side of that one is the
    classifier's own table rather than anything a report carries, so it is
    not here. A check refuses on any recorded value being un-recorded, so a
    test that means to exercise the BUDGET rather than the refusal has to
    hand the gate a measurement that says the other three.
    """
    policy = threads()
    return {'shas': fixture_shas(),
            'toolchain': dict(IDENTITY),
            'excluded_threads': {name: list(policy.excluded_for(name))
                                 for name in journeys().NAMES}}


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
            'thread_bands': dict(threads().BANDS),
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
            ('thread_bands', 'thread bands'),
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
        (over('thread_bands', 'big'), 'thread_bands must be an object'),
        (over('thread_bands', {'no-such-band': 1}), 'unknown thread band'),
        (over('thread_bands', {'front-end-import': 0}),
         'a thread band must be a positive integer'),
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
