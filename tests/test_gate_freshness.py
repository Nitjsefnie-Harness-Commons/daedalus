#!/usr/bin/env python3
"""Contract for the gate-freshness decision core (issue 1008).

Every decision function is pure and driven here through an injected fake for
the ``read`` seam, so a verdict is tested without a network. The two halves
that must never be confused -- "the branch is stale" and "the answer could not
be computed" -- get separate entries, and the publish path is exercised
against a head already carrying a published success, because overwriting that
green is the whole defect. The orchestration (``process``/``main``), the run
bound, and the workflow shape live in ``test_gate_freshness_run.py``.
"""
import json
import os
import re
import subprocess
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


def _encode(value):
    return value if isinstance(value, str) else json.dumps(value)


def _compare_ok(mb, status='ahead'):
    return {'status': status, 'merge_base_commit': {'sha': mb}}


def _head(number=7, sha=HEAD, owner='octo', ref='feature', base='main'):
    return {'number': number, 'sha': sha, 'owner': owner, 'ref': ref,
            'base': base}


def _gate(path, sha):
    return (path, sha)


# ---- gate-defining path set ----

def test_the_set_is_exactly_the_documented_patterns(tmp):
    """Every pattern matches its own shape, and a nested match is included."""
    del tmp
    m = _mod()
    assert tuple(m.GATE_PATTERNS) == (
        '.github/workflows/**', 'scripts/ci/**', 'scripts/check_versions.py',
        '.gitleaks.toml', 'pyrightconfig.json', 'pyrightconfig.tests.json',
        '.pylintrc', 'setup.cfg', 'eslint.config.js', 'pyproject.toml',
        'run_tests.py', 'requirements-dev.txt', 'requirements-test.txt')
    for path in m.GATE_PATTERNS:
        assert m.is_gate_defining(path), path
    assert m.is_gate_defining('scripts/ci/deep/x.py')
    assert m.is_gate_defining('.github/workflows/a/b/c.yml')


def test_near_miss_spellings_are_not_gate_defining(tmp):
    """A change to a test, or a similarly named neighbour, is not a gate."""
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
        'setup.cfg.bak', 'run_tests.pyc', 'scripts/check_version.py',
        '.gitleaks.toml.bak',
    )
    for path in negatives:
        assert not m.is_gate_defining(path), path


def test_the_carried_by_the_branch_exclusion_is_declared_and_small(tmp):
    """The baselines the branch carries itself are excluded, and the rule is
    narrow enough to notice if it is widened by a new entry without a
    reason."""
    del tmp
    m = _mod()
    assert m.CARRIED_BY_THE_BRANCH == ('.github/ci-thresholds.json',)
    # Excluded from the set: a branch measures its own tree, so it is safe.
    assert not m.is_gate_defining('.github/ci-thresholds.json')
    assert '.github/ci-thresholds.json' not in m.GATE_PATTERNS
    doc = m.__doc__ or ''
    assert 'carries itself' in doc or 'carry' in doc


def test_the_docstring_justifies_every_pattern(tmp):
    """Each entry is justified in the module docstring, not merely listed.

    The pattern's OWN spelling must appear, so a `/**` directory pattern is
    held too: taking a leaf of `.github/workflows/**` yields the empty string,
    and `'' in doc` is always true.
    """
    del tmp
    m = _mod()
    doc = m.__doc__ or ''
    for pattern in m.GATE_PATTERNS:
        assert pattern in doc, (pattern, 'its own spelling is not justified')


# ---- derive the set from the workflows (the guard) ----

_EXECUTED = re.compile(
    r'python3?(?:\.\d+)? +(?:-[A-Za-z]+ +)*([\w./-]+\.py)\b'
    r'|(?<![\w.-])([./][\w./-]+\.(?:sh|py))\b')
_FLAG_VALUE = re.compile(
    r'--(?:rcfile|config|requirement|project|input|file)[= ]+([^\s\'"]+)'
    r'|(?<!\w)-r[= ]+([^\s\'"]+)')


def _git_tracked(base):
    """The tracked files of the git repository at `base`."""
    listed = subprocess.run(
        ['git', '-C', str(base), 'ls-files', '-z'],
        capture_output=True, check=True, timeout=30)
    return {os.fsdecode(name) for name in listed.stdout.split(b'\0') if name}


def _normalise(candidate, base, tracked):
    """A TRACKED repo-relative path for `candidate`, or None.

    A candidate qualifies only when it names a file that exists on disk (the
    shape check) AND is in the injected `tracked` set (the real meaning of
    tracked). is_file() alone would admit a file a job generates at run time.
    """
    parts = [p for p in candidate.split('/')
             if p and p not in ('.', '..', 'head')]
    parts = [p for p in parts if not p.startswith('$')]
    rel = '/'.join(parts)
    if rel and (base / rel).is_file() and rel in tracked:
        return rel
    return None


def _workflow_gate_files(directory, base=ROOT, tracked=None):
    """Every TRACKED file a workflow invokes or passes to a tool.

    Three forms: a `.py` a step executes, an executable-path step
    (`./scripts/x.sh`), and a file passed by a config-style flag. Candidates
    are kept only when they name a TRACKED file (see `_normalise`), which is
    what excludes files a job generates at run time and shell fragments like
    `-r 'arrays'` that a jq expression contributes. `tracked` defaults to the
    real `git ls-files` set and is injected for a fabricated tree.
    """
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
    """The set is derived from the workflows, not trusted from a reading.

    Every TRACKED file a workflow invokes or passes to a tool must be gate-
    defining, unless it is a declared carried-by-the-branch file. A gate file
    added to a workflow later -- including one outside scripts/ci/ -- fails
    here rather than going silently unlisted.
    """
    del tmp
    m = _mod()
    used = _workflow_gate_files(ROOT / '.github' / 'workflows')
    assert used, 'the workflows name no gate file'
    unaccounted = used - set(m.CARRIED_BY_THE_BRANCH)
    for path in sorted(unaccounted):
        assert m.is_gate_defining(path), path


def test_the_guard_accounts_for_the_files_the_set_now_carries(tmp):
    """The two files a gate step actually uses outside scripts/ci/ are
    covered."""
    del tmp
    m = _mod()
    used = _workflow_gate_files(ROOT / '.github' / 'workflows')
    assert 'scripts/check_versions.py' in used
    assert '.gitleaks.toml' in used
    for path in ('scripts/check_versions.py', '.gitleaks.toml'):
        assert m.is_gate_defining(path), path


def test_a_planted_gate_script_outside_scripts_ci_is_caught(tmp):
    """The derivation discriminates where the literal list did not.

    A new gate script run by a workflow from OUTSIDE scripts/ci/ -- the shape
    scripts/check_versions.py already ships -- is a real tracked file, so the
    guard requires it to be gate-defining. A literal list of nine config names
    would not have seen this file.
    """
    m = _mod()
    base = Path(tmp) / 'repo'
    (base / 'scripts').mkdir(parents=True)
    (base / 'scripts' / 'scan_secrets_extra.py').write_text(
        '# gate\n', encoding='utf-8')
    workflows = base / '.github' / 'workflows'
    workflows.mkdir(parents=True)
    (workflows / 'probe.yml').write_text(
        'jobs:\n  gate:\n    steps:\n'
        '      - run: python3 scripts/scan_secrets_extra.py\n',
        encoding='utf-8')
    used = _workflow_gate_files(
        workflows, base=base, tracked={'scripts/scan_secrets_extra.py'})
    assert 'scripts/scan_secrets_extra.py' in used
    # The guard's assertion, run against the planted tree: the file is used but
    # not gate-defining, so the guard would fail here -- which is the point.
    unaccounted = used - set(m.CARRIED_BY_THE_BRANCH)
    not_gate = [p for p in unaccounted if not m.is_gate_defining(p)]
    assert not_gate == ['scripts/scan_secrets_extra.py'], not_gate


def test_a_gate_invoked_by_path_outside_scripts_ci_is_caught(tmp):
    """An executable-path step (`./scripts/x.sh`) is seen, not just `.py`.

    The REACH LIMIT is a `python3 -m module` or a bare-tool invocation; a step
    that runs a tracked script BY PATH is reachable and must be accounted for.
    """
    base = Path(tmp) / 'repo'
    (base / 'scripts').mkdir(parents=True)
    (base / 'scripts' / 'version_gate.sh').write_text('#!/bin/sh\n',
                                                      encoding='utf-8')
    workflows = base / '.github' / 'workflows'
    workflows.mkdir(parents=True)
    (workflows / 'probe.yml').write_text(
        'jobs:\n  gate:\n    steps:\n'
        '      - run: ./scripts/version_gate.sh\n', encoding='utf-8')
    used = _workflow_gate_files(
        workflows, base=base, tracked={'scripts/version_gate.sh'})
    assert 'scripts/version_gate.sh' in used, used


def test_flagged_and_versioned_python_invocations_are_reached(tmp):
    """`python3 -u PATH` and `python3.13 PATH` are seen, not just `python3`.

    The REACH LIMIT names only the five discovery-read config files and
    `python3 -m module`; a flagged or versioned interpreter is a path the
    matcher must reach, or the code sees fewer real invocations than the
    sentence admits -- the same overclaim that licensed the Critical.
    """
    for spelling in ('python3 -u scripts/lint_gate.py',
                     'python3.13 scripts/lint_gate.py',
                     'python scripts/lint_gate.py'):
        base = Path(tmp) / spelling.replace(' ', '_').replace('.', '_')
        (base / 'scripts').mkdir(parents=True)
        (base / 'scripts' / 'lint_gate.py').write_text('# gate\n',
                                                       encoding='utf-8')
        workflows = base / '.github' / 'workflows'
        workflows.mkdir(parents=True)
        (workflows / 'probe.yml').write_text(
            f'jobs:\n  gate:\n    steps:\n      - run: {spelling}\n',
            encoding='utf-8')
        used = _workflow_gate_files(
            workflows, base=base, tracked={'scripts/lint_gate.py'})
        assert 'scripts/lint_gate.py' in used, (spelling, used)


def test_a_generated_untracked_requirements_file_is_not_required(tmp):
    """A file a job generates at run time is not a TRACKED gate file.

    `is_file()` alone would admit extras-requirements.txt the moment it exists
    on disk, which it does on any machine that has run audit.yml. The tracked
    SET is what excludes it, so this entry plants the file, injects a tracked
    set WITHOUT it, and shows the guard still does not require it.
    """
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


def test_an_unreadable_compare_is_red_and_states_the_observation(tmp):
    """A 404 is not evidence of access denial. The message names the request
    and its outcome and gives the remedy; it does not diagnose a cause."""
    del tmp
    m = _mod()

    def read(_argv):
        raise m.QueryError('HTTP 404')
    verdict = m.head_verdict(read, 'o/r', _head(), [_gate('.pylintrc', G1)])
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'
    text = verdict.title + ' ' + verdict.summary
    # The observation, positively: the compare request and its failure appear.
    assert 'compare' in text
    assert f'compare/{G1}...{HEAD}' in text
    assert 'HTTP 404' in text
    assert 'rebase' in text.lower()
    assert 'not evidence of access denial' in text


# ---- gate commit enumeration ----

def _commits_read(m, commits=None, raise_for=None, live_shaped=False,
                  asked=None):
    """A `commits?path=` reader. `live_shaped` mimics the real endpoint:
    a path carrying a glob answers [] (GitHub does not expand **), a bare
    path or exact file answers a commit. `commits` overrides per-path."""
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
    del tmp
    m = _mod()
    asked = []
    gates = m.enumerate_gates(_commits_read(m, asked=asked), 'o/r')
    assert len(gates) == len(m.GATE_PATTERNS)
    assert len(asked) == len(m.GATE_PATTERNS)


def test_the_commits_endpoint_receives_a_path_it_understands(tmp):
    """The SHAPE the endpoint accepts, not a well-formed string.

    GitHub's `commits?path=` is a directory-prefix filter and does NOT expand a
    `**` glob. A lexical "is the request well-formed" check would have passed
    while the string meant nothing to the API. This pins that no glob reaches
    the endpoint, that the two directory patterns arrive as their BARE
    directory, and that an exact pattern is sent unchanged.
    """
    del tmp
    m = _mod()
    asked = []
    m.enumerate_gates(_commits_read(m, asked=asked), 'o/r')
    assert asked, 'no gate path was looked up'
    for request in asked:
        assert '*' not in request, request
    assert '.github/workflows' in asked, asked
    assert 'scripts/ci' in asked, asked
    # an exact pattern is sent unchanged
    for exact in ('.pylintrc', 'setup.cfg', 'run_tests.py',
                  'requirements-dev.txt', 'requirements-test.txt',
                  'pyrightconfig.json', 'pyrightconfig.tests.json',
                  'eslint.config.js', 'pyproject.toml', '.gitleaks.toml',
                  'scripts/check_versions.py'):
        assert exact in asked, exact


def test_a_live_shaped_endpoint_resolves_every_pattern(tmp):
    """Against a fake that answers [] for a glob, the run still resolves all
    thirteen -- because the request never carries a glob. Reverting the
    translation makes the two directory patterns ask as `**`, the fake answers
    [], and the run refuses: the motivating case is caught here."""
    del tmp
    m = _mod()
    gates = m.enumerate_gates(_commits_read(m, live_shaped=True), 'o/r')
    assert len(gates) == len(m.GATE_PATTERNS), gates


def test_a_gate_path_with_no_commits_is_a_global_failure(tmp):
    """An empty answer is anomalous, not "no commits": refuse loudly.

    Every pattern in the set has commits on main. A silent skip here would
    under-count the set and turn a stale head green -- the exact defect this
    module exists to prevent -- so an empty answer is a GLOBAL failure that
    names the pattern.
    """
    del tmp
    m = _mod()
    read = _commits_read(m, commits={'eslint.config.js': []})
    try:
        m.enumerate_gates(read, 'o/r')
    except m.QueryError as error:
        assert 'eslint.config.js' in str(error), str(error)
        assert 'no commit' in str(error), str(error)
        return
    raise AssertionError('an empty gate-path answer was not refused')


def test_enumeration_unreadable_is_a_global_failure(tmp):
    del tmp
    m = _mod()

    def read(_argv):
        raise m.QueryError('HTTP 500')
    try:
        m.enumerate_gates(read, 'o/r')
    except m.QueryError as error:
        assert 'gate commit' in str(error), str(error)
        return
    raise AssertionError('an unreadable gate lookup was not refused')


def test_a_non_list_enumeration_payload_is_a_global_failure(tmp):
    """A payload that decodes to a non-list is not 'no commits'."""
    del tmp
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
    """A head that lacks exactly the gate commit `missing_at`."""
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


def test_every_gate_is_compared_not_short_circuited(tmp):
    """Check every gate, never short-circuit. A fake records the gate SHAs it
    was asked about; adding `break` after the first miss would drop them."""
    del tmp
    m = _mod()
    asked = []
    gates = [_gate('a', G1), _gate('b', G2), _gate('c', 'd' * 40)]
    m.head_verdict(_multi_gate_read(G2, asked), 'o/r', _head(), gates)
    assert asked == [G1, G2, 'd' * 40], asked


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
        target = next(t for t in argv if t.startswith('repos/'))
        gate = target.split('compare/')[1].split('...')[0]
        if gate == G2:
            raise m.QueryError('HTTP 404')
        return _encode(_compare_ok(gate, 'ahead'))
    verdict = m.head_verdict(read, 'o/r', _head(), gates)
    assert verdict.conclusion == 'failure'
    assert verdict.kind == 'unreadable'


# ---- publish: a failure must overwrite a published success ----

def _publish_read(existing_ids, recorded, fail_listing=None):
    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        method = 'GET'
        for index, token in enumerate(argv):
            if token in ('-X', '--method') and index + 1 < len(argv):
                method = argv[index + 1].upper()
        if '/check-runs?' in target and method == 'GET':
            if fail_listing is not None:
                raise fail_listing('could not read the check runs')
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


def test_publish_filters_and_sends_no_cache(tmp):
    """The existing-check lookup is filtered server-side and no-cached."""
    del tmp
    m = _mod()
    recorded = []
    captured = []

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        method = 'GET'
        for index, token in enumerate(argv):
            if token in ('-X', '--method') and index + 1 < len(argv):
                method = argv[index + 1].upper()
        if '/check-runs?' in target and method == 'GET':
            captured.append(' '.join(argv))
            return ''
        recorded.append((method, target))
        return '{}'
    m.publish(read, 'o/r', HEAD, 'success', 't', 's', RUN)
    assert captured, 'the existing-check lookup was never made'
    for fragment in ('gate freshness', 'daedalus-gate-freshness/v1',
                     'github-actions', 'filter=all', '--paginate',
                     'Cache-Control: no-cache'):
        assert fragment in captured[0], (fragment, captured[0])


def test_a_per_head_listing_failure_raises_so_the_run_can_skip(tmp):
    """An unreadable existing-check listing is a per-head failure, not a
    crash: publish raises and process() catches and skips that head."""
    del tmp
    m = _mod()
    try:
        m.publish(_publish_read([], [], fail_listing=m.QueryError), 'o/r',
                  HEAD, 'failure', 't', 's', RUN)
    except m.QueryError:
        return
    raise AssertionError('an unreadable listing did not raise QueryError')


# ---- select_heads ----

def test_a_pull_request_not_based_on_main_is_skipped(tmp):
    del tmp
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
