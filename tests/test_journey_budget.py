#!/usr/bin/env python3
"""Contracts for the journey-cost ratchet's POLICY: what is recorded,
which way a recorded number may move, and what a refusal says.
Nothing here asserts a wall-clock margin or a timing bound, because
a count is a number this repository must not write down: the runner
it was measured on is not the runner the next run lands on."""
import io
import contextlib
import subprocess
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ARTIFACT,
    PER_RUN,
    ROOT,
    _util,
    budget_document,
    journeys,
    line_endings,
    measurements_file,
)


def test_the_committed_artefact_is_the_canonical_rendering(tmp):
    del tmp
    policy = _journey_contract.policy()
    document = policy.load(ARTIFACT)
    canonical = policy.render(document)
    committed = line_endings(ARTIFACT.read_bytes())
    assert canonical == committed, (
        'the committed artefact is not what render() writes for it, so the '
        'file is hand-edited or out of date')
    # The control that keeps the folding above from making this vacuous: a
    # CRLF checkout of the same content passes, and an edit to a count does
    # not. Without both, "compare the folded bytes" could be satisfied by
    # folding everything until nothing differed.
    assert line_endings(canonical.replace(b'\n', b'\r\n')) == canonical
    assert line_endings(committed.replace(b'"mcp-exec"', b'"mcp-exec "')) \
        != canonical


def test_the_artefact_names_exactly_the_journeys_that_exist(tmp):
    del tmp
    policy = _journey_contract.policy()
    document = policy.load(ARTIFACT)
    names = journeys().NAMES
    assert sorted(document['journeys']) == sorted(names), (
        'the budget and the journey set disagree: '
        f'{sorted(document["journeys"])} against {sorted(names)}')
    assert policy.stale(document, names) == []
    # The budget is recorded whole or not at all. A journey left carrying
    # no count beside others that carry one is the `unmeasured` false green
    # reached by omission rather than by a runner that could not count: the
    # gate compares the recorded ones and says nothing about the rest.
    missing = policy.unrecorded(document, names)
    if missing:
        assert len(missing) == len(names), (
            f'the budget is half recorded — {len(names) - len(missing)} of '
            f'{len(names)} journeys carry a count and these carry none, so '
            f'the gate never compares them: {missing}')
    else:
        unmeasured = [name for name in names
                      if not isinstance(document['journeys'][name], int)
                      or isinstance(document['journeys'][name], bool)
                      or document['journeys'][name] <= 0]
        assert not unmeasured, (
            f'a recorded count is not a positive integer: {unmeasured}')


def test_the_artefact_is_its_own_file_and_a_tracked_one(tmp):
    del tmp
    policy = _journey_contract.policy()
    assert policy.ARTIFACT == ARTIFACT, policy.ARTIFACT
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '--error-unmatch',
         '.github/journey-budget.json'],
        capture_output=True, text=True, check=False)
    assert listed.returncode == 0, (
        'the artefact is not tracked, so a deny-by-default ignore file is '
        f'holding it out: {listed.stderr.strip()}')
    shared = json.loads(
        (ROOT / '.github' / 'ci-thresholds.json').read_text(encoding='utf-8'))
    assert not [key for key in shared if 'journey' in key], (
        'a journey baseline reached the closed thresholds document, whose '
        'normalise() refuses any field it does not own')


def test_the_artefact_records_the_threads_each_journey_excludes(tmp):
    """A count that drops a background thread means something the count
    alone cannot say, so the roles are recorded beside it.

    The shape is a per-journey map of role names, and every wrong shape is
    refused rather than coerced: a journey excluded nothing, a role the
    profiler never produces, and a repeated role are three different ways
    for the field to say something the measurement did not do.
    """
    del tmp
    policy = _journey_contract.policy()
    document = budget_document()
    good = {'command-round-trip': ['front-end-import', 'uvicorn-serve'],
            'dashboard-fanout': ['front-end-import', 'uvicorn-serve'],
            'mcp-exec': ['front-end-import']}
    document['excluded_threads'] = good
    assert policy._validated(document) is document
    for bad in ([], 'front-end-import', {'mcp-exec': []},
                {'mcp-exec': ['no-such-thread']},
                {'mcp-exec': ['front-end-import', 'front-end-import']},
                {'no-such-journey': ['front-end-import']}):
        document = budget_document()
        document['excluded_threads'] = bad
        try:
            policy._validated(document)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted excluded_threads {bad!r}')


def test_a_count_measured_over_different_threads_is_not_compared(tmp):
    """The same rule as a toolchain that moved, for the same reason.

    Two counts that differ only in which threads were dropped out are two
    quantities; comparing them would measure the difference between them
    rather than the code.
    """
    del tmp
    policy = _journey_contract.policy()
    applied = {'command-round-trip': ['front-end-import', 'uvicorn-serve'],
               'dashboard-fanout': ['front-end-import', 'uvicorn-serve'],
               'mcp-exec': ['front-end-import']}
    assert policy.exclusion_diff(applied, applied) == {}
    assert policy.exclusion_diff(applied, None) != {}
    # Order is not meaning: the same roles in another order are the same gate.
    reversed_roles = {name: list(reversed(roles))
                      for name, roles in applied.items()}
    assert policy.exclusion_diff(applied, reversed_roles) == {}
    moved = dict(applied, **{'mcp-exec': ['front-end-import',
                                          'uvicorn-serve']})
    differs = policy.exclusion_diff(applied, moved)
    assert set(differs) == {'mcp-exec'}, differs
    assert differs['mcp-exec'][0] == ['front-end-import']


def test_the_artefact_schema_is_closed(tmp):
    del tmp
    policy = _journey_contract.policy()
    # 'wall-clock' is a quantity the ratchet does not defend at all, and
    # 'syscalls' is one the counters module measures and never gates on. A
    # recorded count denominated in either would be a number nothing holds.
    for over in ({'unknown': 1}, {'counter': 'wall-clock'},
                 {'counter': 'syscalls'},
                 {'tolerance_pct': -1}, {'journeys': []},
                 {'schema_version': 2},
                 {'journeys': {'command-round-trip': -5}},
                 {'journeys': {'command-round-trip': 'many'}}):
        document = budget_document()
        document.update(over)
        try:
            policy._validated(document)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted {sorted(over)}')


# ─── the toolchain a count is only comparable on ───────────────────────────

# One RECORDED toolchain: the three strings a baseline is stamped with, so
# these tests compare a count against an identity rather than against
# whatever the runner happens to be today.
RECORDED = {'python': '3.13.15 (main, Aug  6 2026, 02:15:18) [GCC 13.3.0]',
            'valgrind_version': 'valgrind-3.24.0',
            'runner_image': 'ubuntu24 20260801.1.0'}


def test_the_artefact_carries_the_toolchain_a_count_depends_on(tmp):
    """The identity is present, complete, and exactly the three things.

    The shape is the invariant, not the emptiness. A block with a field
    missing is a defect that makes a moved toolchain read as a match on
    that field, so each must be a non-empty string and the set must be
    exactly what a count depends on and no code change controls.
    """
    del tmp
    policy = _journey_contract.policy()
    document = policy.load(ARTIFACT)
    recorded = document['toolchain']
    assert sorted(recorded) == sorted(policy.journey_counters
                                      .TOOLCHAIN_FIELDS), recorded
    present = {field: seen for field, seen in recorded.items() if seen}
    if present:
        # Recorded whole or not at all: a field left null beside fields
        # that are recorded compares unequal to every measured value, so a
        # toolchain that moved on it would read as a match.
        assert sorted(present) == sorted(recorded), (
            'the identity is half recorded, and a null field beside recorded '
            'ones reads as a match on every value: '
            f'{sorted(set(recorded) - set(present))}')
        for field, seen in sorted(present.items()):
            assert isinstance(seen, str) and seen.strip(), (
                f'the recorded {field} is not a non-empty string, so a '
                f'toolchain that moved on it would read as a match: {seen!r}')
    for over in ({'toolchain': []}, {'toolchain': {'cpython': '3.13'}},
                 {'toolchain': {'python': 31315}},
                 {'toolchain': {'python': '', 'valgrind_version': 'v',
                                'runner_image': 'i'}}):
        bad = budget_document()
        bad.update(over)
        try:
            policy._validated(bad)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted {over}')


def test_a_toolchain_change_is_not_a_regression_and_says_the_words(tmp):
    """A moved toolchain is not a moved count, and never a silent pass.

    Callgrind's count is deterministic only for a fixed binary, so a
    runner image that ships a different CPython patch build moves every
    number with no change to this repository. Refusing that as a
    regression would be a false red with nothing to fix; passing it
    without saying so would be a green that measured nothing.
    """
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(budget_document(
        toolchain=dict(RECORDED))))
    measurements = measurements_file(
        Path(tmp) / 'counts.json', None,
        toolchain=dict(RECORDED, valgrind_version='valgrind-3.25.0'))
    # stdout, not stderr: this is a report, not a refusal. The two are
    # separated deliberately — a reader must be able to tell an outcome
    # that succeeded from one that failed without reading an exit code.
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 0, spoken.getvalue()
    said = spoken.getvalue()
    assert 'toolchain changed, re-baseline' in said, said
    assert 'no count was compared' in said, said
    assert 'valgrind-3.24.0' in said and 'valgrind-3.25.0' in said, said
    # The table the summary carries, which is what says no count was
    # compared: the journey's own recorded and measured numbers, neither
    # of which was compared against the other.
    report_module = _util.load(ROOT / 'scripts' / 'ci' / 'journey_report.py',
                               'journey_report_contract')
    summary = report_module.toolchain_lines(
        policy.load(artifact), json.loads(measurements.read_text()),
        {'valgrind_version': ('valgrind-3.24.0', 'valgrind-3.25.0')},
        policy.TOOLCHAIN_REMEDY)
    joined = '\n'.join(summary)
    assert '**toolchain changed, re-baseline.**' in joined, joined
    assert 'No count was compared.' in joined, joined
    assert 'What would have been compared:' in joined, joined
    assert policy.TOOLCHAIN_REMEDY in joined, joined


def test_an_identical_toolchain_still_refuses_a_count_over_budget(tmp):
    """The outcome is the toolchain's, and it does not swallow the gate."""
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(budget_document(
        toolchain=dict(RECORDED))))
    measurements = measurements_file(Path(tmp) / 'counts.json', None)
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 1, 'a count over budget passed on a matching toolchain'
    assert policy.OVER_REMEDY in spoken.getvalue(), spoken.getvalue()
    assert policy.toolchain_diff(RECORDED, RECORDED) == {}


def test_no_workflow_step_writes_the_artefact(tmp):
    """A re-baseline is a reviewed commit, so nothing in CI may write it."""
    del tmp
    artefact = '.github/journey-budget.json'
    workflows = ROOT / '.github' / 'workflows'
    named = []
    for path in sorted(workflows.glob('*.y*ml')):
        for number, line in enumerate(path.read_text(
                encoding='utf-8').splitlines(), 1):
            if artefact in line:
                named.append((path.name, number, line.strip()))
    assert all(line.strip() == f"- '{artefact}'"
               for _name, _number, line in named), (
        'a workflow names the artefact outside a paths-ignore entry, so a '
        f'step reads or writes it: {named}')


# ─── which way a recorded number may move ──────────────────────────────────


def test_tightened_follows_a_cheaper_journey_down(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    counts = {name: 900 for name in names}
    assert policy.tightened(counts, document, names) == {
        name: 900 for name in names}


def test_tightening_never_raises_and_never_adds_a_journey(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    assert policy.tightened(
        {name: 1000 for name in names}, document, names) is None
    assert policy.tightened(
        {name: 1001 for name in names}, document, names) is None
    assert policy.tightened(
        dict({name: 1000 for name in names}, invented=10),
        document, names) is None


def test_tightening_drops_a_journey_the_set_no_longer_has(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    document['journeys']['a-journey-nobody-runs'] = 5000
    assert policy.tightened(
        {name: 1000 for name in names}, document, names) == {
            name: 1000 for name in names}


def test_the_tighten_command_is_the_one_the_implementation_uses(tmp):
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    payload = json.dumps(budget_document()).encode('utf-8')
    artifact.write_bytes(payload)
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps({
        'rounds': 1, 'python': sys.version, 'shas': {},
        'counters': {'perf-instructions': {
            'available': True, 'startup_only': 0,
            'journeys': {name: {'min': 800, 'max': 800, 'median': 800,
                                'spread': 0, 'raw': 800}
                         for name in names}}}}, ), encoding='utf-8')
    assert policy.main(['check', '--artifact', str(artifact),
                        '--measurements', str(measurements),
                        '--tighten']) == 0
    written = json.loads(artifact.read_text(encoding='utf-8'))
    assert written['journeys'] == {name: 800 for name in names}, written


# ─── what a refusal says ───────────────────────────────────────────────────


def test_a_count_over_budget_is_a_violation_carrying_its_remedy(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    counts = {name: 1000 for name in names}
    shapes = {name: ['a' * 64] for name in names}
    assert not any(policy.violations(
        counts, shapes, document, names).values())
    over = dict(counts, **{names[0]: 1200})
    found = policy.violations(over, shapes, document, names)
    assert sorted(found) == ['over', 'shape', 'unmeasured']
    assert found['over'] == {names[0]: (1200, 1100.0)}, found['over']
    assert policy.REMEDY_FOR['over'] == policy.OVER_REMEDY
    assert 'never raised by hand' in policy.REMEDY_FOR['over']


def test_an_unrecorded_journey_is_reported_and_never_a_violation(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document(journeys={
        names[0]: 1000, names[1]: None, names[2]: None})
    measured = {names[0]: 1000, names[1]: 10 ** 9, names[2]: 10 ** 9}
    found = policy.violations(measured,
                              {name: ['a' * 64] for name in names},
                              document, names)
    assert not found['over'], found
    assert not found['shape'], found
    assert not found['unmeasured'], found
    assert policy.unrecorded(document, names) == sorted(names[1:])


def test_a_counter_this_runner_refuses_is_a_violation_not_a_pass(tmp):
    """A green that measured nothing is the false green this exists against.

    The artefact names the counter; a runner that cannot produce it produces
    no counts at all, and a check that read that as "none over budget" would
    report a pass on a journey it never ran.
    """
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    shapes = {name: ['a' * 64] for name in names}
    assert not any(policy.violations(
        {name: 1 for name in names}, shapes, document, names).values())
    found = policy.violations({name: None for name in names}, shapes,
                              document, names)
    assert found['unmeasured'] == {
        name: 'perf-instructions' for name in names}, found['unmeasured']
    assert not found['over'], found['over']
    assert policy.REMEDY_FOR['unmeasured'] == policy.UNMEASURED_REMEDY
    assert 'measured nothing' in policy.REMEDY_FOR['unmeasured']


def test_a_sha_mismatch_is_a_violation_naming_both_shas(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    first, second = 'a' * 64, 'b' * 64
    shapes = {name: [first] for name in names}
    shapes[names[0]] = [first, second]
    found = policy.violations({name: 1 for name in names}, shapes,
                              document, names)
    assert found['shape'] == {names[0]: [first, second]}, found['shape']
    assert not found['over'], found['over']
    assert not found['unmeasured'], found['unmeasured']
    assert policy.REMEDY_FOR['shape'] == policy.SHAPE_REMEDY
    assert 'not comparable' in policy.REMEDY_FOR['shape']


def test_the_check_command_refuses_with_the_remedy_it_promises(tmp):
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(budget_document()))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps({
        'rounds': 1, 'python': sys.version, 'shas': {},
        'counters': {'perf-instructions': {
            'available': True, 'startup_only': 0,
            'journeys': {names[0]: {'min': 5000, 'max': 5000,
                                    'median': 5000, 'spread': 0,
                                    'raw': 5000}}}}}),
        encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 1
    said = spoken.getvalue()
    assert 'over:' in said, said
    assert policy.REMEDY_FOR['over'] in said, (
        'the refusal printed a remedy the REMEDY_FOR table does not carry, '
        f'so a reader is told something the table does not promise: {said}')


def test_a_shape_failure_refuses_a_tighten_as_firmly_as_a_check(tmp):
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(budget_document()))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps({
        'rounds': 1, 'python': sys.version, 'shas': {},
        'shape_failure': 'the mcp-exec journey printed no record',
        'counters': {}}), encoding='utf-8')
    for extra in ([], ['--tighten']):
        spoken = io.StringIO()
        with contextlib.redirect_stderr(spoken):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements)] + extra)
        assert code == 1, extra
        assert policy.SHAPE_REMEDY in spoken.getvalue(), spoken.getvalue()
    # A tighten that ran off a failed measurement would write a number no
    # journey produced, so the artefact is untouched.
    assert json.loads(artifact.read_text(encoding='utf-8'))['journeys'] == {
        name: 1000 for name in names}


# ─── the shape the sha is taken over ───────────────────────────────────────


def test_a_rendered_journey_carries_no_per_run_field(tmp):
    journeys = _journey_contract.journeys()
    rendering = journeys.rendering_of('command-round-trip')
    keys = set(rendering) | set(rendering['frame']) | set(rendering['result'])
    leaked = sorted(keys & set(PER_RUN))
    assert not leaked, (
        f'the rendering carries {leaked}, which is minted per run or per '
        'store, so its sha is not stable across rounds')
    assert rendering['frame']['id'] == journeys.COMMAND_ID, rendering
    assert rendering['result']['result'] == journeys.COMMAND_RESULT, (
        rendering)


def test_a_recorded_exclusion_survives_a_render_and_reload(tmp):
    """The block is written, read back and compared against itself, so a
    recorded set of threads cannot drift from what render() emits."""
    policy = _journey_contract.policy()
    document = budget_document()
    document['excluded_threads'] = {
        'command-round-trip': ['front-end-import', 'uvicorn-serve'],
        'dashboard-fanout': ['front-end-import', 'uvicorn-serve'],
        'mcp-exec': ['front-end-import']}
    rendered = policy.render(document)
    target = Path(tmp) / 'journey-budget.json'
    target.write_bytes(rendered)
    assert policy.render(policy.load(target)) == rendered
    assert b'"excluded_threads"' in rendered
    # Absent stays absent, so an artefact recorded before the field existed
    # is still its own canonical rendering.
    assert b'"excluded_threads"' not in policy.render(budget_document())


def test_an_artefact_that_cannot_be_read_says_which_way_it_failed(tmp):
    """Three failures with three sentences, because a reader has to know
    whether the file is missing, malformed or the wrong shape."""
    policy = _journey_contract.policy()
    for path, fragment in (
            (Path(tmp) / 'absent.json', 'cannot read'),
            (Path(tmp) / 'broken.json', 'invalid journey budget JSON')):
        if fragment.startswith('invalid'):
            path.write_text('{not json', encoding='utf-8')
        try:
            policy.load(path)
        except ValueError as error:
            assert fragment in str(error), error
            continue
        raise AssertionError(f'load accepted {path.name}')

    def over(field, value):
        document = budget_document()
        document[field] = value
        return document

    name = journeys().NAMES[0]
    for document, fragment in (
            ('not an object', 'must be an object'),
            (over('toolchain', []), 'toolchain must be an object'),
            (over('toolchain', {'': 'x'}), 'unknown toolchain field'),
            (over('toolchain', {'python': '  '}), 'non-empty string or null'),
            (over('excluded_threads', 'none'),
             'excluded_threads must be an object'),
            (over('excluded_threads', {'no-such-journey':
                                       ['front-end-import']}),
             'names a journey with no count'),
            (over('excluded_threads', {name: 'front-end-import'}),
             'excluded_threads names no thread'),
            (over('excluded_threads', {name: ['front-end-import',
                                              'front-end-import']}),
             'repeats a role'),
            (over('excluded_threads', {name: ['no-such-role']}),
             'unknown excluded thread role')):
        try:
            policy._validated(document)
        except ValueError as error:
            assert fragment in str(error), (fragment, error)
            continue
        raise AssertionError(f'the schema accepted {document!r}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
