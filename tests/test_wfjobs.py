#!/usr/bin/env python3
"""The jobs decode both workflow-structure gates are built on.

Each gate is only as good as the decode underneath it, so the decode gets
its own pins: every spelling a valid workflow may write a job with decodes
to the same value, and a construct the reader cannot classify is refused
rather than read as an empty jobs set.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfjobs import jobs_mapping  # noqa: E402
from _yamlscalar import YAMLReadError  # noqa: E402
from _yamlsteps import workflow_mapping  # noqa: E402


def _decodes_to(source):
    """Return the decoded jobs mapping of a one-job workflow source."""
    decoded = workflow_mapping(source)
    return decoded['jobs']


def test_a_nested_flow_mapping_in_a_flow_sequence_decodes(tmp):
    """`[{run: echo}]` is a sequence of mappings, not a malformed key."""
    del tmp
    jobs = _decodes_to(
        'jobs: {probe: {runs-on: ubuntu-latest, timeout-minutes: 5,'
        ' steps: [{run: echo}]}}\n')
    assert jobs == {'probe': {
        'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
        'steps': [{'run': 'echo'}],
    }}


def test_a_nested_flow_sequence_in_a_flow_sequence_decodes(tmp):
    del tmp
    jobs = _decodes_to('jobs: {probe: {matrix: [[1, 2], [3, 4]]}}\n')
    assert jobs == {'probe': {'matrix': [['1', '2'], ['3', '4']]}}


def test_an_indentless_sequence_under_a_mapping_key_decodes(tmp):
    """`needs:` may carry its sequence at the key's own indentation."""
    del tmp
    jobs = _decodes_to(
        'jobs:\n'
        '  aggregate:\n'
        '    needs:\n'
        '    - suites\n'
        '    - wheel\n'
        '  suites:\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 5\n'
        '  wheel:\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 9\n')
    assert jobs['aggregate']['needs'] == ['suites', 'wheel']
    assert jobs['wheel']['timeout-minutes'] == '9'


def test_an_indentless_sequence_of_mappings_decodes(tmp):
    del tmp
    jobs = _decodes_to(
        'jobs:\n'
        '  probe:\n'
        '    steps:\n'
        '    - name: one\n'
        '      run: echo one\n'
        '    - name: two\n'
        '      run: echo two\n')
    assert jobs['probe']['steps'] == [
        {'name': 'one', 'run': 'echo one'},
        {'name': 'two', 'run': 'echo two'},
    ]


BOUNDED = (
    '    runs-on: ubuntu-latest\n'
    '    timeout-minutes: 5\n'
    '    steps:\n'
    '      - run: echo hi\n')


def _assert_jobs_equal(left, right, source):
    assert left == right, f'{left} != {right} for {source!r}'


def test_every_spelling_of_the_jobs_key_decodes_the_same(tmp):
    """Quoted and spaced keys decode to the same jobs mapping."""
    del tmp
    body = '  probe:\n' + BOUNDED
    for head in ('jobs:\n', 'jobs :\n', '"jobs":\n', 'jobs:  # manual\n'):
        _assert_jobs_equal(jobs_mapping(head + body), {'probe': {
            'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
            'steps': [{'run': 'echo hi'}],
        }}, head + body)


def test_a_job_written_as_an_inline_flow_mapping_decodes(tmp):
    del tmp
    jobs = jobs_mapping(
        'jobs: {probe: {runs-on: ubuntu-latest, timeout-minutes: 5}}\n')
    assert jobs == {
        'probe': {'runs-on': 'ubuntu-latest', 'timeout-minutes': '5'}}


def test_a_job_header_carrying_a_comment_decodes(tmp):
    del tmp
    jobs = jobs_mapping('jobs:\n  probe: # manual probe\n' + BOUNDED)
    assert jobs == {'probe': {
        'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
        'steps': [{'run': 'echo hi'}],
    }}


def test_a_quoted_job_id_decodes(tmp):
    del tmp
    jobs = jobs_mapping('jobs:\n  "probe":\n' + BOUNDED)
    assert jobs == {'probe': {
        'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
        'steps': [{'run': 'echo hi'}],
    }}


def test_jobs_indented_four_spaces_decodes(tmp):
    del tmp
    jobs = jobs_mapping(
        'jobs:\n'
        '    probe:\n'
        '        runs-on: ubuntu-latest\n'
        '        timeout-minutes: 5\n'
        '        steps:\n'
        '            - run: echo hi\n')
    assert jobs['probe']['timeout-minutes'] == '5'


def test_a_workflow_with_no_jobs_key_reads_as_none(tmp):
    del tmp
    assert jobs_mapping('name: x\non: push\n') is None


def test_an_explicit_top_level_jobs_key_is_refused_not_empty(tmp):
    """The fail-open shape a real branch shipped: refusal, never `None`."""
    del tmp
    _assert_refusal(
        jobs_mapping, '? jobs\n:\n  probe:\n    runs-on: x\n',
        'unsupported mapping field')


def test_an_explicit_job_key_is_refused_not_skipped(tmp):
    del tmp
    _assert_refusal(
        jobs_mapping, 'jobs:\n  ? probe\n  : runs-on: x\n',
        'empty mapping key')


def test_whole_job_anchors_and_aliases_are_refused(tmp):
    """A bound on one job must not be read as shared by an alias."""
    del tmp
    _assert_refusal(
        jobs_mapping,
        'jobs:\n  probe: &probe\n    runs-on: x\n  clone: *probe\n',
        'unsupported plain scalar')


def test_duplicate_job_keys_are_refused(tmp):
    del tmp
    _assert_refusal(
        jobs_mapping,
        'jobs:\n  probe:\n    runs-on: x\n  probe:\n    runs-on: y\n',
        'duplicate mapping key')


def test_an_empty_jobs_mapping_is_refused(tmp):
    """A workflow with no job classifies nothing, which is not a pass."""
    del tmp
    _assert_refusal(jobs_mapping, 'jobs:\n', 'has no value')
    _assert_refusal(jobs_mapping, 'jobs: {}\n', 'declares no job')


def test_a_job_whose_value_is_a_sequence_is_refused(tmp):
    del tmp
    _assert_refusal(jobs_mapping, 'jobs:\n  probe:\n    - run: echo\n',
                    'is not a mapping')
    _assert_refusal(jobs_mapping, 'jobs:\n  probe:\n  - run: echo\n',
                    'is not a mapping')


def test_a_job_field_of_unknown_shape_is_refused(tmp):
    """A job value that is neither mapping nor sequence is not read."""
    del tmp
    _assert_refusal(jobs_mapping, 'jobs: probe\n', 'is not a mapping')


def test_the_jobs_block_ends_at_the_next_top_level_key(tmp):
    """A following `defaults:` is not read as a job."""
    del tmp
    jobs = jobs_mapping(
        'jobs:\n'
        '  probe:\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 5\n'
        'defaults:\n'
        '  run:\n'
        '    shell: bash\n')
    assert list(jobs) == ['probe']


def _assert_refusal(reader, source, expected):
    try:
        reader(source)
    except YAMLReadError as error:
        assert expected in str(error), str(error)
        return
    raise AssertionError(f'{source!r} was accepted, expected {expected!r}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
