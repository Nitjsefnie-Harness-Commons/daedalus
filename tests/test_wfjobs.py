#!/usr/bin/env python3
"""The complete decode both workflow-structure gates are built on.

Each gate is only as good as the decode underneath it, so the decode gets
its own pins: every spelling a valid workflow may write a job with decodes
to the same value, a construct the reader cannot classify is refused rather
than read as an empty jobs set, and a bare-empty value reads as `None`
wherever it can sit, against `yaml.safe_load` and the shipped workflows.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import yaml  # noqa: E402
from _wffixtures import (  # noqa: E402
    BLOCK_NEEDS, BLOCK_OUTPUTS, _real, _refuses, _replaced)
from _wfgraph import (  # noqa: E402
    _job_needs, _job_output_mapping, _matrix_job_running, _tests_yml)
from _wfjobs import jobs_mapping, load, workflow_files  # noqa: E402
from _yamlscalar import YAMLReadError  # noqa: E402
from _yamlsteps import (  # noqa: E402
    complete_job_mapping, step_mappings, workflow_mapping)


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


def test_a_field_after_an_indentless_sequence_survives(tmp):
    """The sibling key after the sequence is a field, not its content."""
    del tmp
    jobs = _decodes_to(
        'jobs:\n'
        '  aggregate:\n'
        '    needs:\n'
        '    - probe\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 5\n'
        '  probe:\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 9\n')
    assert jobs['aggregate'] == {
        'needs': ['probe'],
        'runs-on': 'ubuntu-latest',
        'timeout-minutes': '5',
    }
    assert jobs['probe']['timeout-minutes'] == '9'


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
    _assert_refusal(jobs_mapping, 'jobs:\n', 'jobs is not a mapping')
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


BOUNDED_JOB = (
    'jobs:\n'
    '  probe:\n'
    '    runs-on: ubuntu-latest\n'
    '    timeout-minutes: 5\n'
    '    steps:\n'
    '      - run: echo\n')
# Each fixture: name, source, the paths reading `None`, the paths reading ''.
BARE_EMPTY_FIXTURES = (
    ('last-field-at-end-of-document',
     'name: x\n' + BOUNDED_JOB + 'on:\n  workflow_dispatch:\n',
     (('on', 'workflow_dispatch'),), ()),
    ('sibling-key-then-dedent',
     'name: x\non:\n  pull_request:\n  workflow_dispatch:\n' + BOUNDED_JOB,
     (('on', 'pull_request'), ('on', 'workflow_dispatch')), ()),
    ('followed-only-by-blank-lines',
     'name: x\n' + BOUNDED_JOB + 'on:\n  workflow_dispatch:\n\n\n',
     (('on', 'workflow_dispatch'),), ()),
    ('trailing-comment',
     'name: x\non:\n  workflow_dispatch: # manual\n' + BOUNDED_JOB,
     (('on', 'workflow_dispatch'),), ()),
    ('trailing-spaces',
     'name: x\non:\n  workflow_dispatch:   \n' + BOUNDED_JOB,
     (('on', 'workflow_dispatch'),), ()),
    ('nested-two-levels',
     'name: x\non:\n  workflow_dispatch:\n    inputs:\n' + BOUNDED_JOB,
     (('on', 'workflow_dispatch', 'inputs'),), ()),
    ('sequence-item-mapping',
     'name: x\non: push\n'
     'jobs:\n'
     '  probe:\n'
     '    runs-on: ubuntu-latest\n'
     '    timeout-minutes: 5\n'
     '    strategy:\n'
     '      matrix:\n'
     '        include:\n'
     '          - os: ubuntu-latest\n'
     '            extra:\n'
     '          - extra:\n'
     '            os: ubuntu-latest\n'
     '    steps:\n'
     '      - run: echo\n',
     (('jobs', 'probe', 'strategy', 'matrix', 'include', 0, 'extra'),
      ('jobs', 'probe', 'strategy', 'matrix', 'include', 1, 'extra')), ()),
    ('absent-versus-empty',
     'name: x\non: push\nenv:\n  a:\n  b: \'\'\n  c: ""\n' + BOUNDED_JOB,
     (('env', 'a'),), (('env', 'b'), ('env', 'c'))),
)
SHIPPED_WITH_BARE_TRIGGERS = {
    'audit.yml', 'codeql.yml', 'release.yml', 'scorecard.yml', 'tests.yml',
    'version.yml'}


def _at(document, path):
    """Look up one path; `yaml.safe_load` spells the `on` key `True`."""
    for step in path:
        if step == 'on' and 'on' not in document and True in document:
            step = True
        document = document[step]
    return document


def _none_paths(document, path=()):
    """Every path whose value `yaml.safe_load` decoded to `None`."""
    if isinstance(document, dict):
        items = document.items()
    elif isinstance(document, list):
        items = enumerate(document)
    else:
        return []
    found = []
    for key, value in items:
        here = path + ('on' if key is True else key,)
        if value is None:
            found.append(here)
        found.extend(_none_paths(value, here))
    return found


def test_a_trigger_key_with_nothing_under_it_reads_as_none(tmp):
    """The issue's exact source."""
    source = _real(tmp, 'name: x\non:\n  pull_request:\njobs:\n  probe:\n'
                   '    runs-on: ubuntu-latest\n    timeout-minutes: 5\n')
    decoded = workflow_mapping(source)
    assert decoded['on'] == {'pull_request': None}, decoded['on']
    assert decoded['on']['pull_request'] is None


def _bare_trigger_sweep(paths):
    """Count the `None` values, each a filterless top-level trigger."""
    compared = 0
    files_with_none = set()
    for path in paths:
        source = path.read_text(encoding='utf-8')
        decoded = workflow_mapping(source)
        oracle = yaml.safe_load(source)
        for position in _none_paths(oracle):
            assert _at(decoded, position) is None, (path.name, position)
            assert position[0] == 'on' and len(position) == 2, (
                path.name, position)
            compared += 1
            files_with_none.add(path.name)
    return compared, files_with_none


def test_every_shipped_workflow_decodes_with_its_bare_triggers_as_none(tmp):
    """The sweep the issue was filed against: no shipped file is refused."""
    del tmp
    paths = workflow_files()
    names = {path.name for path in paths}
    assert SHIPPED_WITH_BARE_TRIGGERS <= names, sorted(names)
    compared, files_with_none = _bare_trigger_sweep(paths)
    assert compared >= len(SHIPPED_WITH_BARE_TRIGGERS), compared
    assert files_with_none >= SHIPPED_WITH_BARE_TRIGGERS, files_with_none


def test_the_sweep_admits_any_bare_trigger_not_only_the_shipped_two(tmp):
    """The sweep pins the shape of a bare trigger, not the shipped names."""
    _real(tmp, _replaced('on:\n', 'on:\n  push:\n', 'claim.yml'), 'claim.yml')
    compared, files_with_none = _bare_trigger_sweep([Path(tmp) / 'claim.yml'])
    assert compared == 1, compared
    assert files_with_none == {'claim.yml'}, files_with_none


def test_the_sweep_refuses_a_bare_value_that_is_not_a_trigger(tmp):
    """A bare value outside `on` is not accepted as a trigger."""
    _real(tmp, _replaced('permissions:\n  contents: read\n', 'permissions:\n'))
    path = Path(tmp) / 'tests.yml'
    position = ('permissions',)
    message = _refuses(_bare_trigger_sweep, [path])
    assert str((path.name, position)) in message, (message, position)


def test_the_sweep_refuses_a_nested_bare_value_under_on(tmp):
    """A nested bare trigger filter is not a top-level trigger."""
    source = _real(tmp, _replaced(
        'on:\n  push:\n    tags: ["v*"]\n  workflow_dispatch:\n',
        'on:\n  pull_request:\n    branches:\n', 'release.yml'),
        'release.yml')
    path = Path(tmp) / 'release.yml'
    position = ('on', 'pull_request', 'branches')
    oracle = yaml.safe_load(source)
    assert _at(oracle, position) is None, (oracle, position)
    message = _refuses(_bare_trigger_sweep, [path])
    assert str((path.name, position)) in message, (message, position)


def test_the_sweep_refuses_a_nested_bare_value_outside_on_and_jobs(tmp):
    """A nested bare permission is not a top-level trigger."""
    source = _real(tmp, _replaced(
        'permissions:\n  contents: read\n',
        'permissions:\n  contents:\n'))
    path = Path(tmp) / 'tests.yml'
    position = ('permissions', 'contents')
    oracle = yaml.safe_load(source)
    assert _at(oracle, position) is None, (oracle, position)
    message = _refuses(_bare_trigger_sweep, [path])
    assert str((path.name, position)) in message, (message, position)


def test_a_bare_empty_value_reads_as_none_in_every_position(tmp):
    """Every position matches `yaml.safe_load`; `''` stays `''`."""
    compared = 0
    for name, text, none_paths, empty_paths in BARE_EMPTY_FIXTURES:
        source = _real(tmp, text, name + '.yml')
        decoded = workflow_mapping(source)
        oracle = yaml.safe_load(source)
        assert sorted(map(str, _none_paths(oracle))) == sorted(
            map(str, none_paths)), (name, _none_paths(oracle))
        for position in none_paths:
            assert _at(decoded, position) is None, (name, position)
            assert _at(oracle, position) is None, (name, position)
            compared += 1
        for position in empty_paths:
            assert _at(decoded, position) == '', (name, position)
            assert _at(oracle, position) == '', (name, position)
            compared += 1
    assert compared == 12, compared


def test_a_bare_empty_job_field_reads_as_none_through_every_job_reader(tmp):
    """Every job reader reads the same null."""
    name, text, none_paths, _empty = BARE_EMPTY_FIXTURES[6]
    assert name == 'sequence-item-mapping', name
    source = _real(tmp, text, name + '.yml')
    path = Path(tmp) / (name + '.yml')
    expected = workflow_mapping(source)['jobs']['probe']
    include = expected['strategy']['matrix']['include']
    assert include == [{'os': 'ubuntu-latest', 'extra': None},
                       {'extra': None, 'os': 'ubuntu-latest'}], include
    assert complete_job_mapping(source, 'probe') == expected
    assert jobs_mapping(source) == {'probe': expected}
    assert load(path).jobs == {'probe': expected}
    assert len(none_paths) == 2, none_paths


def test_a_bare_empty_needs_is_refused_as_not_a_list_of_names(tmp):
    """`needs:` with nothing under it names no job, and says so."""
    workflow = _real(tmp, _replaced(BLOCK_NEEDS, '    needs:\n'))
    message = _refuses(_job_needs, workflow, 'suites')
    assert 'needs is not a list of job names' in message, message


def test_a_bare_empty_outputs_is_refused_as_not_a_mapping(tmp):
    """`outputs:` with nothing under it declares no output, and says so."""
    workflow = _real(tmp, _replaced(BLOCK_OUTPUTS, '    outputs:\n'))
    message = _refuses(_job_output_mapping, workflow, 'changes')
    assert 'outputs is not a mapping' in message, message


def test_a_bare_empty_strategy_elsewhere_still_finds_the_matrix_job(tmp):
    """A job whose strategy, steps or run is null runs nothing."""
    real = _tests_yml()
    matrix = {'os': ['ubuntu-latest', 'windows-latest', 'macos-latest'],
              'python': ['3.13']}
    name, _job = _matrix_job_running(real, matrix, 'coverage_suites.py')
    header = '    name: Aggregate workflow checks\n'
    install = '      - name: Install the coverage toolchain and the project\n'
    for old, plant in ((header, header + '    strategy:\n'),
                       (install, '      - run:\n' + install)):
        workflow = _real(tmp, _replaced(old, plant))
        found, _job = _matrix_job_running(
            workflow, matrix, 'coverage_suites.py')
        assert found == name, (plant, found)
    source = _real(tmp, (
        'jobs:\n'
        '  idle:\n'
        '    runs-on: ubuntu-latest\n'
        '    strategy:\n'
        '      matrix: {os: [ubuntu-latest]}\n'
        '    steps:\n'
        '  busy:\n'
        '    runs-on: ubuntu-latest\n'
        '    strategy:\n'
        '      matrix: {os: [ubuntu-latest]}\n'
        '    steps:\n'
        '      - run: coverage_suites.py\n'), 'null-steps.yml')
    found, _job = _matrix_job_running(
        source, {'os': ['ubuntu-latest']}, 'coverage_suites.py')
    assert found == 'busy', found


def test_a_bare_empty_step_field_is_still_refused(tmp):
    """The step reader still refuses an empty field."""
    source = _real(tmp, 'jobs:\n  sample:\n    steps:\n      - run: echo\n'
                   '        with:\n')
    message = _refuses(step_mappings, source, 'sample')
    assert 'step with is not a scalar mapping' in message, message


def test_spelled_null_literals_still_read_as_their_spelling(tmp):
    """Spelled nulls stay plain scalars."""
    for spelling in ('null', '~', 'Null'):
        source = _real(tmp, BOUNDED_JOB + '    env: ' + spelling + '\n')
        assert workflow_mapping(source)['jobs']['probe']['env'] == spelling
        assert complete_job_mapping(source, 'probe')['env'] == spelling


def test_shapes_beside_a_bare_empty_value_keep_their_verdicts(tmp):
    """Neighbours of the accepted shape keep their verdicts."""
    cases = (
        ('A:\n  two\n' + BOUNDED_JOB, 'unsupported mapping field'),
        (BOUNDED_JOB + '    needs:\n      - \n', 'unsupported plain scalar'),
        (BOUNDED_JOB + '    needs:\n      -\n', 'unsupported mapping field'),
        (BOUNDED_JOB + '    env: &anchor\n', 'unsupported plain scalar'),
    )
    for text, expected in cases:
        source = _real(tmp, text)
        message = _refuses(workflow_mapping, source)
        assert expected in message, (text, message)
    source = _real(tmp, BOUNDED_JOB + '    env: {a: , b: 1}\n')
    assert workflow_mapping(source)['jobs']['probe']['env'] == {
        'a': None, 'b': '1'}


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
