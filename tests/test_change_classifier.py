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
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflows import (  # noqa: E402
    _workflow_path_filters, _workflow_triggers)
from _yamlread import job_mapping  # noqa: E402
from _ghexpr import evaluate, evaluate_if  # noqa: E402
from _wfgraph import (  # noqa: E402
    _job_if_expression, _job_needs, _job_output_step_ids, _job_section,
    _job_step_ids, _tests_yml)


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


def test_an_unimplemented_pattern_shape_fails_closed(tmp):
    """A third pattern shape must raise, never silently mismatch."""
    del tmp
    mod = _classifier()
    try:
        mod.matches('docs/*', 'docs/a.md')
    except ValueError:
        pass
    else:
        raise AssertionError("'docs/*' was matched instead of refused")


def test_directory_glob_prefix_rejects_unimplemented_metacharacters(tmp):
    del tmp
    mod = _classifier()
    for pattern in ('docs*/**', 'docs?/**', 'docs[ab]/**', 'docs]/**'):
        path = pattern.replace('/**', '/a.md')
        try:
            mod.matches(pattern, path)
        except ValueError:
            continue
        raise AssertionError(f'{pattern!r} was matched instead of refused')


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
STRICT_JOBS = ('changes', 'pycodestyle', 'pylint', 'pyright', 'eslint')


def _aggregate_script(workflow):
    """The aggregate job's run block, dedented, ready for bash."""
    section = '\n'.join(_job_section(workflow, 'aggregate'))
    _, marker, after = section.partition('        run: |\n')
    assert marker, 'aggregate has no run block shaped as this test expects'
    lines = []
    for line in after.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines)


def _run_aggregate(results):
    """Run the real aggregate script against one `needs` result mapping."""
    bash = shutil.which('bash')
    assert bash, 'bash is required to execute the aggregate script'
    needs = {name: {'result': result} for name, result in results.items()}
    env = {**os.environ, 'NEEDS_JSON': json.dumps(needs)}
    return subprocess.run([bash, '-c', _aggregate_script(_tests_yml())],
                          env=env, capture_output=True, text=True, timeout=60)


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
    referenced = _job_output_step_ids(workflow, 'changes')
    declared = _job_step_ids(workflow, 'changes')
    assert referenced <= declared, (referenced, declared)


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
    contexts = (
        ({'success': False, 'failure': False, 'cancelled': False}, True),
        ({'success': False, 'failure': True, 'cancelled': False}, False),
        ({'success': False, 'failure': False, 'cancelled': True}, False),
    )
    for job in ('suites', 'wheel', 'coverage'):
        expression = _job_if_expression(workflow, job)
        assert expression is not None, job
        for status, should_run in contexts:
            context = {
                'status': status,
                'needs': {'changes': {'outputs': {'docs_only': 'false'}}},
            }
            assert evaluate_if(expression, context) is should_run, (
                job, status, expression)


def test_actionlint_runs_only_when_workflow_paths_changed(tmp):
    del tmp
    workflow = _tests_yml()
    assert _job_needs(workflow, 'actionlint') == ['changes']
    section = _job_section(workflow, 'actionlint')
    expected = "    if: ${{ needs.changes.outputs.workflows == 'true' }}"
    conditions = [line for line in section if line.startswith('    if:')]
    assert conditions == [expected], conditions
    expression = conditions[0].strip()[len('if:'):].strip()
    for workflows, should_run in (
            ('true', True), ('false', False), ('', False)):
        context = {'needs': {'changes': {'outputs': {
            'workflows': workflows}}}}
        assert evaluate(expression, context) is should_run, (
            workflows, expression)


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

    def runs(docs_only):
        context = {
            'status': {
                'success': False,
                'failure': False,
                'cancelled': False,
            },
            'needs': {'changes': {'outputs': {'docs_only': docs_only}}},
        }
        return evaluate_if(expression, context)

    assert runs('true') is False
    assert runs('false') is True
    # '' is both an empty emission and what a missing output resolves to.
    assert runs('') is True


def test_aggregate_waits_on_every_job_it_checks(tmp):
    del tmp
    needs = _job_needs(_tests_yml(), 'aggregate')
    expected = ('changes', *GATE_JOBS, 'suites', 'wheel', 'coverage')
    assert set(needs) == set(expected), (needs, expected)
    assert len(needs) == len(set(needs)) == len(expected), (
        needs, expected)


def test_aggregate_script_accepts_only_intentional_gate_skips(tmp):
    """The accept-skipped behaviour, exercised by running the real script.

    This is the case that goes green for the wrong reason when the strict
    set or the accepted results drift, so the fixtures below run the
    workflow's own shell rather than a reading of it.
    """
    del tmp
    all_success = {
        'changes': 'success',
        **{name: 'success' for name in GATE_JOBS},
        'suites': 'success',
        'wheel': 'success',
        'coverage': 'success',
    }
    result = _run_aggregate(all_success)
    assert result.returncode == 0, (result.stdout, result.stderr)

    docs_only = dict(
        all_success, actionlint='skipped', suites='skipped',
        coverage='skipped')
    result = _run_aggregate(docs_only)
    assert result.returncode == 0, (result.stdout, result.stderr)

    for name in ('changes', *GATE_JOBS, 'suites', 'wheel', 'coverage'):
        result = _run_aggregate(dict(all_success, **{name: 'failure'}))
        assert result.returncode != 0, (name, result.stdout, result.stderr)

    for name in STRICT_JOBS:
        result = _run_aggregate(dict(all_success, **{name: 'skipped'}))
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        assert name in result.stderr, result.stderr

    result = _run_aggregate(dict(all_success, actionlint='skipped'))
    assert result.returncode == 0, (result.stdout, result.stderr)
    result = _run_aggregate(dict(all_success, suites='cancelled'))
    assert result.returncode != 0, (result.stdout, result.stderr)


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
