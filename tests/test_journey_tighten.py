#!/usr/bin/env python3
"""The auto-tighten and the re-baseline: which way a recorded count may move,
and what a command may leave on disk.

Its own module because `test_journey_budget.py` is at the 700-line ceiling
and holds the SCHEMA and the gates; these two are about the writing, and
both are questions that file's existing tests do not ask. Nothing here
asserts a number the next run will refuse, and nothing asserts a wall-clock
margin: a count is a number this repository must not write down, so every
control drives a measurement handed to the module.
"""
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _journey_contract  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _journey_contract import (  # noqa: E402
    IDENTITY,
    ROOT,
    journeys,
    measurements_file,
    recorded_document,
    recorded_maps,
)


def _mixed_report(names):
    """One measurement: a journey over its budget, one below, one level.

    Recorded at 1000 each with a 10% tolerance, so the budget is 1100. The
    first name is a rise the job must go red on, the second is a genuine
    saving a tighten would record, and the third is pinned exactly — the
    case a `<` that became `<=` would write over.
    """
    measured = {name: 1000 for name in names}
    measured[names[0]] = 1200
    measured[names[1]] = 800
    return {'rounds': 3, 'python': sys.version, **recorded_maps(),
            'counters': {'perf-instructions': {
                'available': True, 'startup_only': 0,
                'journeys': {name: {'min': seen, 'max': seen,
                                    'median': seen, 'spread': 0, 'raw': seen}
                             for name, seen in measured.items()}}}}


def test_both_remedies_reach_the_step_summary_on_one_failing_run(tmp):
    """Each kind's remedy lands in the summary beside the row that reports it.

    The rows themselves were controlled by rendering `verdict_lines`
    directly, which is the number and not the sentence a reader acts from.
    The two remedy writes were not controlled at all, and they are what the
    summary is for: stderr is collapsed by default, so an unmeasured journey
    whose remedy went there showed three rows and no next step.

    One run carries both — a journey over budget and a journey this runner
    measured nothing — because that is the shape the two writes share a
    path with, and a control that drove only one would not have exercised
    the branch that decides between them.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    report = _mixed_report(names)
    # names[2] is dropped from the measurement entirely, so the counter
    # reports it unmeasured while names[0] is over its budget.
    report['counters']['perf-instructions']['journeys'].pop(names[2])
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    summary = Path(tmp) / 'summary.md'
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    os.environ['GITHUB_STEP_SUMMARY'] = str(summary)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements),
                                '--summary'])
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved
    assert code == 1, 'a journey over budget must exit nonzero'
    said = summary.read_text(encoding='utf-8')
    assert policy.UNMEASURED_REMEDY in said, (
        'the remedy for an unmeasured journey never reached the summary, so '
        f'the rows naming it carried no next step: {said}')
    assert 'journey_budget.py rebaseline' in said, (
        'the one command a re-baseline is run from never reached the '
        f'summary: {said}')


def test_a_tighten_that_meets_a_rise_writes_nothing(tmp):
    """The ruling, in the shape a regression takes it: one up, one down.

    A tighten that lowers the journeys that fell while another rose is the
    commit on a rise that is forbidden: the budget lands lower, so the
    regression that caused the rise is both still in the tree and no longer
    visible as one. So a run with a rise writes the file not at all, and
    exits nonzero so the job says so rather than reporting a green that
    quietly recorded half a run.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(_mixed_report(names)), encoding='utf-8')
    before = artifact.read_bytes()
    with contextlib.redirect_stderr(io.StringIO()):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements),
                            '--tighten'])
    assert code != 0, 'a tighten that met a rise reported success'
    assert artifact.read_bytes() == before, (
        'a run with a rise still wrote the artefact, so the rise was '
        'committed beside the saving and the budget landed lower with the '
        'regression unguarded')


def test_a_committed_tighten_says_how_many_journeys_it_lowered(tmp):
    """The one line a maintainer reads after an auto-commit says a number.

    The artefact on disk is right either way, so a wrong count here is
    invisible to every other control and ships as the single line the summary
    exists to produce. Two journeys measure below their recorded count and
    one holds level, so the number is neither 0 nor "all of them" — the two
    a counting mistake actually produces.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    measured = {name: 1000 for name in names}
    measured[names[0]] = 800
    measured[names[1]] = 800
    report = _mixed_report(names)
    entry = report['counters']['perf-instructions']['journeys']
    for name, seen in measured.items():
        entry[name] = {'min': seen, 'max': seen, 'median': seen,
                       'spread': 0, 'raw': seen}
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    summary = Path(tmp) / 'summary.md'
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    os.environ['GITHUB_STEP_SUMMARY'] = str(summary)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements),
                                '--tighten'])
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved
    assert code == 0, 'a measurement with no rise should tighten'
    written = policy.load(artifact)
    assert written['journeys'][names[0]] == 800, written['journeys']
    assert written['journeys'][names[2]] == 1000, written['journeys']
    said = summary.read_text(encoding='utf-8')
    assert 'tightened the journey budget on 2 journeys' in said, said


def test_a_re_baseline_writes_what_the_next_run_accepts(tmp):
    """The command a rise is answered with, and the artefact it leaves.

    The whole point is that the run AFTER it can read the file: an artefact
    the validator refuses makes the next run refuse before it compares
    anything, so the re-baseline would have moved the problem rather than
    settled it. Every field but the tolerance comes from the one
    measurement — a mix of an old toolchain with new counts is a document
    no run can ever match — and the tolerance stays the recorded one, since
    a re-baseline moves the counts and not the bound.
    """
    policy = _journey_contract.policy()
    # A tolerance the fixture does not default to, so a command that
    # hardcoded the default would pass with it and re-loosen the budget.
    document = recorded_document(tolerance_pct=2.5)
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    report = _journey_contract.fixture_report()
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['rebaseline', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 0, spoken.getvalue()
    written = policy.load(artifact)
    assert written['tolerance_pct'] == 2.5, written['tolerance_pct']
    assert written['toolchain'] == report['toolchain'], written['toolchain']
    assert written['shas'] == {name: seen[0] for name, seen
                               in report['shas'].items()}, written['shas']
    # The signatures come from the classifier and not from the report, so a
    # re-baseline records the table the NEXT run will classify by; the band
    # table it replaced is dropped rather than carried forward as a field
    # nothing reads.
    threads = _journey_contract.threads()
    assert written['thread_signatures'] == {
        role: list(names) for role, names in threads.SIGNATURES.items()}, \
        written['thread_signatures']
    assert written.get('thread_bands') is None, written.get('thread_bands')
    assert policy.render(written) == artifact.read_bytes(), (
        'the artefact on disk is not what render() writes for it, so the '
        'command wrote bytes the canonical-rendering control will refuse')


def _tighten_step():
    """The journey-budget job's tightening step, decoded from the workflow.

    The decoder rather than a text scan: a control that reads the `if:` the
    way Actions reads it is the only one that can be fooled by nothing about
    how it is spelled.
    """
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    job = complete_job_mapping(source, 'journey-budget')
    assert job is not None, 'the journey-budget job is not in tests.yml'
    tighten = [step for step in job['steps'] if '--tighten' in (
        step.get('run') or '')]
    assert len(tighten) == 1, (
        f'the journey-budget job must hold exactly one tightening step: '
        f'{len(tighten)}')
    return job, tighten[0]


def _guard_step_names(guard):
    """The step ids a guard reads, off the guard rather than beside it."""
    return re.findall(r'steps\.([A-Za-z_][\w-]*)\.', guard)


def test_the_tighten_guard_refuses_each_of_its_limbs_in_turn(tmp):
    """Every conjunct limb of the guard decides, on its own, to stop the run.

    Asserting that four strings are PRESENT in the guard proves nothing about
    what the guard does: flipping the connective between them, or renaming
    the step whose conclusion it reads, leaves every one of those strings
    exactly where it was. So each limb is flipped in isolation and the whole
    expression is evaluated, which is the only question a reader of the job
    actually has — does a red run commit, and does a pull request commit.
    """
    del tmp
    _, tighten = _tighten_step()
    guard = tighten['if']
    # The context is built from the step ids the guard itself names, so this
    # control and the one below read one source of truth rather than one
    # inventing a second copy of the id. WHICH step that is must be pinned
    # too, and `test_the_tighten_guard_reads_the_step_that_failed_on_a_rise`
    # does that: a context derived from the guard is only as honest as the
    # guard, and deriving it is not a reason to stop checking it.
    steps = {name: {'conclusion': 'success'}
             for name in _guard_step_names(guard)}
    green = {'github': {'event_name': 'push', 'ref': 'refs/heads/main'},
             'steps': steps,
             'status': {'success': True, 'failure': False,
                        'cancelled': False}}
    assert evaluate_if(guard, green) is True, (
        'the tighten guard does not run on a green push to main, so the '
        f'feature never fires and no run will ever tighten the budget: '
        f'{guard}')
    # One flipped context per conjunct limb. Each names the single thing that
    # must stop this run: the job was cancelled, the check found a rise, the
    # event was not a push, or the branch was not main.
    flipped = [{**green, 'status': {
        'success': False, 'failure': False, 'cancelled': True}}]
    failed = {name: {'conclusion': 'failure'} for name in steps}
    flipped += [{**green, 'steps': failed}]
    flipped += [
        {**green, 'github': {'event_name': 'pull_request',
                             'ref': 'refs/heads/main'}},
        {**green, 'github': {'event_name': 'push',
                             'ref': 'refs/heads/other'}}]
    for limb, context in zip(
            ('cancelled', 'the check found a rise', 'not a push',
             'not main'), flipped):
        assert evaluate_if(guard, context) is False, (
            f'the tighten guard ran even though {limb} does not hold: '
            f'{guard}')


def test_the_tighten_guard_reads_the_step_that_failed_on_a_rise(tmp):
    """WHICH step the guard names, not merely that what it names exists.

    Two failures here look the same to every other control. A guard naming
    an id no step declares resolves to empty and the step never runs at all.
    A guard naming a step that DOES run but does not fail on a rise — the
    one that counts instructions, say — resolves fine, passes on a run that
    found a regression, and commits a tightened budget over it. That is the
    defect the guard exists to prevent, so the id is pinned to the step that
    runs the budget check, and that step's own name is pinned to the one
    that says so.
    """
    del tmp
    job, tighten = _tighten_step()
    declared = {step['id']: step for step in job['steps'] if step.get('id')}
    named = set(_guard_step_names(tighten['if']))
    assert named, f'the guard reads no step at all: {tighten["if"]}'
    assert not sorted(named - set(declared)), (
        f'the guard reads steps the job does not declare, so it resolves to '
        f'empty and the step never runs: {sorted(named - set(declared))} vs '
        f'{sorted(declared)}')
    assert named == {'check'}, (
        'the tighten guard does not read the outcome of the step that checks '
        'the journeys against the budget, so it runs whatever those journeys '
        f'did: it reads {sorted(named)} against {sorted(declared)}')
    assert declared['check']['name'] == (
        'Check the journeys against the budget'), declared['check']
    assert '--measurements' in declared['check']['run'] and (
        '--tighten' not in declared['check']['run']), (
        'the step the guard waits on no longer runs the budget check itself, '
        f'so waiting on it means nothing: {declared["check"]["run"]}')


def test_every_step_the_job_reads_is_a_step_the_job_declares(tmp):
    """The same cross-check over the WHOLE job, not the tighten guard alone.

    A guard naming an id no step declares resolves to empty, so whichever
    step reads it either never runs or runs unconditionally. The tighten
    guard is checked limb by limb above; this covers every other reader in
    the job too, so an id renamed in one place and not the other is caught
    wherever it is read.
    """
    del tmp
    job, _ = _tighten_step()
    declared = {step['id'] for step in job['steps'] if step.get('id')}
    readers = [step for step in job['steps']
               if _guard_step_names(step.get('if') or '')]
    assert len(readers) >= 2, (
        'the job reads steps from fewer than two guards, so this control is '
        f'walking a shape the job no longer has: '
        f'{[step.get("name") for step in readers]}')
    for step in readers:
        named = set(_guard_step_names(step['if']))
        missing = sorted(named - declared)
        assert not missing, (
            f'the guard on {step.get("name")!r} reads steps the job does not '
            f'declare, so it resolves to empty and that step does not run as '
            f'written: {missing} vs {sorted(declared)}')


def test_a_journey_recorded_at_null_still_renders_a_row(tmp):
    """The schema admits a null recorded count, and a command must not die.

    `journey_artifact._validated` skips a journey whose recorded count is
    `None` rather than refusing it, so an artefact can name a journey with no
    count yet — which is what a maintainer writes when a fourth journey is
    added before it has been measured. There is no budget to print for such
    a row and no delta against one, so the row says so, and the check
    exits 0 having written the summary rather than dying inside the render.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = recorded_document()
    document['journeys'][names[0]] = None
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(_mixed_report(names)), encoding='utf-8')
    summary = Path(tmp) / 'summary.md'
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    os.environ['GITHUB_STEP_SUMMARY'] = str(summary)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements),
                                '--summary'])
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved
    said = summary.read_text(encoding='utf-8')
    assert code == 0, (
        'a journey recorded at null is a legal artefact, and the check '
        f'said: {said}')
    wanted = f'| {names[0]} '
    row = [line for line in said.splitlines() if line.startswith(wanted)]
    assert row, (
        f'the table carries no row for a journey recorded at null: {said}')
    assert row[0].endswith('| not recorded yet |'), row[0]


def test_a_recorded_gate_that_moved_tightens_nothing_and_succeeds(tmp):
    """A gate that moved is not a regression, so the tighten path agrees.

    `runner_image` is recorded, and `runs-on: ubuntu-latest` moves it on the
    hosted image's own schedule. In that state the check exits 0 — nothing
    was compared, nothing regressed — and a tighten that exited 1 would red
    `journey-budget`, a `needs:` of the aggregate, and through the gate
    patterns every open pull request too. What the tighten refuses is the
    WRITE, and it does not write: so it says what it did, and succeeds.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    measurements = measurements_file(
        Path(tmp) / 'counts.json', None,
        toolchain=dict(IDENTITY, runner_image='ubuntu24 20991231.999.9'))
    summary = Path(tmp) / 'summary.md'
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    os.environ['GITHUB_STEP_SUMMARY'] = str(summary)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements),
                                '--tighten', '--summary'])
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved
    assert code == 0, (
        'a recorded gate that moved is not a regression, and a tighten that '
        'exits nonzero reds a required context every time the hosted runner '
        'image moves')
    assert artifact.read_bytes() == before, (
        'nothing was compared, so nothing may be written')
    said = summary.read_text(encoding='utf-8')
    assert 'toolchain' in said, said
    assert 'nothing was tightened' in said, (
        f'a green run that compared nothing must say so: {said}')
    del names


def _rebaseline():
    """The command a rise is answered with, loaded by its own path.

    Driven here rather than through `journey_budget.main` because the
    refusals under test are the ones that reach `run` itself: a missing
    file, a file the decoder rejects, and a measurement whose rounds
    disagree. The command's own spelling of each is what is pinned, and
    it prints to stderr where a reader of a `check` run never looks.
    """
    return _util.load(ROOT / 'scripts' / 'ci' / 'journey_rebaseline.py',
                      'journey_rebaseline_contract')


def _written_artifact(tmp, document):
    """The recorded budget as bytes, for a control about what a refusal
    leaves on disk.

    Every refusal in `run` writes nothing, so each control below reads the
    file back and compares it with what it wrote — the exit code alone
    would not tell a reader whose recorded budget was still the one that
    holds.
    """
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(_journey_contract.policy().render(document))
    return artifact


def _parse_refusal(source):
    """What the decoder itself says about this file, read the same way.

    `run` prints the exception's own text, so the expectation is computed
    by decoding the same bytes rather than spelled out beside it: a second
    literal could drift from the one the command prints and the control
    would then pass against a refusal that changed.
    """
    try:
        json.loads(source.read_bytes().decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return str(error)
    raise AssertionError(f'{source.name} is readable after all')


def test_a_rebaseline_missing_its_measurement_file_says_which_path(tmp):
    """A measurement file that is not there is the commonest refusal there
    is: the job points at the upload and the upload never arrived.

    The refusal names the path it looked at, because that path is the one
    thing the person who has to fix it cannot otherwise see — a summary
    reading "no measurements file" over a step whose `--measurements`
    argument is scrolled off above it is a question, not a remedy.
    """
    rebaseline = _rebaseline()
    artifact = _written_artifact(tmp, recorded_document())
    before = artifact.read_bytes()
    missing = Path(tmp) / 'journey-counts.json'
    spoken = io.StringIO()
    with contextlib.redirect_stderr(spoken):
        code = rebaseline.run(str(missing), str(artifact))
    said = spoken.getvalue()
    assert code == 1, said
    assert 'no measurements file to read at' in said, said
    assert str(missing) in said, said
    assert artifact.read_bytes() == before, said


def test_a_measurement_file_the_decoder_rejects_is_refused_in_its_words(
        tmp):
    """The parser's own refusal reaches the reader, verbatim.

    Two arms, because they are told apart by that text and nothing else:
    a document truncated mid-object is a JSON error naming a line and a
    column, and bytes that are not UTF-8 at all are a codec error. A
    command that flattened both into "could not read the file" answers a
    question nobody asked — was the upload truncated, or was it something
    other than a measurement?
    """
    rebaseline = _rebaseline()
    artifact = _written_artifact(tmp, recorded_document())
    before = artifact.read_bytes()
    truncated = Path(tmp) / 'truncated.json'
    truncated.write_text('{"rounds": 1, "counters":', encoding='utf-8')
    binary = Path(tmp) / 'binary.json'
    binary.write_bytes(b'\xff\xfe not a measurement at all')
    for source in (truncated, binary):
        spoken = io.StringIO()
        with contextlib.redirect_stderr(spoken):
            code = rebaseline.run(str(source), str(artifact))
        said = spoken.getvalue()
        assert code == 1, (source.name, said)
        assert _parse_refusal(source) in said, (source.name, said)
        assert artifact.read_bytes() == before, (source.name, said)


def test_a_shape_failure_is_refused_with_the_remedy_it_promises(tmp):
    """Rounds that disagree is a refusal, and the remedy rides with it.

    The remedy is what turns "the command said shape" into "here is what
    to do about it", so it is printed whenever the caller supplied one.
    A caller that supplied none gets the refusal on its own: printing a
    remedy it never passed would put words in the repository's mouth at
    the one point the measurement is known to be unusable.
    """
    rebaseline = _rebaseline()
    gate = _journey_contract.policy()
    artifact = _written_artifact(tmp, recorded_document())
    before = artifact.read_bytes()
    reason = 'the mcp-exec journey printed no record'
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(
        {'rounds': 1, 'python': sys.version, 'shape_failure': reason}),
        encoding='utf-8')
    for remedy, expected in ((gate.SHAPE_REMEDY, f'shape: {reason}\n'
                              f'{gate.SHAPE_REMEDY}'),
                             (None, f'shape: {reason}')):
        spoken = io.StringIO()
        with contextlib.redirect_stderr(spoken):
            code = rebaseline.run(str(measurements), str(artifact),
                                  remedy=remedy)
        said = spoken.getvalue()
        assert code == 1, (remedy, said)
        assert said.strip() == expected, (
            f'remedy={remedy is not None}: the refusal must read as the '
            f'shape line and nothing else, or the shape line and the '
            f'remedy, got: {said!r}')
        assert artifact.read_bytes() == before, (remedy, said)


def _rebaseline_over(report, tmp):
    """Drive the real rebaseline and report what it did to the artefact.

    Returns `(code, wrote, said)`. `wrote` is read off the bytes rather than
    off the exit status, because a refusal that wrote anyway is exactly the
    case an exit code alone would hide.
    """
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['rebaseline', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    return code, artifact.read_bytes() != before, spoken.getvalue()


def _assert_refused(report, tmp, what):
    """A measurement this incomplete must leave the artefact untouched.

    Each of these three refusals was planted out in turn and each one WROTE
    an artefact with exit 0 and no output at all: `_validated` accepts the
    result, so the incompleteness travelled into the committed baseline
    rather than stopping the run. The damage is downstream — a count of
    `null` compares as nothing, an empty toolchain reads as a match
    against every measured value — which is why each gets its own case
    rather than one over all three.
    """
    code, wrote, said = _rebaseline_over(report, tmp)
    assert not wrote, (
        f'{what} was recorded anyway, so the committed budget now describes '
        f'a measurement that was never made: {said}')
    assert code != 0, (
        f'{what} left the artefact byte-identical and still reported '
        f'success, so the refusal is doing nothing a reader can see: {said}')


def test_a_measurement_with_no_count_for_a_journey_is_not_recorded(tmp):
    """A journey with no count records nothing, rather than a null.

    `violations()` and `tightened()` both skip a null recorded count, so a
    `null` in the journeys map is a budget that compares nothing — the
    budget going inert rather than a run going red.
    """
    report = _journey_contract.fixture_report()
    report['counters']['valgrind-callgrind']['journeys'].pop(
        journeys().NAMES[0])
    _assert_refused(report, tmp, 'a journey with no count')


def test_a_measurement_with_no_toolchain_is_not_recorded(tmp):
    """An empty toolchain records nothing.

    `_validated` accepts an all-null toolchain, so this one reaches the
    artefact rather than being caught on the way: every field then reads as
    a match against whatever the next run measures, and no count is ever
    compared against a recorded one again.
    """
    report = _journey_contract.fixture_report()
    report['toolchain'] = {}
    _assert_refused(report, tmp, 'a measurement with no toolchain identity')


def test_a_measurement_missing_a_journeys_threads_is_not_recorded(tmp):
    """A journey whose excluded threads go unrecorded records nothing.

    A count that drops a background thread means something the count alone
    cannot say, so `excluded_threads` is what says it. Writing a document
    without that for one journey leaves the count looking comparable to
    every other run's, which is the comparison the field exists to refuse.
    """
    report = _journey_contract.fixture_report()
    report['excluded_threads'].pop(journeys().NAMES[0])
    _assert_refused(report, tmp,
                    'a measurement that excludes no thread for one journey')


def test_rounds_that_disagree_about_a_sha_are_not_rebaselined_over(tmp):
    """A re-baseline records a journey's rendering, so it may only record one
    the rounds agreed on.

    This is the one refusal in the module whose failure is not an
    over-refusal. The others stop a human whose measurement was unusable,
    and a human reads the message and fixes the input. This one, if it stops
    refusing, writes a sha no journey rendered: the next run's sha gate
    then refuses to compare, every journey goes unrecorded against the new
    baseline, and the budget goes inert — silently, with every check green.
    So the write is the thing under test, not the exit code alone.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    report = _journey_contract.fixture_report()
    # `--rounds 3` whose rounds disagree about one journey: the count is
    # real, the rendering is not.
    report['shas'][names[0]] = ['a' * 64, 'b' * 64]
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['rebaseline', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code != 0, (
        'a measurement whose rounds disagree about what a journey rendered '
        'was rebaselined, so the artefact now records a sha no journey '
        'produced and the next run cannot compare against it')
    assert artifact.read_bytes() == before, (
        'a re-baseline over rounds that disagree wrote the artefact anyway')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeytighten_')


if __name__ == '__main__':
    raise SystemExit(main())
