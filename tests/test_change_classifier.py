#!/usr/bin/env python3
"""The change classifier: which shape a tests.yml run gets.

scripts/ci/classify_changes.py decides whether a run changed only
documentation — the same set speed.yml and version.yml ignore — and so pays
for one suite leg and no coverage instead of the full matrix. These tests pin
the pattern matcher, the event-to-API mapping, the over-run fallbacks and the
two contracts that keep the module's constants honest against the workflows.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflows import (  # noqa: E402
    _flow_sequence, _workflow_path_filters, _workflow_triggers)


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
        mod.matches('docs/**', 'docs/a.md')
    except ValueError:
        pass
    else:
        raise AssertionError("'docs/**' was matched instead of refused")


def test_documentation_paths_classify_to_the_reduced_matrix(tmp):
    del tmp
    mod = _classifier()
    _calls, run = _recorder('README.md\ndocs/guide.md\nLICENSE\n')
    docs_only, matrix, reason = mod.classify(_event(), run)
    assert docs_only is True, reason
    assert matrix == mod.DOCUMENTATION_MATRIX, matrix
    assert matrix == {'os': ['ubuntu-latest'], 'python': ['3.13']}, matrix

    _calls, run = _recorder('README.md\nserver.py\ndocs/guide.md\n')
    docs_only, matrix, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert matrix == mod.FULL_MATRIX, matrix


def test_empty_and_missing_path_lists_run_the_full_matrix(tmp):
    del tmp
    mod = _classifier()
    assert mod.documentation_only(()) is False
    assert mod.documentation_only(None) is False
    _calls, run = _recorder('\n')
    docs_only, matrix, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
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
    docs_only, matrix, _reason = mod.classify(
        _event(name='push', pull_request=None, before='0' * 40), run)
    assert (docs_only, matrix) == (False, mod.FULL_MATRIX)
    docs_only, matrix, _reason = mod.classify(
        _event(name='workflow_dispatch'), run)
    assert (docs_only, matrix) == (False, mod.FULL_MATRIX)
    assert calls == [], calls


def test_a_failed_read_falls_back_to_the_full_matrix(tmp):
    del tmp
    mod = _classifier()

    def run(argv):
        raise RuntimeError(f'api down for {argv}')

    docs_only, matrix, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert matrix == mod.FULL_MATRIX, matrix
    assert 'could not read' in reason, reason


def test_a_capped_push_file_list_falls_back_to_the_full_matrix(tmp):
    """300 paths can be a truncated compare page, so they prove nothing."""
    del tmp
    mod = _classifier()
    stdout = ''.join(f'docs/file{index}.md\n' for index in range(300))
    _calls, run = _recorder(stdout)
    docs_only, matrix, reason = mod.classify(
        _event(name='push', pull_request=None), run)
    assert docs_only is False, reason
    assert matrix == mod.FULL_MATRIX, matrix


def test_write_outputs_appends_the_two_output_lines(tmp):
    mod = _classifier()
    out = Path(tmp) / 'github_output'
    matrix = {'os': ['ubuntu-latest'], 'python': ['3.13']}
    mod.write_outputs(str(out), True, matrix)
    mod.write_outputs(str(out), False, matrix)
    text = out.read_text(encoding='utf-8')
    lines = text.splitlines()
    rendered = f'matrix={json.dumps(matrix)}'
    assert lines == ['docs_only=true', rendered,
                     'docs_only=false', rendered], lines
    assert json.loads(lines[1][len('matrix='):]) == matrix
    assert '\n' not in json.dumps(matrix)
    assert text.count('\n') == 4 and text.endswith('\n'), repr(text)


def test_write_outputs_without_a_file_writes_nothing(tmp):
    del tmp
    mod = _classifier()
    mod.write_outputs(None, True, mod.DOCUMENTATION_MATRIX)
    mod.write_outputs('', True, mod.DOCUMENTATION_MATRIX)


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


def _suites_matrix(workflow):
    """jobs.suites.strategy.matrix, read as text from the workflow source."""
    lines = workflow.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == '      matrix:':
            assert start is None, 'a second matrix: block appeared'
            start = index
    assert start is not None, 'no suites matrix block found'
    matrix = {}
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if len(line) - len(line.lstrip(' ')) <= 6:
            break
        key, colon, value = stripped.partition(':')
        assert colon, line
        matrix[key] = _flow_sequence(value.strip(), key, 'tests.yml')
    return matrix


def test_full_matrix_matches_the_workflow_suites_matrix(tmp):
    """FULL_MATRIX is the literal suites matrix tests.yml declares.

    Task 2 of this change replaces that literal with
    fromJSON(needs.changes.outputs.matrix) and retargets this test, so a
    reader who finds this test failing on that task's branch should look
    there first.
    """
    del tmp
    mod = _classifier()
    text = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    assert mod.FULL_MATRIX == _suites_matrix(text), _suites_matrix(text)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='changeclassifier_')


if __name__ == '__main__':
    raise SystemExit(main())
