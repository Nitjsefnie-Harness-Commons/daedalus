#!/usr/bin/env python3
"""The change classifier: which shape a tests.yml run gets.

scripts/ci/classify_changes.py decides whether a run changed only
documentation — the same set speed.yml and version.yml ignore — and so pays
for one suite leg and no coverage instead of the full matrix. These tests pin
the pattern matcher, the event-to-API mapping, the over-run fallbacks and the
two contracts that keep the module's constants honest against the workflows.
"""
import contextlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflows import (  # noqa: E402
    _workflow_path_filters, _workflow_triggers)
from _yamlread import YAMLReadError, job_mapping  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _wfgraph import (  # noqa: E402
    _actionlint_runs, _job_condition_runs,
    _job_if_expression, _job_needs, _job_output_step_ids, _job_section,
    _job_names_with_outputs, _job_step_ids, _run_aggregate, _tests_yml,
    aggregate_expected)


def _classifier():
    return _util.load(ROOT / 'scripts' / 'ci' / 'classify_changes.py',
                      'classify_mod')


def _event(name='pull_request', repository='octo/daedalus',
           sha='a' * 40, pull_request='248', before='b' * 40):
    return {'name': name, 'repository': repository, 'sha': sha,
            'pull_request': pull_request, 'before': before}


def _recorder(stdout):
    calls = []

    def run(argv):
        calls.append(argv)
        return stdout

    return calls, run


def test_markdown_pattern_matches_at_any_depth(tmp):
    del tmp
    mod = _classifier()
    assert mod.matches('**/*.md', 'README.md')
    assert mod.matches('**/*.md', 'docs/deep/nested/guide.md')
    assert not mod.matches('**/*.md', 'readme.mdx')
    assert not mod.matches('**/*.md', 'server.py')


def test_exact_patterns_match_only_the_root_file(tmp):
    del tmp
    mod = _classifier()
    assert mod.matches('LICENSE', 'LICENSE')
    assert not mod.matches('LICENSE', 'LICENSE.txt')
    assert not mod.matches('LICENSE', 'sub/LICENSE')
    assert not mod.matches('LICENSE', 'licence')
    assert mod.matches('.gitignore', '.gitignore')
    assert not mod.matches('.gitignore', 'sub/.gitignore')
    assert mod.matches('docs]', 'docs]')


def test_unimplemented_metacharacters_fail_closed_at_any_position(tmp):
    # `docs?`/`docs[ab]` skip the `/**` strip, hit the literal branch (#290).
    del tmp
    mod = _classifier()
    cases = [('docs/*', 'docs/a.md'),
             ('do[c]s/README.md', 'do[c]s/README.md')]
    cases += [(p, p.replace('/**', '/a.md'))
              for p in ('docs*/**', 'docs?/**', 'docs[ab]/**', 'docs]/**')]
    cases += [(p, v) for p in ('docs?', 'docs[ab]') for v in (p, 'docsa')]
    for pattern, path in cases:
        try:
            mod.matches(pattern, path)
        except ValueError:
            continue
        raise AssertionError(f'{pattern!r} was matched instead of refused')
    assert mod.matches('docs/README.md', 'docs/README.md')


def test_nested_glob_shape_still_fails_closed(tmp):
    del tmp
    mod = _classifier()
    try:
        mod.matches('docs/**/*.md', 'docs/guide.md')
    except ValueError:
        pass
    else:
        raise AssertionError("'docs/**/*.md' was matched instead of refused")


def test_directory_glob_does_not_match_the_directory_itself(tmp):
    del tmp
    mod = _classifier()
    assert not mod.matches('.github/workflows/**', '.github/workflows')


def test_star_star_pattern_compares_the_final_segment(tmp):
    """`**/rest` matches on the path's final segment, not the whole path.

    A whole-path comparison would fail 'foo/doc1.md' here: fnmatch does
    not treat '/' specially, but the pattern's literal 'doc' prefix would
    have to match at the path's start.
    """
    del tmp
    mod = _classifier()
    assert mod.matches('**/doc*.md', 'foo/doc1.md')
    assert mod.matches('**/doc*.md', 'doc1.md')


def test_workflow_patterns_match_root_nested_and_dependabot_paths(tmp):
    del tmp
    mod = _classifier()
    assert mod.is_workflow('.github/workflows/actionlint.yml')
    assert mod.is_workflow('.github/workflows/nested/check.yml')
    assert mod.is_workflow('.github/dependabot.yml')


def test_workflow_patterns_reject_a_similarly_prefixed_path_and_source(tmp):
    del tmp
    mod = _classifier()
    assert not mod.is_workflow('.github/workflowsx/a.yml')
    assert not mod.is_workflow('scripts/server.py')


def test_workflows_changed_reports_whether_any_path_matches(tmp):
    del tmp
    mod = _classifier()
    assert mod.workflows_changed(['README.md', '.github/dependabot.yml'])
    assert not mod.workflows_changed(['README.md', 'scripts/server.py'])


def test_workflow_patterns_pin_the_actionlint_paths_filter(tmp):
    """The classifier retains the deleted actionlint workflow's filter."""
    del tmp
    mod = _classifier()
    assert mod.WORKFLOW_PATTERNS == (
        '.github/workflows/**', '.github/dependabot.yml')


def test_documentation_paths_classify_to_the_reduced_matrix(tmp):
    del tmp
    mod = _classifier()
    _calls, run = _recorder('README.md\ndocs/guide.md\nLICENSE\n')
    docs_only, matrix, workflows, reason = mod.classify(_event(), run)
    assert docs_only is True, reason
    assert workflows is False
    assert matrix == mod.DOCUMENTATION_MATRIX, matrix
    assert matrix == {'os': ['ubuntu-latest'], 'python': ['3.13']}, matrix

    _calls, run = _recorder('README.md\nserver.py\ndocs/guide.md\n')
    docs_only, matrix, workflows, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert workflows is False
    assert matrix == mod.FULL_MATRIX, matrix


def test_an_unreadable_path_list_overruns_the_workflow_gate(tmp):
    del tmp
    mod = _classifier()

    def run(argv):
        raise RuntimeError(f'api down for {argv}')

    result = mod.classify(_event(), run)
    assert result[2] is True, result


def test_empty_and_missing_path_lists_run_the_full_matrix(tmp):
    del tmp
    mod = _classifier()
    assert mod.documentation_only(()) is False
    assert mod.documentation_only(None) is False
    _calls, run = _recorder('\n')
    docs_only, matrix, workflows, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert workflows is True
    assert matrix == mod.FULL_MATRIX, matrix


def test_a_pull_request_reads_its_file_list(tmp):
    del tmp
    mod = _classifier()
    calls, run = _recorder('README.md\n')
    mod.classify(_event(pull_request='247'), run)
    assert calls == [[
        'gh', 'api', '--paginate', '-H', 'Cache-Control: no-cache',
        'repos/octo/daedalus/pulls/247/files', '--jq', '.[].filename']], calls


def test_a_push_reads_the_compare_between_before_and_sha(tmp):
    del tmp
    mod = _classifier()
    before, sha = 'b' * 40, 'a' * 40
    calls, run = _recorder('docs/guide.md\n')
    mod.classify(_event(name='push', pull_request=None,
                        before=before, sha=sha), run)
    assert calls == [[
        'gh', 'api', '-H', 'Cache-Control: no-cache',
        f'repos/octo/daedalus/compare/{before}...{sha}', '--jq',
        '.files[].filename']], calls


def test_unusable_events_never_call_the_api(tmp):
    del tmp
    mod = _classifier()
    calls, run = _recorder('README.md\n')
    docs_only, matrix, workflows, _reason = mod.classify(
        _event(name='push', pull_request=None, before='0' * 40), run)
    assert (docs_only, matrix, workflows) == (False, mod.FULL_MATRIX, True)
    docs_only, matrix, workflows, _reason = mod.classify(
        _event(name='workflow_dispatch'), run)
    assert (docs_only, matrix, workflows) == (False, mod.FULL_MATRIX, True)
    # A non-numeric PR number must not reach the API either: 'abc' is not
    # digits, and '²' isdigit() but is not an ASCII digit string.
    for bad_number in ('abc', '²', ''):
        docs_only, matrix, workflows, _reason = mod.classify(
            _event(pull_request=bad_number), run)
        assert (docs_only, matrix, workflows) == (
            False, mod.FULL_MATRIX, True), bad_number
    docs_only, matrix, workflows, _reason = mod.classify(
        _event(repository=None), run)
    assert (docs_only, matrix, workflows) == (False, mod.FULL_MATRIX, True)
    assert calls == [], calls


def test_a_failed_read_falls_back_to_the_full_matrix(tmp):
    del tmp
    mod = _classifier()

    def run(argv):
        raise RuntimeError(f'api down for {argv}')

    docs_only, matrix, workflows, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert workflows is True
    assert matrix == mod.FULL_MATRIX, matrix
    assert 'could not read' in reason, reason


def test_a_capped_push_file_list_falls_back_to_the_full_matrix(tmp):
    """300 paths can be a truncated compare page, so they prove nothing."""
    del tmp
    mod = _classifier()
    stdout = ''.join(f'docs/file{index}.md\n' for index in range(300))
    _calls, run = _recorder(stdout)
    docs_only, matrix, workflows, reason = mod.classify(
        _event(name='push', pull_request=None), run)
    assert docs_only is False, reason
    assert workflows is True
    assert matrix == mod.FULL_MATRIX, matrix


def test_a_capped_pull_request_file_list_runs_the_full_matrix(tmp):
    """3000 paths can be a truncated pulls/files list, despite pagination.

    The pulls files endpoint paginates but hard-caps the collection at
    3000, so a list that long may be missing a code file sorted past the
    cutoff; classifying it documentation-only would under-run.
    """
    del tmp
    mod = _classifier()
    stdout = ''.join(f'docs/file{index}.md\n' for index in range(3000))
    _calls, run = _recorder(stdout)
    docs_only, matrix, workflows, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert workflows is True
    assert matrix == mod.FULL_MATRIX, matrix


def test_write_outputs_appends_the_three_output_lines(tmp):
    mod = _classifier()
    out = Path(tmp) / 'github_output'
    matrix = {'os': ['ubuntu-latest'], 'python': ['3.13']}
    mod.write_outputs(str(out), True, matrix, True)
    mod.write_outputs(str(out), False, matrix, False)
    text = out.read_text(encoding='utf-8')
    lines = text.splitlines()
    rendered = f'matrix={json.dumps(matrix)}'
    assert lines == ['docs_only=true', rendered, 'workflows=true',
                     'docs_only=false', rendered, 'workflows=false'], lines
    assert json.loads(lines[1][len('matrix='):]) == matrix
    assert '\n' not in json.dumps(matrix)
    assert text.count('\n') == 6 and text.endswith('\n'), repr(text)


def test_write_outputs_appends_the_workflow_line(tmp):
    mod = _classifier()
    out = Path(tmp) / 'github_output'
    matrix = {'os': ['ubuntu-latest'], 'python': ['3.13']}
    mod.write_outputs(str(out), True, matrix, True)
    lines = out.read_text(encoding='utf-8').splitlines()
    assert lines == ['docs_only=true', f'matrix={json.dumps(matrix)}',
                     'workflows=true'], lines


def test_write_outputs_without_a_file_writes_nothing(tmp):
    del tmp
    mod = _classifier()
    mod.write_outputs(None, True, mod.DOCUMENTATION_MATRIX, True)
    mod.write_outputs('', True, mod.DOCUMENTATION_MATRIX, True)


def test_event_from_environment_maps_the_documented_variables(tmp):
    del tmp
    mod = _classifier()
    event = mod.event_from_environment({
        'GITHUB_EVENT_NAME': 'pull_request',
        'GITHUB_REPOSITORY': 'octo/daedalus',
        'GITHUB_SHA': 'a' * 40,
        'PR_NUMBER': '248',
        'BEFORE_SHA': 'b' * 40,
    })
    assert event == {
        'name': 'pull_request',
        'repository': 'octo/daedalus',
        'sha': 'a' * 40,
        'pull_request': '248',
        'before': 'b' * 40,
    }, event
    assert mod.event_from_environment({})['name'] == ''


def test_documentation_patterns_match_the_workflow_path_filters(tmp):
    """The set speed.yml and version.yml ignore is the set we classify."""
    del tmp
    mod = _classifier()
    for name in ('speed.yml', 'version.yml'):
        text = (ROOT / '.github' / 'workflows' / name).read_text(
            encoding='utf-8')
        triggers = _workflow_triggers(text, name)
        for event in ('push', 'pull_request'):
            filters = _workflow_path_filters(triggers[event], name)
            assert filters == {
                'paths-ignore': list(mod.DOCUMENTATION_PATTERNS)}, (
                name, event, filters)


GATE_JOBS = ('pycodestyle', 'pylint', 'pyright', 'eslint', 'actionlint')
AGGREGATE_ALLOWED_RESULTS = {
    'changes': frozenset(('success',)),
    'pycodestyle': frozenset(('success',)),
    'pylint': frozenset(('success',)),
    'pyright': frozenset(('success',)),
    'eslint': frozenset(('success',)),
    'actionlint': frozenset(('success', 'skipped')),
    'suites': frozenset(('success', 'skipped')),
    # wheel is intentionally not strict: its condition skips only after a
    # failed or cancelled dependency, and aggregate rejects that dependency.
    'wheel': frozenset(('success', 'skipped')),
    'coverage': frozenset(('success', 'skipped')),
}
CONDITION_CONTEXTS = (
    ({'success': True, 'failure': False, 'cancelled': False}, True),
    ({'success': False, 'failure': False, 'cancelled': False}, True),
    ({'success': False, 'failure': True, 'cancelled': False}, False),
    ({'success': False, 'failure': False, 'cancelled': True}, False),
)
DOCS_ONLY_VALUES = ('true', 'false', '')
AGGREGATE_RESULT_STATES = ('success', 'failure', 'cancelled', 'skipped')


def test_changes_job_permissions_are_exactly_read_only(tmp):
    del tmp
    permissions = job_mapping(_tests_yml(), 'changes', 'permissions')
    assert permissions == {'contents': 'read', 'pull-requests': 'read'}, (
        permissions)


def test_changes_job_exposes_every_classifier_output(tmp):
    del tmp
    section = _job_section(_tests_yml(), 'changes')
    expected = (
        '      matrix: ${{ steps.classify.outputs.matrix }}',
        '      docs_only: ${{ steps.classify.outputs.docs_only }}',
        '      workflows: ${{ steps.classify.outputs.workflows }}',
    )
    missing = [line for line in expected if line not in section]
    assert not missing, missing


def test_changes_job_outputs_reference_existing_step_ids(tmp):
    del tmp
    workflow = _tests_yml()
    output_jobs = _job_names_with_outputs(workflow)
    assert set(output_jobs) == {'changes'}, output_jobs
    assert len(output_jobs) == len(set(output_jobs)) == 1, output_jobs
    for job in output_jobs:
        referenced = _job_output_step_ids(workflow, job)
        declared = _job_step_ids(workflow, job)
        assert referenced <= declared, (job, referenced, declared)


def test_changes_job_rejects_an_unrecognized_output_expression(tmp):
    del tmp
    workflow = (
        'jobs:\n'
        '  changes:\n'
        '    outputs:\n'
        "      ghost: ${{ steps[format('mi{0}', 'ssing')].outputs.value }}\n"
        '    steps:\n'
        '      - id: classify\n')
    try:
        _job_output_step_ids(workflow, 'changes')
    except (AssertionError, ValueError, YAMLReadError):
        return
    raise AssertionError('unrecognized output expression was accepted')


def test_job_if_expression_decodes_a_structural_block_scalar(tmp):
    del tmp
    workflow = (
        'jobs:\n'
        '  sample:\n'
        '    if: >-\n'
        '      ${{ !cancelled() && !failure() }}\n')
    assert _job_if_expression(workflow, 'sample') == (
        '${{ !cancelled() && !failure() }}')


def test_static_analysis_jobs_keep_their_required_ids_and_names(tmp):
    del tmp
    workflow = _tests_yml()
    jobs = workflow.partition('\njobs:\n')[2]
    job_ids = re.findall(r'^  ([A-Za-z0-9_-]+):$', jobs, re.MULTILINE)
    gate_ids = [job for job in job_ids if job in GATE_JOBS]
    assert set(gate_ids) == set(GATE_JOBS), (gate_ids, GATE_JOBS)
    assert len(gate_ids) == len(set(gate_ids)) == len(GATE_JOBS), (
        gate_ids)
    for job in GATE_JOBS:
        section = _job_section(workflow, job)
        assert section[0] == f'  {job}:', job
        assert not any(line.startswith('    name:') for line in section), job


def test_expensive_jobs_wait_on_every_static_analysis_gate(tmp):
    del tmp
    expected = ['changes', *GATE_JOBS]
    workflow = _tests_yml()
    for job in ('suites', 'wheel', 'coverage'):
        needs = _job_needs(workflow, job)
        assert set(needs) == set(expected), (job, needs, expected)
        assert len(needs) == len(set(needs)) == len(expected), (
            job, needs, expected)


def test_expensive_job_conditions_run_after_a_skipped_gate_not_a_failed_one(
        tmp):
    del tmp
    workflow = _tests_yml()
    for job in ('suites', 'wheel', 'coverage'):
        expression = _job_if_expression(workflow, job)
        assert expression is not None, job
        for status, should_run in CONDITION_CONTEXTS:
            for docs_only in DOCS_ONLY_VALUES:
                context = {
                    'status': status,
                    'needs': {'changes': {'outputs': {
                        'docs_only': docs_only}}},
                }
                expected = (
                    should_run
                    and not (job == 'coverage'
                             and docs_only == 'true'))
                assert evaluate_if(expression, context) is expected, (
                    job, status, docs_only, expression)


def test_actionlint_survives_a_replacement_push_and_keeps_pr_behavior(tmp):
    del tmp
    mod = _classifier()
    workflow = _tests_yml()
    assert _job_needs(workflow, 'actionlint') == ['changes']
    pushes = (
        (_event(name='push', pull_request=None),
         '.github/workflows/tests.yml\n'),
        (_event(name='push', pull_request=None,
                before='a' * 40, sha='c' * 40), 'README.md\n'),
    )
    classifications = []
    runs = []
    for event, paths in pushes:
        _docs, _matrix, workflows, _reason = mod.classify(
            event, _recorder(paths)[1])
        classifications.append(workflows)
        runs.append(_actionlint_runs(
            workflow, event['name'], 'true' if workflows else 'false'))
    assert classifications == [True, False], classifications
    assert runs == [True, True], runs

    for paths, expected in (('.github/workflows/tests.yml\n', True),
                            ('README.md\n', False)):
        _docs, _matrix, workflows, _reason = mod.classify(
            _event(name='pull_request'), _recorder(paths)[1])
        assert workflows is expected
        assert _actionlint_runs(
            workflow, 'pull_request', 'true' if workflows else 'false') is (
                expected)


def test_coverage_and_suites_take_their_shape_from_the_classifier(tmp):
    """coverage skips on docs_only; suites runs the classifier's matrix."""
    del tmp
    workflow = _tests_yml()
    assert 'changes' in _job_needs(workflow, 'coverage')
    # Without this needs the needs.changes context is empty at runtime
    # and fromJSON(null) fails matrix evaluation on the runner.
    assert 'changes' in _job_needs(workflow, 'suites')
    coverage = _job_section(workflow, 'coverage')
    assert any(line.startswith('    if:') for line in coverage), (
        'coverage has no job-level if')
    assert any('needs.changes.outputs.docs_only' in line
               for line in coverage), coverage
    matrix_lines = [line.strip() for line in _job_section(workflow, 'suites')
                    if line.strip().startswith('matrix:')]
    assert matrix_lines == [
        'matrix: ${{ fromJSON(needs.changes.outputs.matrix) }}'], (
        matrix_lines)


def test_coverage_runs_unless_docs_only_is_exactly_true(tmp):
    """The if: over-runs on every docs_only value except an exact 'true'.

    Evaluated, not substring-matched: `== 'false'` reads as equivalent but
    skips coverage when the classifier emitted nothing — Actions resolves
    a missing output to '' — and a skipped required check reports success
    with nothing measured. The status calls also bypass Actions' implicit
    success check when actionlint skips. Only this shape keeps both cases on
    the over-run branch the design promises.
    """
    del tmp
    expression = _job_if_expression(_tests_yml(), 'coverage')
    assert expression is not None

    def runs(docs_only, status):
        context = {
            'status': status,
            'needs': {'changes': {'outputs': {'docs_only': docs_only}}},
        }
        return evaluate_if(expression, context)

    for status, should_run in CONDITION_CONTEXTS:
        assert runs('true', status) is False
        assert runs('false', status) is should_run
        # '' is both an empty emission and what a missing output resolves to.
        assert runs('', status) is should_run


def test_aggregate_waits_on_every_job_it_checks(tmp):
    del tmp
    needs = _job_needs(_tests_yml(), 'aggregate')
    expected = tuple(AGGREGATE_ALLOWED_RESULTS)
    assert set(needs) == set(expected), (needs, expected)
    assert len(needs) == len(set(needs)) == len(expected), (
        needs, expected)


def test_aggregate_in_process_captures_exit_without_spawning(tmp):
    with mock.patch.object(subprocess, 'run',
                           side_effect=AssertionError('aggregate spawned')):
        result = _run_aggregate(_tests_yml(), {'changes': 'failure'})
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == 'Dependencies not successful: changes=failure\n'


def test_aggregate_failing_result_matches_the_bash_launcher(tmp):
    del tmp
    in_process = _run_aggregate(_tests_yml(), {'changes': 'failure'})
    through_bash = _run_aggregate(
        _tests_yml(), {'changes': 'failure'}, through_bash=True)
    assert (in_process.returncode, in_process.stdout, in_process.stderr) == (
        through_bash.returncode, through_bash.stdout, through_bash.stderr)


def test_aggregate_script_accepts_only_tabled_results(tmp):
    """The aggregate result domain is one contract for every dependency."""
    del tmp
    expected = tuple(AGGREGATE_ALLOWED_RESULTS)
    needs = _job_needs(_tests_yml(), 'aggregate')
    assert set(needs) == set(expected), (needs, expected)
    assert len(needs) == len(set(needs)) == len(expected), (
        needs, expected)

    all_success = {name: 'success' for name in expected}
    for name, accepted in AGGREGATE_ALLOWED_RESULTS.items():
        for result_name in AGGREGATE_RESULT_STATES:
            result = _run_aggregate(
                _tests_yml(), dict(all_success, **{name: result_name}))
            assert (result.returncode == 0) is (result_name in accepted), (
                name, result_name, accepted, result.stdout, result.stderr)

    # Nine dependencies choose two, with four result states per side: 576.
    for names in itertools.combinations(expected, 2):
        for result_names in itertools.product(
                AGGREGATE_RESULT_STATES, repeat=2):
            results = dict(all_success, **dict(zip(names, result_names)))
            expected_success = aggregate_expected(
                results, AGGREGATE_ALLOWED_RESULTS)
            result = _run_aggregate(_tests_yml(), results)
            assert (result.returncode == 0) is expected_success, (
                names, result_names, result.stdout, result.stderr)

    workflow = _tests_yml()
    docs_only = dict(all_success)
    for job, outputs in (
            ('actionlint', {'workflows': 'false'}),
            ('suites', {'docs_only': 'true'}),
            ('wheel', {'docs_only': 'true'}),
            ('coverage', {'docs_only': 'true'})):
        docs_only[job] = 'success' if _job_condition_runs(
            workflow, job, outputs) else 'skipped'
    result = _run_aggregate(_tests_yml(), docs_only)
    assert result.returncode == 0, (result.stdout, result.stderr)
    for name, result_name in docs_only.items():
        assert result_name in AGGREGATE_ALLOWED_RESULTS[name], (
            name, result_name)
    bash_result = _run_aggregate(
        _tests_yml(), docs_only, through_bash=True)
    assert (result.returncode, result.stdout, result.stderr) == (
        bash_result.returncode, bash_result.stdout, bash_result.stderr)


def test_suites_matrix_is_the_classifier_output_not_a_literal(tmp):
    """The workflow no longer declares the suites matrix literally.

    Retargeted when the classifier was wired in: the os/python lists moved
    to FULL_MATRIX in scripts/ci/classify_changes.py, which the changes job
    renders into the suites matrix at runtime. The classifier's constant
    keeps its shape pinned here instead.
    """
    del tmp
    mod = _classifier()
    section = '\n'.join(_job_section(_tests_yml(), 'suites'))
    assert not re.search(r'^\s+(?:os|python): \[', section, re.MULTILINE), (
        section)
    assert len(mod.FULL_MATRIX['os']) == 3, mod.FULL_MATRIX
    assert len(mod.FULL_MATRIX['python']) == 4, mod.FULL_MATRIX
    for axis, values in mod.FULL_MATRIX.items():
        for value in values:
            assert isinstance(value, str) and value, (axis, value)


def test_main_records_the_fallback_outputs_and_returns_zero(tmp):
    """main never returns nonzero, even on the fallback branch.

    A workflow_dispatch event reads no paths, so this exercises main end
    to end — environment in, outputs file out — without spawning `gh`.
    """
    mod = _classifier()
    out = Path(tmp) / 'github_output'
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_OUTPUT': str(out),
        })
        with contextlib.redirect_stdout(io.StringIO()):
            status = mod.main()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert status == 0, status
    lines = out.read_text(encoding='utf-8').splitlines()
    assert lines == ['docs_only=false',
                     f'matrix={json.dumps(mod.FULL_MATRIX)}',
                     'workflows=true'], lines


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='changeclassifier_')


if __name__ == '__main__':
    raise SystemExit(main())
