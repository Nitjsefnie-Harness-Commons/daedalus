#!/usr/bin/env python3
"""Routing, snapshot flow and workflow contracts for freshness."""
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _gate_freshness_fixtures import _check, _encode  # noqa: E402
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
HEAD = 'c' * 40
RUN = 'https://github.com/o/r/actions/runs/1'


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_run')


def _pr(number, sha=HEAD, base='main', owner='o', ref='feature'):
    return {'number': number, 'head': {'sha': sha, 'ref': ref,
                                       'repo': {'owner': {'login': owner}}},
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


def _main_env(tmp, event_name, event, monkey=None, moved=None, run=None):
    m = _mod()
    event_path = Path(tmp) / 'event.json'
    event_path.write_text(json.dumps(event), encoding='utf-8')
    calls, published = [], []
    monkey = monkey or {}

    def fake_gh_read(argv):
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
            return _encode({'status': 'ahead',
                            'merge_base_commit': {'sha': 'f' * 40
                                                  if monkey.get('stale')
                                                  else gate}})
        if re.search(r'/pulls/\d+$', target):
            number = int(target.rsplit('/', 1)[1])
            if monkey.get('head_fail') == number:
                raise m.QueryError('head unavailable')
            pulls = event.get('open_pulls', [event.get('pull_request')])
            sha = next(p['head']['sha'] for p in pulls
                       if p and p['number'] == number)
            return _encode({'head': {'sha': 'e' * 40 if number == moved
                                     else sha}})
        if '/check-runs?' in target and 'GET' in argv:
            if 'checks' in monkey:
                return '\n'.join(_encode(c) for c in monkey['checks'])
            if monkey.get('completed'):
                return _encode(_check(101, external_id=m.EXTERNAL_ID,
                                      summary=f'main {G1}'))
            return (_encode(_check(101, 'in_progress', 'runner-uuid'))
                    if event_name != 'push' else '')
        if '/contents/' in target:
            assert 'Accept: application/vnd.github.raw+json' in argv
            if monkey.get('contents_fail'):
                raise m.QueryError('HTTP 404')
            if '/contents/scripts/ci/' in target:
                assert target.endswith(f'?ref={G1}')
                return monkey['source']
            assert target.endswith(f'?ref={HEAD}')
            return monkey.get('workflow', 'on: [workflow_dispatch]')
        if target.endswith('/gate-freshness.yml/dispatches'):
            assert 'POST' in argv and 'ref=feature' in argv
            if monkey.get('dispatch_fail'):
                raise m.QueryError('HTTP 403')
            published.append(target)
            return ''
        if 'POST' in argv or 'PATCH' in argv:
            assert any(a.endswith('main ' + monkey.get('snapshot', G1))
                       for a in argv if a.startswith('output[summary]='))
            published.append(target)
            return '{}'
        raise AssertionError(target)
    old = {k: os.environ.get(k) for k in
           ('GITHUB_EVENT_NAME', 'GITHUB_EVENT_PATH', 'GITHUB_REPOSITORY',
            'GITHUB_SERVER_URL', 'GITHUB_RUN_ID', 'GATE_FRESHNESS_MAX_CALLS',
            'GITHUB_SHA', 'GATE_FRESHNESS_MAIN_SOURCE')}
    os.environ.update({
        'GITHUB_EVENT_NAME': event_name,
        'GITHUB_EVENT_PATH': str(event_path),
        'GITHUB_REPOSITORY': 'o/r',
        'GITHUB_SHA': monkey.get('sha', HEAD),
        'GATE_FRESHNESS_MAIN_SOURCE': monkey.get('marker', G1),
    })
    for key in ('GITHUB_SERVER_URL', 'GITHUB_RUN_ID',
                'GATE_FRESHNESS_MAX_CALLS'):
        os.environ.pop(key, None)
    try:
        kwargs = {'run': run} if run is not None else {}
        code = m.main(monkey.get('argv', []), read=fake_gh_read, **kwargs)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return code, calls, published


def test_moving_main_keeps_gate_queries_and_marker_on_one_snapshot(tmp):
    snapshot = 'd' * 40
    code, calls, published = _main_env(
        tmp, 'pull_request_target', {'pull_request': _pr(7)},
        monkey={'snapshot': snapshot, 'completed': True})
    assert code == 0 and len(published) == 1
    assert calls.count('repos/o/r/commits/main') == 1
    lookups = [path for path in calls if '/commits?' in path]
    assert lookups and all(f'?sha={snapshot}&' in path for path in lookups)
    assert calls.index('repos/o/r/commits/main') < min(
        i for i, path in enumerate(calls) if '/commits?' in path)
    assert all(f'/compare/{G1}...' in path
               for path in calls if '/compare/' in path)


def test_unreadable_main_snapshot_publishes_nothing(tmp):
    for options in ({'main_fail': True}, {'main_answer': {}},
                    {'main_answer': {'sha': 'z' * 40}}):
        code, _calls, published = _main_env(
            tmp, 'push', {'open_pulls': [_pr(7)]}, monkey=options)
        assert code == 1 and published == []


def test_the_pull_request_target_route_publishes_for_that_head(tmp):
    code, calls, published = _main_env(tmp, 'pull_request_target', {
        'pull_request': _pr(7)})
    assert code == 0, code
    assert not any('state=open' in c for c in calls), calls
    assert published == [], published


def test_event_job_conclusion_tracks_only_its_own_pr_verdict(tmp):
    for event_name in ('pull_request_target', 'workflow_dispatch', 'push'):
        for stale in (False, True):
            event = {'pull_request': _pr(7), 'open_pulls': [_pr(7)]}
            code, _calls, _published = _main_env(
                tmp, event_name, event, monkey={'stale': stale})
            assert code == int(event_name != 'push' and stale)
    code, _calls, published = _main_env(
        tmp, 'pull_request_target', {'pull_request': _pr(7)},
        monkey={'stale': True, 'argv': ['--dry-run']})
    assert code == 0 and published == []


def _push(tmp, **options):
    event = {'open_pulls': [_pr(7, owner=options.pop('owner', 'o'))]}
    return _main_env(tmp, 'push', event, monkey={
        'stale': True, 'checks': [_check(101)], **options})


def test_stale_runner_green_dispatches_once_on_the_head_ref(tmp):
    m = _mod()
    for checks in ([_check(101)], [_check(101), _check(
            80, conclusion='failure', external_id=m.EXTERNAL_ID)]):
        code, calls, writes = _push(tmp, checks=checks)
        target = 'repos/o/r/actions/workflows/gate-freshness.yml/dispatches'
        assert (code, writes) == (0, [target]), writes
        assert calls.count(target) == 1
        assert (f'repos/o/r/contents/.github/workflows/gate-freshness.yml'
                f'?ref={HEAD}') in calls
        assert len(calls) <= m.required_calls(1, len(m.GATE_PATTERNS)), calls


def test_newest_failure_across_all_suites_is_kept(tmp):
    for external in (None, 'daedalus-gate-freshness/v1'):
        checks = [_check(101),
                  _check(202, conclusion='failure', external_id=external)]
        code, calls, writes = _push(tmp, checks=checks)
        assert code == 0 and writes == [], writes
        assert not any('/contents/' in c for c in calls), calls


def test_fresh_runner_head_never_dispatches_or_writes(tmp):
    code, calls, writes = _push(tmp, stale=False)
    assert code == 0 and writes == [], writes
    assert not any('/contents/' in c for c in calls), calls


def _assert_loud_skip(tmp, reason, **options):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, calls, writes = _push(tmp, **options)
    assert code == 0 and writes == [], writes
    assert reason in err.getvalue(), err.getvalue()
    return calls


def test_cross_repository_head_is_a_loud_skip(tmp):
    calls = _assert_loud_skip(tmp, 'cross-repository', owner='fork-owner')
    assert not any('/contents/' in c for c in calls), calls


def test_workflow_without_dispatch_is_a_loud_skip(tmp):
    _assert_loud_skip(tmp, 'workflow_dispatch', workflow='on: [push]')


def test_unreadable_workflow_is_a_loud_skip(tmp):
    _assert_loud_skip(tmp, 'HTTP 404', contents_fail=True)


def test_refused_dispatch_is_a_loud_skip(tmp):
    _assert_loud_skip(tmp, 'HTTP 403', dispatch_fail=True)


def test_push_head_failure_does_not_abandon_siblings_or_fail_main(tmp):
    code, calls, writes = _main_env(tmp, 'push', {
        'open_pulls': [_pr(7), _pr(8)]}, monkey={'head_fail': 7})
    assert code == 0 and writes == [], (code, writes)
    assert 'repos/o/r/pulls/8' in calls


def test_dispatch_route_selects_only_the_event_sha(tmp):
    code, calls, writes = _main_env(tmp, 'workflow_dispatch', {
        'open_pulls': [_pr(8, sha='d' * 40), _pr(7)]},
        monkey={'stale': True})
    assert code == 1 and writes == [], (code, writes)
    assert 'repos/o/r/pulls/7' in calls
    assert 'repos/o/r/pulls/8' not in calls, calls


def test_dispatch_route_without_a_matching_head_publishes_nothing(tmp):
    code, calls, writes = _main_env(tmp, 'workflow_dispatch', {
        'open_pulls': [_pr(7, sha='d' * 40), _pr(8, base='release')]})
    assert code == 0 and writes == [], writes
    assert not any('/compare/' in c or '/commits/' in c for c in calls)


def test_dispatch_bootstraps_main_source_once_and_returns_child_exit(tmp):
    from types import SimpleNamespace
    launched = []

    def run(argv, *, env, **kwargs):
        assert argv[0] == sys.executable
        assert argv[2:] == ['--dry-run'], argv
        assert env['GATE_FRESHNESS_MAIN_SOURCE'] == G1, env
        source = Path(argv[1]).read_text(encoding='utf-8')
        launched.append(source)
        return SimpleNamespace(returncode=23)
    code, calls, writes = _main_env(tmp, 'workflow_dispatch', {
        'open_pulls': [_pr(7)]}, monkey={
            'marker': '', 'source': 'raise SystemExit(23)\n',
            'argv': ['--dry-run']}, run=run)
    assert code == 23 and writes == [], (code, writes)
    assert launched == ['raise SystemExit(23)\n'], launched
    assert calls.count('repos/o/r/contents/scripts/ci/'
                       f'gate_freshness.py?ref={G1}') == 1
    assert not any('/commits?' in c for c in calls), calls
    code, calls, writes = _main_env(tmp, 'workflow_dispatch', {
        'open_pulls': [_pr(7)]}, monkey={'marker': G1}, run=run)
    assert code == 0 and writes == []
    assert not any('/contents/scripts/' in c for c in calls), calls
    assert len(launched) == 1, launched


def test_unreadable_main_source_refuses_without_publishing(tmp):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, _calls, writes = _main_env(tmp, 'workflow_dispatch', {
            'open_pulls': [_pr(7)]}, monkey={
                'marker': '', 'contents_fail': True})
    assert code == 1 and writes == [], (code, writes)
    assert 'publishing nothing' in err.getvalue(), err.getvalue()
    assert 'HTTP 404' in err.getvalue(), err.getvalue()


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
    m, published = _mod(), []

    def read(argv):
        if 'POST' in argv or 'PATCH' in argv:
            published.append(next(t for t in argv if t.startswith('repos/')))
            return '{}'
        raise AssertionError('no read should be needed to refuse')
    for event in ({}, {'pull_request': {}},
                  {'pull_request': {'number': 7}}):
        code, err = _run_main_event(tmp, m, read, 'pull_request_target',
                                    event)
        assert code == 1, (event, code)
        assert 'pull_request' in err, err
    assert published == [], 'a missing event published a verdict'


def _run_main(tmp, m, reader, event):
    return _run_main_event(tmp, m, reader, 'push', event)


def _run_main_event(tmp, m, reader, event_name, event):
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
        code, err = _run_main(tmp, m, read, {})
        assert code == 1 and published == [], (code, published)
        assert reason in err and 'publishing nothing' in err, err


def _process_capturing_stderr(m, read, heads, **kwargs):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, verdicts = m.process(read, 'o/r', heads,
                                   [('.pylintrc', G1)], RUN, G1, **kwargs)
    return code, verdicts, err.getvalue()


_MOVED = 'gate freshness: head of pull request 7 moved; skipping'


def test_a_head_that_moved_is_skipped_not_published_onto(tmp):
    cases = (({'.pylintrc': [G1]}, {}, {'moved_to': 'd' * 40}),
             ({}, {7: HEAD}, {'moved': 'e' * 40}))
    for gates, current, flow in cases:
        m = _mod()
        published = []
        read = _flow_read(m, gates, current, published, **flow)
        code, verdicts, err = _process_capturing_stderr(
            m, read, m.select_heads([_pr(7)]))
        assert published == [], 'published onto a superseded SHA'
        assert code == 0, 'a moved head gets its own verdict; not a failure'
        assert verdicts == []
        assert _MOVED in err, 'a moved head must still be reported on stderr'


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
                        if isinstance(payload, m.QueryError):
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
    for current, flow, expected, reason in (
            ('e' * 40, {}, 0, 'moved'),
            (bad, {'unreadable': (7,)}, 1, 'could not read'),
            (bad, {'fail_listing_for': (bad,)}, 1, 'could not publish')):
        published = []
        read = _flow_read(m, {}, {7: current, 8: HEAD}, published, **flow)
        code, verdicts, err = _process_capturing_stderr(
            m, read, m.select_heads([_pr(7, sha=bad), _pr(8)]))
        assert published == [('repos/o/r/check-runs', 'POST')], published
        assert code == expected and len(verdicts) == 1, (code, verdicts)
        assert verdicts[0].conclusion == 'success'
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


def test_the_run_fails_when_the_only_head_cannot_be_published(tmp):
    m, published = _mod(), []
    bad = 'b' * 40
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: bad}, published,
                      fail_listing_for=(bad,))
    code, verdicts, err = _process_capturing_stderr(
        m, read, m.select_heads([_pr(7, sha=bad)]))
    assert published == [], 'published onto a head whose write failed'
    assert verdicts == []
    assert code == 1, 'a run with no verdict published at all must fail'
    assert 'could not publish the verdict for pull request 7' in err, err


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
    for old_ids in (('99',), tuple(str(i) for i in range(99, 106))):
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
        assert len(calls) == estimate + len(old_ids) - 6
        if len(old_ids) == 7:
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
    _run_main(tmp, m, read, {})
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
    return (ROOT / '.github' / 'workflows' / 'gate-freshness.yml').read_text(
        encoding='utf-8')


def test_the_job_carries_the_required_check_context(tmp):
    from _wfjobs import load
    m = _mod()
    assert load(ROOT / '.github' / 'workflows' / 'gate-freshness.yml').jobs[
        'publish-freshness']['name'] == m.NAME == 'gate freshness'


def test_workflow_permissions_include_dispatch_authority(tmp):
    from _yamlread import top_level_mapping
    permissions = top_level_mapping(_workflow_text(), 'permissions')
    assert permissions == {'checks': 'write', 'contents': 'read',
                           'pull-requests': 'read',
                           'actions': 'write'}, permissions


def test_workflow_declares_dispatch_without_inputs(tmp):
    from _workflows import _event_option_keys, _workflow_triggers
    triggers = _workflow_triggers(_workflow_text(), 'gate-freshness.yml')
    assert set(triggers) == {'push', 'pull_request_target',
                             'workflow_dispatch'}, triggers
    assert not _event_option_keys(triggers['workflow_dispatch'], 'g')
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
    assert 'persist-credentials: false' in text
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


if __name__ == '__main__':
    raise SystemExit(main())
