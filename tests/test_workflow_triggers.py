#!/usr/bin/env python3
"""Trigger and gate-condition contracts for the workflows in .github/.

The trigger blocks read through the bounded reader, and the shipped jobs'
own gate conditions and step handles are pinned beside them.
"""
import sys
import fnmatch
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import sole_context_path  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_if_expression  # noqa: E402
from _wfjobs import jobs_mapping  # noqa: E402
from _workflows import (  # noqa: E402
    _event_option_keys, _workflow_path_filters, _workflow_triggers)
from _yamlread import step_scalar  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402


def _assert_no_workflow_gates_one_commit_twice(workflows):
    """Reject every push/pull_request pair without an event-level branch."""
    checked = []
    for path in sorted(workflows.iterdir()):
        if path.suffix not in ('.yml', '.yaml'):
            continue
        triggers = _workflow_triggers(
            path.read_text(encoding='utf-8'), path.name)
        if 'pull_request' not in triggers or 'push' not in triggers:
            continue
        checked.append(path.name)
        # The event's OWN keys, not any line in its block: a `branches:`
        # nested a level deeper filters something else, and reading it as
        # the push filter passes a trigger that carries none.
        assert 'branches' in _event_option_keys(
            triggers['push'], path.name), (
            f'{path.name} runs on every branch push AND on pull_request, so a '
            f'pull request from this repository gates its head SHA twice')
    assert checked, 'no workflow declares both triggers; has one been renamed?'


def test_double_gate_scan_reads_yaml_and_event_owned_options(tmp):
    """The double-gate helper must inspect both suffixes and own keys."""
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    control = ('name: control\n\non:\n'
               '  push:\n    branches: [main]\n'
               '  pull_request:\n')
    (workflows / 'control.yml').write_text(control, encoding='utf-8')
    branchless = ('name: branchless\n\non:\n'
                  '  push:\n  pull_request:\n')
    branchless_path = workflows / 'branchless.yaml'
    branchless_path.write_text(branchless, encoding='utf-8')
    try:
        _assert_no_workflow_gates_one_commit_twice(workflows)
    except AssertionError as failure:
        assert 'branchless.yaml' in str(failure), failure
    else:
        raise AssertionError('branchless .yaml workflow was accepted')

    branchless_path.unlink()
    nested = ('name: nested\n\non:\n'
              '  push:\n'
              '    paths:\n'
              '      - src/**\n'
              '    types:\n'
              '      branches: [main]\n'
              '  pull_request:\n')
    nested_path = workflows / 'nested.yaml'
    nested_path.write_text(nested, encoding='utf-8')
    try:
        _assert_no_workflow_gates_one_commit_twice(workflows)
    except AssertionError as failure:
        assert 'nested.yaml' in str(failure), failure
    else:
        raise AssertionError('nested branches key was treated as an option')


def test_no_workflow_gates_one_commit_twice(tmp):
    """A pull request's head SHA gets one run per workflow, not two.

    A branch push and its pull request fire `push` and `pull_request`
    against the same SHA, so every workflow ran twice per commit and the
    runner pool saturated. The fix is the `branches:` filter on `push`
    pinned here; a branch with no pull request open gets no run — the trade.
    """
    del tmp
    _assert_no_workflow_gates_one_commit_twice(
        ROOT / '.github' / 'workflows')


def _assert_workflow_trigger_filters_match(workflows):
    """Assert symmetric path filters for every paired workflow in a tree."""
    checked = []
    for path in sorted(workflows.iterdir()):
        if path.suffix not in ('.yml', '.yaml'):
            continue
        triggers = _workflow_triggers(
            path.read_text(encoding='utf-8'), path.name)
        if 'pull_request' not in triggers or 'push' not in triggers:
            continue
        checked.append(path.name)
        if path.name in ('tests.yml', 'codeql.yml'):
            assert not _workflow_path_filters(
                triggers['pull_request'], path.name), (
                    f'{path.name} pull_request trigger must remain unfiltered')
            continue
        filters = [_workflow_path_filters(triggers[event], path.name)
                   for event in ('push', 'pull_request')]
        assert filters[0] == filters[1], (
            f'{path.name} filters push and pull_request differently: '
            f'{filters[0]!r} != {filters[1]!r}')
    assert checked, 'no workflow declares both triggers; has one been renamed?'


def test_workflow_trigger_filters_match_between_push_and_pull_request(tmp):
    """Push and pull_request must make the same path-filtering choice.

    A push-only filter lets a documentation-only commit skip the gates on
    main while the identical pull request runs them; this test owns only
    the symmetry property, and release safety is pinned separately.
    """
    del tmp
    _assert_workflow_trigger_filters_match(ROOT / '.github' / 'workflows')


def test_workflow_reader_accepts_string_controls_and_a_leading_bom(tmp):
    """Positive scalar controls stay green through the policy helper."""
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    spelling = "[main, 'release', .gitignore, release-candidate, '**/*.md']"
    content = ('name: control\n\non:\n  push:\n    branches: [main]\n'
               f'    paths-ignore: {spelling}\n'
               f'  pull_request:\n    paths-ignore: {spelling}\n')
    (workflows / 'controls.yml').write_text(content, encoding='utf-8')
    bom = '\ufeff' + content.replace(spelling, '[docs\ufeff.md]')
    (workflows / 'bom.yml').write_text(bom, encoding='utf-8')
    _assert_workflow_trigger_filters_match(workflows)
    expected = {'paths-ignore': [
        'main', 'release', '.gitignore', 'release-candidate', '**/*.md']}
    triggers = _workflow_triggers(content, 'controls.yml')
    assert _workflow_path_filters(triggers['push'], 'controls.yml') == expected
    assert _workflow_path_filters(
        triggers['pull_request'], 'controls.yml') == expected
    triggers = _workflow_triggers(bom, 'bom.yml')
    assert _workflow_path_filters(triggers['push'], 'bom.yml') == {
        'paths-ignore': ['docs\ufeff.md']}


def test_workflow_trigger_gate_rejects_quote_collisions_and_accepts_comments(
        tmp):
    """The gate refuses unequal quote spellings and accepts equal comments."""
    cases = (
        ('flow-quote-collision',
         "    paths-ignore: [don't.md, isn't.md]\n",
         '    paths-ignore: ["don\'t.md, isn\'t.md"]\n', False),
        ('block-quote-collision',
         "    paths-ignore:\n      - don't.md  # docs\n",
         '    paths-ignore:\n      - "don\'t.md  # docs"\n', False),
        ('flow-trailing-comment',
         "    paths-ignore: ['**/*.md', 'LICENSE', '.gitignore']  # "
         "docs and metadata\n",
         "    paths-ignore: ['**/*.md', 'LICENSE', '.gitignore']  # "
         "docs and metadata\n",
         True),
    )
    for name, push_value, pull_value, accepted in cases:
        workflows = Path(tmp) / name
        workflows.mkdir()
        content = ('name: control\n\non:\n'
                   f'  push:\n{push_value}'
                   f'  pull_request:\n{pull_value}')
        (workflows / 'control.yml').write_text(content, encoding='utf-8')
        if accepted:
            _assert_workflow_trigger_filters_match(workflows)
            continue
        try:
            _assert_workflow_trigger_filters_match(workflows)
        except AssertionError as failure:
            assert 'control.yml' in str(failure), failure
        else:
            raise AssertionError(f'{name}: unequal filters were accepted')


def _workflow_runs_for_paths(path, event, changed):
    text = path.read_text(encoding='utf-8')
    triggers = _workflow_triggers(text, path.name)
    if event not in triggers:
        return False
    filters = _workflow_path_filters(triggers[event], path.name)
    ignored = filters.get('paths-ignore', [])
    return not ignored or any(
        not any(fnmatch.fnmatchcase(item, pattern) for pattern in ignored)
        for item in changed)


def test_threshold_only_push_skips_only_expensive_gates(tmp):
    del tmp
    workflows = ROOT / '.github' / 'workflows'
    threshold = '.github/ci-thresholds.json'
    budget = '.github/journey-budget.json'
    source = 'server.py'
    # Each workflow's own data file: `tests.yml` measures what its budget
    # records, and `codeql.yml` scans no Python a budget commit changed.
    ignored_by = {'tests.yml': budget, 'codeql.yml': None}
    for name, data_file in ignored_by.items():
        path = workflows / name
        assert _workflow_runs_for_paths(path, 'push', [threshold]) is False
        assert _workflow_runs_for_paths(path, 'push', [threshold, source])
        assert _workflow_runs_for_paths(path, 'push', [source])
        assert _workflow_runs_for_paths(path, 'pull_request', [threshold])
        triggers = _workflow_triggers(path.read_text(encoding='utf-8'), name)
        # A superset check, not equality: both ignore more generated data
        # files than this one, and equality would red once one is MET.
        assert threshold in _workflow_path_filters(
            triggers['push'], name).get('paths-ignore', []), triggers['push']
        assert not _workflow_path_filters(triggers['pull_request'], name)

        # The run set, not the spelling: a data file alone never wakes these
        # gates on push; unfiltered pull_request reaches them there.
        for target in (threshold, data_file):
            if target is None:
                continue
            assert _workflow_runs_for_paths(path, 'push', [target]) is False
            assert _workflow_runs_for_paths(path, 'push', [target, source])
            assert _workflow_runs_for_paths(path, 'pull_request', [target])

    audit = workflows / 'audit.yml'
    assert _workflow_runs_for_paths(audit, 'push', [threshold])
    assert _workflow_runs_for_paths(audit, 'push', [threshold, source])
    assert not _workflow_path_filters(
        _workflow_triggers(audit.read_text(encoding='utf-8'), 'audit.yml')
        ['push'], 'audit.yml')


def test_threshold_filter_mutations_change_the_run_set(tmp):
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    threshold = "      - '.github/ci-thresholds.json'\n"
    push_block = "    paths-ignore:\n" + threshold
    for label, mutated in (
            ('missing', source.replace(push_block, '', 1)),
            ('broad', source.replace(
                "      - '.github/ci-thresholds.json'",
                "      - '.github/**'", 1))):
        path = workflows / f'{label}.yml'
        path.write_text(mutated, encoding='utf-8')
        assert _workflow_runs_for_paths(
            path, 'push', ['.github/ci-thresholds.json']) \
            is (label == 'missing')
        if label == 'broad':
            assert _workflow_runs_for_paths(
                path, 'push',
                ['.github/ci-thresholds.json',
                 '.github/workflows/source.yml']) \
                is False

    audit_source = (ROOT / '.github' / 'workflows' / 'audit.yml').read_text(
        encoding='utf-8')
    audit_mutated = audit_source.replace(
        '  push:\n    # main only.',
        "  push:\n    paths-ignore:\n      - '.github/ci-thresholds.json'\n"
        '    # main only.', 1)
    audit_path = workflows / 'audit.yml'
    audit_path.write_text(audit_mutated, encoding='utf-8')
    assert _workflow_runs_for_paths(
        audit_path, 'push', ['.github/ci-thresholds.json']) is False


def test_contribution_gates_have_unfiltered_push_triggers(tmp):
    del tmp
    workflows = ROOT / '.github' / 'workflows'
    for name in ('tests.yml', 'codeql.yml', 'audit.yml'):
        path = workflows / name
        assert path.is_file(), f'named contribution gate is missing: {name}'
        triggers = _workflow_triggers(
            path.read_text(encoding='utf-8'), name)
        assert 'push' in triggers, f'{name} has no push trigger'
    audit = _workflow_triggers(
        (workflows / 'audit.yml').read_text(encoding='utf-8'), 'audit.yml')
    assert not _workflow_path_filters(audit['push'], 'audit.yml')
    for retired in ('lint.yml', 'types.yml', 'eslint.yml', 'actionlint.yml'):
        assert not (workflows / retired).exists(), (
            f'moved gate workflow still exists: {retired}')


def test_workflow_trigger_filters_accept_string_pairs_and_opposite_quotes(tmp):
    """Equal strings survive plain/quoted and opposite-quote spellings."""
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    cases = (
        ('exponent', '1e1_0', "'1e1_0'", ['1e1_0']),
        ('nan', '-.NaN', "'-.NaN'", ['-.NaN']),
        ('apostrophe', '"**/what\'s-new.md"',
         '"**/what\'s-new.md"', ["**/what's-new.md"]),
        ('double-quote', "'say \"hi\".md'", "'say \"hi\".md'",
         ['say "hi".md']),
    )
    for name, push_value, pull_value, expected in cases:
        content = ('name: control\n\non:\n'
                   f'  push:\n    paths-ignore: [{push_value}]\n'
                   f'  pull_request:\n    paths-ignore: [{pull_value}]\n')
        path = workflows / f'{name}.yml'
        path.write_text(content, encoding='utf-8')
        _assert_workflow_trigger_filters_match(workflows)
        triggers = _workflow_triggers(content, path.name)
        for event in ('push', 'pull_request'):
            assert _workflow_path_filters(
                triggers[event], path.name) == {'paths-ignore': expected}

    refusals = (
        ('surrounding-single', "['a''b']"),
        ('surrounding-double', r'["a\"b"]'),
        ('backslash', r'["a\\b"]'),
    )
    for name, value in refusals:
        refusal_dir = Path(tmp) / name
        refusal_dir.mkdir()
        content = ('name: refusal\n\non:\n'
                   f'  push:\n    paths-ignore: {value}\n'
                   f'  pull_request:\n    paths-ignore: {value}\n')
        (refusal_dir / 'control.yml').write_text(
            content, encoding='utf-8')
        try:
            _assert_workflow_trigger_filters_match(refusal_dir)
        except AssertionError as failure:
            assert 'control.yml' in str(failure), failure
        else:
            raise AssertionError(f'{name}: unsupported quote was accepted')


def test_duplicate_branches_option_is_refused(tmp):
    """A repeated `branches:` is refused rather than read last-wins."""
    del tmp
    content = ('name: control\n\non:\n  push:\n'
               '    branches: [main]\n    branches: [release]\n')
    try:
        triggers = _workflow_triggers(content, 'control.yml')
        _workflow_path_filters(triggers['push'], 'control.yml')
    except AssertionError as failure:
        assert 'duplicate event option' in str(failure), failure
    else:
        raise AssertionError('a duplicate branches option was accepted')


def test_duplicate_paths_ignore_option_is_refused(tmp):
    """A repeated `paths-ignore:` is refused rather than read last-wins."""
    del tmp
    content = ('name: control\n\non:\n  push:\n'
               '    paths-ignore: [docs/**]\n'
               '    paths-ignore: [.github/**]\n')
    try:
        triggers = _workflow_triggers(content, 'control.yml')
        _workflow_path_filters(triggers['push'], 'control.yml')
    except AssertionError as failure:
        assert 'duplicate event option' in str(failure), failure
    else:
        raise AssertionError('a duplicate paths-ignore option was accepted')


def test_repeated_key_below_the_option_indent_is_not_an_option(tmp):
    """A key below the option indent is not one of the event's options."""
    del tmp
    content = ('name: control\n\non:\n  push:\n'
               '    paths:\n      - src/**\n'
               '    types:\n'
               '      branches: [main]\n      branches: [release]\n')
    triggers = _workflow_triggers(content, 'control.yml')
    assert _workflow_path_filters(triggers['push'], 'control.yml') == {
        'paths': ['src/**']}, triggers['push']


def test_coverage_gates_run_only_on_a_successful_measurement(tmp):
    """A coverage gate runs only on a measurement that succeeded.

    This table and its four siblings hold the gates the shipped workflows
    carry; a gate added later needs its own row in one of them — nothing
    enforces that, for the reason the handle table beside it gives.
    """
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    measured = "${{ !cancelled() && steps.measure.conclusion == 'success' }}"
    expected_by_step = (
        ('Python coverage summary', measured),
        ('Python coverage gate', measured),
        ('JavaScript coverage summary', measured),
        ('JavaScript coverage gate', measured),
        ('JavaScript per-module coverage gate', measured),
        ('Upload coverage XML', measured),
        ('Work out the raise this run justifies',
         "${{ !cancelled() && steps.measure.conclusion == 'success'"
         " && github.event_name == 'push'"
         " && github.ref == 'refs/heads/main' }}"),
    )
    for step, expected in expected_by_step:
        actual = step_scalar(tests_yml, 'coverage', step, 'if')
        assert actual == expected, f'{step}: {actual!r}'


def test_the_ratchet_is_only_committed_when_it_changed(tmp):
    """The job writes its own calibration file; only a real change is one."""
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    actual = step_scalar(tests_yml, 'coverage', 'Commit the raise', 'if')
    assert actual == (
        "${{ !cancelled() && steps.ratchet.outputs.changed == 'true'"
        " && env.RATCHET_SSH_KEY != '' }}"), (
            f'coverage/Commit the raise: {actual!r}')


def test_the_audit_is_gated_on_a_successful_install(tmp):
    """The audit runs only where the tool it needs actually installed."""
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    actual = step_scalar(tests_yml, 'actionlint', 'zizmor', 'if')
    assert actual == (
        "${{ !cancelled() && steps.install_zizmor.outcome == 'success' }}"), (
            f'actionlint/zizmor: {actual!r}')


def test_coverage_matrix_uploads_are_split_by_leg(tmp):
    """Each matrix leg uploads exactly one leg's coverage data."""
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    for step, expected in (
            ('Upload Ubuntu coverage data',
             "${{ !cancelled() && steps.measure.conclusion == 'success'"
             " && matrix.os == 'ubuntu-latest' }}"),
            ('Upload Python coverage data',
             "${{ !cancelled() && steps.measure.conclusion == 'success'"
             " && matrix.os != 'ubuntu-latest' }}")):
        actual = step_scalar(tests_yml, 'coverage-matrix', step, 'if')
        assert actual == expected, f'coverage-matrix/{step}: {actual!r}'


def test_journey_budget_steps_are_gated_on_the_steps_before_them(tmp):
    """The journey job's later steps read the earlier steps' outcomes."""
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    for step, expected in (
            ('Upload the measured counts',
             "${{ !cancelled() && steps.measure.conclusion == 'success' }}"),
            ('Follow the journeys that got cheaper',
             "${{ !cancelled() && steps.check.conclusion == 'success'"
             " && github.event_name == 'push'"
             " && github.ref == 'refs/heads/main' }}"),
            ('Commit the tighten',
             "${{ !cancelled() && steps.tighten.outputs.changed == 'true'"
             " && env.RATCHET_SSH_KEY != '' }}")):
        actual = step_scalar(tests_yml, 'journey-budget', step, 'if')
        assert actual == expected, f'journey-budget/{step}: {actual!r}'


def test_shipped_step_ids_are_the_handles_the_workflow_uses(tmp):
    """The handles the shipped workflows declare, at the step declaring each.

    `actionlint` is the one nothing reads; the deleted suite held it. A
    handle added later needs its own row here — nothing enforces that.
    """
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    for job, step, expected in (
            ('changes', 'Classify the changed paths', 'classify'),
            ('actionlint', 'Install zizmor', 'install_zizmor'),
            ('actionlint', 'actionlint', 'actionlint'),
            ('coverage-matrix', 'Measure', 'measure'),
            ('coverage', 'Combine per-OS coverage data', 'measure'),
            ('coverage', 'Work out the raise this run justifies', 'ratchet'),
            ('journey-budget', 'Measure the journeys', 'measure'),
            ('journey-budget', 'Check the journeys against the budget',
             'check'),
            ('journey-budget', 'Follow the journeys that got cheaper',
             'tighten')):
        actual = step_scalar(tests_yml, job, step, 'id')
        assert actual == expected, f'{job}/{step}: {actual!r}'
    comment_yml = (ROOT / '.github' / 'workflows' / 'coverage-comment.yml'
                   ).read_text(encoding='utf-8')
    for job, step, expected in (
            ('comment', 'Check for the comment artifact', 'artifact'),
            ('comment', 'Resolve the target pull request from the event',
             'pr'),
            ('comment', 'Mark missing patch coverage', 'missing')):
        actual = step_scalar(comment_yml, job, step, 'id')
        assert actual == expected, f'{job}/{step}: {actual!r}'


def _without_call_spacing(expression):
    """Return an expression with whitespace at `(`, `,` and `)` removed.

    Quote-blind, so it strips inside a quoted argument too and `'a, b'`
    reads as `'a,b'`. Safe on the one expression this control compares:
    neither quoted operand holds any of the three.
    """
    return re.sub(r'\s*([(),])\s*', r'\1', expression)


def test_scorecard_publishes_only_the_upstream_default_branch(tmp):
    """One score, from one ref, published only where it means something.

    A structural pin, not an evaluation: the shared expression reader admits
    a call only with no arguments, so it refuses `format(...)` outright and
    the guard is never run under the contexts that would decide it. Each
    limb is pinned as the shape it decodes to, and a limb of any other shape
    fails rather than passing unread.
    """
    del tmp
    scorecard = (ROOT / '.github' / 'workflows' / 'scorecard.yml').read_text(
        encoding='utf-8')

    condition = _job_if_expression(scorecard, 'analysis')
    assert condition is not None, (
        'the analysis job has no if:, so a dispatch on another branch '
        'publishes that branch as the repository score')
    guard = ' '.join(condition.split())
    assert guard.startswith('${{') and guard.endswith('}}'), condition
    conjuncts = guard[3:-2].split('&&')
    assert len(conjuncts) == 2, f'the guard is not two conjuncts: {guard!r}'
    fork = ref = None
    for conjunct in (part.strip() for part in conjuncts):
        negated = conjunct.startswith('!')
        subject, operator, expected = conjunct.partition('==')
        operand = sole_context_path(subject[1:] if negated else subject)
        if negated and not operator and operand == (
                'github', 'event', 'repository', 'fork'):
            fork = conjunct
        elif operator == '==' and operand == ('github', 'ref'):
            ref = expected.strip()
        else:
            raise AssertionError(f'unread guard limb: {conjunct!r}')
    assert fork is not None, f'no limb negates the fork flag: {guard!r}'
    assert _without_call_spacing(ref) == _without_call_spacing(
        "format('refs/heads/{0}',"
        " github.event.repository.default_branch)"), (
        f'the ref limb compares against {ref!r}')

    jobs = jobs_mapping(scorecard)
    assert jobs is not None, 'scorecard.yml declares no jobs mapping'
    assert tuple(jobs) == ('analysis',), (
        f'scorecard also publishes from {sorted(jobs)}, '
        f'and that job carries none of the guard above')

    concurrency = workflow_mapping(scorecard).get('concurrency')
    assert concurrency == {
        'group': 'scorecard-${{ github.ref }}',
        'cancel-in-progress': 'true',
    }, f'scorecard concurrency: {concurrency!r}'


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='workflowtriggers_')


if __name__ == '__main__':
    raise SystemExit(main())
