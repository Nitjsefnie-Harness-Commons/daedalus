#!/usr/bin/env python3
"""The gate-freshness run: routing, the bound, and the workflow shape. The
decision core is in ``test_gate_freshness.py``. The global-failure entries
assert the exit code AND that nothing was published: inventing a verdict where
one could not be read is the exact defect this issue records.
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
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
HEAD = 'c' * 40
RUN = 'https://github.com/o/r/actions/runs/1'


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_run')


def _encode(value):
    return value if isinstance(value, str) else json.dumps(value)


def _pr(number, sha=HEAD, base='main', owner='octo', ref='feature'):
    return {'number': number, 'head': {'sha': sha, 'ref': ref,
                                       'repo': {'owner': {'login': owner}}},
            'base': {'ref': base}}


def _flow_read(m, gates, current, published, heads_missing=(), moved=None,
               fail_listing_for=()):
    """The per-head flow. `moved` changes the head after the first read;
    `fail_listing_for` names heads whose listing fails."""
    state = {'reads': 0}

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        method = 'GET'
        for index, token in enumerate(argv):
            if token in ('-X', '--method') and index + 1 < len(argv):
                method = argv[index + 1].upper()
        if re.search(r'/pulls/\d+$', target):
            number = int(target.rsplit('/', 1)[1])
            if number not in current:
                raise m.QueryError('HTTP 404')
            state['reads'] += 1
            sha = current[number]
            if moved is not None and state['reads'] > 1:
                sha = moved
            return _encode({'head': {'sha': sha}})
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


# ---- main(): the two trigger routes ----

def _main_env(tmp, event_name, event, monkey=None):
    """Run main() with GITHUB_* env and a fake gh_read; return (code,
    calls)."""
    m = _mod()
    event_path = Path(tmp) / 'event.json'
    event_path.write_text(json.dumps(event), encoding='utf-8')
    calls = []
    published = []

    def fake_gh_read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        calls.append(target)
        if '/pulls?state=open' in target:
            return _encode(event.get('open_pulls', []))
        if '/commits?' in target:
            return _encode([{'sha': G1}])
        if '/compare/' in target:
            gate = target.split('compare/')[1].split('...')[0]
            return _encode({'status': 'ahead',
                            'merge_base_commit': {'sha': gate}})
        if re.search(r'/pulls/\d+$', target):
            return _encode({'head': {'sha': HEAD}})
        if '/check-runs?' in target and 'GET' in argv:
            return ''
        if 'POST' in argv or 'PATCH' in argv:
            published.append(target)
            return '{}'
        raise AssertionError(target)
    monkey = monkey or {}
    old = {k: os.environ.get(k) for k in
           ('GITHUB_EVENT_NAME', 'GITHUB_EVENT_PATH', 'GITHUB_REPOSITORY',
            'GITHUB_SERVER_URL', 'GITHUB_RUN_ID', 'GATE_FRESHNESS_MAX_CALLS')}
    os.environ.update({
        'GITHUB_EVENT_NAME': event_name,
        'GITHUB_EVENT_PATH': str(event_path),
        'GITHUB_REPOSITORY': 'o/r',
    })
    for key in ('GITHUB_SERVER_URL', 'GITHUB_RUN_ID',
                'GATE_FRESHNESS_MAX_CALLS'):
        os.environ.pop(key, None)
    try:
        code = m.main(monkey.get('argv', []), read=fake_gh_read)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return code, calls, published


def test_the_push_route_publishes_for_every_open_head(tmp):
    """A push to main is the bootstrap: it publishes for every open head."""
    code, calls, published = _main_env(tmp, 'push', {
        'open_pulls': [_pr(7), _pr(8)]})
    assert code == 0, code
    assert any('state=open' in c for c in calls)
    assert len(published) == 2, published


def test_the_pull_request_target_route_publishes_for_that_head(tmp):
    """A rebase goes green within a minute: the event payload's own head."""
    code, calls, published = _main_env(tmp, 'pull_request_target', {
        'pull_request': _pr(7)})
    assert code == 0, code
    assert not any('state=open' in c for c in calls), calls
    assert len(published) == 1, published


def test_the_pull_request_target_route_refuses_a_missing_event(tmp):
    """A route that cannot establish WHICH pull request it is deciding fails
    closed: a missing or unparseable event must not become "no heads" and exit
    0 as "fresh"."""
    m = _mod()
    published = []

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
    """Run main() on `event_name`, capturing stderr. `m` is the caller's module
    instance: the reader raises that instance's QueryError and main catches the
    SAME class, so the two share it."""
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


def _only_fail(target_marker, m):
    """A reader that answers every read normally EXCEPT `target_marker`."""

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if target_marker in target:
            raise m.QueryError('HTTP 500')
        return _flow_read(m, {}, {}, [])(argv)
    return read


def test_an_unreadable_open_pull_request_list_publishes_nothing_and_fails(tmp):
    """A GLOBAL failure on the open-PR list publishes NOTHING and exits 1.
    The fake fails ONLY this read, so the gate-lookup branch does NOT fire; the
    stderr line is asserted so a DIFFERENT global failure cannot satisfy it."""
    m = _mod()
    code, err = _run_main(tmp, m, _only_fail('/pulls?state=open', m), {})
    assert code == 1, code
    assert 'open pull request list' in err, err


def test_a_non_list_open_pull_request_payload_publishes_nothing_and_fails(tmp):
    """A non-list `/pulls` answer at HTTP 200 is a GLOBAL failure, not
    "none": an error OBJECT at status 200 is a successful read whose payload is
    malformed, distinct from the QueryError limb."""
    m = _mod()
    published = []

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if '/pulls?state=open' in target:
            return _encode({'message': 'API rate limit exceeded'})
        if 'POST' in argv or 'PATCH' in argv:
            published.append(target)
            return '{}'
        return _flow_read(m, {}, {}, published)(argv)
    code, err = _run_main(tmp, m, read, {})
    assert code == 1, code
    assert 'open pull request list' in err, err
    assert published == [], 'a non-list payload published a verdict'


def test_an_unreadable_gate_lookup_publishes_nothing_and_fails(tmp):
    """A GLOBAL failure on the gate lookup publishes NOTHING and exits 1.
    The fake SUCCEEDS on the open-PR list and fails only the gate lookup, so
    this entry is satisfied by the gate branch alone."""
    m = _mod()
    published = []

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if '/commits?' in target:
            raise m.QueryError('HTTP 500')
        if '/pulls?state=open' in target:
            return _encode([_pr(7)])
        if 'POST' in argv or 'PATCH' in argv:
            published.append(target)
            return '{}'
        return _flow_read(m, {}, {}, published)(argv)
    code, err = _run_main(tmp, m, read, {})
    assert code == 1, code
    assert 'gate commit' in err, err
    assert published == [], 'a global failure published a verdict'


# ---- process(): the per-head flow ----

def test_a_fresh_head_publishes_a_green_check(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: HEAD}, published)
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [('.pylintrc', G1)], RUN)
    assert code == 0, verdicts
    assert len(published) == 1
    assert published[0][1] == 'POST'
    assert verdicts[0].conclusion == 'success'


def test_a_stale_head_publishes_a_red_check(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: HEAD}, published,
                      heads_missing={G1})
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [('.pylintrc', G1)], RUN)
    assert code == 0, 'a stale branch is a verdict, not a script failure'
    assert len(published) == 1
    assert verdicts[0].conclusion == 'failure'
    assert verdicts[0].kind == 'stale'


def _process_capturing_stderr(m, read, heads, **kwargs):
    """Returns (code, verdicts, stderr): the exit code pins what the run
    does, the stderr is the only witness that it said so."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code, verdicts = m.process(read, 'o/r', heads,
                                   [('.pylintrc', G1)], RUN, **kwargs)
    return code, verdicts, err.getvalue()


_MOVED = 'gate freshness: head of pull request 7 moved; skipping'


def test_a_head_that_moved_is_skipped_not_published_onto(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: 'z' * 40}, published)
    code, verdicts, err = _process_capturing_stderr(
        m, read, m.select_heads([_pr(7)]))
    assert published == [], 'published onto a superseded SHA'
    assert code == 0, 'a moved head gets its own verdict; not a failure'
    assert verdicts == []
    assert _MOVED in err, 'a moved head must still be reported on stderr'


def test_a_head_that_moves_between_decision_and_write_is_skipped(tmp):
    """Revalidate immediately before the write: a head that moves between the
    two revalidations is skipped, not published onto."""
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {}, {7: HEAD}, published, moved='m' * 40)
    code, _, err = _process_capturing_stderr(m, read,
                                             m.select_heads([_pr(7)]))
    assert published == [], 'published onto a SHA that stopped being the head'
    assert code == 0, 'a moved head gets its own verdict; not a failure'
    assert _MOVED in err, 'a moved head must still be reported on stderr'


def _writing_read(m, published, current, **flow):
    """A `_flow_read` whose recorded writes carry the head sha each was
    written onto, so a test can say which head was written. `m` is the
    caller's module instance, as `_only_fail` also takes it: the reader
    raises that instance's QueryError and process catches the same class."""
    base = _flow_read(m, {'.pylintrc': [G1]}, current, published, **flow)

    def read(argv):
        before = len(published)
        answer = base(argv)
        if len(published) > before:
            sha = next(field[len('head_sha='):] for field in argv
                       if field.startswith('head_sha='))
            published[-1] = (sha, 'POST')
        return answer
    return read


def test_a_moved_head_beside_a_healthy_head_publishes_the_healthy_one(tmp):
    del tmp
    m = _mod()
    published = []
    heads = m.select_heads([_pr(7), _pr(8)])
    read = _writing_read(m, published, current={7: 'e' * 40, 8: HEAD})
    code, verdicts = m.process(read, 'o/r', heads, [('.pylintrc', G1)], RUN)
    assert code == 0, 'a moved head is not a failure'
    assert published == [(HEAD, 'POST')], published
    assert len(verdicts) == 1
    assert verdicts[0].conclusion == 'success'


def test_a_moved_head_and_a_write_failure_and_a_healthy_head(tmp):
    """Rules out "exit nonzero if anything was skipped" and "always exit 0",
    each of which the one-directional tests let through."""
    del tmp
    m = _mod()
    published = []
    bad_sha = 'b' * 40
    heads = m.select_heads([_pr(7), _pr(8, sha=bad_sha), _pr(9)])
    read = _writing_read(m, published,
                         current={7: 'e' * 40, 8: bad_sha, 9: HEAD},
                         fail_listing_for=(bad_sha,))
    code, verdicts = m.process(read, 'o/r', heads, [('.pylintrc', G1)], RUN)
    assert code == 1, 'the failed write must still fail the run'
    assert published == [(HEAD, 'POST')], published
    assert len(verdicts) == 1
    assert verdicts[0].conclusion == 'success'


def test_a_head_is_revalidated_before_the_decision(tmp):
    """The first revalidation saves compare work on a superseded head."""
    del tmp
    m = _mod()
    published = []
    asked = []
    base = _flow_read(m, {'.pylintrc': [G1]}, {7: 'z' * 40}, published)

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if '/compare/' in target:
            asked.append(target)
        return base(argv)
    m.process(read, 'o/r', m.select_heads([_pr(7)]),
              [('.pylintrc', G1)], RUN)
    assert asked == [], 'a superseded head was compared before revalidation'


def test_a_per_head_write_failure_skips_that_head_and_continues(tmp):
    """A transient listing failure must not abandon the later heads: the
    failing head is skipped loudly and the healthy one still publishes."""
    del tmp
    m = _mod()
    published = []
    bad = 'b' * 40
    good = 'd' * 40
    heads = m.select_heads([_pr(7, sha=bad), _pr(8, sha=good)])
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: bad, 8: good}, published,
                      fail_listing_for=(bad,))
    code, verdicts = m.process(read, 'o/r', heads, [('.pylintrc', G1)], RUN)
    assert len(published) == 1, 'the run abandoned the later head'
    assert code != 0, 'a per-head write failure must be loud'
    assert len(verdicts) == 1


def test_the_run_fails_when_the_only_head_cannot_be_published(tmp):
    """A healthy sibling is not what makes the run red: this is the limb a
    `failed and published` exit code drops."""
    del tmp
    m = _mod()
    published = []
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
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: HEAD}, published)
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [('.pylintrc', G1)], RUN, dry_run=True)
    assert code == 0
    assert len(verdicts) == 1
    assert verdicts[0].conclusion == 'success'
    assert published == [], 'a dry run wrote a check run'


# ---- the bound ----

def test_the_per_head_flow_costs_one_compare_per_gate_plus_the_overhead(tmp):
    """Count the REAL reads a one-head flow makes: G + PER_HEAD_OVERHEAD, so
    a THIRD revalidation (a real G+5) is caught."""
    del tmp
    m = _mod()
    published = []
    reads = []
    gates = {pattern: [G1] for pattern in m.GATE_PATTERNS}
    gate_list = [(pattern, G1) for pattern in m.GATE_PATTERNS]
    base = _flow_read(m, gates, {7: HEAD}, published)

    def read(argv):
        reads.append(argv)
        return base(argv)
    m.process(read, 'o/r', m.select_heads([_pr(7)]), gate_list, RUN)
    assert len(reads) == len(m.GATE_PATTERNS) + m.PER_HEAD_OVERHEAD, (
        f'one head issued {len(reads)} reads; the shipped overhead is '
        f'{m.PER_HEAD_OVERHEAD}')


def test_every_read_carries_the_no_cache_header(tmp):
    """Every read routes through _api. This drives main() END TO END so the
    two GLOBAL call sites are reached; a per-head flow misses them."""
    m = _mod()
    published = []
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
    assert 'commits?sha=main' in joined_all, 'the gate enumeration never ran'
    assert reads, 'no reads were issued'
    for argv in reads:
        joined = ' '.join(argv)
        assert 'Cache-Control: no-cache' in joined, joined


def test_over_the_bound_refuses_and_publishes_nothing(tmp):
    """With readable heads the entry can only pass through the refusal."""
    del tmp
    m = _mod()
    published = []
    heads = m.select_heads([_pr(i) for i in range(1, 6)])
    read = _flow_read(m, {'.pylintrc': [G1]},
                      {i: HEAD for i in range(1, 6)}, published)
    code, _ = m.process(read, 'o/r', heads, [('.pylintrc', G1)], RUN,
                        call_budget=5)
    assert code != 0
    assert published == [], 'a truncated run must publish nothing'
    # All heads were readable, so nothing could have been skipped.
    assert code == 1, code


def test_within_the_bound_publishes_every_head(tmp):
    del tmp
    m = _mod()
    published = []
    heads = m.select_heads([_pr(i) for i in range(1, 4)])
    read = _flow_read(m, {'.pylintrc': [G1]},
                      {i: HEAD for i in (1, 2, 3)}, published)
    code, verdicts = m.process(read, 'o/r', heads, [('.pylintrc', G1)], RUN)
    assert code == 0, verdicts
    assert len(published) == 3
    assert len(verdicts) == 3


# ---- the workflow shape ----

def _workflow_text():
    return (ROOT / '.github' / 'workflows' / 'gate-freshness.yml').read_text(
        encoding='utf-8')


def test_the_job_is_not_named_the_published_check(tmp):
    """On pull_request_target the job's own check lands on the head, so the
    job must not carry the name the ruleset matches, or a ruleset reading by
    context sees two different things under one name."""
    del tmp
    m = _mod()
    text = _workflow_text()
    assert '  publish-freshness:' in text
    assert 'name: publish freshness' in text
    assert '  gate-freshness:' not in text, (
        'the job name collides with the published check context')
    assert m.NAME == 'gate freshness'


def test_workflow_permissions_are_exactly_the_three_scopes(tmp):
    del tmp
    from _yamlread import top_level_mapping
    permissions = top_level_mapping(_workflow_text(), 'permissions')
    assert permissions == {'checks': 'write', 'contents': 'read',
                           'pull-requests': 'read'}, permissions


def test_workflow_declares_both_triggers(tmp):
    del tmp
    from _workflows import _event_option_keys, _workflow_triggers
    triggers = _workflow_triggers(_workflow_text(), 'gate-freshness.yml')
    assert 'push' in triggers
    assert 'pull_request_target' in triggers
    assert 'branches' in _event_option_keys(triggers['push'], 'g')
    assert 'main' in ' '.join(triggers['push'])
    assert 'types' in _event_option_keys(triggers['pull_request_target'], 'g')
    types = ' '.join(triggers['pull_request_target'])
    for kind in ('opened', 'synchronize', 'reopened', 'ready_for_review'):
        assert kind in types


def test_workflow_is_well_formed(tmp):
    del tmp
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


def test_the_workflow_timeout_clears_the_enforced_call_budget(tmp):
    """The RELATION, not a literal: budget x assumed-rate < timeout. A bound
    with no per-call rate is not a bound."""
    del tmp
    text = _workflow_text()
    m = _mod()
    assert 'scripts/ci/gate_freshness.py' in text, 'no pointer to the script'
    assert 'THE BOUND' in text, 'the header does not point at the BOUND'
    doc = m.__doc__ or ''
    bound = doc[doc.index('THE BOUND'):]
    assert 'one compare' in bound
    assert 'PER_HEAD_OVERHEAD' in bound, bound
    from _wfjobs import load
    timeout = int(load(ROOT / '.github' / 'workflows'
                       / 'gate-freshness.yml').jobs[
                           'publish-freshness']['timeout-minutes'])
    needed_minutes = (m.DEFAULT_CALL_BUDGET
                      * m.ASSUMED_SECONDS_PER_CALL / 60)
    assert needed_minutes < timeout, (
        f'budget {m.DEFAULT_CALL_BUDGET} at '
        f'{m.ASSUMED_SECONDS_PER_CALL}s/call needs {needed_minutes:.1f} min, '
        f'the timeout is {timeout}')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gatefreshrun_')


if __name__ == '__main__':
    raise SystemExit(main())
