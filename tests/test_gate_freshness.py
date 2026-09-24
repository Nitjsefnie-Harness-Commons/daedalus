#!/usr/bin/env python3
"""Contract for the gate-freshness publication (issue 1008).

Every decision function is pure and driven here through an injected fake for
the ``read`` seam, so a verdict is tested without a network and the network is
tested without a real pull request. The two halves that must never be confused
-- "the branch is stale" and "the answer could not be computed" -- get separate
entries, and the publish path is exercised against a head already carrying a
published success, because overwriting that green is the whole defect.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
G2 = 'b' * 40
HEAD = 'c' * 40
RUN = 'https://github.com/o/r/actions/runs/1'


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_mod')


def _url(argv):
    for token in argv:
        if token.startswith('repos/'):
            return token
    raise AssertionError(f'no repos/ target in {argv!r}')


def _method(argv):
    for index, token in enumerate(argv):
        if token in ('-X', '--method') and index + 1 < len(argv):
            return argv[index + 1].upper()
    return 'GET'


def _encode(value):
    return value if isinstance(value, str) else json.dumps(value)


def _compare_ok(mb, status='ahead'):
    return {'status': status, 'merge_base_commit': {'sha': mb}}


def _head(number=7, sha=HEAD, owner='octo', ref='feature', base='main'):
    return {'number': number, 'sha': sha, 'owner': owner, 'ref': ref,
            'base': base}


def _gate(path, sha):
    return (path, sha)


def _pr(number, sha=HEAD, base='main', owner='octo', ref='feature'):
    return {'number': number, 'head': {'sha': sha, 'ref': ref,
                                       'repo': {'owner': {'login': owner}}},
            'base': {'ref': base}}


# ---- gate-defining path set ----

def test_the_set_is_exactly_the_documented_patterns(tmp):
    """Every pattern matches its own shape, and a nested match is included."""
    del tmp
    m = _mod()
    assert tuple(m.GATE_PATTERNS) == (
        '.github/workflows/**', '.github/ci-thresholds.json',
        'scripts/ci/**', 'pyrightconfig.json', 'pyrightconfig.tests.json',
        '.pylintrc', 'setup.cfg', 'eslint.config.js', 'pyproject.toml',
        'run_tests.py', 'requirements-dev.txt', 'requirements-test.txt')
    for path in m.GATE_PATTERNS:
        assert m.is_gate_defining(path), path
    assert m.is_gate_defining('scripts/ci/deep/x.py')
    assert m.is_gate_defining('.github/workflows/a/b/c.yml')  # nested


def test_near_miss_spellings_are_not_gate_defining(tmp):
    """A change to a test or a similarly named neighbour is not a gate."""
    del tmp
    m = _mod()
    negatives = (
        'scripts/ci_not/x.py',        # sibling directory, not under scripts/ci
        '.github/workflowsfoo/x.yml',  # workflows, not workflows/
        'tests/anything.py',          # a changed test is not a gate change
        'tests/test_gate_freshness.py', 'daedalus_bridge/result_routes.py',
        'server.py', 'README.md',
        '.github/dependabot.yml',     # proposes updates; decides no verdict
        '.github/ci-thresholds.json.bak', 'pyrightconfig.jsonx',
        'setup.cfg.bak', 'run_tests.pyc',
    )
    for path in negatives:
        assert not m.is_gate_defining(path), path


def test_the_docstring_justifies_every_pattern(tmp):
    """Each entry is justified in the module docstring, not merely listed."""
    del tmp
    m = _mod()
    doc = m.__doc__ or ''
    for pattern in m.GATE_PATTERNS:
        leaf = pattern.rsplit('/', 1)[-1].replace('*', '')
        assert leaf in doc, (pattern, leaf)


# ---- derive the set from the workflows ----

_GATE_REFS = (
    re.compile(r'--rcfile=([\w.\-]+)'),
    re.compile(r'\b(requirements-[\w\-]+\.txt)\b'),
    re.compile(r'\b(pyrightconfig[\w.\-]*\.json)\b'),
    re.compile(r'\b(eslint\.config(?:\.[\w\-]+)*\.js)\b'),
    re.compile(r'\b(run_tests\.py)\b'),
    re.compile(r'\b(setup\.cfg)\b'),
    re.compile(r'\b(pyproject\.toml)\b'),
    re.compile(r'\b(\.github/ci-thresholds\.json)\b'),
    re.compile(r'\b(scripts/ci/[\w\-]+\.py)\b'),
)


def _referenced_gate_files(directory):
    found = set()
    for path in sorted(directory.glob('*.yml')):
        text = path.read_text(encoding='utf-8')
        for pattern in _GATE_REFS:
            found.update(pattern.findall(text))
    return found


def test_every_gate_file_the_workflows_name_is_in_the_set(tmp):
    """The set is derived from the workflows, not trusted from a reading."""
    del tmp
    m = _mod()
    referenced = _referenced_gate_files(ROOT / '.github' / 'workflows')
    assert referenced, 'the workflows name no gate-configuration file'
    for path in sorted(referenced):
        assert m.is_gate_defining(path), path


def test_a_planted_new_gate_file_is_caught_by_the_derivation(tmp):
    """The derivation discriminates: a new requirements file is not listed."""
    m = _mod()
    root = Path(tmp) / 'workflows'
    root.mkdir()
    (root / 'probe.yml').write_text(
        'jobs:\n  lint:\n    steps:\n      - run: pip install -r'
        ' requirements-lint.txt\n', encoding='utf-8')
    found = _referenced_gate_files(root)
    assert 'requirements-lint.txt' in found
    assert not m.is_gate_defining('requirements-lint.txt')


# ---- merge base: the shape guard ----

def test_the_merge_base_tells_ancestor_from_not(tmp):
    del tmp
    m = _mod()
    same, attempts = m.merge_base(
        lambda _a: _encode(_compare_ok(G1)), 'o/r', G1, _head())
    assert same == G1
    assert attempts[0][1].endswith(G1[:12])
    other, _ = m.merge_base(lambda _a: _encode(_compare_ok('d' * 40)),
                            'o/r', G1, _head())
    assert other == 'd' * 40


def test_status_is_not_load_bearing_the_merge_base_is(tmp):
    """`ahead` with a different merge base is still stale; `diverged` with a
    matching merge base is still fresh. The decision reads merge_base."""
    del tmp
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
    del tmp
    m = _mod()
    for bad in (None, '', 'ZZZ', 'A' * 40, 'a' * 39, 'a' * 41, 12345):
        def read(_argv, shape=bad):
            return _encode(
                {'status': 'ahead', 'merge_base_commit': {'sha': shape}})
        mb, _ = m.merge_base(read, 'o/r', G1, _head())
        assert mb is None, bad


def test_an_absent_merge_base_field_is_unreadable(tmp):
    del tmp
    m = _mod()
    mb, _ = m.merge_base(lambda _a: _encode({'status': 'ahead'}),
                         'o/r', G1, _head())
    assert mb is None


# ---- fork / unreadable: the two compares ----

def _two_form_read(m, first_error=None, second=None, mb=G1):
    """Route the first compare (head SHA) and the fallback (owner:ref)."""
    def read(argv):
        target = _url(argv)
        if '/compare/' not in target:
            raise AssertionError(target)
        if target.endswith(f'...{HEAD}'):
            if first_error is not None:
                raise first_error
            return _encode(_compare_ok(mb, 'ahead'))
        if isinstance(second, BaseException):
            raise second
        if second is not None:
            return _encode(second)
        return _encode(_compare_ok(mb, 'ahead'))
    return read


def test_first_compare_fails_but_the_fallback_succeeds_is_fresh(tmp):
    """A head the first spelling cannot reach but the second can is green.
    This is the discriminator between the fallback and the unreadable branch:
    a single '404 goes red' assertion passes with the fallback gone too."""
    del tmp
    m = _mod()
    read = _two_form_read(m, first_error=m.QueryError('HTTP 404'),
                          second=_compare_ok(G1, 'ahead'))
    mb, _ = m.merge_base(read, 'o/r', G1, _head())
    assert mb == G1
    verdict = m.head_verdict(read, 'o/r', _head(), [_gate('.pylintrc', G1)])
    assert verdict.conclusion == 'success', verdict.summary


def test_first_compare_fails_and_fallback_returns_a_different_base_is_stale(
        tmp):
    del tmp
    m = _mod()
    read = _two_form_read(m, first_error=m.QueryError('HTTP 404'),
                          second=_compare_ok('e' * 40, 'diverged'))
    verdict = m.head_verdict(read, 'o/r', _head(), [_gate('.pylintrc', G1)])
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'stale'


def test_both_compares_failing_is_unreadable_and_red(tmp):
    del tmp
    m = _mod()
    read = _two_form_read(m, first_error=m.QueryError('HTTP 404'),
                          second=m.QueryError('HTTP 404'))
    mb, _ = m.merge_base(read, 'o/r', G1, _head())
    assert mb is None
    verdict = m.head_verdict(read, 'o/r', _head(), [_gate('.pylintrc', G1)])
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'


def test_unreadable_summary_states_the_observation_not_a_diagnosis(tmp):
    """A 404 is not evidence of access denial; the message names the two
    requests and their outcomes, and the remedy."""
    del tmp
    m = _mod()
    read = _two_form_read(m, first_error=m.QueryError('HTTP 404'),
                          second=m.QueryError('HTTP 404'))
    verdict = m.head_verdict(read, 'o/r', _head(), [_gate('.pylintrc', G1)])
    text = (verdict.title + ' ' + verdict.summary).lower()
    assert 'access denied' not in text
    assert 'permission' not in text
    assert 'compare' in text
    assert '404' in text
    assert 'rebase' in text


def _enumeration_read(commits):
    def read(argv):
        target = _url(argv)
        if '/commits?' not in target:
            raise AssertionError(target)
        pattern = target.split('path=')[1]
        return _encode([{'sha': s} for s in commits.get(pattern, [])])
    return read


def test_enumerate_gates_reads_one_commit_per_path(tmp):
    del tmp
    m = _mod()
    read = _enumeration_read({'.pylintrc': [G1], 'setup.cfg': [G2]})
    gates = m.enumerate_gates(read, 'o/r')
    assert sorted(gates) == sorted([('.pylintrc', G1), ('setup.cfg', G2)])


def test_a_gate_path_with_no_commits_contributes_nothing(tmp):
    """The API returning [] for a path is not an error."""
    del tmp
    m = _mod()
    read = _enumeration_read({'.pylintrc': [G1], 'eslint.config.js': []})
    assert m.enumerate_gates(read, 'o/r') == [('.pylintrc', G1)]


def test_enumeration_unreadable_is_a_global_failure(tmp):
    del tmp
    m = _mod()

    def read(_argv):
        raise m.QueryError('HTTP 500')
    assert m.enumerate_gates(read, 'o/r') is None


def _multi_gate_read(missing_at):
    """A head that lacks exactly the gate commit `missing_at`."""
    def read(argv):
        gate = _url(argv).split('compare/')[1].split('...')[0]
        if gate == missing_at:
            return _encode(_compare_ok('f' * 40, 'diverged'))
        return _encode(_compare_ok(gate, 'ahead'))
    return read


def test_a_fresh_head_is_green_and_names_no_missing_gate(tmp):
    del tmp
    m = _mod()
    gates = [_gate('a', G1), _gate('b', G2)]
    verdict = m.head_verdict(_multi_gate_read(None), 'o/r', _head(), gates)
    assert verdict.conclusion == 'success'
    assert verdict.kind == 'fresh'
    assert not verdict.paths


def test_a_head_missing_a_middle_gate_is_red_and_names_it(tmp):
    del tmp
    m = _mod()
    gates = [_gate('path-a', G1), _gate('path-b', G2)]
    verdict = m.head_verdict(_multi_gate_read(G2), 'o/r', _head(), gates)
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'stale'
    assert G2[:12] in verdict.summary
    assert 'path-b' in verdict.summary
    assert 'path-a' not in verdict.summary


def test_every_gate_commit_is_checked_not_just_the_newest(tmp):
    """The newest gate is not a proxy for the rest: a head lacking only the
    older one is still red."""
    del tmp
    m = _mod()
    gates = [_gate('older', G1), _gate('newer', G2)]
    verdict = m.head_verdict(_multi_gate_read(G1), 'o/r', _head(), gates)
    assert verdict.conclusion == 'failure'
    assert G1[:12] in verdict.summary


def test_a_gate_commit_that_cannot_be_read_makes_the_whole_head_red(tmp):
    del tmp
    m = _mod()
    gates = [_gate('a', G1), _gate('b', G2)]

    def read(argv):
        gate = _url(argv).split('compare/')[1].split('...')[0]
        if gate == G2:
            raise m.QueryError('HTTP 404')
        return _encode(_compare_ok(gate, 'ahead'))
    verdict = m.head_verdict(read, 'o/r', _head(), gates)
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'


# ---- publishing: a failure must overwrite a published success ----

def _publish_read(existing_ids, recorded):
    def read(argv):
        target = _url(argv)
        method = _method(argv)
        if '/check-runs?' in target and method == 'GET':
            return '\n'.join(str(i) for i in existing_ids)
        if method in ('POST', 'PATCH'):
            recorded.append((method, target, list(argv)))
            return '{}'
        raise AssertionError(f'{method} {target}')
    return read


def test_a_failure_overwrites_a_published_success_by_patching(tmp):
    """The regression: a head that already shows `gate freshness` green must
    be PATCHed to red, not left alone. Deleting the PATCH leaves the stale
    green in place, which is the defect."""
    del tmp
    m = _mod()
    recorded = []
    m.publish(_publish_read([4242], recorded), 'o/r', HEAD, 'failure', 's',
              'why', RUN)
    assert len(recorded) == 1
    method, target, argv = recorded[0]
    assert method == 'PATCH', method
    assert target.endswith('/check-runs/4242'), target
    assert 'conclusion=failure' in argv
    assert 'name=gate freshness' in argv
    assert 'external_id=daedalus-gate-freshness/v1' in argv
    assert not any(str(a).startswith('head_sha') for a in argv), \
        'a PATCH must not resend head_sha'


def test_a_success_overwrites_a_published_failure(tmp):
    del tmp
    m = _mod()
    recorded = []
    m.publish(_publish_read([7], recorded), 'o/r', HEAD, 'success', 'f', 'ok',
              RUN)
    assert recorded[0][0] == 'PATCH'
    assert 'conclusion=success' in recorded[0][2]


def test_no_existing_check_posts_a_new_one_carrying_head_sha(tmp):
    del tmp
    m = _mod()
    recorded = []
    m.publish(_publish_read([], recorded), 'o/r', HEAD, 'success', 'f', 'ok',
              RUN)
    assert len(recorded) == 1
    method, target, argv = recorded[0]
    assert method == 'POST', method
    assert target.endswith('/check-runs'), target
    assert f'head_sha={HEAD}' in argv
    assert 'conclusion=success' in argv


def test_re_running_updates_the_same_check_rather_than_accumulating(tmp):
    del tmp
    m = _mod()
    recorded = []
    read = _publish_read([99], recorded)
    m.publish(read, 'o/r', HEAD, 'failure', 't', 's', RUN)
    m.publish(read, 'o/r', HEAD, 'failure', 't', 's', RUN)
    assert [r[0] for r in recorded] == ['PATCH', 'PATCH']
    assert all(r[1].endswith('/check-runs/99') for r in recorded)


def test_publish_filters_on_name_external_id_and_app_slug(tmp):
    del tmp
    m = _mod()
    recorded = []
    captured = []

    def read(argv):
        target = _url(argv)
        if '/check-runs?' in target and _method(argv) == 'GET':
            captured.append(' '.join(argv))
            return ''
        recorded.append((_method(argv), target))
        return '{}'
    m.publish(read, 'o/r', HEAD, 'success', 't', 's', RUN)
    assert captured, 'the existing-check lookup was never made'
    for fragment in ('gate freshness', 'daedalus-gate-freshness/v1',
                     'github-actions', 'filter=all', '--paginate'):
        assert fragment in captured[0], (fragment, captured[0])


# ---- the per-head flow ----

def _flow_read(m, gates, current, published, heads_missing=(), moved=None):
    """The whole per-head flow. `moved` makes the head change after the first
    pull-request read, so the pre-write revalidation sees a different sha."""
    state = {'reads': 0}

    def read(argv):
        target = _url(argv)
        method = _method(argv)
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
                return _encode(_compare_ok('f' * 40, 'diverged'))
            return _encode(_compare_ok(gate, 'ahead'))
        if '/check-runs?' in target and method == 'GET':
            return ''
        if method in ('POST', 'PATCH'):
            published.append((target, method))
            return '{}'
        raise AssertionError(f'{method} {target}')
    return read


def test_a_fresh_head_publishes_a_green_check(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: HEAD}, published)
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [_gate('.pylintrc', G1)], RUN)
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
                               [_gate('.pylintrc', G1)], RUN)
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
                               [_gate('.pylintrc', G1)], RUN)
    assert published == [], 'published onto a superseded SHA'
    assert code != 0, 'skipping a moved head must be loud'
    assert verdicts == []


def test_a_head_that_moves_between_decision_and_write_is_skipped(tmp):
    """Revalidate immediately before the write.

    The only entry that reaches this is a read whose answer changes between
    the two revalidations; it discriminates the pre-write revalidation from
    the first one, which the moved-head entry above already covers.
    """
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {}, {7: HEAD}, published, moved='m' * 40)
    code, _ = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                        [_gate('.pylintrc', G1)], RUN)
    assert published == [], 'published onto a SHA that stopped being the head'
    assert code != 0, 'skipping a head that moved must be loud'


def test_a_pull_request_not_based_on_main_is_skipped(tmp):
    del tmp
    m = _mod()
    assert m.select_heads([_pr(7, base='release/1.0')]) == []


def test_an_empty_open_pull_request_list_publishes_nothing(tmp):
    del tmp
    m = _mod()
    code, verdicts = m.process(_flow_read(m, {}, {}, []), 'o/r', [], [], RUN)
    assert code == 0
    assert verdicts == []


def test_a_head_whose_current_read_fails_is_not_published(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {}, published)  # 7 unreadable
    code, _ = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                        [_gate('.pylintrc', G1)], RUN)
    assert published == []
    assert code != 0


def test_dry_run_computes_every_verdict_and_publishes_nothing(tmp):
    del tmp
    m = _mod()
    published = []
    read = _flow_read(m, {'.pylintrc': [G1]}, {7: HEAD}, published)
    code, verdicts = m.process(read, 'o/r', m.select_heads([_pr(7)]),
                               [_gate('.pylintrc', G1)], RUN, dry_run=True)
    assert code == 0
    assert len(verdicts) == 1
    assert verdicts[0].conclusion == 'success'
    assert published == [], 'a dry run wrote a check run'


def test_required_calls_accounts_for_both_compares_per_gate(tmp):
    del tmp
    m = _mod()
    assert m.required_calls(3, 4) == len(m.GATE_PATTERNS) + 3 * (4 + 2 * 4)


def test_over_the_head_bound_refuses_and_publishes_nothing(tmp):
    del tmp
    m = _mod()
    published = []
    heads = m.select_heads([_pr(i) for i in range(1, 6)])
    read = _flow_read(m, {'.pylintrc': [G1]}, {}, published)
    code, _ = m.process(read, 'o/r', heads, [_gate('.pylintrc', G1)], RUN,
                        call_budget=5)
    assert code != 0
    assert published == [], 'a truncated run must publish nothing'


def test_within_the_bound_publishes_every_head(tmp):
    del tmp
    m = _mod()
    published = []
    heads = m.select_heads([_pr(i) for i in range(1, 4)])
    read = _flow_read(m, {'.pylintrc': [G1]},
                      {i: HEAD for i in (1, 2, 3)}, published)
    code, verdicts = m.process(read, 'o/r', heads, [_gate('.pylintrc', G1)],
                               RUN)
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


def test_workflow_header_states_the_bound_and_its_arithmetic(tmp):
    del tmp
    text = _workflow_text().lower()
    assert 'timeout-minutes' in text
    assert 'compare' in text
    assert re.search(r'open pull request', text), text[:400]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gatefresh_')


if __name__ == '__main__':
    raise SystemExit(main())
