#!/usr/bin/env python3
"""The gate-freshness run: routing, the bound, and the workflow shape.

`scripts/ci/gate_freshness.py`'s decision core is exercised in
``test_gate_freshness.py``. This module drives the parts that only exist at
run time: ``main``'s two trigger routes and its global-failure exits, the
per-head flow through ``process`` (revalidation, a per-head write failure, the
dry run), the call bound, and the shape of the workflow that runs it. The
global-failure entries assert the exit code AND that nothing was published: a
run that invents a verdict where it could not read one is the exact defect this
issue records.
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
    """The whole per-head flow. `moved` makes the head change after the first
    pull-request read, so the pre-write revalidation sees a different sha.
    `fail_listing_for` names heads whose existing-check listing fails."""
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
    # The event head, not the open-PR list.
    assert not any('state=open' in c for c in calls), calls
    assert len(published) == 1, published


def _run_main(tmp, m, reader, event):
    """Run main() on a `push` event, capturing stderr; return (code, err).

    `m` is the caller's module instance: the reader raises that instance's
    QueryError, and main catches the SAME class, so the two must share it.
    """
    event_path = Path(tmp) / 'event.json'
    event_path.write_text(json.dumps(event), encoding='utf-8')
    os.environ.update({'GITHUB_EVENT_NAME': 'push',
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

    The fake fails ONLY the open-PR listing and answers every other read
    normally, so the gate-lookup branch does NOT fire: this entry is satisfied
    by the open-PR branch alone. A fail-open (`pulls = []`) would continue to a
    successful gate lookup, publish zero, and exit 0 -- caught here. The
    stderr line is asserted so a DIFFERENT global failure cannot satisfy it.
    """
    m = _mod()
    code, err = _run_main(tmp, m, _only_fail('/pulls?state=open', m), {})
    assert code == 1, code
    assert 'open pull request list' in err, err


def test_an_unreadable_gate_lookup_publishes_nothing_and_fails(tmp):
    """A GLOBAL failure on the gate lookup publishes NOTHING and exits 1.

    The fake SUCCEEDS on the open-PR list and fails only the gate lookup, so
    this entry is satisfied by the gate branch alone. The severe direction:
    treating an unreadable gate as "no gates" would publish every head GREEN
    claiming it contains every gate-defining commit.
    """
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


def test_a_head_that_moved_is_skipped_not_published_onto(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: 'z' * 40}, published)
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [('.pylintrc', G1)], RUN)
    assert published == [], 'published onto a superseded SHA'
    assert code != 0, 'skipping a moved head must be loud'
    assert verdicts == []


def test_a_head_that_moves_between_decision_and_write_is_skipped(tmp):
    """Revalidate immediately before the write.

    A head can move after the first revalidation and before the publish. The
    only entry that reaches this is a read whose answer changes between the
    two revalidations; it discriminates the pre-write revalidation from the
    first one, which the moved-head entry above already covers.
    """
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {}, {7: HEAD}, published, moved='m' * 40)
    code, _ = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                        [('.pylintrc', G1)], RUN)
    assert published == [], 'published onto a SHA that stopped being the head'
    assert code != 0, 'skipping a head that moved must be loud'


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
    """A transient listing failure must not abandon the later heads.

    One head's write failing raises out of the old loop, killing the run; the
    later heads -- including stale ones waiting for a red -- get nothing. Here
    the failing head is skipped loudly and the healthy one still publishes.
    """
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
    """Count the REAL reads a one-head flow makes: G + PER_HEAD_OVERHEAD.

    Three entries once restated the formula and none measured the flow, so a
    THIRD revalidation (a real G+5) left them green. This one counts the reads
    the flow actually issues and compares that count to the shipped overhead.
    """
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
    """Every `gh api` read routes through _api, which adds the header.

    Dropping the header from any call site used to leave the suite green; now
    one entry drives a full flow and checks each read argv carries it, so the
    single _api helper is the one place the convention lives.
    """
    del tmp
    m = _mod()
    published = []
    reads = []
    gates = {pattern: [G1] for pattern in m.GATE_PATTERNS}
    base = _flow_read(m, gates, {7: HEAD}, published)

    def read(argv):
        reads.append(list(argv))
        return base(argv)
    m.process(read, 'o/r', m.select_heads([_pr(7)]),
              [(p, G1) for p in m.GATE_PATTERNS], RUN)
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
    # The heads were all readable, so nothing could have been skipped: the only
    # way to publish nothing here is the bound refusal.
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
    """The RELATION, not a literal: budget x assumed-rate < timeout.

    A bound with no per-call rate is not a bound. The workflow's timeout must
    clear DEFAULT_CALL_BUDGET calls at ASSUMED_SECONDS_PER_CALL; the header
    points at the script's BOUND section, whose test pins this relation.
    """
    del tmp
    text = _workflow_text()
    m = _mod()
    assert 'scripts/ci/gate_freshness.py' in text, 'no pointer to the script'
    assert 'THE BOUND' in text, 'the header does not point at the BOUND'
    doc = m.__doc__ or ''
    bound = doc[doc.index('THE BOUND'):]
    assert 'one compare' in bound
    assert 'PER_HEAD_OVERHEAD' in bound, bound
    # The relation: the timeout the workflow sets must clear the budget.
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
