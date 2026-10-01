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
    ROOT,
    budget_document,
    journeys,
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
    return {'rounds': 1, 'python': sys.version, **recorded_maps(),
            'counters': {'perf-instructions': {
                'available': True, 'startup_only': 0,
                'journeys': {name: {'min': seen, 'max': seen,
                                    'median': seen, 'spread': 0, 'raw': seen}
                             for name, seen in measured.items()}}}}


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
    assert written['thread_bands'] == report['thread_bands'], written
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
    green = {'github': {'event_name': 'push', 'ref': 'refs/heads/main'},
             'steps': {'check': {'conclusion': 'success'}},
             'status': {'success': True, 'failure': False,
                        'cancelled': False}}
    assert evaluate_if(guard, green) is True, guard
    # One flipped context per conjunct limb. Each names the single thing that
    # must stop this run: the job was cancelled, the check found a rise, the
    # event was not a push, or the branch was not main.
    for limb, context in (
            ('cancelled', {**green, 'status': {
                'success': False, 'failure': False, 'cancelled': True}}),
            ('check concluded', {**green, 'steps': {
                'check': {'conclusion': 'failure'}}}),
            ('not a push', {**green, 'github': {
                'event_name': 'pull_request', 'ref': 'refs/heads/main'}}),
            ('not main', {**green, 'github': {
                'event_name': 'push', 'ref': 'refs/heads/other'}})):
        assert evaluate_if(guard, context) is False, (
            f'the tighten guard ran even though {limb} does not hold: '
            f'{guard}')


def test_the_tighten_guard_reads_a_step_the_job_actually_declares(tmp):
    """`steps.check.conclusion` must name a real step, or the guard is a
    lookup that always resolves empty.

    A guard naming an id no step declares is not a weakened guard, it is a
    step that never runs on any push — the feature is dead and nothing says
    so, because the guard itself still reads correctly.
    """
    del tmp
    job, tighten = _tighten_step()
    declared = {step['id']: step for step in job['steps'] if step.get('id')}
    # Every step the guard reads, read back off the guard rather than off a
    # literal list beside it: a step id added there tomorrow is covered
    # today.
    named = set(re.findall(r'steps\.([A-Za-z_][\w-]*)\.', tighten['if']))
    assert named, f'the guard reads no step at all: {tighten["if"]}'
    assert not sorted(named - set(declared)), (
        f'the guard reads steps the job does not declare, so it resolves to '
        f'empty and the step never runs: {sorted(named - set(declared))} vs '
        f'{sorted(declared)}')
    assert declared['check']['name'] == (
        'Check the journeys against the budget'), declared['check']


def test_every_deploy_key_push_carries_its_own_rejection_discrimination(tmp):
    """Each copy of the write credential must answer for its own failed push.

    There are two of them because the coverage job's is a required context
    that had to stay byte-identical, and two copies of a security-shaped
    block is where they drift: the second is a copy, so an edit to the
    discrimination in the first does not follow it. This walks EVERY step in
    every workflow whose body holds the key rather than naming either copy,
    so the third one is covered when it is written.

    A rejected push has two causes and they must not be confused. Main
    moving under a run is ordinary and the next push retries; a rejection
    with main standing still is a real failure — a revoked key, a ruleset
    refusal, a hook — and reporting that green is how it would go unnoticed.
    """
    del tmp
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    job = complete_job_mapping(source, 'coverage')
    assert job is not None, 'the coverage job is not in tests.yml'
    holders = [step for step in job['steps']
               if 'RATCHET_SSH_KEY' in (step.get('run') or '')]
    journey = complete_job_mapping(source, 'journey-budget')
    assert journey is not None, 'the journey-budget job is not in tests.yml'
    holders += [step for step in journey['steps']
                if 'RATCHET_SSH_KEY' in (step.get('run') or '')]
    assert len(holders) >= 2, (
        'the deploy key now reaches fewer than two steps, so this control is '
        f'walking a shape the workflow no longer has: '
        f'{[step.get("name") for step in holders]}')
    for step in holders:
        body = step['run']
        name = step.get('name')
        for wanted in (
                'git fetch --quiet "git@github.com:${REPO}.git" main',
                'git rev-parse HEAD^',
                'git rev-parse FETCH_HEAD',
        ):
            assert wanted in body, (
                f'the {name!r} step does not {wanted!r}, so a push it rejects '
                'for a real reason is reported as main having moved: '
                f'{body}')
        assert '--force' not in body, (
            f'the {name!r} step force-pushes to main: {body}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeytighten_')


if __name__ == '__main__':
    raise SystemExit(main())
