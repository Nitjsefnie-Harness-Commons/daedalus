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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
