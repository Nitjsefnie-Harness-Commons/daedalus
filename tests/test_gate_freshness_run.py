#!/usr/bin/env python3
"""Routing, snapshot flow and workflow contracts for freshness."""
import contextlib, io, json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _gate_freshness_fixtures import _check, _encode  # noqa: E402
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
HEAD = 'c' * 40
RUN = 'https://github.com/o/r/actions/runs/1'
_RUN = {'id': 77, 'event': 'pull_request_target', 'status': 'completed',
        'path': '.github/workflows/gate-freshness.yml',
        'head_branch': 'feature', 'head_repository': {'full_name': 'o/r'}}


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_run')


def _pr(number, sha=HEAD, base='main', owner='o', ref='feature'):
    return {'number': number, 'head': {'sha': sha, 'ref': ref,
                                       'repo': {'full_name': owner + '/r'}},
            'base': {'ref': base}}


def _flow_read(m, gates, current, published, heads_missing=(), moved=None,
               fail_listing_for=(), moved_to=None, unreadable=()):
    state = {'reads': 0}

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        method = 'GET'
        for index, token in enumerate(argv):
            if token in ('-X', '--method') and index + 1 < len(argv):
                method = argv[index + 1].upper()
        if re.search(r'/pulls/\d+$', target):
            number = int(target.rsplit('/', 1)[1])
            state['reads'] += 1
            if number in unreadable:
                raise m.QueryError('HTTP 502')
            sha = current.get(number, moved_to)
            if moved is not None and state['reads'] > 1:
                sha = moved
            return _encode({'head': {'sha': sha}})
        if target == 'repos/o/r/commits/main':
            return _encode({'sha': G1})
        if '/commits?' in target:
            pattern = target.split('path=')[1]
            return _encode([{'sha': s} for s in gates.get(pattern, [])])
        if '/compare/' in target:
            gate = target.split('compare/')[1].split('...')[0]
            if gate in heads_missing:
                return _encode({'status': 'diverged',
                                'merge_base_commit': {'sha': 'f' * 40}})
            return _encode({'status': 'ahead',
                            'merge_base_commit': {'sha': gate}})
        if '/check-runs?' in target and method == 'GET':
            head_sha = target.split('/commits/')[1].split('/')[0]
            if head_sha in fail_listing_for:
                raise m.QueryError('could not read the check runs')
            return ''
        if method in ('POST', 'PATCH'):
            published.append((target, method))
            return '{}'
        raise AssertionError(f'{method} {target}')
    return read


def _main_env(tmp, event_name, event, monkey=None):
    m = _mod()
    event_path = Path(tmp) / 'event.json'
    event_path.write_text(json.dumps(event), encoding='utf-8')
    calls, published = [], []
    monkey = monkey or {}
    files = {p: (ROOT / p).read_bytes().decode() for p in (
        '.github/workflows/gate-freshness.yml',
        'scripts/ci/gate_freshness.py')}

    def fake_gh_read(argv):
        if 'argvs' in monkey: monkey['argvs'].append(argv)
        if '--slurp' in argv and '--jq' in argv:
            raise m.QueryError('the `--slurp` option is not supported with '
                               '`--jq` or `--template`')
        target = next(t for t in argv if t.startswith('repos/'))
        calls.append(target)
        if '/pulls?state=open' in target:
            return _encode(event.get('open_pulls', []))
        if '/commits?' in target:
            return _encode([{'sha': G1}])
        if target == 'repos/o/r/commits/main':
            if monkey.get('main_fail'):
                raise m.QueryError('main snapshot unavailable')
            return _encode(monkey.get('main_answer', {
                'sha': monkey.get('snapshot', G1)}))
        if '/compare/' in target:
            gate = target.split('compare/')[1].split('...')[0]
            stale = monkey.get('stale') and target.rsplit('...', 1)[1] not in (
                monkey.get('fresh', ()))
            return _encode({'status': 'ahead',
                            'merge_base_commit': {'sha': 'f' * 40 if stale
                                                  else gate}})
        if re.search(r'/pulls/\d+$', target):
            number = int(target.rsplit('/', 1)[1])
            if monkey.get('head_fail') == number:
                raise m.QueryError('head unavailable')
            pulls = event.get('open_pulls', [event.get('pull_request')])
            sha = next(p['head']['sha'] for p in pulls
                       if p and p['number'] == number)
            if ((number, calls.count(target)) == monkey.get('move')
                    or number == monkey.get('moved_after_listing')):
                sha = 'e' * 40
            return _encode({'head': {'sha': sha}})
        if '/check-runs?' in target and 'GET' in argv:
            if monkey.get('listing_fail'): raise m.QueryError('listing failed')
            if 'listing' in monkey: return monkey['listing']
            if 'checks' in monkey:
                return '\n'.join(_encode(c) for c in monkey['checks'])
            return (_encode(_check(101, 'in_progress', 'runner-uuid'))
                    if event_name != 'push' else '')
        if '/contents/' in target:
            assert 'Accept: application/vnd.github.raw+json' in argv
            path, ref = target.split('/contents/')[1].split('?ref=')
            if monkey.get('contents_fail') in (True, path):
                raise m.QueryError('HTTP 404')
            assert ref in {p['head']['sha'] for p in event['open_pulls']}
            key = 'workflow' if path.endswith('.yml') else 'script'
            return monkey.get(key, files[path])
        if '/actions/runs?head_sha=' in target:
            assert '--paginate' in argv
            assert argv[argv.index('--jq') + 1] == '.workflow_runs[]'
            assert 'Cache-Control: no-cache' in argv
            sha = target.split('head_sha=')[1].split('&')[0]
            if monkey.get('move_at_listing'):
                monkey['moved_after_listing'] = 7
            if sha in monkey.get('run_listing', {}):
                return monkey['run_listing'][sha]
            return '\n'.join(_encode(r) for r in monkey.get('runs', [{
                **_RUN, 'id': 77 if sha == HEAD else 78,
                'head_branch': 'feature' if sha == HEAD else 'later'}]))
        if target.endswith('/rerun'):
            assert 'POST' in argv
            if monkey.get('rerun_fail'):
                raise m.QueryError(monkey['rerun_fail'])
            published.append(int(target.split('/')[-2]))
            return ''
        if 'POST' in argv or 'PATCH' in argv:
            if 'name=superseded gate freshness' not in argv:
                assert any(a.endswith('main ' + monkey.get('snapshot', G1))
                           for a in argv if a.startswith('output[summary]='))
            if target == f'repos/o/r/check-runs/{monkey.get("write_fail")}':
                raise m.QueryError('write unavailable')
            published.append(target)
            return '{}'
        raise AssertionError(target)
    with patch.dict(os.environ, {
        'GITHUB_EVENT_NAME': event_name,
        'GITHUB_EVENT_PATH': str(event_path),
        'GITHUB_REPOSITORY': 'o/r',
        'DAEDALUS_GATE_FRESHNESS_MAX_CALLS': str(monkey.get('budget', 1200)),
    }):
        for key in ('GITHUB_SERVER_URL', 'GITHUB_RUN_ID'):
            os.environ.pop(key, None)
        local_read = (patch.object(Path, 'read_bytes',
                                   side_effect=OSError('local unreadable'))
                      if monkey.get('local_fail')
                      else contextlib.nullcontext())
        with local_read:
            code = m.main(monkey.get('argv', []), read=fake_gh_read)
    return code, calls, published


def test_moving_main_keeps_gate_queries_and_marker_on_one_snapshot(tmp):
    snapshot = 'd' * 40
    for fail, written, status in ((None, 1, 0), (101, 0, 1), (100, 1, 1)):
        checks = [_check(101, external_id=_mod().EXTERNAL_ID,
                         summary=f'main {G1}')]
        if fail: checks.append(_check(100, external_id=_mod().EXTERNAL_ID))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, calls, published = _main_env(
                tmp, 'pull_request_target', {'pull_request': _pr(7)},
                monkey={'snapshot': snapshot, 'checks': checks,
                        'write_fail': fail})
        assert code == status and len(published) == written, (code, published)
        assert calls.count('repos/o/r/commits/main') == 1
        lookups = [path for path in calls if '/commits?' in path]
        assert lookups and all(f'?sha={snapshot}&' in path for path in lookups)
        assert calls.index('repos/o/r/commits/main') < min(
            i for i, path in enumerate(calls) if '/commits?' in path)
        assert all(f'/compare/{G1}...' in path
                   for path in calls if '/compare/' in path)
        assert out.getvalue().splitlines()[-1] == (
            f'gate freshness: written {written}, reran 0, kept 0; '
            f'evaluated {written}/1 heads; resolved 18/18 gate paths'), (
                fail, out.getvalue())


def test_unreadable_main_snapshot_publishes_nothing(tmp):
    for options in ({'main_fail': True}, {'main_answer': {}},
                    {'main_answer': {'sha': 'z' * 40}}, {'budget': 1}):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, _calls, published = _main_env(
                tmp, 'push', {'open_pulls': [_pr(7)]}, monkey=options)
        assert code == 1 and published == [], (options, code, published)
        if 'budget' in options: assert 'estimated calls' in err.getvalue()


def test_event_job_conclusion_tracks_only_its_own_pr_verdict(tmp):
    for event_name in ('pull_request_target', 'push'):
        for stale in (False, True):
            event = {'pull_request': _pr(7), 'open_pulls': [_pr(7)]}
            code, calls, published = _main_env(
                tmp, event_name, event, monkey={'stale': stale})
            assert code == int(event_name != 'push' and stale)
            if event_name == 'pull_request_target':
                assert not any('state=open' in c for c in calls), calls
                assert published == [], published
    code, _calls, published = _main_env(
        tmp, 'pull_request_target', {'pull_request': _pr(7)},
        monkey={'stale': True, 'argv': ['--dry-run']})
    assert code == 0 and published == []


def _push(tmp, heads=None, **options):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code, calls, writes = _main_env(tmp, 'push', {
            'open_pulls': heads or [_pr(7)]}, monkey={
                'stale': True, 'checks': [_check(101)], **options})
    return code, calls, writes, out.getvalue(), err.getvalue()


def test_push_walks_every_head_and_reports_each_outcome(tmp):
    m, pair = _mod(), [_pr(7), _pr(8, sha='d' * 40, ref='later')]
    single, green = [_pr(7)], [_check(101)]
    runs = [{**_RUN, 'id': 2}, _RUN,
            {**_RUN, 'id': 999, 'event': 'workflow_dispatch'},
            {**_RUN, 'id': 998, 'path': 'other.yml'},
            {**_RUN, 'id': 997, 'status': 'in_progress'},
            {**_RUN, 'id': 78, 'head_branch': 'other'}, {**_RUN, 'id': 3}]
    cases = [
        ([_pr(7, owner='fork-a'), _pr(8, owner='fork-b')], {'runs': [
            {**_RUN, 'head_repository': {'full_name': 'fork-a/r'}},
            {**_RUN, 'id': 88, 'head_repository': {'full_name': 'fork-b/r'}}]},
         [77, 88], ''),
        ([_pr(7), _pr(8, ref='other')], {'runs': runs}, [77, 78], ''),
        (single, {'checks': green + [_check(80, conclusion='failure')]},
         [77], ''),
        (single, {'checks': [_check(202, conclusion='failure')]}, [], ''),
        (single, {'checks': [_check(202, external_id=m.EXTERNAL_ID,
                                    conclusion='failure')]}, [], ''),
        (pair, {'fresh': (HEAD,)}, [78], ''), (pair, {}, [77, 78], ''),
        ([_pr(7, sha='not-a-sha'), pair[1]], {}, [78], ''),
        (pair, {'move': (7, 1)}, [78], _MOVED),
        (pair, {'move': (7, 2)}, [78], _MOVED),
        (single, {'argv': ['--dry-run']}, [], '')]
    edited = 'head carries an edited gate definition'
    cases += [(single, {'runs': [_RUN, {**_RUN, 'id': 88, 'event': 'push',
                                        k: None}]}, [77], '')
              for k in ('head_branch', 'status')]
    cases += [(single, {'runs': [{**_RUN, 'head_repository': repo}]}, [],
               'no completed pull_request_target run') for repo in (None, {})]
    cases += [(single, options, [], reason) for reason, options in (
        ('no completed pull_request_target run', {'runs': []}),
        (edited, {'workflow': _workflow_text() + '# edited\n'}),
        (edited, {'script': 'raise SystemExit(0)\n'}),
        ('HTTP 404', {'contents_fail': True}),
        ('HTTP 404', {'contents_fail': 'scripts/ci/gate_freshness.py'}),
        ('could not read existing check runs', {'listing_fail': True}),
        ('malformed existing check listing', {'listing': 'not JSON'}),
        ('local unreadable', {'local_fail': True}))]
    cases += [(single, {'rerun_fail': f'gh: {r}'}, [], f'run 77: gh: {r}')
              for r in ('older than 30 days', 'maximum of 50 re-runs',
                        'run is not completed')]
    for heads, options, expected, reason in cases:
        argvs = []
        code, calls, writes, out, err = _push(
            tmp, heads, argvs=argvs, **options)
        assert not any('--slurp' in a and '--jq' in a for a in argvs)
        assert code == 0 and writes == expected, (code, writes, expected)
        assert f'reran {len(expected)},' in out, out
        if reason: assert reason in err and 'skipping' in err, err
        if reason and not expected: assert HEAD in err and 'kept 0;' in out
        assert len(calls) <= m.required_calls(len(heads), len(m.GATE_PATTERNS))


def _entrypoint(tmp, event_name, answers):
    from _workflowrun import run_step
    from _yamlsteps import workflow_mapping
    if os.name == 'nt': _util.skip('the gh executable fixture needs POSIX')
    work = Path(tmp) / 'tree'
    for path in ('scripts/ci/gate_freshness.py',
                 '.github/workflows/gate-freshness.yml'):
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    event = Path(tmp) / 'event.json'
    event.write_text('{}', encoding='utf-8')
    calls, gh = Path(tmp) / 'calls', Path(tmp) / 'gh'
    gh.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                  'if "--slurp" in sys.argv and "--jq" in sys.argv:\n'
                  '    sys.exit("the `--slurp` option is not supported "\n'
                  '             "with `--jq` or `--template`")\n'
                  'url = next(a for a in sys.argv if a.startswith("repos/"))\n'
                  'with open(os.environ["FRESHNESS_CALLS"], "a") as log:\n'
                  '    log.write(json.dumps(sys.argv[1:]) + "\\n")\n'
                  'answers = json.loads(os.environ["FRESHNESS_RESPONSES"])\n'
                  'if "/commits?" in url: url = "gates"\n'
                  'answer = answers[url]\n'
                  'if isinstance(answer, list):\n'
                  '    sys.stderr.write(answer[1]); sys.exit(answer[0])\n'
                  'sys.stdout.buffer.write(answer.encode("utf-8"))\n',
                  encoding='utf-8')
    gh.chmod(0o700)
    answers = {
        'repos/o/r/pulls?state=open&base=main&per_page=100': _encode([_pr(7)]),
        'repos/o/r/commits/main': _encode({'sha': G1}),
        **answers}
    environment = {**os.environ,
                   'PATH': str(tmp) + os.pathsep + os.environ['PATH'],
                   'GITHUB_EVENT_NAME': event_name,
                   'GITHUB_REPOSITORY': 'o/r', 'GITHUB_EVENT_PATH': str(event),
                   'FRESHNESS_CALLS': str(calls),
                   'FRESHNESS_RESPONSES': json.dumps(answers)}
    workflow = workflow_mapping(_workflow_text())
    job = workflow['jobs']['publish-freshness']
    step = next(step for step in job['steps'] if 'run' in step)
    return run_step(work, step, environment, workflow=workflow, job=job), calls


def test_push_entrypoint_ignores_adjacent_imports_and_invalid_heads(tmp):
    shadow = Path(tmp) / 'tree/scripts/ci/json.py'
    shadow.parent.mkdir(parents=True)
    shadow.write_text('raise SystemExit(17)\n', encoding='utf-8')
    result, calls = _entrypoint(tmp, 'push', {
        'repos/o/r/pulls?state=open&base=main&per_page=100':
            _encode([_pr(7, sha='z' * 40)]),
        'gates': _encode([{'sha': G1}])})
    assert result.returncode == 0, result.stderr
    assert 'evaluated 0/0 heads' in result.stdout
    assert '/pulls/7' not in calls.read_text(encoding='utf-8')


def test_push_real_adapter_refusals_are_loud_named_skips(tmp):
    from _workflowrun import recorded_writes
    for crlf in (True, False):
        result, calls = _entrypoint(tmp, 'push', {
            'gates': _encode([{'sha': G1}]),
            'repos/o/r/pulls/7': _encode({'head': {'sha': HEAD}}),
            f'repos/o/r/compare/{G1}...{HEAD}':
                _encode({'merge_base_commit': {'sha': 'f' * 40}}),
            f'repos/o/r/commits/{HEAD}/check-runs?filter=all&per_page=100': '',
            'repos/o/r/contents/.github/workflows/'
            f'gate-freshness.yml?ref={HEAD}': _workflow_text().replace(
                '\n', '\r\n') if crlf else _workflow_text(),
            f'repos/o/r/contents/scripts/ci/gate_freshness.py?ref={HEAD}':
                (ROOT / 'scripts/ci/gate_freshness.py').read_bytes().decode(),
            f'repos/o/r/actions/runs?head_sha={HEAD}&per_page=100':
                _encode({**_RUN, 'id': 7}),
            'repos/o/r/actions/runs/7/rerun':
                [1, 'gh: workflow run is older than 30 days']})
        assert result.returncode == 0, result.stderr
        writes = recorded_writes(calls)
        assert len(writes) == int(not crlf), writes
        if writes: assert '/actions/runs/7/rerun' in writes[0]
        reason = ('head carries an edited gate definition' if crlf else
                  'run 7: gh: workflow run is older than 30 days')
        assert reason in result.stderr and HEAD in result.stderr
        assert 'skipping' in result.stderr and 'kept 0;' in result.stdout


def test_unreadable_run_rows_skip_only_their_head(tmp):
    bad = [None, 'null', '[null]', '[{}]', '{}', '[]', 'not JSON',
           _encode(_RUN) + '\n\n']
    bad += [_encode({**_RUN, 'id': v}) for v in (True, 0, -1, '77', None)]
    bad += [_encode({**_RUN, k: None}) for k in ('event', 'path')]
    bad += [_encode({**_RUN, k: True}) for k in (
        'status', 'head_branch', 'head_repository')]
    bad += [_encode(_RUN) + '\n' + _encode({**_RUN, 'id': '78'})]
    for listing in bad:
        code, _calls, writes, _out, err = _push(tmp, [
            _pr(7), _pr(8, sha='d' * 40, ref='later')],
            head_fail=7 if listing is None else None,
            run_listing={HEAD: listing})
        assert code == 0 and writes == [78], (listing, code, writes)
        assert HEAD in err and 'skipping' in err, err
        reason = ('head unavailable' if listing is None else
                  'malformed workflow runs listing')
        assert reason in err, listing


def test_head_moving_at_run_listing_is_not_rerun(tmp):
    code, _calls, writes, _out, err = _push(tmp, move_at_listing=True)
    assert code == 0 and writes == [], writes
    assert HEAD in err and 'moved to ' + 'e' * 40 in err and 'skipping' in err


def test_event_head_identity_must_be_readable(tmp):
    for sha in (None, '', 'z' * 40, 'A' * 40, 'a' * 39, 123):
        pr = {'number': 7, 'head': {'sha': sha}, 'base': {'ref': 'main'}}
        code, _calls, published = _main_env(
            tmp, 'pull_request_target', {'pull_request': pr})
        assert code == 1 and published == [], sha
    code, _calls, published = _main_env(
        tmp, 'pull_request_target', {'pull_request': _pr(7, base='release')})
    assert code == 0 and published == []


def test_the_pull_request_target_route_refuses_a_missing_event(tmp):
    m = _mod()

    def read(_argv):
        raise AssertionError('no read should be needed to refuse')
    for event in ({}, {'pull_request': {}},
                  {'pull_request': {'number': 7}}):
        code, err = _run_main_event(tmp, m, read, event,
                                    event_name='pull_request_target')
        assert code == 1, (event, code)
        assert 'pull_request' in err, err


def _run_main_event(tmp, m, reader, event, event_name='push'):
    event_path = Path(tmp) / 'event.json'
    event_path.write_text(json.dumps(event), encoding='utf-8')
    os.environ.update({'GITHUB_EVENT_NAME': event_name,
                       'GITHUB_EVENT_PATH': str(event_path),
                       'GITHUB_REPOSITORY': 'o/r'})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            code = m.main([], read=reader)
    finally:
        os.environ.pop('GITHUB_EVENT_NAME', None)
        os.environ.pop('GITHUB_EVENT_PATH', None)
    return code, err.getvalue()


def test_global_read_failures_publish_nothing_and_fail(tmp):
    for broken, malformed, reason in (
            ('/pulls?state=open', False, 'open pull request list'),
            ('/pulls?state=open', True, 'open pull request list'),
            ('/commits?', False, 'gate commit')):
        m, published = _mod(), []
        base = _flow_read(m, {}, {}, published)

        def read(argv, broken=broken, malformed=malformed, m=m, base=base):
            target = next(t for t in argv if t.startswith('repos/'))
            if broken in target:
                if malformed:
                    return _encode({'message': 'API rate limit exceeded'})
                raise m.QueryError('HTTP 500')
            if '/pulls?state=open' in target:
                return _encode([_pr(7)])
            return base(argv)
        code, err = _run_main_event(tmp, m, read, {})
        assert code == 1 and published == [], (code, published)
        assert reason in err and 'publishing nothing' in err, err


def _process_capturing_stderr(m, read, heads, **kwargs):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, verdicts = m.process(read, 'o/r', heads,
                                   [('.pylintrc', G1)], RUN, G1, **kwargs)
    return code, verdicts, err.getvalue()


_MOVED = 'gate freshness: head of pull request 7 moved; skipping'


def test_an_unreadable_sha_fails_either_revalidation(tmp):
    m, calls = _mod(), []
    for payload in ({}, {'head': {}}, {'head': {'sha': None}},
                    {'head': {'sha': 'z' * 40}}, {'head': {'sha': 'a' * 39}},
                    m.QueryError('HTTP 502')):
        for invalid_at in (1, 2):
            published = []
            calls.clear()
            base = _flow_read(m, {}, {7: HEAD}, published)

            def read(argv, invalid_at=invalid_at,
                     payload=payload, base=base):
                if re.search(r'/pulls/\d+$', ' '.join(argv)):
                    calls.append(argv)
                    if len(calls) == invalid_at:
                        if isinstance(payload, BaseException):
                            raise payload
                        return _encode(payload)
                return base(argv)
            code, verdicts, err = _process_capturing_stderr(
                m, read, m.select_heads([_pr(7)]))
            assert code == 1 and verdicts == [] and published == []
            assert _MOVED not in err and 'could not read' in err


def test_a_healthy_sibling_still_publishes_beside_a_skipped_head(tmp):
    m = _mod()
    bad = '9' * 40
    for current, flow, expected, reason, sibling in (
            ('e' * 40, {}, 0, 'moved', True),
            (bad, {'unreadable': (7,)}, 1, 'could not read', True),
            (bad, {'fail_listing_for': (bad,)}, 1, 'could not publish', True),
            (bad, {'fail_listing_for': (bad,)}, 1,
             'could not publish', False)):
        published = []
        read = _flow_read(m, {}, {7: current, 8: HEAD}, published, **flow)
        heads = [_pr(7, sha=bad)] + ([_pr(8)] if sibling else [])
        code, verdicts, err = _process_capturing_stderr(
            m, read, m.select_heads(heads))
        assert published == ([('repos/o/r/check-runs', 'POST')]
                             if sibling else []), published
        assert code == expected and len(verdicts) == int(sibling), verdicts
        if sibling: assert verdicts[0].conclusion == 'success'
        assert reason in err, err


def test_a_head_is_revalidated_before_the_decision(tmp):
    m, published = _mod(), []
    asked = []
    base = _flow_read(m, {'.pylintrc': [G1]}, {}, published,
                      moved_to='d' * 40)

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if '/compare/' in target:
            asked.append(target)
        return base(argv)
    m.process(read, 'o/r', m.select_heads([_pr(7)]),
              [('.pylintrc', G1)], RUN, G1)
    assert asked == [], 'a superseded head was compared before revalidation'


def test_dry_run_computes_every_verdict_and_publishes_nothing(tmp):
    m, published = _mod(), []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: HEAD}, published)
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [('.pylintrc', G1)], RUN, G1, dry_run=True)
    assert code == 0
    assert len(verdicts) == 1
    assert verdicts[0].conclusion == 'success'
    assert published == [], 'a dry run wrote a check run'


def test_duplicate_ids_publish_past_the_single_id_call_estimate(tmp):
    m = _mod()
    published, calls = [], []
    for old_ids in (('99',), tuple(str(i) for i in range(99, 107))):
        published.clear()
        calls.clear()
        gates = {m._request_path(p): [G1] for p in m.GATE_PATTERNS}
        base = _flow_read(m, gates, {7: HEAD}, published)

        def read(argv, ids=old_ids, base=base):
            calls.append(argv)
            target = next(t for t in argv if t.startswith('repos/'))
            if target == 'repos/o/r/commits/main':
                return _encode({'sha': 'b' * 40})
            if '/check-runs?' in target:
                return '\n'.join(_encode(_check(
                    int(i), external_id=m.EXTERNAL_ID, summary=f'main {G1}'))
                    for i in ids)
            return base(argv)
        snapshot = m.main_head(read, 'o/r')
        gate_list = m.enumerate_gates(read, 'o/r', snapshot)
        estimate = m.required_calls(1, len(gate_list))
        code, verdicts = m.process(
            read, 'o/r', m.select_heads([_pr(7)]), gate_list, RUN, snapshot,
            call_budget=estimate)
        assert code == 0 and verdicts, 'duplicate-id overrun must publish'
        assert [v for _, v in published] == ['PATCH'] * len(old_ids)
        assert len(calls) == estimate + len(old_ids) - 5
        if len(old_ids) == 8:
            assert len(calls) > estimate, 'duplicate overrun not reached'


def test_every_read_carries_the_no_cache_header(tmp):
    m, published = _mod(), []
    reads = []

    def read(argv):
        reads.append(list(argv))
        target = next(t for t in argv if t.startswith('repos/'))
        if '/pulls?state=open' in target:
            return _encode([_pr(7)])
        if '/commits?' in target:
            return _encode([{'sha': G1}])
        if 'POST' in argv or 'PATCH' in argv:
            published.append(target)
            return '{}'
        return _flow_read(m, {p: [G1] for p in m.GATE_PATTERNS},
                          {7: HEAD}, published)(argv)
    _run_main_event(tmp, m, read, {})
    joined_all = ' '.join(' '.join(a) for a in reads)
    assert 'state=open' in joined_all, 'the open-PR listing was never read'
    assert f'commits?sha={G1}' in joined_all
    assert reads, 'no reads were issued'
    for argv in reads:
        joined = ' '.join(argv)
        assert 'Cache-Control: no-cache' in joined, joined


def test_refusal_discloses_estimate_before_publishing_nothing(tmp):
    m, published = _mod(), []
    heads = m.select_heads([_pr(i) for i in range(1, 6)])
    read = _flow_read(m, {'.pylintrc': [G1]},
                      {i: HEAD for i in range(1, 6)}, published)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code, _ = m.process(read, 'o/r', heads, [('.pylintrc', G1)], RUN, G1,
                            call_budget=5)
    assert code == 1 and not published, 'a refusal must publish nothing'
    assert 'estimated' in stderr.getvalue(), stderr.getvalue()


def _workflow_text():
    path = ROOT / '.github/workflows/gate-freshness.yml'
    return path.read_bytes().decode('utf-8')


def test_the_job_carries_the_required_check_context(tmp):
    from _wfjobs import load
    m = _mod()
    assert load(ROOT / '.github' / 'workflows' / 'gate-freshness.yml').jobs[
        'publish-freshness']['name'] == m.NAME == 'gate freshness'


def test_workflow_permissions_include_rerun_authority(tmp):
    from _yamlread import top_level_mapping
    permissions = top_level_mapping(_workflow_text(), 'permissions')
    assert permissions == {'checks': 'write', 'contents': 'read',
                           'pull-requests': 'read',
                           'actions': 'write'}, permissions


def test_workflow_declares_only_push_and_pull_request_target(tmp):
    from _workflows import _event_option_keys, _workflow_triggers
    triggers = _workflow_triggers(_workflow_text(), 'gate-freshness.yml')
    assert set(triggers) == {'push', 'pull_request_target'}, triggers
    from _yamlsteps import workflow_mapping
    assert workflow_mapping(_workflow_text())['on'][
        'pull_request_target'].get('branches') == ['main']
    assert 'branches' in _event_option_keys(triggers['push'], 'g')
    assert 'main' in ' '.join(triggers['push'])
    assert 'types' in _event_option_keys(triggers['pull_request_target'], 'g')
    types = ' '.join(triggers['pull_request_target'])
    for kind in ('opened', 'synchronize', 'reopened', 'ready_for_review'):
        assert kind in types


def test_workflow_is_well_formed(tmp):
    text = _workflow_text()
    assert 'concurrency:' in text
    from _wfjobs import load
    for name, job in load(ROOT / '.github' / 'workflows'
                          / 'gate-freshness.yml').jobs.items():
        assert 'timeout-minutes' in job, name
    assert 'github.event.pull_request.head' not in text
    assert 'refs/pull' not in text
    assert 'thresholds.py --check' not in text


def test_workflow_timeout_leaves_slack_above_the_call_estimate(tmp):
    m = _mod()
    from _wfjobs import load
    timeout = int(load(ROOT / '.github' / 'workflows'
                       / 'gate-freshness.yml').jobs[
                           'publish-freshness']['timeout-minutes'])
    needed_minutes = (m.DEFAULT_CALL_BUDGET
                      * m.ASSUMED_SECONDS_PER_CALL / 60)
    assert (needed_minutes, timeout) == (30, 35)
    assert needed_minutes < timeout


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gatefreshrun_')


def test_call_budget_environment_precedence(tmp):
    m = _mod()
    for primary, legacy, want in (('17', '9', 17), (None, '9', 9),
                                  ('', '9', 9), ('17', None, 17),
                                  (None, None, m.DEFAULT_CALL_BUDGET)):
        env = {} if legacy is None else {'GATE_FRESHNESS_MAX_CALLS': legacy}
        if primary is not None:
            env['DAEDALUS_GATE_FRESHNESS_MAX_CALLS'] = primary
        with patch.dict(os.environ, env, clear=True):
            assert m._call_budget() == want


if __name__ == '__main__':
    raise SystemExit(main())
