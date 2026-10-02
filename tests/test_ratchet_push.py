#!/usr/bin/env python3
"""The ratchet push, exercised against a real repository rather than read.

Its own module because `test_journey_tighten.py` holds the CLI and the
workflow guard and reached the 700-line ceiling, and these are a different
subject rather than more of the same: `scripts/ci/ratchet_push.py` is the
one step holding a write credential, so its behaviour is pinned by RUNNING
it against a real bare repository and reading what each state did, rather
than by reading its text.

Reading cannot settle what these controls settle. `=` and `!=` are two
behaviourally different versions of the same three tokens, and a control
that accepts both pins neither. So the fixtures drive the real script as a
child process, with git's own `url.<base>.insteadOf` pointing its remote at
a local bare repository, and every state is a real git state: a
`pre-receive` refusal, a non-fast-forward rejection from a concurrent push, a
remote that cannot answer, and a commit with nothing to commit.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

ROOT = _util.ROOT

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
    summary = work.parent / 'summary.md'
    # The declaration is at the launch rather than at a name: this helper
    # gives every test its own HOME and its own summary path, and a
    # module-level environment could carry neither.
    return subprocess.run(
        [sys.executable, str(PUSH), 'ratcheted.json',
         'ci: tighten the journey budget'],
        cwd=str(work), capture_output=True, text=True,
        env=_util.child_coverage('scrub', dict(
            os.environ,
            HOME=str(work.parent / 'home'),
            REPO='o/r',
            RATCHET_SSH_KEY='not-a-real-key',
            GITHUB_STEP_SUMMARY=str(summary)))), summary


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
    return _util.runner(_util.collect(globals()), tmp_prefix='ratchetpush_')


if __name__ == '__main__':
    raise SystemExit(main())
