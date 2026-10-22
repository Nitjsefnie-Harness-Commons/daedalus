#!/usr/bin/env python3
"""Judged histories authorize only the scalar's numeric raises."""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _document, _git, _captured_main  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts/ci'))

GUARD = ROOT / 'scripts/ci/thresholds_guard.py'
FAMILIES = ('module_size_baseline', 'long_line_baseline',
            'type_error_baseline', 'js_coverage_baseline')


def _history(tmp, name, subjects, base=None, head=None):
    repo = Path(tmp) / name
    (repo / '.github').mkdir(parents=True)
    (repo / 'backend').mkdir()
    (repo / 'backend/kept.py').write_text('pass\n', encoding='utf-8')
    (repo / 'backend/second.py').write_text('pass\n', encoding='utf-8')
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.name', 'Threshold Test')
    _git(repo, 'config', 'user.email', 'test@example.invalid')
    _git(repo, 'config', 'commit.gpgsign', 'false')
    target = repo / '.github/ci-thresholds.json'
    target.write_text(json.dumps(base or _document(), default=float))
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'feat: outside judged range')
    base_rev = _rev(repo)
    for subject in subjects:
        _git(repo, 'commit', '--allow-empty', '--cleanup=verbatim',
             '-qm', subject)
    if head is not None:
        target.write_text(json.dumps(head, default=float))
        _git(repo, 'add', '.github/ci-thresholds.json')
        _git(repo, 'commit', '-qm', 'chore: record thresholds')
    return repo, base_rev


def _rev(repo):
    return subprocess.run(
        ['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True,
        capture_output=True, text=True, timeout=10).stdout.strip()


def _judge(repo, base_rev, base, head, revs=None):
    guard = _util.load(GUARD, 'judged_threshold_guard')
    guard.__file__ = str(repo / 'scripts/ci/thresholds_guard.py')
    base_path, head_path = repo / 'base.json', repo / 'head.json'
    base_path.write_text(json.dumps(base, default=float))
    head_path.write_text(json.dumps(head, default=float))
    args = ['--base', str(base_path), '--head', str(head_path)]
    args += ['--base-rev', base_rev] if revs is None else revs
    return _captured_main(guard, args)


def _raised():
    base = _document()
    base['tests_line_baseline'] = 10
    head = copy.deepcopy(base)
    head['tests_line_baseline'] = 11
    return base, head


def test_feature_anywhere_in_range_allows_recording_a_raise(tmp):
    for index, subjects in enumerate((
            ['feat: new command'], ['feat(ci): new command'],
            ['feat: new command', 'fix: polish'],
            ['fix: prepare', 'feat(ci): new command', 'chore: publish'])):
        base, head = _raised()
        repo, rev = _history(tmp, str(index), subjects, base, head)
        status, _out, err = _judge(repo, rev, base, head)
        assert status == 0, err

    base, head = _raised()
    repo, _rev_before = _history(tmp, 'insufficient', ['feat: add tests'],
                                 base, head)
    ci = repo / 'scripts/ci'
    ci.mkdir(parents=True)
    for name in ('tests_lines.py', 'thresholds.py'):
        shutil.copy2(GUARD.with_name(name), ci / name)
    (repo / 'tests').mkdir()
    (repo / 'tests/cost.py').write_text('pass\n' * 12)
    _git(repo, 'add', 'tests/cost.py')
    _git(repo, 'commit', '-qm', 'feat: add tests')
    result = subprocess.run(
        [sys.executable, str(ci / 'tests_lines.py')], cwd=repo,
        env=_util.child_coverage('scrub'), capture_output=True,
        text=True, timeout=60, check=False)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert '12' in result.stderr and '11' in result.stderr, result.stderr


def test_nonfeatures_refuse_raise_with_remedy(tmp):
    for index, subject in enumerate((
            'fix: trim', 'chore: tidy', 'perf: optimise', 'Feat: change',
            ' feat: change', 'feature: change', 'feat!: change',
            'feat(ci)!: change', 'fix: change\n\nfeat: body only',
            'fix: subject\x0bfeat: embedded',
            'fix: subject\u2028feat: embedded')):
        base, head = _raised()
        repo, rev = _history(tmp, str(index), [subject], base, head)
        status, _out, err = _judge(repo, rev, base, head)
        assert status == 1, (subject, err)
        assert all(word in err for word in ('10', '11', 'feat:', 'trim')), err


def test_unproved_ranges_fail_even_when_documents_match(tmp):
    base, head = _raised()
    repo, rev = _history(tmp, 'ranges', ['feat: eligible'], base, head)
    for revs in (
            [], ['--base-rev', ''], ['--base-rev', ' '],
            ['--base-rev', 'unknown'],
            ['--base-rev', rev, '--head-rev', 'unknown']):
        for after in (base, head):
            status, _out, err = _judge(repo, rev, base, after, revs)
            assert status == 1 and err.strip(), (revs, err)
    status, _out, err = _judge(
        repo, rev, base, head, ['--base-rev', 'HEAD', '--head-rev', 'HEAD'])
    assert status == 1 and 'feat:' in err, err


def test_history_failures_refuse_both_reads(tmp):
    base, head = _raised()
    repo, rev = _history(tmp, 'failures', ['feat: eligible'], base, head)
    real = subprocess.run
    for kind in ('--format=%s', '--format=%B'):
        for failure in (OSError('unreadable history'),
                        subprocess.TimeoutExpired('git', 10), 128):
            def read(argv, kind=kind, failure=failure, **kwargs):
                if kind in argv:
                    assert kwargs == {'capture_output': True,
                                      'check': False, 'timeout': 10}
                    if isinstance(failure, Exception):
                        raise failure
                    return subprocess.CompletedProcess(
                        argv, failure, b'', b'bad range')
                return real(argv, **kwargs)

            with patch.object(subprocess, 'run', read):
                status, _out, err = _judge(repo, rev, base, base)
            assert status == 1 and 'history' in err, err


def test_feature_does_not_waive_validation_removal_or_map_policy(tmp):
    base, head = _raised()
    repo, rev = _history(tmp, 'limits', ['feat: eligible'], base, head)
    for value in (None, False, 0, -1, 1.5, '12', {}):
        bad = dict(head, tests_line_baseline=value)
        status, _out, err = _judge(repo, rev, base, bad)
        assert status == 1 and 'tests_line_baseline' in err, err
    removed = dict(head)
    del removed['tests_line_baseline']
    assert _judge(repo, rev, base, removed)[0] == 1
    for family in FAMILIES:
        before, after = copy.deepcopy(base), copy.deepcopy(head)
        before[family] = {'backend/kept.py': 9}
        after[family] = {'backend/kept.py': 10}
        status, _out, err = _judge(repo, rev, before, after)
        assert status == 1 and f'{family}.backend/kept.py' in err, err
        after[family]['backend/kept.py'] = 8
        assert _judge(repo, rev, before, after)[0] == 0


def test_absent_scalar_seed_and_unchanged_absence_are_valid(tmp):
    head = _document()
    base = copy.deepcopy(head)
    del base['tests_line_baseline']
    repo, rev = _history(tmp, 'seed', ['fix: record'], base, head)
    assert _judge(repo, rev, base, head)[0] == 0
    assert _judge(repo, rev, base, base)[0] == 0
    assert _judge(repo, rev, head, head)[0] == 0
    lower = dict(head, tests_line_baseline=1)
    assert _judge(repo, rev, head, lower)[0] == 0


def test_unreadable_base_and_head_are_refusals(tmp):
    base, head = _raised()
    repo, rev = _history(tmp, 'documents', ['feat: eligible'], base, head)
    guard = _util.load(GUARD, 'bad_threshold_documents')
    guard.__file__ = str(repo / 'scripts/ci/thresholds_guard.py')
    valid, bad = repo / 'valid.json', repo / 'bad.json'
    valid.write_text(json.dumps(base, default=float))
    for raw in (b'{', b'[]', b'\xff', b'{"schema_version": 2}'):
        bad.write_bytes(raw)
        for before, after in ((valid, bad), (bad, valid)):
            status, _out, err = _captured_main(guard, [
                '--base', str(before), '--head', str(after),
                '--base-rev', rev])
            assert status == 1 and err.strip(), err
    status, _out, err = _captured_main(guard, [
        '--base', str(repo / 'missing'), '--head', str(valid),
        '--base-rev', rev])
    assert status == 1 and 'cannot read thresholds' in err, err


def test_audit_executes_one_judged_base_and_refuses_missing_history(tmp):
    from _yamlsteps import complete_job_mapping, workflow_mapping
    workflow = (ROOT / '.github/workflows/audit.yml').read_text()
    job = complete_job_mapping(workflow, 'thresholds')
    assert job is not None
    step = next(s for s in job['steps']
                if s.get('name') == 'Threshold direction guard')
    checkout = next(s for s in job['steps']
                    if 'checkout@' in s.get('uses', ''))
    assert str(checkout['with']['fetch-depth']) == '0'
    resolver = next(s for s in job['steps']
                    if s.get('name') == 'Resolve threshold base')
    jobs = workflow_mapping((ROOT / '.github/workflows/tests.yml').read_text())
    for name in ('suites', 'coverage-matrix', 'coverage', 'pyright'):
        steps = jobs['jobs'][name]['steps']
        assert sum(s.get('run') == 'python scripts/ci/threshold_base.py'
                   for s in steps) == 1
        assert all(str(s['with']['fetch-depth']) == '0' for s in steps
                   if 'checkout@' in s.get('uses', ''))
    script = resolver['run'] + '\n. "$GITHUB_ENV"\n' + step['run']
    assert not any(key in obj for obj in (job, step)
                   for key in ('if', 'continue-on-error'))
    base, head = _raised()
    repo, rev = _history(tmp, 'workflow', ['fix: record'], base, head)
    ci = repo / 'scripts/ci'
    ci.mkdir(parents=True)
    for name in ('thresholds.py', 'thresholds_guard.py',
                 'thresholds_removed.py', 'threshold_base.py'):
        shutil.copy2(GUARD.with_name(name), ci / name)
    _git(repo, 'commit', '--allow-empty', '-qm', 'chore: followup')
    _git(repo, 'branch', 'target', rev)
    _git(repo, 'checkout', '-q', 'target')
    target_doc = dict(base, tests_line_baseline=20)
    path = repo / '.github/ci-thresholds.json'
    path.write_text(json.dumps(target_doc, default=float))
    _git(repo, 'add', '.github/ci-thresholds.json')
    _git(repo, 'commit', '-qm', 'feat: target-side feature')
    _git(repo, 'checkout', '-q', '-')
    shim = repo / 'bin'
    shim.mkdir()
    shutil.copy2(sys.executable, shim / 'python')
    executable = shim / 'git'
    real_git = shutil.which('git')
    assert real_git is not None
    executable.write_text('#!/bin/sh\n'
                          'if [ "${GIT_FAIL:-}" = "$1" ]; then exit 128; fi\n'
                          'if [ "$1" = fetch ]; then\n'
                          '  exec "$REAL_GIT" fetch --no-tags "$LOCAL_REPO" '
                          'refs/heads/target\n'
                          'fi\nexec "$REAL_GIT" "$@"\n')
    executable.chmod(0o755)
    env = dict(_util.child_coverage('scrub'),
               PATH=str(shim) + os.pathsep + os.environ['PATH'],
               REAL_GIT=real_git, LOCAL_REPO=str(repo), RUNNER_TEMP=str(repo),
               REPO='unused/repo', BASE_REF='target',
               GITHUB_ENV=str(repo / 'env'))
    for event, before, status in (('pull_request', '', 1), ('push', rev, 1),
                                  ('push', 'unknown', 0),
                                  ('push', '0' * 40, 0),
                                  ('schedule', '', 0)):
        env.update(EVENT=event, BEFORE=before)
        result = subprocess.run([_util.workflow_bash(), '-e', '-o', 'pipefail',
                                 '-c', script], cwd=repo, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == status, (
            event, before, result.stdout, result.stderr)
        if event == 'pull_request' or before == rev:
            assert rev in result.stdout, result.stdout
            assert 'feat:' in result.stderr, result.stderr
    for command in ('fetch', 'merge-base'):
        env.update(EVENT='pull_request', GIT_FAIL=command)
        result = subprocess.run([_util.workflow_bash(), '-e', '-c', script],
                                cwd=repo, env=env, capture_output=True,
                                text=True, timeout=60)
        assert result.returncode != 0 and result.stderr.strip(), result.stdout
    env.pop('GIT_FAIL')
    _git(repo, 'checkout', '--orphan', 'root-only')
    _git(repo, 'add', '.github/ci-thresholds.json')
    _git(repo, 'commit', '-qm', 'fix: root')
    env.update(EVENT='push', BEFORE='unknown')
    result = subprocess.run([_util.workflow_bash(), '-e', '-c', script],
                            cwd=repo, env=env, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode != 0, result.stdout
    assert all(word in result.stderr for word in ('unknown', 'HEAD^'))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='thguard_')


if __name__ == '__main__':
    raise SystemExit(main())
