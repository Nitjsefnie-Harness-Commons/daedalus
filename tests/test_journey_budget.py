#!/usr/bin/env python3
"""Contracts for the journey-cost ratchet and the artefact it owns.

Nothing here asserts a wall-clock margin or a timing bound. What a ratchet
owns is the POLICY — what is recorded, which way a recorded number may move,
and what a refusal says — and that policy is what is decided here. A count is
a number this repository must not write down, because the runner it was
measured on is not the runner the next run lands on.
"""
import contextlib
import io
import json
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


def _policy():
    return _util.load(POLICY_SOURCE, 'journey_budget_contract')


def _journeys():
    sys.path.insert(0, str(ROOT / 'tests'))
    try:
        import _journeys
    finally:
        sys.path.pop(0)
    return _journeys


def _budget_document(**overrides):
    document = {
        'schema_version': 1,
        'counter': 'perf-instructions',
        'tolerance_pct': 10,
        'journeys': {name: 1000 for name in _journeys().NAMES},
    }
    document.update(overrides)
    return document


# ─── the artefact ──────────────────────────────────────────────────────────

def test_the_committed_artefact_is_the_canonical_rendering(tmp):
    del tmp
    policy = _policy()
    document = policy.load(ARTIFACT)
    assert policy.render(document) == ARTIFACT.read_bytes(), (
        'the committed artefact is not what render() writes for it, so the '
        'file is hand-edited or out of date')


def test_the_artefact_names_exactly_the_journeys_that_exist(tmp):
    del tmp
    document = _policy().load(ARTIFACT)
    names = _journeys().NAMES
    assert sorted(document['journeys']) == sorted(names), (
        'the budget and the journey set disagree: '
        f'{sorted(document["journeys"])} against {sorted(names)}')
    assert _policy().stale(document, names) == []
    assert _policy().unrecorded(document, names) == sorted(names), (
        'every journey is unrecorded until the first baseline is written')


def test_the_artefact_is_its_own_file_and_a_tracked_one(tmp):
    del tmp
    policy = _policy()
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


def test_the_artefact_schema_is_closed(tmp):
    del tmp
    policy = _policy()
    # 'wall-clock' is a quantity the ratchet does not defend at all, and
    # 'syscalls' is one the counters module measures and never gates on. A
    # recorded count denominated in either would be a number nothing holds.
    for over in ({'unknown': 1}, {'counter': 'wall-clock'},
                 {'counter': 'syscalls'},
                 {'tolerance_pct': -1}, {'journeys': []},
                 {'schema_version': 2},
                 {'journeys': {'command-round-trip': -5}},
                 {'journeys': {'command-round-trip': 'many'}}):
        document = _budget_document()
        document.update(over)
        try:
            policy._validated(document)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted {sorted(over)}')


# ─── which way a recorded number may move ──────────────────────────────────

def test_tightened_follows_a_cheaper_journey_down(tmp):
    del tmp
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document()
    counts = {name: 900 for name in names}
    assert policy.tightened(counts, document, names) == {
        name: 900 for name in names}


def test_tightening_never_raises_and_never_adds_a_journey(tmp):
    del tmp
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document()
    assert policy.tightened(
        {name: 1000 for name in names}, document, names) is None
    assert policy.tightened(
        {name: 1001 for name in names}, document, names) is None
    assert policy.tightened(
        dict({name: 1000 for name in names}, invented=10),
        document, names) is None


def test_tightening_drops_a_journey_the_set_no_longer_has(tmp):
    del tmp
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document()
    document['journeys']['a-journey-nobody-runs'] = 5000
    assert policy.tightened(
        {name: 1000 for name in names}, document, names) == {
            name: 1000 for name in names}


def test_the_tighten_command_is_the_one_the_implementation_uses(tmp):
    policy = _policy()
    names = _journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    payload = json.dumps(_budget_document()).encode('utf-8')
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
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document()
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
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document(journeys={names[0]: 1000, names[1]: None,
                                   names[2]: None})
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
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document()
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
    policy = _policy()
    names = _journeys().NAMES
    document = _budget_document()
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
    policy = _policy()
    names = _journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_budget_document()))
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
    policy = _policy()
    names = _journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(_budget_document()))
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
    journeys = _journeys()
    rendering = journeys.rendering_of('command-round-trip')
    keys = set(rendering) | set(rendering['frame']) | set(rendering['result'])
    leaked = sorted(keys & set(PER_RUN))
    assert not leaked, (
        f'the rendering carries {leaked}, which is minted per run or per '
        'store, so its sha is not stable across rounds')
    assert rendering['frame']['id'] == journeys.COMMAND_ID, rendering
    assert rendering['result']['result'] == journeys.COMMAND_RESULT, (
        rendering)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())