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
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _journey_contract  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
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


PUSH = ROOT / 'scripts' / 'ci' / 'ratchet_push.py'


def _summary_text(summary):
    """What the step summary says, or '' when nothing wrote one.

    An absent summary is not an error here: several correct paths write
    nothing, and "no Main moved line" is exactly what those paths should
    leave behind.
    """
    return summary.read_text(encoding='utf-8') if summary.exists() else ''


def _push_repo(base, remote_at=None):
    """A working checkout with one commit, and a bare repo standing in for
    github.

    The remote the script builds is `git@github.com:${REPO}.git`, so git's own
    `url.<base>.insteadOf` maps it onto the bare repo. Nothing else is
    stubbed: `push` and `fetch` are the real git against a real repository,
    and the script's reads of HEAD^ and FETCH_HEAD are the repository's.

    `remote_at` points the mapping somewhere else — at a path that is not a
    repository, which is what an unreachable host looks like to git. Both the
    push and the fetch then fail against a remote that cannot answer, which
    is the transient failure the recovery branch must not read as movement.
    """
    work, bare = Path(base) / 'work', Path(base) / 'bare.git'
    bare.mkdir(parents=True)
    _git(bare, 'init', '--quiet', '--bare', '-b', 'main')
    work.mkdir()
    _git(work, 'init', '--quiet', '-b', 'main')
    _git(work, 'config', 'user.email', 'tests@example.invalid')
    _git(work, 'config', 'user.name', 'Tests')
    _git(work, 'config', f'url.{bare}.insteadOf', 'git@github.com:o/r.git')
    _git(work, 'remote', 'add', 'origin', 'git@github.com:o/r.git')
    (work / 'ratcheted.json').write_text('{"n": 2}\n', encoding='utf-8')
    _git(work, 'add', 'ratcheted.json')
    _git(work, 'commit', '--quiet', '-m', 'base')
    _git(work, 'push', '--quiet', 'origin', 'main')
    if remote_at:
        # Re-point AFTER the setup push, so the checkout has a real history
        # on the bare repo and only the script's own push and fetch find a
        # remote that cannot answer.
        _git(work, 'config', f'url.{remote_at}.insteadOf',
             'git@github.com:o/r.git')
        _git(work, 'config', '--unset', f'url.{bare}.insteadOf')
    return work, bare


def _drive_push(work, refuse, change=True):
    """Run the real script over the prepared checkout.

    `refuse` installs a pre-receive hook that rejects everything, which is
    what a revoked key or a ruleset refusal looks like from the pushing side:
    the push fails and main does not move. Without it the push is real, and
    whether it is rejected is then decided by whether main has moved.

    `change` is what gets committed. Leaving it False stages nothing, so
    `git commit` has nothing to commit and fails — the route where the
    recorded file was already committed by an earlier step.
    """
    if refuse:
        hook = work.parent / 'bare.git' / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)
    # The change is left UNCOMMITTED: staging and committing it is the
    # script's own work, and pre-committing here would leave the script
    # with nothing to commit on every leg.
    if change:
        (work / 'ratcheted.json').write_text('{"n": 1}\n', encoding='utf-8')
    env = dict(os.environ,
               HOME=str(work.parent / 'home'),
               REPO='o/r',
               RATCHET_SSH_KEY='not-a-real-key')
    summary = work.parent / 'summary.md'
    env['GITHUB_STEP_SUMMARY'] = str(summary)
    return subprocess.run(
        [sys.executable, str(PUSH), 'ratcheted.json',
         'ci: tighten the journey budget'],
        cwd=str(work), capture_output=True, text=True, env=env), summary


def test_the_push_script_tells_a_refusal_from_a_concurrent_push(tmp):
    """Both branches of the discrimination, driven through the real script.

    A rejected push has two causes and they must not be confused. Main
    moving under a run is ordinary and the next push retries; a rejection
    with main standing still is a real failure — a revoked key, a ruleset
    refusal, a hook — and reporting that green is how it goes unnoticed. The
    converse reds a required context on an ordinary concurrent push.

    This runs the script rather than reading it, because the connective
    between two `git rev-parse` lines is exactly what no grep can see: `=`
    and `!=` are two behaviourally different versions of the same three
    tokens, and a control that accepts both pins neither. Both jobs call this
    one script, so there is no second copy to drift.
    """
    base = Path(tmp) / 'stood-still'
    work, _ = _push_repo(base)
    outcome, _summary = _drive_push(work, refuse=True)
    assert outcome.returncode != 0, (
        'a push rejected while main stood still was reported as a success, so '
        f'a revoked key or a ruleset refusal would never be seen: '
        f'{outcome.stdout}{outcome.stderr}')
    assert 'stood still' in outcome.stderr, outcome.stderr

    base = Path(tmp) / 'concurrent'
    work, bare = _push_repo(base)
    # main moves under the run: someone else pushes between our fetch and
    # our comparison, which is what an ordinary concurrent push looks like.
    other = base / 'other'
    other.mkdir()
    _git(base, 'clone', '--quiet', str(bare), str(other))
    _git(other, 'config', 'user.email', 'other@example.invalid')
    _git(other, 'config', 'user.name', 'Other')
    (other / 'unrelated.txt').write_text('x\n', encoding='utf-8')
    _git(other, 'add', 'unrelated.txt')
    _git(other, 'commit', '--quiet', '-m', 'concurrent')
    _git(other, 'push', '--quiet', 'origin', 'main')
    outcome, summary = _drive_push(work, refuse=False)
    assert outcome.returncode == 0, (
        'an ordinary concurrent push reddened a required context, which is '
        f'the outcome the discrimination exists to avoid: '
        f'{outcome.stdout}{outcome.stderr}')
    assert 'stood still' not in outcome.stderr, outcome.stderr
    assert 'Main moved while this run measured' in _summary_text(
        summary), _summary_text(summary)


def test_a_push_that_succeeds_says_nothing_about_main_moving(tmp):
    """The third branch of the script: the one where the push works.

    Without an early exit a successful push falls through to the recovery
    path, the comparison there is false, and every run that pushed
    successfully writes "Main moved while this run measured" into its
    summary. The refusal legs cannot see it, because they never get that
    far.
    """
    base = Path(tmp) / 'success'
    work, _bare = _push_repo(base)
    outcome, summary = _drive_push(work, refuse=False)
    assert outcome.returncode == 0, (
        'a push that succeeded was reported as a failure, so every ordinary '
        f'run reddens a required context: {outcome.stderr}')
    said = _summary_text(summary)
    assert 'Main moved' not in said, (
        'a push that succeeded reported that main had moved under the run: '
        f'{said}')
    assert 'stood still' not in outcome.stderr, outcome.stderr


def test_a_failed_fetch_aborts_rather_than_claiming_main_moved(tmp):
    """A fetch that cannot read main is a failure, not evidence of movement.

    This is the route the inline block this script replaced handled by
    aborting red under the shell Actions runs a `run:` step with. Without
    that, an unreadable `FETCH_HEAD` compares unequal to `HEAD^`, the
    comparison is false, and the script concludes — in the summary, with
    exit 0 — that main moved. On a required context that is a false green
    dressed as the exact reassurance the discrimination exists to give.
    """
    base = Path(tmp) / 'unreadable-main'
    # The remote cannot answer at all, so both the push and the fetch fail.
    work, _bare = _push_repo(base, remote_at=str(base / 'no-such-remote'))
    outcome, summary = _drive_push(work, refuse=False)
    assert outcome.returncode != 0, (
        'a fetch that could not read main still reported success, so a '
        'transient network failure reads as main having moved: '
        f'{outcome.stdout}{outcome.stderr}{_summary_text(summary)}')
    assert 'Main moved' not in _summary_text(summary), (
        'a fetch that could not read main was reported as main having moved: '
        f'{_summary_text(summary)}')


def test_a_commit_with_nothing_to_commit_aborts(tmp):
    """A push recording nothing is a failure, not a green.

    `git add` matches nothing when the file a previous step already
    committed, or when it was renamed away. `git commit` then fails, and a
    script that carries on pushes a no-op fast-forward and reports success
    having written no ratchet commit at all.
    """
    base = Path(tmp) / 'nothing-to-commit'
    work, bare = _push_repo(base)
    before = subprocess.run(['git', '-C', str(bare), 'rev-parse', 'main'],
                            capture_output=True, text=True, check=True)
    outcome, summary = _drive_push(work, refuse=False, change=False)
    assert outcome.returncode != 0, (
        'a commit with nothing to commit reported success, so the job went '
        f'green having recorded nothing: {outcome.stdout}{outcome.stderr}')
    after = subprocess.run(['git', '-C', str(bare), 'rev-parse', 'main'],
                           capture_output=True, text=True, check=True)
    assert after.stdout == before.stdout, (
        'the remote took a commit from a run that had nothing to record')
    # Aborting is the claim; saying main had moved is the shape of the same
    # mistake this control exists to catch, on the route where the push
    # itself succeeds and the recovery branch is what would run.
    assert 'Main moved' not in _summary_text(summary), (
        'a run with nothing to commit reported that main had moved: '
        f'{_summary_text(summary)}')


def test_both_jobs_call_the_one_push_implementation(tmp):
    """One script, called with each job's own file and message.

    The drift this retires was two copies of a write credential. There is
    now one. The walk names the two jobs that hold the key, so a third
    holder elsewhere in the file is not covered by it — that is the limit of
    this control, stated rather than implied.
    """
    del tmp
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    seen = {}
    for name in ('coverage', 'journey-budget'):
        job = complete_job_mapping(source, name)
        assert job is not None, f'the {name} job is not in tests.yml'
        for step in job['steps']:
            body = step.get('run') or ''
            # The key is in the step's env block, not its body: the
            # body is the script call.
            if 'RATCHET_SSH_KEY' not in str(step):
                continue
            assert 'ratchet_push.py' in body, (
                f'the {step.get("name")!r} step holds the deploy key and does '
                f'not call the shared script, so a second copy of the push '
                f'is back: {body}')
            # The line continuations go first: left in, `shlex` counts each
            # trailing backslash as an argument of its own and a one-argument
            # call parses as two.
            call = body.replace('\\\n', ' ')
            assert len(shlex.split(
                call.split('ratchet_push.py', 1)[1])) == 2, (
                'the push script takes exactly the committed path and the '
                f'commit message; the call does not supply both: {body}')
            seen[name] = body
    assert sorted(seen) == ['coverage', 'journey-budget'], sorted(seen)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeytighten_')


if __name__ == '__main__':
    raise SystemExit(main())
