#!/usr/bin/env python3
"""Trigger and gate-condition contracts for the workflows in .github/."""
import sys
import fnmatch
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import _tokenize, _unwrap, sole_context_path  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_if_expression  # noqa: E402
from _wfjobs import jobs_mapping  # noqa: E402
from _workflows import (  # noqa: E402
    _event_option_keys, _workflow_path_filters, _workflow_triggers)
from _yamlread import step_scalar, step_scalars  # noqa: E402
from _yamlsteps import step_mappings, workflow_mapping  # noqa: E402


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
        # nested a level deeper filters something else entirely.
        assert 'branches' in _event_option_keys(
            triggers['push'], path.name), (
            f'{path.name} runs on every branch push AND on pull_request, so a '
            f'pull request from this repository gates its head SHA twice')
    assert checked, 'no workflow declares both triggers; has one been renamed?'


def test_double_gate_scan_reads_yaml_and_event_owned_options(tmp):
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    (workflows / 'control.yml').write_text(
        'name: control\n\non:\n'
        '  push:\n    branches: [main]\n'
        '  pull_request:\n', encoding='utf-8')
    cases = (
        ('branchless.yaml',
         'name: branchless\n\non:\n  push:\n  pull_request:\n'),
        ('nested.yaml',
         'name: nested\n\non:\n  push:\n'
         '    paths:\n      - src/**\n'
         '    types:\n'
         '      branches: [main]\n'
         '  pull_request:\n'))
    for name, content in cases:
        (workflows / name).write_text(content, encoding='utf-8')
        try:
            _assert_no_workflow_gates_one_commit_twice(workflows)
        except AssertionError as failure:
            assert name in str(failure), failure
        else:
            raise AssertionError(f'{name} was accepted')
        (workflows / name).unlink()


def test_no_workflow_gates_one_commit_twice(tmp):
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
    del tmp
    _assert_workflow_trigger_filters_match(ROOT / '.github' / 'workflows')


def test_workflow_reader_accepts_string_controls_and_a_leading_bom(tmp):
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
    triggers = _workflow_triggers(bom, 'bom.yml')
    assert _workflow_path_filters(triggers['push'], 'bom.yml') == {
        'paths-ignore': ['docs\ufeff.md']}


def test_workflow_trigger_gate_rejects_quote_collisions_and_accepts_comments(
        tmp):
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
    # Each workflow's own data file; codeql scans no Python to ignore.
    ignored_by = {'tests.yml': budget, 'codeql.yml': None}
    for name, data_file in ignored_by.items():
        path = workflows / name
        assert _workflow_runs_for_paths(path, 'push', [threshold]) is False
        assert _workflow_runs_for_paths(path, 'push', [threshold, source])
        assert _workflow_runs_for_paths(path, 'push', [source])
        assert _workflow_runs_for_paths(path, 'pull_request', [threshold])
        triggers = _workflow_triggers(path.read_text(encoding='utf-8'), name)
        # Membership, not equality: other ignores don't break the requirement.
        assert threshold in _workflow_path_filters(
            triggers['push'], name).get('paths-ignore', []), triggers['push']
        assert not _workflow_path_filters(triggers['pull_request'], name)

        # The run set: a data file alone never wakes these gates on push.
        for target in (threshold, data_file):
            if target is None:
                continue
            assert _workflow_runs_for_paths(path, 'push', [target]) is False
            assert _workflow_runs_for_paths(path, 'push', [target, source])
            assert _workflow_runs_for_paths(path, 'pull_request', [target])

    audit = workflows / 'audit.yml'
    assert _workflow_runs_for_paths(audit, 'push', [threshold])


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


def test_duplicate_event_options_are_refused(tmp):
    del tmp
    for option, first, second in (
            ('branches', '[main]', '[release]'),
            ('paths-ignore', '[docs/**]', '[.github/**]')):
        content = ('name: control\n\non:\n  push:\n'
                   f'    {option}: {first}\n    {option}: {second}\n')
        try:
            triggers = _workflow_triggers(content, 'control.yml')
            _workflow_path_filters(triggers['push'], 'control.yml')
        except AssertionError as failure:
            assert 'duplicate event option' in str(failure), failure
        else:
            raise AssertionError(f'a duplicate {option} was accepted')


def test_repeated_key_below_the_option_indent_is_not_an_option(tmp):
    del tmp
    content = ('name: control\n\non:\n  push:\n'
               '    paths:\n      - src/**\n'
               '    types:\n'
               '      branches: [main]\n      branches: [release]\n')
    triggers = _workflow_triggers(content, 'control.yml')
    assert _workflow_path_filters(triggers['push'], 'control.yml') == {
        'paths': ['src/**']}, triggers['push']


def test_coverage_gates_run_only_on_a_successful_measurement(tmp):
    """Every shipped gate holds a row here or in a sibling table; a gate
    added later needs its own row — nothing enforces that."""
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
    del tmp
    tests_yml = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    actual = step_scalar(tests_yml, 'actionlint', 'zizmor', 'if')
    assert actual == (
        "${{ !cancelled() && steps.install_zizmor.outcome == 'success' }}"), (
            f'actionlint/zizmor: {actual!r}')


def test_coverage_matrix_uploads_are_split_by_leg(tmp):
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


_OUTPUT_REDIRECT = '>> "$GITHUB_OUTPUT"'
_OUTPUT_NAME = r'[a-z_][a-z0-9_]*'


def _run_output_names(script):
    """The output names one run block writes to `$GITHUB_OUTPUT`."""
    names, group = [], None
    for raw in script.splitlines():
        line = raw.strip()
        if line.startswith('#'):
            continue
        if group is not None and line.startswith('}'):
            if _OUTPUT_REDIRECT in line:
                names.extend(group)
            group = None
        elif line == '{':
            group = []
        elif _OUTPUT_REDIRECT in line:
            assert not line.startswith('}'), f'stray group close: {line!r}'
            written = re.search(rf"echo (['\"])(?P<name>{_OUTPUT_NAME})=",
                                line)
            assert written, f'output redirect not admitted: {line!r}'
            names.append(written['name'])
        elif group is not None:
            written = re.search(rf"printf '({_OUTPUT_NAME})=", line)
            if written:
                names.append(written[1])
    assert group is None, 'unclosed `{` output group'
    return list(dict.fromkeys(names))


def _condition_output_reads(expression):
    """The (handle, field) steps-outputs lookups one condition makes."""
    tokens = _tokenize(_unwrap(expression))
    reads, index = [], 0
    while index < len(tokens):
        cursor = index + 1
        while (cursor + 1 < len(tokens)
               and tokens[cursor].kind == '.'
               and tokens[cursor + 1].kind == 'IDENT'):
            cursor += 2
        path = [token.value for token in tokens[index:cursor:2]]
        if len(path) == 4 and path[0] == 'steps' and path[2] == 'outputs':
            reads.append((path[1], path[3]))
        index = max(cursor, index + 1)
    return reads


def test_shipped_step_ids_are_the_handles_the_workflow_uses(tmp):
    """The handles the shipped workflows declare, at the declaring step.
    tests.yml's and actionlint's rows rely on disclosure alone."""
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
    census = ('artifact', 'missing', 'pr')
    declared = step_scalars(comment_yml, 'comment', 'id')
    assert declared is not None, 'job comment declares no id values'
    assert tuple(sorted(declared)) == census
    rows = (
        ('Check for the comment artifact', 'artifact'),
        ('Resolve the target pull request from the event', 'pr'),
        ('Mark missing patch coverage', 'missing'))
    assert tuple(sorted(row[-1] for row in rows)) == census
    for step, expected in rows:
        actual = step_scalar(comment_yml, 'comment', step, 'id')
        assert actual == expected, f'comment/{step}: {actual!r}'
    steps = step_mappings(comment_yml, 'comment')
    assert steps is not None, 'job comment declares no steps'
    produced = {step['id']: _run_output_names(step.get('run') or '')
                for step in steps if step.get('id')}
    job_condition = _job_if_expression(comment_yml, 'comment')
    reads = ([] if job_condition is None
             else _condition_output_reads(job_condition))
    reads += [read for step in steps if step.get('if')
              for read in _condition_output_reads(step['if'])]
    unproduced = [(handle, field) for handle, field in reads
                  if field not in produced.get(handle, ())]
    assert not unproduced, (
        f'conditions read outputs no declared handle writes: {unproduced} '
        f'(produced: {sorted(produced.items())})')
    assert _run_output_names(
        "# echo 'phantom=1' >> \"$GITHUB_OUTPUT\"\n"
        "echo 'present=true' >> \"$GITHUB_OUTPUT\"\n") == ['present']


def _without_call_spacing(expression):
    """Quote-blind (`'a, b'` reads as `'a,b'`); safe here: neither quoted
    operand holds any of the three."""
    return re.sub(r'\s*([(),])\s*', r'\1', expression)


def test_scorecard_publishes_only_the_upstream_default_branch(tmp):
    """One score, from one ref, published only where it means something.
    A structural pin, not an evaluation: the expression reader refuses
    `format(...)` outright, and each limb of any other shape fails rather
    than passing unread."""
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


def test_tests_concurrency_scopes_runs_per_commit_and_per_pull(tmp):
    """A landed main SHA's tests verdict must complete: push and dispatch
    runs group by commit SHA, so neither a newer push nor a re-run of an
    older SHA cancels another commit's in-flight run (issue 1547), while a
    pull request keeps one group per number so a superseded head push is
    still cancelled. The `queue` key is not expressible here; the vendored
    actionlint build rejects it."""
    del tmp
    tests = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    concurrency = workflow_mapping(tests).get('concurrency')
    assert concurrency == {
        'group':
        'tests-${{ github.event.pull_request.number || github.sha }}',
        'cancel-in-progress': 'true',
    }, f'tests concurrency: {concurrency!r}'


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='workflowtriggers_')


if __name__ == '__main__':
    raise SystemExit(main())
