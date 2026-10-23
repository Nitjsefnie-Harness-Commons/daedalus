#!/usr/bin/env python3
"""Contracts for journey comparisons, refusals, reporting and CI setup."""
import io
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _journey_contract  # noqa: E402
from _wfgraph import _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
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
    """A changed toolchain reports an unmeasured run, not a regression."""
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
    """Only successful main runs may tighten; re-baselines need review."""
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
    # Plain cells rather than code spans: the two values go through the
    # reason's escape so a field name or a toolchain identity carrying a pipe
    # cannot spend the row, and a code span is the one context where a
    # renderer does not consume the doubled backslash the escape relies on.
    assert ('| valgrind_version | valgrind-3.24.0 | valgrind-3.25.0 |'
            in said), (
        'the summary must carry BOTH values of the field that moved, or a '
        f'reader is told to re-baseline without being told what moved: {said}')
    assert policy.TOOLCHAIN_REMEDY in said, said
    assert 'nothing was tightened' not in said, (
        f'a check that was not a tighten reported one: {said}')


# ─── which way a recorded number may move ──────────────────────────────────


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
    for baseline, counts, kind, expected, remedy, phrase in (
            (1000, {names[0]: 1200}, 'over', {names[0]: (1200, 1100.0)},
             policy.OVER_REMEDY, 'never raised by hand'),
            (1, {name: None for name in names}, 'unmeasured',
             {name: 'perf-instructions' for name in names},
             policy.UNMEASURED_REMEDY, 'measured nothing')):
        clean = {name: baseline for name in names}
        assert not any(policy.violations(clean, document, names).values())
        found = policy.violations(dict(clean, **counts), document, names)
        assert sorted(found) == ['over', 'unmeasured', 'unresolved']
        assert found[kind] == expected, found[kind]
        if kind == 'unmeasured':
            assert not found['over'], found['over']
        assert policy.REMEDY_FOR[kind] == remedy
        assert phrase in policy.REMEDY_FOR[kind]


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


def test_a_rise_outranks_the_single_round_guard_under_tighten(tmp):
    policy = _journey_contract.policy()
    counts = {name: 500 for name in journeys().NAMES}
    counts[journeys().NAMES[0]] = 2000
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(counts, rounds=1)))
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements), '--tighten'])
    assert code != 0 and policy.REMEDY_FOR['over'] in spoken.getvalue(), code


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
    """The decoder's own error is the command's promised refusal."""
    try:
        json.loads(source.read_bytes().decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return str(error)
    raise AssertionError(f'{source.name} is readable after all')


def test_an_input_the_run_cannot_read_is_refused_in_its_own_words(tmp):
    """Unreadable inputs report their cause and leave budgets alone."""
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
    """Changed and missing shas must report without comparing counts."""
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


def test_valgrind_install_restores_and_replenishes_debs_and_refuses_missing(
        tmp):
    if sys.platform == 'win32':
        _util.skip('the journey-budget install step uses POSIX-only paths '
                   'and sudo; this control is exercised on Linux and macOS')
    job = complete_job_mapping(_tests_yml(), 'journey-budget')
    assert job is not None, 'the journey-budget job is not in tests.yml'
    name = 'Install valgrind, which supplies the instruction counter'
    script = next(step['run'] for step in job['steps']
                  if step.get('name') == name)
    stub = ('sudo() { "$@"; }\n'
            'apt-get() { printf "%s\\n" "$@" >> "$APT_LOG";\n'
            '  if [[ -f "$RUNNER_TEMP/vg-debs/cached.deb" '
            '&& ! -f "$ARCHIVES/cached.deb" ]]; then\n'
            '    echo "archive not seeded at apt install" >&2; return 1;\n'
            '  fi; }\n')
    for state in ('warm', 'cold', 'missing'):
        root = Path(tmp) / state
        cache, archives = root / 'vg-debs', root / 'archives'
        archives.mkdir(parents=True)
        if state != 'cold':
            cache.mkdir()
            (cache / 'cached.deb').write_bytes(b'cached')
        (archives / 'candidate.deb').write_bytes(b'candidate')
        manifest = 'candidate.deb\n'
        if state == 'missing':
            manifest += 'absent.deb\n'
        (root / 'vg-manifest.txt').write_text(manifest, encoding='utf-8')
        log = root / 'apt.log'
        done = subprocess.run(
            [_util.workflow_bash(), '-e', '-c', stub + script.replace(
                '/var/cache/apt/archives/', f'{archives.as_posix()}/')],
            cwd=root, capture_output=True, text=True, timeout=10,
            env=_util.child_coverage('scrub', dict(
                os.environ, RUNNER_TEMP=str(root), APT_LOG=str(log),
                ARCHIVES=str(archives))))
        if state == 'missing':
            assert done.returncode != 0 and 'absent.deb' in done.stderr, done
        else:
            assert done.returncode == 0, done.stderr
        assert log.read_text(encoding='utf-8').splitlines() == [
            '-o', 'Acquire::http::Timeout=30', '-o', 'Acquire::Retries=5',
            'install', '-y', '--no-install-recommends', 'valgrind']
        assert (cache / 'candidate.deb').read_bytes() == b'candidate'
        if state != 'cold':
            assert (archives / 'cached.deb').read_bytes() == b'cached'


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeygates_')


if __name__ == '__main__':
    raise SystemExit(main())
