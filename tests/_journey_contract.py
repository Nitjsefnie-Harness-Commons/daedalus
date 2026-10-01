"""What the three journey-budget suites share: how to load each module, and
the documents they hand it.

A shared module rather than three copies, for the reason every other shared
module here exists: the loaders are the same four lines, and a duplicated
loader is one that drifts from the module it names without anything
noticing. Not a suite itself — `run_tests.py` only loads `test_*.py`.
"""
import contextlib
import json
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
    report = {'rounds': 1, 'python': sys.version, 'shas': {},
              'toolchain': dict(over.get('toolchain', IDENTITY)),
              'counters': {'perf-instructions': {
                  'available': True, 'startup_only': 0,
                  'journeys': {name: {'min': 5000, 'max': 5000,
                                      'median': 5000, 'spread': 0,
                                      'raw': 5000}
                               for name in names}}}}
    Path(path).write_text(json.dumps(report), encoding='utf-8')
    return path


def probe():
    return {'python': '3.13.0 (main)', 'perf_event_paranoid': 4,
            'perf_path': '/usr/bin/perf',
            'perf_stat': {'event': 'instructions:u', 'returncode': 0,
                          'counts': True, 'stderr': ''},
            'valgrind_path': '/usr/bin/valgrind',
            'valgrind_version': 'valgrind-3.22',
            'callgrind_control_path': None, 'strace_path': '/usr/bin/strace',
            'strace_usable': True, 'callgrind_control_version': None,
            'callgrind_control_usable': False,
            'selected': 'perf-instructions'}


def counter_facts():
    """What `measure` reads off the probe, with a counter it can use."""
    found = dict(probe())
    found['perf_stat'] = {'event': 'instructions:u', 'returncode': 0,
                          'counts': False, 'stderr': 'not permitted'}
    return found


@contextlib.contextmanager
def planting(module, **attributes):
    """Set a loaded module's attributes for a block, and put them back.

    `setattr` rather than `module.name = ...`, because the module came out
    of `_util.load` and a type checker knows nothing about its attributes:
    the assignment is exactly the shape it cannot see, and a reader of the
    test is no better off. This is the planting idiom `tests/_cli_dispatch`
    already uses, and the restore matters as much as the set — a module
    object outlives the test that loaded it.
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
