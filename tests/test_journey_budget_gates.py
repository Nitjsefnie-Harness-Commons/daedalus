#!/usr/bin/env python3
"""Contracts for the journey-cost ratchet's GATES: what each recorded
comparison refuses on, which way a recorded number may move, and what a run
that compared nothing reports.

Its other half of a module that held the policy, the gates and the command
line together, for the reason the sibling file gives. Nothing here asserts a
wall-clock margin or a timing bound, because a count is a number this
repository must not write down: the runner it was measured on is not the
runner the next run lands on."""
import io
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ARTIFACT,
    IDENTITY,
    PER_RUN,
    ROOT,
    _report_file,
    budget_document,
    fixture_shas,
    journeys,
    measured_report,
    measurements_file,
    recorded_document,
    recorded_maps,
    summary_file,
)


# One RECORDED toolchain: the three strings a baseline is stamped with, so
# these tests compare a count against an identity rather than against
# whatever the runner happens to be today.
RECORDED = {'python': '3.13.15 (main, Aug  6 2026, 02:15:18) [GCC 13.3.0]',
            'valgrind_version': 'valgrind-3.24.0',
            'runner_image': 'ubuntu24 20260801.1.0'}


# ─── a recorded gate that moved is not a regression ──────────────────────────


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
    artifact.write_bytes(policy.render(recorded_document()))
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
    artifact.write_bytes(policy.render(recorded_document()))
    measurements = measurements_file(Path(tmp) / 'counts.json', None)
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 1, 'a count over budget passed on a matching toolchain'
    assert policy.OVER_REMEDY in spoken.getvalue(), spoken.getvalue()
    assert policy.toolchain_diff(RECORDED, RECORDED) == {}


def test_only_the_main_tighten_may_write_the_artefact(tmp):
    """A re-baseline is a reviewed commit; a tighten DOWN is CI's to make.

    The maintainer's ruling: "autocommit tighten only, where a PR can
    increase its own cap if necessary". So exactly one job may write the
    file, on a push to main, and only after the check step concluded
    success — the run that found a journey over budget is the run that must
    write nothing. Every other mention of the file is a `paths-ignore`
    entry, which is a workflow choosing not to run at all.
    """
    del tmp
    artefact = '.github/journey-budget.json'
    workflows = ROOT / '.github' / 'workflows'
    lines = (workflows / 'tests.yml').read_text(encoding='utf-8').splitlines()
    start = lines.index('  journey-budget:')
    end = next(number for number, line in enumerate(lines[start + 1:], 1)
               if line.startswith('  ') and not line.startswith('    ')
               and line.rstrip().endswith(':')) + start
    for number, line in enumerate(lines, 1):
        if artefact not in line:
            continue
        assert start < number <= end or line.strip() == f"- '{artefact}'", (
            f'tests.yml:{number} names the artefact outside the '
            'journey-budget job and outside a paths-ignore entry, so a step '
            f'reads or writes it: {line!r}')
    named = [(path.name, number, line.strip())
             for path in sorted(workflows.glob('*.y*ml'))
             if path.name != 'tests.yml'
             for number, line in enumerate(path.read_text(
                 encoding='utf-8').splitlines(), 1)
             if artefact in line and line.strip() != f"- '{artefact}'"]
    assert not named, (
        'a workflow other than tests.yml names the artefact outside a '
        f'paths-ignore entry: {named}')
    job = '\n'.join(lines[start:end])
    steps = [block for block in job.split('\n      - ') if block.strip()]
    tighten = [block for block in steps if '--tighten' in block]
    assert len(tighten) == 1, (
        f'the journey-budget job must hold exactly one tightening step: '
        f'{len(tighten)}')
    # Match the INVOCATION, not the script's name: a comment above the
    # step belongs to the block before it, and naming the script in
    # prose would read as a second caller.
    commit = [block for block in steps
              if 'python3 scripts/ci/ratchet_push.py' in block]
    assert len(commit) == 1, f'expected one commit step, found {len(commit)}'
    assert artefact in commit[0], (
        f'the commit step does not commit the artefact: {commit[0]}')
    assert "'ci: tighten the journey budget'" in commit[0], (
        'the commit message is what a reader of the history sees a tighten '
        f'was: {commit[0]}')


def test_a_gate_nothing_was_recorded_for_says_so_in_the_summary(tmp):
    """The never-recorded refusal reaches the surface a reader actually has.

    It exits 1 with its remedy on stderr, and stderr is collapsed by default
    — so the step summary is the only place the run left anything. It has to
    name WHICH gate was never recorded, say that no count was compared, and
    carry the remedy; a summary that says none of those reads as a run that
    found nothing wrong.
    """
    policy = _journey_contract.policy()
    document = recorded_document()
    document.pop('toolchain')
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(
        {name: 1000 for name in journeys().NAMES})), encoding='utf-8')
    with summary_file(tmp) as summary:
        with contextlib.redirect_stderr(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements),
                                '--summary'])
        said = summary.read_text(encoding='utf-8')
    assert code == 1, 'a gate nothing was recorded for is not a pass'
    assert 'the journey budget records no toolchain yet.' in said, said
    assert 'No count was compared.' in said, said
    assert policy.TOOLCHAIN_REMEDY in said, said
    # The table of what WOULD have been compared, so a reader can see that
    # the run had counts and declined to compare them rather than having
    # none to begin with.
    assert 'What would have been compared:' in said, said
    assert journeys().NAMES[0] in said, said


def test_a_gate_that_moved_says_so_in_the_summary_of_a_plain_check(tmp):
    """The moved-gate outcome succeeds, so its words have one home.

    The exit status is 0 because the tree did not regress, which leaves the
    summary as the whole of the account: the changed field's two values, the
    statement that no count was compared, and the re-baseline remedy. The
    tighten half of the same outcome already says it tightened nothing, so
    a plain check must not claim it — it is not tightening.
    """
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(
        {name: 1000 for name in journeys().NAMES},
        toolchain=dict(IDENTITY, valgrind_version='valgrind-3.25.0'))),
        encoding='utf-8')
    with summary_file(tmp) as summary:
        with contextlib.redirect_stdout(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements),
                                '--summary'])
        said = summary.read_text(encoding='utf-8')
    assert code == 0, 'a recorded gate that moved is not a regression'
    assert artifact.read_bytes() == before, (
        'a run that compared no count wrote the artefact anyway')
    assert '**toolchain changed, re-baseline.**' in said, said
    assert 'No count was compared.' in said, said
    assert ('| valgrind_version | `valgrind-3.24.0` | `valgrind-3.25.0` |'
            in said), (
        'the summary must carry BOTH values of the field that moved, or a '
        f'reader is told to re-baseline without being told what moved: {said}')
    assert policy.TOOLCHAIN_REMEDY in said, said
    assert 'nothing was tightened' not in said, (
        f'a check that was not a tighten reported one: {said}')


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
    payload = json.dumps(recorded_document()).encode('utf-8')
    artifact.write_bytes(payload)
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps({
        'rounds': 1, 'python': sys.version, **recorded_maps(),
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


def test_a_tighten_writes_nothing_for_a_journey_it_measured_nothing(tmp):
    """A journey this run measured nothing keeps the count it was recorded
    with, rather than being written down as something it did not cost.

    `tightened` walks the RECORDED journeys, so a journey the counter
    refused is a name in the mapping with no count beside it, and it has to
    be skipped: the neighbours are followed down while it stays where it was
    recorded. A comparison that treated the missing measurement as a value
    would land a budget of zero beside a saving, which is the exact
    measurement the next run cannot reproduce.

    WHICH journey is the unmeasured one, and what the neighbours are followed
    down TO, are chosen here and read out of the tree's own recorded map. A
    control that indexed a journey set by position and spelled the recorded
    count beside its assertion named two things the journey set may change:
    it went stale the first time the set grew.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    recorded = recorded_document(journeys=policy.load(ARTIFACT)['journeys'])
    unmeasured, measured = names[0], names[1:]
    kept = recorded['journeys'][unmeasured]
    # A quarter off, because a tighten follows a journey down only past its
    # own tolerance (issue 1484), and the fixture's is ten percent: a drop
    # of one count is the journey's own spread, not a saving to record.
    followed = {name: recorded['journeys'][name] * 3 // 4 for name in measured}
    assert kept not in followed.values(), (
        f'the count {unmeasured} keeps is one a measured journey is followed '
        'down to, so "left alone" and "followed down" are the same value and '
        'this control cannot tell them apart')
    artifact.write_bytes(policy.render(recorded))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(followed)),
                            encoding='utf-8')
    # The PROPERTY is about `tightened`, and it is driven there: the command
    # no longer reaches it on this measurement, because a budget the next
    # check refuses is not one the tighten may write. Reading the property
    # off a command that no longer calls the function left a fixture
    # documenting a path nothing takes.
    assert policy.tightened(followed, recorded, names) == dict(
        followed, **{unmeasured: kept}), (
        f'{unmeasured} was measured by nothing and must keep the count it '
        f'was recorded with while every journey beside it was followed '
        f'down: {policy.tightened(followed, recorded, names)}')
    spoken, refused = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(spoken), \
            contextlib.redirect_stderr(refused):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements),
                            '--tighten'])
    assert code != 0, (
        'the tighten followed the measured journeys down beside one it could '
        f'not measure, and reported success: {spoken.getvalue()}')
    assert 'tightened the journey budget' not in spoken.getvalue(), (
        spoken.getvalue())
    assert json.loads(artifact.read_text(
        encoding='utf-8'))['journeys'] == recorded['journeys'], (
        'a refused tighten wrote the artefact anyway')


def test_a_tighten_that_lowered_nothing_says_so_and_writes_nothing(tmp):
    """The no-op tighten is a reported outcome, not a silent empty diff.

    A run where no journey measured below what it was recorded with has
    nothing to follow down, and it must say exactly that: CI commits whatever
    a tighten wrote, so a step that printed nothing and wrote nothing leaves
    a maintainer deciding whether the job ran at all — and a step that
    printed the tightening line would commit a rewritten artefact for a
    mapping identical to the one committed.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(
        measured_report({name: 1000 for name in names})), encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements),
                            '--tighten'])
    assert code == 0, spoken.getvalue()
    said = spoken.getvalue()
    assert 'no journey measured a drop wider than its own tolerance' in said, (
        said)
    assert 'tightened the journey budget' not in said, (
        f'a run that lowered nothing reported a tightening: {said}')
    assert artifact.read_bytes() == before, (
        'a tighten that lowered nothing still rewrote the artefact')


# ─── what a refusal says ───────────────────────────────────────────────────


def test_a_count_over_budget_is_a_violation_carrying_its_remedy(tmp):
    del tmp
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    counts = {name: 1000 for name in names}
    assert not any(policy.violations(counts, document, names).values())
    over = dict(counts, **{names[0]: 1200})
    found = policy.violations(over, document, names)
    # Three kinds, and the third is a journey this run could not
    # resolve against a count the budget holds — which is a failure,
    # not an absence. See the two controls above.
    assert sorted(found) == ['over', 'unmeasured', 'unresolved']
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
    found = policy.violations(measured, document, names)
    assert not found['over'], found
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
    assert not any(policy.violations(
        {name: 1 for name in names}, document, names).values())
    found = policy.violations({name: None for name in names}, document, names)
    assert found['unmeasured'] == {
        name: 'perf-instructions' for name in names}, found['unmeasured']
    assert not found['over'], found['over']
    assert policy.REMEDY_FOR['unmeasured'] == policy.UNMEASURED_REMEDY
    assert 'measured nothing' in policy.REMEDY_FOR['unmeasured']


def test_rounds_that_disagree_are_a_refusal_naming_both(tmp):
    """A measurement whose rounds disagree about what a journey rendered.

    This case used to live in `violations`, where `main` could no longer reach
    it — the sha gate refuses first — so it is pinned on the gate that owns
    it, and the remedy it prints is the one a reader is sent to.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    first, second = 'a' * 64, 'b' * 64
    agreed = {name: [first] for name in names}
    differs = {name: [first] for name in names}
    differs[names[0]] = [first, second]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main([
            'check', '--artifact', str(artifact),
            '--measurements', str(_report_file(
                tmp, {}, dict(recorded_maps(), shas=differs)))])
    assert code == 0, spoken.getvalue()
    said = spoken.getvalue()
    assert f'  {names[0]}: recorded' in said, said
    assert first in said and second in said, said
    # Both sides are round-lists here, which is the shape a caller holding a
    # measurement's own map has; the recorded artefact holds one string and
    # compares the same way.
    found_diff = policy.sha_diff(agreed, differs)
    assert list(found_diff) == [names[0]], found_diff
    assert found_diff[names[0]] == ([first], [first, second]), found_diff
    assert policy.sha_diff({name: first for name in names}, differs) == {
        names[0]: (first, [first, second])}


def test_the_check_command_refuses_with_the_remedy_it_promises(tmp):
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps({
        'rounds': 1, 'python': sys.version, **recorded_maps(),
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
    artifact.write_bytes(policy.render(recorded_document()))
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


def _decoder_says(source):
    """What the decoder itself says about this file, read the same way.

    The command prints the exception's text, so the expectation is computed
    from the same decode rather than spelled out beside it: a literal here
    could drift from the one the command prints and the control would then
    pass against a refusal that changed.
    """
    try:
        json.loads(source.read_bytes().decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return str(error)
    raise AssertionError(f'{source.name} is readable after all')


def test_an_input_the_run_cannot_read_is_refused_in_its_own_words(tmp):
    """Every failure with no verdict of its own is one sentence and exit 1.

    A budget that is not there and a measurement file the decoder rejects
    are the two the job meets in practice — a step that points at an upload
    which never arrived, and a truncated one. Neither may reach the reader as
    a traceback or as a bare status: the message has to carry the cause, and
    the cause is the whole of what a red step can tell the person who has to
    fix it.
    """
    policy = _journey_contract.policy()
    absent = Path(tmp) / 'no-such-budget.json'
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = policy.main(['check', '--artifact', str(absent)])
    assert code == 1, 'a budget that is not there is not a pass'
    said = spoken.getvalue()
    assert 'cannot read the journey budget' in said, said
    # The message names the file it could not read. Its NAME and not its
    # path: a checkout's absolute path is spelled with a separator this
    # repository does not write down, so asserting one here would make the
    # control a test of the runner's filesystem rather than of the refusal.
    assert absent.name in said, said
    assert 'Traceback' not in said, said

    broken = Path(tmp) / 'broken-counts.json'
    broken.write_text('{not json', encoding='utf-8')
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(broken), '--tighten'])
    assert code == 1, 'a measurement file the decoder rejects is not a pass'
    assert spoken.getvalue().strip() == _decoder_says(broken), (
        "the refusal must be the decoder's own sentence about that file, "
        f'not a generic one: {spoken.getvalue()}')
    assert artifact.read_bytes() == before, (
        'a run that could not read its measurement wrote the artefact anyway')


# ─── the shape a recorded sha is taken over ──────────────────────────────────


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


def test_a_journey_that_renders_differently_is_not_compared(tmp):
    """A recorded count describes the journey that rendered when it was
    recorded, so a different rendering refuses rather than reading as a
    regression or a saving.

    Two ways to differ: one journey's sha moved, and one journey's sha is
    not in the measurement at all. Both are a difference from the recorded
    one, and the never-recorded case is that gate's own test below.

    The ROUNDS disagreeing is a third case and it is a different one, so it
    is not here: it is `test_rounds_that_disagree`, which asserts on
    `sha_diff` directly, because a measurement whose rounds disagree is not
    a measurement to compare counts from at all.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    changed = dict(fixture_shas(), **{names[0]: ['f' * 64]})
    missing = {name: seen for name, seen in fixture_shas().items()
               if name != names[0]}
    for report_maps, named in ((changed, names[0]), (missing, names[0])):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = policy.main([
                'check', '--artifact', str(artifact),
                '--measurements', str(_report_file(
                    tmp, {}, dict(recorded_maps(), shas=report_maps)))])
        # A CHANGED recorded value is a REPORT, on stdout, exit 0: the tree
        # did not regress, and the summary says no count was compared.
        assert code == 0, (report_maps, out.getvalue(), err.getvalue())
        said = out.getvalue()
        assert 'journey shas changed' in said, said
        assert f'  {named}: recorded' in said, said
        assert 'no count was compared' in said, said


def test_nothing_recorded_is_a_refusal_rather_than_a_pass(tmp):
    """A gate whose value was never recorded has nothing to differ from, and
    falling through would compare a count against a question never asked."""
    policy = _journey_contract.policy()
    names = journeys().NAMES
    for field, subject in _journey_contract.never_recorded_gates():
        artifact = Path(tmp) / f'{field}.json'
        document = recorded_document()
        document.pop(field, None)
        artifact.write_bytes(policy.render(document))
        spoken = io.StringIO()
        with contextlib.redirect_stderr(spoken):
            code = policy.main([
                'check', '--artifact', str(artifact),
                '--measurements', str(_report_file(
                    tmp, {}, recorded_maps()))])
        assert code == 1, (field, spoken.getvalue())
        said = spoken.getvalue()
        assert subject in said, (field, said)
        assert f'{subject} not recorded' in said, (field, said)
    del names


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeygates_')


if __name__ == '__main__':
    raise SystemExit(main())
