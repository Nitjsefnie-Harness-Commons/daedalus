#!/usr/bin/env python3
"""Drive release dispatch through real git remotes and the gh executable."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _fake_gh import FakeGh  # noqa: E402
from _ghexpr import evaluate, evaluate_if  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402
from _workflowrun import run_step  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402

ROOT = _util.ROOT
SCRIPT = Path('scripts/ci/release_dispatch.py')
SHA = 'a' * 40
VERSION = '0.28.0'


def _out(repo, *args):
    return subprocess.run(
        ['git', '-C', str(repo), *args], check=True, capture_output=True,
        text=True, env=_util.child_coverage('scrub')).stdout.strip()


def _repo(tmp):
    work, bare = Path(tmp) / 'tree', Path(tmp) / 'bare.git'
    work.mkdir()
    _git(work, 'init', '--quiet', '-b', 'main')
    _git(work, 'config', 'user.name', 'Tests')
    _git(work, 'config', 'user.email', 'tests@example.invalid')
    bare.mkdir()
    _git(bare, 'init', '--bare', '--quiet', '-b', 'main')
    for relative in (
            SCRIPT, Path('scripts/ci/ratchet_push.py'),
            Path('scripts/check_versions.py'),
            Path('scripts/version_regions.py'),
            Path('scripts/version_html_context.py'),
            Path('extension/manifest.json'), Path('extension/background.js'),
            Path('extension/page.js'), Path('dashboard/index.html'),
            Path('daedalus_cli/__init__.py')):
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        assert (ROOT / relative).is_file(), f'missing {relative}'
        shutil.copy2(ROOT / relative, target)
    _git(work, 'add', '.')
    _git(work, 'commit', '--quiet', '-m', 'base')
    _git(work, 'config', f'url.{bare}.insteadOf', 'git@github.com:o/r.git')
    _git(work, 'remote', 'add', 'origin', 'git@github.com:o/r.git')
    _git(work, 'push', '--quiet', 'origin', 'main')
    return work, bare


def _drive(work, *args, env=None):
    home = work.parent / 'home'
    home.mkdir(exist_ok=True)
    child = dict(os.environ, **(env or {}))
    child.update(HOME=str(home), USERPROFILE=str(home),
                 MASTER_PUSH_DEPLOY_KEY='fixture-key', REPO='o/r')
    child.setdefault('GITHUB_OUTPUT', str(work.parent / 'output'))
    return subprocess.run(
        [sys.executable, str(work / SCRIPT), *args], cwd=work,
        env=_util.child_coverage('keep', child, cwd=work),
        capture_output=True, text=True, timeout=60)


def _ok(result):
    assert result.returncode == 0, result.stdout + result.stderr


def test_bump_current_main_all_sites_and_output(tmp):
    work, bare = _repo(tmp)
    stale = _out(work, 'rev-parse', 'HEAD')
    (work / 'concurrent').write_text('main moved before dispatch\n')
    _git(work, 'add', 'concurrent')
    _git(work, 'commit', '--quiet', '-m', 'advance')
    _git(work, 'push', '--quiet', 'origin', 'main')
    current = _out(work, 'rev-parse', 'HEAD')
    _git(work, 'reset', '--hard', stale)
    (work / 'untracked').write_text('never stage me\n')
    output = work.parent / 'output'
    result = _drive(work, 'bump', VERSION,
                    env={'GITHUB_OUTPUT': str(output)})
    _ok(result)
    sha = result.stdout.strip()
    assert sha == _out(bare, 'rev-parse', 'main'), result.stdout
    assert _out(work, 'rev-parse', 'HEAD^') == current
    assert output.read_text() == f'sha={sha}\n'
    assert 'untracked' not in _out(
        work, 'ls-tree', '-r', '--name-only', 'HEAD')
    checked = subprocess.run(
        [sys.executable, 'scripts/check_versions.py', '--print'], cwd=work,
        capture_output=True, text=True, check=True,
        env=_util.child_coverage('keep', cwd=work))
    assert checked.stdout.strip() == VERSION, checked.stdout
    _ok(_drive(work, 'bump', VERSION, '--successor'))
    assert f'release {VERSION} published' in _out(
        work, 'log', '-1', '--format=%s')
    assert '0.28.0.1' in (work / 'extension/manifest.json').read_text()


def test_bump_no_change_and_illegal_version_refuse(tmp):
    work, bare = _repo(tmp)
    _ok(_drive(work, 'bump', VERSION))
    before = _out(bare, 'rev-parse', 'main')
    for value, wanted in ((VERSION, 'already claims'),
                          (VERSION + '.2', 'successor'),
                          ('not-a-version', 'FAIL:')):
        result = _drive(work, 'bump', value)
        assert result.returncode != 0, result.stdout
        assert wanted in result.stderr, result.stderr
        assert before == _out(bare, 'rev-parse', 'main')


def _race(work, permanent=False):
    hook = work / '.git/hooks/pre-push'
    hook.write_text(
        '#!/bin/sh\n'
        'old=$(git rev-parse refs/remotes/origin/main)\n'
        'tree=$(git rev-parse "$old^{tree}")\n'
        'next=$(printf "concurrent\\n" | git --git-dir="../bare.git" '
        '-c user.name=Tests -c user.email=tests@example.invalid '
        'commit-tree "$tree" -p "$old")\n'
        'git --git-dir="../bare.git" update-ref refs/heads/main "$next"\n'
        + ('' if permanent else 'rm -- "$0"\n'), encoding='utf-8',
        newline='\n')
    hook.chmod(0o755)


def test_bump_retries_a_moved_main(tmp):
    work, bare = _repo(tmp)
    _race(work)
    result = _drive(work, 'bump', VERSION)
    _ok(result)
    assert result.stdout.strip() == _out(bare, 'rev-parse', 'main')
    assert _out(work, 'log', '-2', '--format=%s').splitlines() == [
        f'Claim {VERSION} across every version site', 'concurrent']
    assert 'retry' in result.stderr.lower(), result.stderr


def test_bump_permanent_refusal_is_loud_and_bounded(tmp):
    work, bare = _repo(tmp)
    before = _out(bare, 'rev-parse', 'main')
    hook = bare / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\necho "fixture refusal" >&2\nexit 1\n',
                    encoding='utf-8', newline='\n')
    hook.chmod(0o755)
    result = _drive(work, 'bump', VERSION)
    assert result.returncode != 0, result.stdout
    assert 'fixture refusal' in result.stderr, result.stderr
    assert before == _out(bare, 'rev-parse', 'main')
    hook.unlink()
    _race(work, permanent=True)
    result = _drive(work, 'bump', VERSION)
    assert result.returncode != 0, result.stdout
    assert '3 attempts' in result.stderr, result.stderr
    assert 'rejected' in result.stderr.lower(), result.stderr


def test_annotated_tag_push_and_no_overwrite(tmp):
    work, bare = _repo(tmp)
    sha = _out(work, 'rev-parse', 'HEAD')
    _ok(_drive(work, 'tag', VERSION, sha))
    assert _out(bare, 'cat-file', '-t', f'v{VERSION}') == 'tag'
    assert _out(bare, 'rev-parse', f'v{VERSION}^{{}}') == sha
    result = _drive(work, 'tag', VERSION, sha)
    assert result.returncode != 0, result.stdout
    assert 'already exists' in result.stderr, result.stderr


def _row(name='tests', status='completed', conclusion='success', **extra):
    return {'name': name, 'status': status, 'conclusion': conclusion,
            'event': 'push', **extra}


def _wait(tmp, mode, pages, *args):
    work = Path(tmp) / 'tree'
    (work / SCRIPT.parent).mkdir(parents=True)
    for name in ('release_dispatch.py', 'ratchet_push.py'):
        source = ROOT / SCRIPT.parent / name
        assert source.is_file(), f'missing {source.name}'
        shutil.copy2(source, work / SCRIPT.parent / name)
    endpoint = f'repos/o/r/actions/runs?head_sha={SHA}'
    fake = FakeGh(Path(tmp) / 'gh', {endpoint: pages})
    result = _drive(work, mode, SHA, '--interval', '0.001', *args,
                    env=fake.env())
    for call in fake.calls():
        assert '--paginate' in call['argv'], call
        assert 'Cache-Control: no-cache' in call['argv'], call
        assert call['request'] == endpoint, call
    return result, fake


def test_wait_gates_pending_then_green_with_exact_exclusions(tmp):
    excluded = [_row('release', conclusion='failure'),
                _row('pr gate', conclusion='failure')]
    comment = _row('comment gate', conclusion='failure')
    comment['event'] = 'issue_comment'
    excluded.append(comment)
    pages = [{'workflow_runs': [_row(status='queued')] + excluded},
             {'workflow_runs': [_row(), _row('lint', conclusion='skipped')]
              + excluded}]
    result, fake = _wait(tmp, 'wait-gates', pages)
    _ok(result)
    assert len(fake.calls()) == 2, fake.calls()
    assert '2 gates' in result.stdout, result.stdout


def test_wait_gates_red_neutral_cancelled_and_timed_out(tmp):
    for conclusion in ('failure', 'neutral', 'cancelled', 'timed_out'):
        child = Path(tmp) / conclusion
        result, fake = _wait(child, 'wait-gates', [{'workflow_runs': [
            _row(conclusion=conclusion), _row('lint', status='in_progress')]}])
        assert result.returncode != 0, result.stdout
        assert f'tests ({conclusion})' in result.stderr, result.stderr
        assert len(fake.calls()) == 1, fake.calls()


def test_wait_gates_zero_grace_and_deadline(tmp):
    for name, pages, args, status, wanted, count in (
            ('registration', [{'workflow_runs': []},
                              {'workflow_runs': [_row()]}],
             ['--grace', '10'], 0, '1 gates', 2),
            ('empty', [{'workflow_runs': []}],
             ['--grace', '0'], 1, 'no workflow', 1),
            ('deadline', [{'workflow_runs': [_row(status='queued')]}],
             ['--timeout', '0'], 1, 'deadline', 1)):
        result, fake = _wait(Path(tmp) / name, 'wait-gates', pages, *args)
        assert result.returncode == status, result.stdout + result.stderr
        assert wanted in result.stdout + result.stderr, result.stderr
        assert len(fake.calls()) == count, fake.calls()


def test_wait_release_requires_success(tmp):
    for conclusion in ('success', 'failure', 'cancelled', 'skipped'):
        result, fake = _wait(Path(tmp) / conclusion, 'wait-release', [
            {'workflow_runs': [_row(), _row('release', status='queued')]},
            {'workflow_runs': [_row(), _row('release',
                                            conclusion=conclusion)]}])
        assert result.returncode == int(conclusion != 'success'), (
            result.stdout + result.stderr)
        assert f'release ({conclusion})' in result.stdout + result.stderr
        assert len(fake.calls()) == 2, fake.calls()


def test_wait_refuses_api_error(tmp):
    result, fake = _wait(tmp, 'wait-gates', [
        {'status': 403, 'body': 'fixture API refusal'}])
    assert result.returncode != 0, result.stdout
    assert 'fixture API refusal' in result.stderr, result.stderr
    assert len(fake.calls()) == 1, fake.calls()


def test_workflow_failure_stops_tag_or_successor(tmp):
    if sys.platform == 'win32':
        _util.skip('the dispatched workflow runs on Ubuntu with python3')
    workflow = workflow_mapping((ROOT / '.github/workflows/'
                                 'release-dispatch.yml').read_text())
    job, = workflow['jobs'].values()
    for gate, release in (('failure', 'success'), ('success', 'failure'),
                          ('success', 'success')):
        directory = Path(tmp) / (gate + '-' + release)
        directory.mkdir()
        work, bare = _repo(directory)
        before = _out(bare, 'rev-parse', 'main')
        fake = FakeGh(directory / 'gh', {'actions/runs?head_sha=': [
            {'workflow_runs': [_row(conclusion=gate)]},
            {'workflow_runs': [_row('release', conclusion=release)]}]})
        home = directory / 'home'
        home.mkdir()
        context = {'inputs': {'version': VERSION}, 'steps': {},
                   'github': {'ref': 'refs/heads/main', 'repository': 'o/r',
                              'token': 'fixture-token'},
                   'secrets': {'MASTER_PUSH_DEPLOY_KEY': 'fixture-key'},
                   'status': {'success': True, 'failure': False,
                              'cancelled': False}}
        for step in job['steps']:
            if 'run' not in step:
                continue
            if not evaluate_if(step.get('if', 'success()'), context):
                continue
            output = directory / 'step-output'
            output.write_text('')
            env = dict(fake.env(), HOME=str(home), USERPROFILE=str(home),
                       GITHUB_OUTPUT=str(output))
            env.update({k: str(evaluate(v, context))
                        for k, v in step.get('env', {}).items()})
            done = run_step(work, step, env)
            context['status'].update(success=done.returncode == 0,
                                     failure=done.returncode != 0)
            if 'id' in step:
                context['steps'][step['id']] = {'outputs': dict(
                    row.split('=', 1)
                    for row in output.read_text().splitlines())}
        expected = VERSION + '.1' if release == gate == 'success' else VERSION
        assert expected in (work / 'extension/manifest.json').read_text()
        assert _out(bare, 'rev-parse', 'main') != before
        assert bool(_out(bare, 'tag', '-l')) == (gate == 'success')
        assert context['status']['success'] == (gate == release == 'success')
        assert len(fake.calls()) == (1 if gate == 'failure' else 2)


def test_paginated_runs_do_not_hide_a_red_gate(tmp):
    first = json.dumps({'workflow_runs': [_row()]})
    second = json.dumps({'workflow_runs': [
        _row('lint', conclusion='failure')]})
    result, fake = _wait(tmp, 'wait-gates', [{'body': first + '\n' + second}])
    assert result.returncode != 0, result.stdout
    assert 'lint (failure)' in result.stderr, result.stderr
    assert len(fake.calls()) == 1, fake.calls()


def test_workflow_orders_release_and_protects_secret(tmp):
    del tmp
    path = ROOT / '.github/workflows/release-dispatch.yml'
    assert path.is_file(), 'missing release-dispatch.yml'
    workflow = workflow_mapping(path.read_text())
    assert workflow['on']['workflow_dispatch']['inputs']['version'][
        'required'] == 'true'
    assert workflow['permissions'] == {'actions': 'read', 'contents': 'read'}
    assert workflow['concurrency']['cancel-in-progress'] == 'false'
    job, = workflow['jobs'].values()
    assert job['runs-on'] == 'ubuntu-latest'
    for ref, expected in (('refs/heads/main', 'main-push'),
                          ('refs/heads/other', ''), ('refs/tags/v1', '')):
        context = {'github': {'ref': ref}}
        assert evaluate(job['environment'], context) == expected
    steps = job['steps']
    assert steps[0]['name'].startswith('Refuse'), steps[0]
    assert 'refs/heads/main' in steps[0]['if'], steps[0]
    assert 'exit 1' in steps[0]['run'], steps[0]
    script = SCRIPT.as_posix()
    runs = [s for s in steps if script in s.get('run', '')]
    assert [s['run'].split(script)[1].split()[0] for s in runs] == [
        'bump', 'wait-gates', 'tag', 'wait-release', 'bump']
    assert '--successor' in runs[-1]['run'] and all(
        'if' not in s for s in runs), runs
    assert runs[1]['env']['SHA'] == '${{ steps.bump.outputs.sha }}'
    assert runs[2]['env']['SHA'] == '${{ steps.bump.outputs.sha }}'
    assert runs[3]['env']['SHA'] == '${{ steps.bump.outputs.sha }}'
    for step in runs:
        assert '${{' not in step['run'], step
        if 'VERSION' in step['run']:
            assert step['env']['VERSION'] == '${{ inputs.version }}', step
        if 'bump' in step['run'] or ' tag ' in step['run']:
            assert step['env']['MASTER_PUSH_DEPLOY_KEY'] == (
                '${{ secrets.MASTER_PUSH_DEPLOY_KEY }}'), step
    assert int(job['timeout-minutes']) >= 120


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals()),
                                  tmp_prefix='release_dispatch_'))
