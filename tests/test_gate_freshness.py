#!/usr/bin/env python3
"""Contracts for gate-freshness decisions and check publication."""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _gate_freshness_fixtures import _check, _encode  # noqa: E402
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
G2 = 'b' * 40
HEAD = 'c' * 40
RUN = 'https://github.com/o/r/actions/runs/1'

_CHECK_LIST = (f'repos/o/r/commits/{HEAD}/check-runs'
               '?filter=all&per_page=100')
_SELECTOR = ('.check_runs[] | select(.name == "gate freshness" and '
             '.app.slug == "github-actions") | '
             '{id, status, conclusion, external_id, summary: .output.summary}')


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_mod')


def _compare_ok(mb, status='ahead'):
    return {'status': status, 'merge_base_commit': {'sha': mb}}


def _head(number=7, sha=HEAD, owner='octo', ref='feature', base='main'):
    return {'number': number, 'sha': sha, 'owner': owner, 'ref': ref,
            'base': base}


def test_the_set_is_exactly_the_documented_patterns(tmp):
    m = _mod()
    assert tuple(m.GATE_PATTERNS) == (
        '.github/workflows/**', '.github/ci-thresholds.json',
        '.github/journey-budget.json',
        'scripts/ci/**', 'scripts/check_versions.py',
        '.gitleaks.toml', 'pyrightconfig.json', 'pyrightconfig.tests.json',
        '.pylintrc', 'setup.cfg', 'eslint.config.js', 'pyproject.toml',
        'run_tests.py', 'requirements-dev.txt', 'requirements-test.txt',
        'requirements-release.txt', 'requirements-pip-audit.txt',
        'requirements-zizmor.txt')
    for path in m.GATE_PATTERNS:
        assert m.is_gate_defining(path), path
    assert m.is_gate_defining('scripts/ci/deep/x.py')
    assert m.is_gate_defining('.github/workflows/a/b/c.yml')


def test_near_miss_spellings_are_not_gate_defining(tmp):
    m = _mod()
    negatives = (
        'scripts/ci_not/x.py',        # sibling directory, not under scripts/ci
        '.github/workflowsfoo/x.yml',  # workflows, not workflows/
        'tests/anything.py',          # a changed test is not a gate change
        'tests/test_gate_freshness.py', 'daedalus_bridge/result_routes.py',
        'server.py', 'README.md',
        '.github/dependabot.yml',     # proposes updates; decides no verdict
        '.github/ci-thresholds.json.bak', 'pyrightconfig.jsonx',
        'setup.cfg.bak', 'run_tests.pyc', 'scripts/check_version.py',
        '.gitleaks.toml.bak',
    )
    for path in negatives:
        assert not m.is_gate_defining(path), path


def test_no_file_is_carried_by_the_branch_outside_the_gate_set(tmp):
    m = _mod()
    assert not hasattr(m, 'CARRIED_BY_THE_BRANCH')
    for moved in ('.github/ci-thresholds.json', '.github/journey-budget.json'):
        assert m.is_gate_defining(moved), moved
        assert moved in m.GATE_PATTERNS, moved


_EXECUTED = re.compile(
    r'python3?(?:\.\d+)? +(?:-[A-Za-z]+ +)*([\w./-]+\.py)\b'
    r'|(?<![\w.-])([./][\w./-]+\.(?:sh|py))\b')
_FLAG_VALUE = re.compile(
    r'--(?:rcfile|config|requirement|project|input|file)[= ]+([^\s\'"]+)'
    r'|(?<!\w)-r[= ]+([^\s\'"]+)')


def _git_tracked(base):
    listed = subprocess.run(
        ['git', '-C', str(base), 'ls-files', '-z'],
        capture_output=True, check=True)
    return {os.fsdecode(name) for name in listed.stdout.split(b'\0') if name}


def _normalise(candidate, base, tracked):
    parts = [p for p in candidate.split('/')
             if p and p not in ('.', '..', 'head')]
    parts = [p for p in parts if not p.startswith('$')]
    rel = '/'.join(parts)
    if rel and (base / rel).is_file() and rel in tracked:
        return rel
    return None


def _workflow_gate_files(directory, base=ROOT, tracked=None):
    if tracked is None:
        tracked = _git_tracked(base)
    found = set()
    for path in sorted(directory.glob('*.yml')):
        text = path.read_text(encoding='utf-8')
        for first, second in _EXECUTED.findall(text):
            resolved = _normalise(first or second, base, tracked)
            if resolved:
                found.add(resolved)
        for first, second in _FLAG_VALUE.findall(text):
            resolved = _normalise(first or second, base, tracked)
            if resolved:
                found.add(resolved)
    return found


def test_every_gate_file_a_workflow_uses_is_accounted_for(tmp):
    m = _mod()
    used = _workflow_gate_files(ROOT / '.github' / 'workflows')
    assert used, 'the workflows name no gate file'
    for path in sorted(used):
        assert m.is_gate_defining(path), path


def test_the_guard_accounts_for_the_files_the_set_now_carries(tmp):
    m = _mod()
    used = _workflow_gate_files(ROOT / '.github' / 'workflows')
    assert 'scripts/check_versions.py' in used
    assert '.gitleaks.toml' in used
    for path in ('scripts/check_versions.py', '.gitleaks.toml'):
        assert m.is_gate_defining(path), path


def test_a_planted_gate_script_outside_scripts_ci_is_caught(tmp):
    m = _mod()
    for spelling, path in (
            ('./scripts/version_gate.sh', 'scripts/version_gate.sh'),
            ('python3 scripts/scan_secrets_extra.py',
             'scripts/scan_secrets_extra.py'),
            ('python3 -u scripts/lint_gate.py', 'scripts/lint_gate.py'),
            ('python3.13 scripts/lint_gate.py', 'scripts/lint_gate.py'),
            ('python scripts/lint_gate.py', 'scripts/lint_gate.py')):
        base = Path(tmp) / spelling.replace(' ', '_').replace('.', '_')
        (base / 'scripts').mkdir(parents=True)
        (base / path).write_text('# gate\n', encoding='utf-8')
        workflows = base / '.github' / 'workflows'
        workflows.mkdir(parents=True)
        (workflows / 'probe.yml').write_text(
            f'jobs:\n  gate:\n    steps:\n      - run: {spelling}\n',
            encoding='utf-8')
        used = _workflow_gate_files(workflows, base=base, tracked={path})
        assert used == {path}, (spelling, used)
        assert not m.is_gate_defining(path), path


def test_a_generated_untracked_requirements_file_is_not_required(tmp):
    m = _mod()
    base = Path(tmp) / 'repo'
    (base / '.github' / 'workflows').mkdir(parents=True)
    (base / 'extras-requirements.txt').write_text('mcp\n', encoding='utf-8')
    (base / '.github' / 'workflows' / 'probe.yml').write_text(
        'jobs:\n  gate:\n    steps:\n'
        '      - run: pip install --requirement extras-requirements.txt\n',
        encoding='utf-8')
    used = _workflow_gate_files(
        base / '.github' / 'workflows', base=base, tracked=set())
    assert 'extras-requirements.txt' not in used, used
    assert not m.is_gate_defining('extras-requirements.txt')
    real = _workflow_gate_files(ROOT / '.github' / 'workflows')
    assert 'extras-requirements.txt' not in real, real


def test_the_merge_base_tells_ancestor_from_not(tmp):
    m = _mod()
    same, attempts = m.merge_base(
        lambda _a: _encode(_compare_ok(G1)), 'o/r', G1, _head())
    assert same == G1
    assert attempts[0][1].endswith(G1[:12])
    other, _ = m.merge_base(lambda _a: _encode(_compare_ok('d' * 40)),
                            'o/r', G1, _head())
    assert other == 'd' * 40


def test_status_is_not_load_bearing_the_merge_base_is(tmp):
    m = _mod()
    gates = [('.pylintrc', G1)]
    ahead = m.head_verdict(
        lambda _a: _encode(_compare_ok('d' * 40, 'ahead')), 'o/r', _head(),
        gates)
    assert ahead.conclusion == 'failure'
    diverged = m.head_verdict(
        lambda _a: _encode(_compare_ok(G1, 'diverged')), 'o/r', _head(),
        gates)
    assert diverged.conclusion == 'success'


def test_a_merge_base_that_is_not_forty_lowercase_hex_is_unreadable(tmp):
    m = _mod()
    for bad in (None, '', 'ZZZ', 'A' * 40, 'a' * 39, 'a' * 41, 12345):
        mb, _ = m.merge_base(_encoded_read(_compare_ok(bad)),
                             'o/r', G1, _head())
        assert mb is None, bad


def _encoded_read(payload):
    def read(_argv):
        return _encode(payload)
    return read


def test_an_absent_merge_base_field_is_unreadable(tmp):
    m = _mod()
    mb, _ = m.merge_base(lambda _a: _encode({'status': 'ahead'}),
                         'o/r', G1, _head())
    assert mb is None
    for shape in ([], 'text', 12345, None, True):
        mb, _ = m.merge_base(_encoded_read(shape), 'o/r', G1, _head())
        assert mb is None, shape


def test_an_unreadable_compare_is_red_and_states_the_observation(tmp):
    m = _mod()

    def read(_argv):
        raise m.QueryError('HTTP 404')
    verdict = m.head_verdict(read, 'o/r', _head(), [('.pylintrc', G1)])
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'
    text = verdict.title + ' ' + verdict.summary
    assert 'compare' in text
    assert f'compare/{G1}...{HEAD}' in text
    assert 'HTTP 404' in text
    assert 'rebase' in text.lower()
    assert 'not evidence of access denial' in text


def _commits_read(m, commits=None, raise_for=None, live_shaped=False,
                  asked=None):
    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if '/commits?' not in target:
            raise AssertionError(target)
        request = target.split('path=')[1]
        if asked is not None:
            asked.append(request)
        if raise_for is not None and raise_for(request):
            raise m.QueryError('HTTP 500')
        if commits is not None and request in commits:
            return _encode([{'sha': s} for s in commits[request]])
        if live_shaped and '*' in request:
            return _encode([])
        return _encode([{'sha': G1}])
    return read


def test_enumerate_gates_reads_one_commit_for_every_pattern(tmp):
    m = _mod()
    for live_shaped in (False, True):
        asked = []
        gates = m.enumerate_gates(
            _commits_read(m, asked=asked, live_shaped=live_shaped), 'o/r', G1)
        assert len(gates) == len(m.GATE_PATTERNS)
        assert len(asked) == len(m.GATE_PATTERNS)


def test_the_commits_endpoint_receives_a_path_it_understands(tmp):
    m, asked = _mod(), []
    m.enumerate_gates(_commits_read(m, asked=asked), 'o/r', G1)
    assert asked, 'no gate path was looked up'
    for request in asked:
        assert '*' not in request, request
    assert '.github/workflows' in asked, asked
    assert 'scripts/ci' in asked, asked
    for exact in ('.pylintrc', 'setup.cfg', 'run_tests.py',
                  'requirements-dev.txt', 'requirements-test.txt',
                  'pyrightconfig.json', 'pyrightconfig.tests.json',
                  'eslint.config.js', 'pyproject.toml', '.gitleaks.toml',
                  'scripts/check_versions.py'):
        assert exact in asked, exact


def test_a_gate_path_with_no_commits_is_a_global_failure(tmp):
    m = _mod()
    read = _commits_read(m, commits={'eslint.config.js': []})
    try:
        m.enumerate_gates(read, 'o/r', G1)
    except m.QueryError as error:
        assert 'eslint.config.js' in str(error), str(error)
        assert 'no commit' in str(error), str(error)
        return
    raise AssertionError('an empty gate-path answer was not refused')


def test_a_non_list_enumeration_payload_is_a_global_failure(tmp):
    m = _mod()
    for bad in (42, {'error': 'rate limited'}, '"a string"'):
        try:
            m.enumerate_gates(_encoded_read(bad), 'o/r', G1)
        except m.QueryError as error:
            assert 'list' in str(error), str(error)
            continue
        raise AssertionError(f'a non-list payload {bad!r} was not refused')


def _multi_gate_read(missing_at, asked=None):
    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        gate = target.split('compare/')[1].split('...')[0]
        if asked is not None:
            asked.append(gate)
        if gate == missing_at:
            return _encode(_compare_ok('f' * 40, 'diverged'))
        return _encode(_compare_ok(gate, 'ahead'))
    return read


def test_a_fresh_head_is_green_and_names_no_missing_gate(tmp):
    m = _mod()
    gates = [('a', G1), ('b', G2)]
    verdict = m.head_verdict(_multi_gate_read(None), 'o/r', _head(), gates)
    assert verdict.conclusion == 'success'
    assert verdict.kind == 'fresh'
    assert not verdict.paths


def test_a_head_missing_a_middle_gate_is_red_and_names_it(tmp):
    m = _mod()
    for gates, missing, absent in (
            ([('path-a', G1), ('path-b', G2)], G2, 'path-a'),
            ([('older', G1), ('newer', G2)], G1, 'newer')):
        verdict = m.head_verdict(
            _multi_gate_read(missing), 'o/r', _head(), gates)
        assert verdict.conclusion == 'failure'
        assert verdict.kind == 'stale'
        assert missing[:12] in verdict.summary
        assert gates[0 if missing == G1 else 1][0] in verdict.summary
        assert absent not in verdict.summary


def test_every_gate_is_compared_not_short_circuited(tmp):
    m, asked = _mod(), []
    gates = [('a', G1), ('b', G2), ('c', 'd' * 40)]
    m.head_verdict(_multi_gate_read(G2, asked), 'o/r', _head(), gates)
    assert asked == [G1, G2, 'd' * 40], asked


def test_a_gate_commit_that_cannot_be_read_makes_the_whole_head_red(tmp):
    m = _mod()
    gates = [('a', G1), ('b', G2)]

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        gate = target.split('compare/')[1].split('...')[0]
        if gate == G2:
            raise m.QueryError('HTTP 404')
        return _encode(_compare_ok(gate, 'ahead'))
    verdict = m.head_verdict(read, 'o/r', _head(), gates)
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'


def _publish_read(checks, recorded, fail=None, requests=None,
                  error=AssertionError, listing=None, base=None):
    checks = [_check(*c) if isinstance(c, tuple) else c for c in checks]

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        method = next((argv[i + 1].upper()
                       for i, flag in enumerate(argv)
                       if flag in ('-X', '--method')), 'GET')
        if requests is not None:
            requests.append(list(argv))
        if target == _CHECK_LIST:
            assert method == 'GET' and '--paginate' in argv
            assert '--jq' in argv
            assert argv[argv.index('--jq') + 1] == _SELECTOR
            if fail == 'GET':
                raise error('listing unavailable')
            return (listing if listing is not None else
                    '\n'.join(_encode(c) for c in checks))
        if target.startswith('repos/o/r/compare/'):
            assert method == 'GET' and base is not None
            if fail == 'compare':
                raise error('compare unavailable')
            return _encode(_compare_ok(base))
        allowed = {('POST', 'repos/o/r/check-runs')} | {
            ('PATCH', f'repos/o/r/check-runs/{c["id"]}') for c in checks}
        assert (method, target) in allowed, (method, target)
        recorded.append((method, target, list(argv)))
        if fail in (method, target):
            raise error('write unavailable')
        return '{}'
    return read


def test_a_completed_carrier_is_patched_without_rewriting_its_external_id(tmp):
    recorded = []
    _mod().publish(_publish_read([(4242, 'completed', 'runner-uuid')],
                                 recorded),
                   'o/r', HEAD, 'failure', 't', 's\nmore', RUN, G1)
    assert len(recorded) == 1
    method, target, argv = recorded[0]
    assert (method, target) == ('PATCH', 'repos/o/r/check-runs/4242')
    assert argv[argv.index('-X') + 2:] == [
        '-f', 'name=gate freshness', '-f', 'status=completed',
        '-f', 'conclusion=failure', '-f', f'details_url={RUN}',
        '-f', 'output[title]=t', '-f', f'output[summary]=s\nmore\nmain {G1}']


def test_newest_carries_the_verdict_and_retires_completed_checks(tmp):
    m = _mod()
    for external in ('runner-uuid', m.EXTERNAL_ID, None, 'other id'):
        for status in ('completed', 'in_progress', 'queued'):
            recorded = []
            checks = [_check(100, external_id='carrier-uuid'),
                      _check(9, status, external, conclusion='failure'),
                      _check(100)]
            m.publish(_publish_read(checks, recorded),
                      'o/r', HEAD, 'success', 't', 's', RUN, G1)
            assert recorded[0][:2] == ('PATCH', 'repos/o/r/check-runs/100')
            assert 'conclusion=success' in recorded[0][2]
            assert len(recorded) == (2 if status == 'completed' else 1)
            if status == 'completed':
                assert recorded[1][:2] == ('PATCH', 'repos/o/r/check-runs/9')
                assert recorded[1][2][recorded[1][2].index('-X') + 2:] == [
                    '-f', 'name=superseded gate freshness']
                state = {c['id']: {'name': m.NAME,
                                   'conclusion': c['conclusion']}
                         for c in checks}
                for _, target, argv in recorded:
                    state[int(target.rsplit('/', 1)[-1])].update(dict(
                        field.split('=', 1) for field in argv
                        if field.startswith(('name=', 'conclusion='))))
                assert [(i, c['conclusion']) for i, c in state.items()
                        if c['name'] == m.NAME] == [(100, 'success')]


def test_runner_ownership_beats_a_newer_legacy_id(tmp):
    m, recorded = _mod(), []
    m.publish(_publish_read([(9, 'completed', 'runner-uuid'),
                             (100, 'completed', m.EXTERNAL_ID)], recorded),
              'o/r', HEAD, 'success', 't', 's', RUN, G1)
    assert [(method, target.rsplit('/', 1)[-1])
            for method, target, _ in recorded] == [('PATCH', '9'),
                                                   ('PATCH', '100')]
    assert 'conclusion=success' in recorded[0][2]
    assert recorded[1][2][recorded[1][2].index('-X') + 2:] == [
        '-f', 'name=superseded gate freshness']


def test_a_live_job_is_left_to_the_runner_while_legacy_checks_are_retired(tmp):
    m = _mod()
    for status in ('in_progress', 'queued'):
        for legacy in ([], [(8, 'completed', m.EXTERNAL_ID)]):
            recorded = []
            checks = [(99, status, 'runner-uuid'), *legacy]
            m.publish(_publish_read(checks, recorded),
                      'o/r', HEAD, 'failure', 't', 's', RUN, G1)
            assert len(recorded) == len(legacy)
            if legacy:
                assert recorded[0][:2] == ('PATCH', 'repos/o/r/check-runs/8')
                assert recorded[0][2][recorded[0][2].index('-X') + 2:] == [
                    '-f', 'name=superseded gate freshness']


def test_no_carrier_posts_once_with_the_legacy_external_id(tmp):
    recorded = []
    _mod().publish(_publish_read([], recorded),
                   'o/r', HEAD, 'success', 't', 's', RUN, G1)
    assert len(recorded) == 1
    method, target, argv = recorded[0]
    assert (method, target) == ('POST', 'repos/o/r/check-runs')
    for field in (f'head_sha={HEAD}', 'name=gate freshness',
                  'status=completed', 'conclusion=success',
                  'external_id=daedalus-gate-freshness/v1',
                  f'details_url={RUN}', 'output[title]=t',
                  f'output[summary]=s\nmain {G1}'):
        assert field in argv, field


def test_listing_and_write_failures_raise_without_further_writes(tmp):
    m = _mod()
    for checks, fail, expected in (
            ([], 'GET', []), ([], 'POST', ['POST']),
            ([(9, 'completed', 'runner-uuid'),
              (8, 'completed', m.EXTERNAL_ID)], 'PATCH', ['PATCH']),
            ([(100, 'completed', 'runner-uuid'),
              (9, 'completed', m.EXTERNAL_ID),
              (8, 'completed', m.EXTERNAL_ID)],
             'repos/o/r/check-runs/9', ['PATCH', 'PATCH'])):
        recorded = []
        try:
            m.publish(_publish_read(checks, recorded, fail=fail,
                                    error=m.QueryError),
                      'o/r', HEAD, 'failure', 't', 's', RUN, G1)
        except m.QueryError:
            assert [v for v, _, _ in recorded] == expected
            if fail == 'repos/o/r/check-runs/9':
                assert [t.rsplit('/', 1)[-1] for _, t, _ in recorded] == [
                    '100', '9']
            continue
        raise AssertionError(f'a failed {fail} did not raise')


def test_listing_parses_paginated_lines_and_keeps_external_ids(tmp):
    m, requests = _mod(), []
    checks = [_check(9), _check(100, 'in_progress', 'uuid with spaces')]
    read = _publish_read(checks, [], requests=requests)
    assert m._existing_checks(read, 'o/r', HEAD) == checks
    assert all('Cache-Control: no-cache' in argv for argv in requests)


def test_a_malformed_listing_refuses_before_any_write(tmp):
    m = _mod()
    for row in ('not JSON', '[]', 'null', '1', '{}',
                '{"id": true, "status": "completed"}',
                '{"id": 100, "status": null}', ''):
        for raw in (f'{row}\n', f'{_encode(_check(9))}\n{row}\n'):
            recorded = []
            try:
                m.publish(_publish_read([(9, 'completed', None)], recorded,
                                        listing=raw),
                          'o/r', HEAD, 'success', 't', 's', RUN, G1)
            except m.QueryError:
                assert recorded == []
                continue
            raise AssertionError(f'malformed listing accepted: {raw!r}')


def test_older_success_cannot_replace_a_marked_newer_verdict(tmp):
    m, writes, requests = _mod(), [], []
    checks = [_check(100, conclusion='failure', summary=f's\nmain {G2}')]
    read = _publish_read(checks, writes, base=G1, requests=requests)
    m.publish(read, 'o/r', HEAD, 'success', 't', 's', RUN, G1)
    assert writes == []
    assert any(f'/compare/{G2}...{G1}' in a for argv in requests for a in argv)


def test_newer_snapshot_replaces_an_older_verdict_and_retires_on_skip(tmp):
    m = _mod()
    for base, expected in ((G1, ['100', '9']), ('d' * 40, ['9'])):
        writes = []
        checks = [_check(100, summary=f'old\nmain {G1}'), _check(9)]
        m.publish(_publish_read(checks, writes, base=base),
                  'o/r', HEAD, 'failure', 't', 's', RUN, G2)
        assert [t.rsplit('/', 1)[-1] for _, t, _ in writes] == expected
        if base == G1:
            assert f'output[summary]=s\nmain {G2}' in writes[0][2]
            assert not any(a.startswith('external_id=') for a in writes[0][2])


def test_unmarked_failure_is_not_unfrozen_but_green_is_written(tmp):
    m = _mod()
    for old, expected in (('failure', 0), ('success', 1)):
        for summary in (None, 'text', f'main {G2}\nnot the last line'):
            writes = []
            checks = [_check(100, conclusion=old, summary=summary)]
            m.publish(_publish_read(checks, writes),
                      'o/r', HEAD, 'success', 't', 's', RUN, G1)
            assert len(writes) == expected


def test_an_unreadable_ordering_compare_refuses_without_writing(tmp):
    m, writes = _mod(), []
    checks = [_check(100, summary=f'main {G2}')]
    try:
        m.publish(_publish_read(checks, writes, fail='compare', base=G1,
                                error=m.QueryError),
                  'o/r', HEAD, 'success', 't', 's', RUN, G1)
    except m.QueryError:
        assert writes == []
        return
    raise AssertionError('unreadable ordering compare was accepted')


def test_overlapping_reads_allow_the_older_snapshot_to_write_last(tmp):
    m, writes, requests = _mod(), [], []
    checks = [_check(100, summary=f'main {G1}')]
    older = _publish_read(checks, writes, requests=requests)

    def read(argv):
        if 'conclusion=success' in argv:
            newer = _publish_read(checks, writes, base=G1, requests=requests)
            m.publish(newer, 'o/r', HEAD, 'failure', 't', 's', RUN, G2)
        return older(argv)
    m.publish(read, 'o/r', HEAD, 'success', 't', 's', RUN, G1)
    assert len(requests) >= 2 and all(_CHECK_LIST in a for a in requests[:2])
    assert [next(a for a in argv if a.startswith('conclusion='))
            for _, _, argv in writes] == ['conclusion=failure',
                                          'conclusion=success']
    assert f'output[summary]=s\nmain {G1}' in writes[-1][2]


def test_publish_fake_refuses_unmodelled_listing_calls(tmp):
    read = _publish_read([(7, 'completed', None)], [])
    flags = ['--paginate', '--jq', _SELECTOR]
    for flag in ('-X', '--method'):
        assert read(['gh', 'api', _CHECK_LIST, flag, 'GET', *flags]) == (
            _encode(_check(7)))
    bad = [['gh', 'api', target, '--method', 'GET', *flags] for target in (
        _CHECK_LIST.replace('o/r', 'unmodelled/repo'),
        _CHECK_LIST.replace(HEAD, 'wrong-head'),
        _CHECK_LIST.replace('filter=all', 'filter=latest'))]
    bad += [['gh', 'api', _CHECK_LIST, flag, 'POST', *flags]
            for flag in ('-X', '--method')]
    bad += [['gh', 'api', _CHECK_LIST, '--method', 'GET', *shape]
            for shape in ([], ['--jq', _SELECTOR], ['--paginate'],
                          ['--paginate', '--jq', 'unmodelled projection'])]
    accepted = []
    for argv in bad:
        try:
            read(argv)
        except AssertionError:
            continue
        accepted.append(argv)
    assert not accepted, f'unmodelled listing calls accepted: {accepted}'


def test_a_pull_request_not_based_on_main_is_skipped(tmp):
    m = _mod()
    pr = {
        'number': 7,
        'head': {'sha': HEAD, 'ref': 'f',
                 'repo': {'owner': {'login': 'o'}}},
        'base': {'ref': 'release/1.0'},
    }
    assert m.select_heads([pr]) == []


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gatefresh_')


if __name__ == '__main__':
    raise SystemExit(main())
