#!/usr/bin/env python3
"""Contracts for gate-freshness decisions and check publication."""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _gate_freshness_fixtures import _encode  # noqa: E402
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
G2 = 'b' * 40
HEAD = 'c' * 40
RUN = 'https://github.com/o/r/actions/runs/1'

_CHECK_LIST = (f'repos/o/r/commits/{HEAD}/check-runs'
               '?filter=all&per_page=100')
_SELECTOR = ('.check_runs[] | select(.name == "gate freshness" and '
             '.external_id == "daedalus-gate-freshness/v1" and '
             '.app.slug == "github-actions") | "\\(.id) \\(.check_suite.id)"')


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_mod')


def _compare_ok(mb, status='ahead'):
    return {'status': status, 'merge_base_commit': {'sha': mb}}


def _head(number=7, sha=HEAD, owner='octo', ref='feature', base='main'):
    return {'number': number, 'sha': sha, 'owner': owner, 'ref': ref,
            'base': base}


def _gate(path, sha):
    return (path, sha)


# ---- gate-defining path set ----

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
    assert not hasattr(m, 'CARRIED_BY_THE_BRANCH'), (
        'the carried-by-the-branch exclusion is back: a baseline a ratchet '
        'can move is exactly what a stale head must be refused for')
    for moved in ('.github/ci-thresholds.json', '.github/journey-budget.json'):
        assert m.is_gate_defining(moved), moved
        assert moved in m.GATE_PATTERNS, moved


# ---- derive the set from the workflows (the guard) ----

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


# ---- merge base: the shape guard ----

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
    gates = [_gate('.pylintrc', G1)]
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
        def read(_argv, shape=bad):
            return _encode(
                {'status': 'ahead', 'merge_base_commit': {'sha': shape}})
        mb, _ = m.merge_base(read, 'o/r', G1, _head())
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
    verdict = m.head_verdict(read, 'o/r', _head(), [_gate('.pylintrc', G1)])
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'
    text = verdict.title + ' ' + verdict.summary
    assert 'compare' in text
    assert f'compare/{G1}...{HEAD}' in text
    assert 'HTTP 404' in text
    assert 'rebase' in text.lower()
    assert 'not evidence of access denial' in text


# ---- gate commit enumeration ----

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
            _commits_read(m, asked=asked, live_shaped=live_shaped), 'o/r')
        assert len(gates) == len(m.GATE_PATTERNS)
        assert len(asked) == len(m.GATE_PATTERNS)


def test_the_commits_endpoint_receives_a_path_it_understands(tmp):
    m = _mod()
    asked = []
    m.enumerate_gates(_commits_read(m, asked=asked), 'o/r')
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
        m.enumerate_gates(read, 'o/r')
    except m.QueryError as error:
        assert 'eslint.config.js' in str(error), str(error)
        assert 'no commit' in str(error), str(error)
        return
    raise AssertionError('an empty gate-path answer was not refused')


def test_a_non_list_enumeration_payload_is_a_global_failure(tmp):
    m = _mod()
    for bad in (42, {'error': 'rate limited'}, '"a string"'):
        def read(_argv, shape=bad):
            return _encode(shape)
        try:
            m.enumerate_gates(read, 'o/r')
        except m.QueryError as error:
            assert 'list' in str(error), str(error)
            continue
        raise AssertionError(f'a non-list payload {bad!r} was not refused')


# ---- verdicts over many gate commits ----

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
    gates = [_gate('a', G1), _gate('b', G2)]
    verdict = m.head_verdict(_multi_gate_read(None), 'o/r', _head(), gates)
    assert verdict.conclusion == 'success'
    assert verdict.kind == 'fresh'
    assert not verdict.paths


def test_a_head_missing_a_middle_gate_is_red_and_names_it(tmp):
    m = _mod()
    for gates, missing, absent in (
            ([_gate('path-a', G1), _gate('path-b', G2)], G2, 'path-a'),
            ([_gate('older', G1), _gate('newer', G2)], G1, 'newer')):
        verdict = m.head_verdict(
            _multi_gate_read(missing), 'o/r', _head(), gates)
        assert verdict.conclusion == 'failure'
        assert verdict.kind == 'stale'
        assert missing[:12] in verdict.summary
        assert gates[0 if missing == G1 else 1][0] in verdict.summary
        assert absent not in verdict.summary


def test_every_gate_is_compared_not_short_circuited(tmp):
    m = _mod()
    asked = []
    gates = [_gate('a', G1), _gate('b', G2), _gate('c', 'd' * 40)]
    m.head_verdict(_multi_gate_read(G2, asked), 'o/r', _head(), gates)
    assert asked == [G1, G2, 'd' * 40], asked


def test_a_gate_commit_that_cannot_be_read_makes_the_whole_head_red(tmp):
    m = _mod()
    gates = [_gate('a', G1), _gate('b', G2)]

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        gate = target.split('compare/')[1].split('...')[0]
        if gate == G2:
            raise m.QueryError('HTTP 404')
        return _encode(_compare_ok(gate, 'ahead'))
    verdict = m.head_verdict(read, 'o/r', _head(), gates)
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'


# ---- publish: a failure must overwrite a published success ----

def _publish_read(existing_ids, recorded, runs=(), suites=None,
                  fail=None, requests=None, error=AssertionError,
                  listing=None):
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
            assert argv[argv.index('--jq') + 1] == _SELECTOR, (
                'check name, external_id and app slug must all match exactly')
            if listing is not None:
                return listing
            return '\n'.join(f'{i} {(suites or {}).get(i, 50)}'
                             for i in existing_ids)
        if target == f'repos/o/r/actions/runs?head_sha={HEAD}&per_page=100':
            assert existing_ids and method == 'GET'
            assert '--paginate' in argv
            assert '--slurp' in argv
            if fail == 'runs':
                raise error('runs unavailable')
            return _encode(runs)
        allowed = {('POST', 'repos/o/r/check-runs')} | {
            ('PATCH', f'repos/o/r/check-runs/{i}') for i in existing_ids}
        assert (method, target) in allowed, (method, target)
        recorded.append((method, target, list(argv)))
        if method == fail:
            raise error('write unavailable')
        return '{}'
    return read


def _run(run_id: int | str = 10, suite=50,
         path: str | None = '.github/workflows/other.yml', **extra):
    return {'id': run_id, 'check_suite_id': suite, 'path': path, **extra}


def test_stranded_publication_payload_precedes_name_only_retirement(tmp):
    m, recorded, requests = _mod(), [], []
    read = _publish_read([7, 8], recorded, suites={7: 91, 8: 73},
                         requests=requests,
                         runs=[{'workflow_runs': [_run(5, 91, 'healthy.yml'),
                                                  _run(suite=73)]},
                               {'workflow_runs': [_run(11, 74)]}])
    m.publish(read, 'o/r', HEAD, 'failure', 't', 's', RUN)
    assert [(v, t.rsplit('/', 1)[-1]) for v, t, _ in recorded] == [
        ('POST', 'check-runs'), ('PATCH', '7'), ('PATCH', '8')]
    assert m.NAME_RETIRED == 'superseded gate freshness'
    for _, _, argv in recorded[1:]:
        assert argv[argv.index('-X') + 2:] == [
            '-f', 'name=superseded gate freshness']
    for field in (f'head_sha={HEAD}', 'name=gate freshness',
                  'status=completed', 'conclusion=failure',
                  'external_id=daedalus-gate-freshness/v1',
                  f'details_url={RUN}', 'output[title]=t',
                  'output[summary]=s'):
        assert field in recorded[0][2], field
    assert sum('/actions/runs?' in ' '.join(a) for a in requests) == 1
    assert all('Cache-Control: no-cache' in a for a in requests)


def test_malformed_or_wrong_owner_cannot_trigger_republication(tmp):
    m = _mod()
    owner = _run(suite=73)
    cases = [[], [{'workflow_runs': []}],
             [{'workflow_runs': [owner]}],
             [{'workflow_runs': [_run(9, 74), owner]}],
             [{'workflow_runs': [owner, _run(11, 74, 'different.yml')]}],
             [{'workflow_runs': [_run(suite=91), _run(11, 74)]}],
             [{'workflow_runs': [owner, _run(10, 74)]}],
             [{'workflow_runs': [owner, _run(11, 73)]}],
             [{'workflow_runs': [owner, {}]}],
             [{'workflow_runs': [owner, _run('11', 74)]}],
             [{'workflow_runs': [owner, _run(11, 74, None)]}],
             [{'workflow_runs': [owner, _run(11, 74, '')]}],
             [{'workflow_runs': [owner, None]}],
             [{'workflow_runs': 'bad'}], {}, [{'missing': []}],
             [None], 'unparseable',
             [{'workflow_runs': [_run(True, 73), _run(11, 74)]}],
             [{'workflow_runs': [_run(suite=50), _run(11, 74)]}],
             [{'workflow_runs': [owner, _run(11, True)]}]]
    for runs in cases:
        recorded = []
        m.publish(_publish_read([7, 8], recorded, runs=runs,
                                suites={7: 73, 8: 73}),
                  'o/r', HEAD, 'success', 't', 's', RUN)
        assert [v for v, _, _ in recorded] == ['PATCH', 'PATCH'], runs
        assert all('name=gate freshness' in a for _, _, a in recorded)


def test_wrong_or_missing_suite_cannot_borrow_another_runs_owner(tmp):
    m = _mod()
    for listing in ('7', '7 null', '7 73 extra', '\ninvalid 50\n7 91\n'):
        recorded = []
        read = _publish_read(
            [7], recorded, listing=listing,
            runs=[{'workflow_runs': [_run(suite=50), _run(11, 74)]}])
        m.publish(read, 'o/r', HEAD, 'success', 't', 's', RUN)
        assert [(v, t) for v, t, _ in recorded] == [
            ('PATCH', 'repos/o/r/check-runs/7')], listing


def test_unreadable_runs_keep_the_verdict_patch(tmp):
    m, recorded = _mod(), []
    m.publish(_publish_read([7], recorded, fail='runs', error=m.QueryError),
              'o/r', HEAD, 'success', 't', 's', RUN)
    assert [v for v, _, _ in recorded] == ['PATCH']
    assert 'name=gate freshness' in recorded[0][2]


def test_failed_republish_or_retirement_raises_without_more_writes(tmp):
    m = _mod()
    for fail, attempted in (('POST', ['POST']), ('PATCH', ['POST', 'PATCH'])):
        recorded = []
        read = _publish_read([7, 8], recorded, fail=fail, error=m.QueryError,
                             suites={7: 83, 8: 83},
                             runs=[{'workflow_runs': [_run(suite=83),
                                                      _run(11, 84)]}])
        try:
            m.publish(read, 'o/r', HEAD, 'failure', 't', 's', RUN)
        except m.QueryError:
            assert [v for v, _, _ in recorded] == attempted
            continue
        raise AssertionError(f'a failed {fail} did not raise')


def test_a_failure_overwrites_a_published_success_by_patching(tmp):
    m = _mod()
    for ids, conclusion, title, summary in (
            ([4242], 'failure', 's', 'why'),
            ([7], 'success', 'f', 'ok'),
            ([], 'success', 'f', 'ok')):
        recorded = []
        m.publish(_publish_read(ids, recorded), 'o/r', HEAD, conclusion,
                  title, summary, RUN)
        assert len(recorded) == 1
        method, target, argv = recorded[0]
        assert f'conclusion={conclusion}' in argv
        assert 'name=gate freshness' in argv
        assert 'external_id=daedalus-gate-freshness/v1' in argv
        if ids:
            assert method == 'PATCH', method
            assert target.endswith(f'/check-runs/{ids[0]}'), target
            assert not any(str(a).startswith('head_sha') for a in argv), \
                'a PATCH must not resend head_sha'
        else:
            assert method == 'POST', method
            assert target.endswith('/check-runs'), target
            assert f'head_sha={HEAD}' in argv


def test_re_running_updates_the_same_check_rather_than_accumulating(tmp):
    m = _mod()
    recorded = []
    read = _publish_read([99], recorded)
    m.publish(read, 'o/r', HEAD, 'failure', 't', 's', RUN)
    m.publish(read, 'o/r', HEAD, 'failure', 't', 's', RUN)
    assert [r[0] for r in recorded] == ['PATCH', 'PATCH']
    assert all(r[1].endswith('/check-runs/99') for r in recorded)


def test_retired_suffix_cannot_pass_the_exact_listing_selector(tmp):
    requests = []
    _mod().publish(_publish_read([], [], requests=requests),
                   'o/r', HEAD, 'success', 't', 's', RUN)
    assert all('Cache-Control: no-cache' in argv for argv in requests)
    assert requests[0][requests[0].index('--jq') + 1] == _SELECTOR, (
        'check name, external_id and app slug must all match exactly')


def test_publish_fake_refuses_unmodelled_listing_calls(tmp):
    read = _publish_read([7], [])
    flags = ['--paginate', '--jq', _SELECTOR]
    for flag in ('-X', '--method'):
        assert read(['gh', 'api', _CHECK_LIST, flag, 'GET', *flags]) == '7 50'
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


# ---- select_heads ----

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
