"""Exercise ratchet pushes against real local git repositories."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import evaluate  # noqa: E402
from _wfjobs import jobs_mapping  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

ROOT = _util.ROOT

PUSH = ROOT / 'scripts' / 'ci' / 'ratchet_push.py'
SECRET_CONTEXT = re.compile(r'\bsecrets\b', re.IGNORECASE)


def _expression_uses_secrets(source):
    index = 0
    while (start := source.find('${{', index)) >= 0:
        index = start + 3
        quote = None
        unquoted = []
        while index < len(source):
            char = source[index]
            if quote:
                if char == quote:
                    if source[index:index + 2] == quote * 2:
                        index += 2
                        continue
                    quote = None
            elif char in "'\"":
                quote = char
                unquoted.append(' ')
            elif source.startswith('}}', index):
                index += 2
                break
            else:
                unquoted.append(char)
            index += 1
        if SECRET_CONTEXT.search(''.join(unquoted)):
            return True
    return False


def _uses_secrets_context(value):
    if isinstance(value, str):
        return _expression_uses_secrets(value)
    if isinstance(value, dict):
        value = list(value.items())
    return isinstance(value, (list, tuple)) and any(
        map(_uses_secrets_context, value))


def _summary_text(summary):
    """What the step summary says, or '' when nothing wrote one."""
    return summary.read_text(encoding='utf-8') if summary.exists() else ''


def _push_repo(base, remote_at=None):
    """A checkout and bare remote, redirected through git's URL rewrite."""
    root = Path(base) / 'tree'
    work, bare = root, root / 'bare.git'
    subject = root / 'scripts' / 'ci' / 'ratchet_push.py'
    subject.parent.mkdir(parents=True)
    shutil.copy2(PUSH, subject)
    bare.mkdir(parents=True)
    _git(bare, 'init', '--quiet', '--bare', '-b', 'main')
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
        # on the bare repo and only the script's own push and fetch find a
        # remote that cannot answer.
        _git(work, 'config', f'url.{remote_at}.insteadOf',
             'git@github.com:o/r.git')
        _git(work, 'config', '--unset', f'url.{bare}.insteadOf')
    return work, bare


def _drive_push(work, refuse, change=True, arguments=None, environment=None):
    """Run the copied script against a real repository."""
    if refuse:
        hook = work / 'bare.git' / 'hooks' / 'pre-receive'
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
    # The COPY, not `PUSH`: it stands where the collector can see it, and
    # `paths` maps that path back onto the repository's own file.
    child = dict(os.environ,
                 HOME=str(work.parent / 'home'),
                 DAEDALUS_REPO='o/r',
                 DAEDALUS_MASTER_PUSH_DEPLOY_KEY='not-a-real-key',
                 GITHUB_STEP_SUMMARY=str(summary))
    for name, value in (environment or {}).items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value
    return subprocess.run(
        [sys.executable, str(work / 'scripts' / 'ci' / 'ratchet_push.py'),
         *(arguments if arguments is not None else
           ['ratcheted.json', 'ci: tighten the journey budget'])],
        cwd=str(work), capture_output=True, text=True,
        env=_util.child_coverage('keep', child, cwd=work)), summary


def test_the_push_script_tells_a_refusal_from_a_concurrent_push(tmp):
    """Both branches of the discrimination, driven through the real script."""
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
    """The third branch of the script: the one where the push works."""
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
    """A fetch that cannot read main is a failure, not evidence of movement."""
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
    """A push recording nothing is a failure, not a green."""
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


def test_a_refusal_names_what_was_missing_and_runs_no_git(tmp):
    """Missing inputs fail before git; the secret is named for repair."""
    base = Path(tmp) / 'refusals'
    work, bare = _push_repo(base)
    message = 'ci: tighten the journey budget'
    usage = 'usage: ratchet_push.py <path> <commit message>'
    before = subprocess.run(['git', '-C', str(bare), 'rev-parse', 'main'],
                            capture_output=True, text=True, check=True)
    for arguments, environment, wanted in (
            ([], {}, usage),
            (['ratcheted.json'], {}, usage),
            ([message], {}, usage),
            (['ratcheted.json', ''], {}, usage),
            (['', message], {}, usage),
            (['ratcheted.json', message],
             {'DAEDALUS_MASTER_PUSH_DEPLOY_KEY': None},
             'DAEDALUS_MASTER_PUSH_DEPLOY_KEY is not set; '
             'ratchet change is not pushed'),
            (['ratcheted.json', message],
             {'DAEDALUS_MASTER_PUSH_DEPLOY_KEY': ''},
             'DAEDALUS_MASTER_PUSH_DEPLOY_KEY is not set; '
             'ratchet change is not pushed'),
            (['ratcheted.json', message], {'DAEDALUS_REPO': None},
             'the repository is not set; there is nowhere to push'),
            (['ratcheted.json', message], {'DAEDALUS_REPO': ''},
             'the repository is not set; there is nowhere to push')):
        outcome, _summary = _drive_push(
            work, refuse=False, change=False, arguments=arguments,
            environment=environment)
        said = f'{outcome.stdout}{outcome.stderr}'
        assert outcome.returncode == 2, (arguments, environment, said)
        assert wanted in outcome.stderr, (
            f'arguments={arguments!r} {environment!r}: the refusal must name '
            f'what was wrong, and said: {said!r}')
    after = subprocess.run(['git', '-C', str(bare), 'rev-parse', 'main'],
                           capture_output=True, text=True, check=True)
    assert after.stdout == before.stdout, (
        'a refusal still pushed, so the ratchet landed from a run that '
        'recorded nothing')
    summary = work.parent / 'summary.md'
    assert 'Main moved' not in _summary_text(summary), _summary_text(summary)


def test_both_jobs_call_the_one_push_implementation(tmp):
    del tmp
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    seen = {}
    for name in ('coverage', 'journey-budget'):
        job = complete_job_mapping(source, name)
        assert job is not None, f'the {name} job is not in tests.yml'
        for step in job['steps']:
            body = step.get('run') or ''
            if 'DAEDALUS_MASTER_PUSH_DEPLOY_KEY' not in str(step):
                continue
            assert step['env']['DAEDALUS_MASTER_PUSH_DEPLOY_KEY'] == (
                '${{ secrets.MASTER_PUSH_DEPLOY_KEY }}'), step
            assert 'ratchet_push.py' in body, (
                f'the {step.get("name")!r} step holds the deploy key and does '
                f'not call the shared script, so a second copy of the push '
                f'is back: {body}')
            call = body.replace('\\\n', ' ')
            assert len(shlex.split(
                call.split('ratchet_push.py', 1)[1])) == 2, (
                'the push script takes exactly the committed path and the '
                f'commit message; the call does not supply both: {body}')
            seen[name] = body
    assert sorted(seen) == ['coverage', 'journey-budget'], sorted(seen)


def test_push_secrets_bind_only_the_main_push_environment(tmp):
    del tmp
    consumers = {}
    for workflow in ('tests.yml', 'comment-share.yml'):
        jobs = jobs_mapping((ROOT / '.github/workflows' / workflow).read_text(
            encoding='utf-8'))
        assert jobs is not None, workflow
        consumers.update(((workflow, name), job) for name, job in jobs.items()
                         if _uses_secrets_context(job))
    assert {('tests.yml', 'coverage'), ('tests.yml', 'journey-budget'),
            ('comment-share.yml', 'comment-share')} <= consumers.keys()
    for name, job in consumers.items():
        expression = job.get('environment', '')
        for event, ref, expected in (
                ('push', 'refs/heads/main', 'main-push'),
                ('pull_request', 'refs/heads/main', ''),
                ('pull_request', 'refs/pull/1/merge', ''),
                ('push', 'refs/heads/other', ''),
                ('push', 'refs/tags/v1', ''),
                ('workflow_dispatch', 'refs/heads/main', ''),
                ('workflow_dispatch', 'refs/heads/other', '')):
            actual = (evaluate(expression, {'github': {
                'event_name': event, 'ref': ref}})
                if expression.startswith('${{') else expression)
            assert actual == expected, (name, event, ref, actual)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ratchetpush_')


def test_prefixed_credentials_precede_legacy_environment(tmp):
    from unittest.mock import patch
    mod = _util.load(PUSH, 'ratchet_environment')
    for primary in ('new', '', None):
        values = {'REPO': 'o/r', 'MASTER_PUSH_DEPLOY_KEY': 'fixture-key'}
        env = {k: 'ignored' if primary else v for k, v in values.items()}
        env.update(('DAEDALUS_' + k, v if primary else primary)
                   for k, v in values.items() if primary is not None)
        env['USERPROFILE' if os.name == 'nt' else 'HOME'] = str(tmp)
        with (patch.dict(os.environ, env, clear=True),
              patch.object(mod, '_git') as git):
            assert mod.main(['ratcheted.json', 'ci: tighten']) == 0
        assert git.call_args.args == ('push', 'git@github.com:o/r.git',
                                      'HEAD:main'), git.call_args
        assert (Path(tmp) / '.ssh/ratchet').read_text() == 'fixture-key\n'


if __name__ == '__main__':
    raise SystemExit(main())
