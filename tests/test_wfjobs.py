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
    _job_needs, _job_output_mapping, _matrix_job_running, _python_matrix,
    _tests_yml)
from _wfjobs import (  # noqa: E402
    jobs_mapping, load, run_path_candidates, unresolved_run_paths,
    workflow_files)
from _yamlscalar import YAMLReadError  # noqa: E402
from _yamlsteps import (  # noqa: E402
    complete_job_mapping, step_mappings, workflow_mapping)


def _decodes_to(source):
    """Return the decoded jobs mapping of a one-job workflow source."""
    decoded = workflow_mapping(source)
    return decoded['jobs']


def test_a_field_after_an_indentless_sequence_survives(tmp):
    del tmp
    jobs = _decodes_to(
        'jobs: {probe: {runs-on: ubuntu-latest, timeout-minutes: 5,'
        ' steps: [{run: echo}]}}\n')
    assert jobs == {'probe': {
        'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
        'steps': [{'run': 'echo'}],
    }}

    jobs = _decodes_to('jobs: {probe: {matrix: [[1, 2], [3, 4]]}}\n')
    assert jobs == {'probe': {'matrix': [['1', '2'], ['3', '4']]}}

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

    jobs = jobs_mapping(
        'jobs:\n'
        '    probe:\n'
        '        runs-on: ubuntu-latest\n'
        '        timeout-minutes: 5\n'
        '        steps:\n'
        '            - run: echo hi\n')
    assert jobs['probe']['timeout-minutes'] == '5'

    jobs = jobs_mapping(
        'jobs: {probe: {runs-on: ubuntu-latest, timeout-minutes: 5}}\n')
    assert jobs == {
        'probe': {'runs-on': 'ubuntu-latest', 'timeout-minutes': '5'}}

    jobs = jobs_mapping('jobs:\n  probe: # manual probe\n' + BOUNDED)
    assert jobs == {'probe': {
        'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
        'steps': [{'run': 'echo hi'}],
    }}

    jobs = jobs_mapping('jobs:\n  "probe":\n' + BOUNDED)
    assert jobs == {'probe': {
        'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
        'steps': [{'run': 'echo hi'}],
    }}

    body = '  probe:\n' + BOUNDED
    for head in ('jobs:\n', 'jobs :\n', '"jobs":\n', 'jobs:  # manual\n'):
        _assert_jobs_equal(jobs_mapping(head + body), {'probe': {
            'runs-on': 'ubuntu-latest', 'timeout-minutes': '5',
            'steps': [{'run': 'echo hi'}],
        }}, head + body)


def test_a_workflow_with_no_jobs_key_reads_as_none(tmp):
    del tmp
    assert jobs_mapping('name: x\non: push\n') is None


def test_a_job_whose_value_is_a_sequence_is_refused(tmp):
    del tmp
    _assert_refusal(jobs_mapping, 'jobs:\n', 'jobs is not a mapping')
    _assert_refusal(jobs_mapping, 'jobs: {}\n', 'declares no job')

    _assert_refusal(jobs_mapping, 'jobs: probe\n', 'is not a mapping')

    _assert_refusal(
        jobs_mapping, '? jobs\n:\n  probe:\n    runs-on: x\n',
        'unsupported mapping field')

    _assert_refusal(
        jobs_mapping, 'jobs:\n  ? probe\n  : runs-on: x\n',
        'empty mapping key')

    _assert_refusal(
        jobs_mapping,
        'jobs:\n  probe:\n    runs-on: x\n  probe:\n    runs-on: y\n',
        'duplicate mapping key')

    _assert_refusal(jobs_mapping, 'jobs:\n  probe:\n    - run: echo\n',
                    'is not a mapping')
    _assert_refusal(jobs_mapping, 'jobs:\n  probe:\n  - run: echo\n',
                    'is not a mapping')


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


def test_an_absent_needs_field_reads_as_no_dependencies(tmp):
    """A job with no `needs:` key names no job, though the next one does."""
    del tmp
    assert _job_needs(_replaced(BLOCK_NEEDS, ''), 'suites') == []


def _planted_needs(spelling):
    """`_job_needs` on the shipped suites job with one `needs:` spelling.

    Named for the planting, and not `_needs_of`: test_aggregate_needs.py
    keeps a different helper under that name, over its own decoder.
    """
    return _job_needs(_replaced(BLOCK_NEEDS, spelling), 'suites')


def _refuses_needs(spelling):
    """The refusal on one spelling, with the spelling in the message.

    `_refuses` reports an accepted defect in its own words, so a row
    dying by acceptance would otherwise not name which spelling regressed.
    """
    workflow = _replaced(BLOCK_NEEDS, spelling)
    try:
        message = _refuses(_job_needs, workflow, 'suites')
    except AssertionError as accepted:
        raise AssertionError(
            f'{spelling!r} accepted: {accepted}') from accepted
    assert 'not a list of job names' in message, spelling


def test_the_needs_spellings_only_the_tolerance_grants_all_decode(tmp):
    del tmp
    for spelling in ('    needs:\n      - changes\n',
                     '    needs: [changes]\n',
                     '    needs: changes\n'):
        assert _planted_needs(spelling) == ['changes'], spelling

    for spelling in ('    needs: [changes, ]\n',
                     '    needs: ["changes"]\n',
                     '    needs:\n      - "changes"\n',
                     '    needs  : [changes]  # the cells\n'):
        assert _planted_needs(spelling) == ['changes'], spelling


def test_a_needs_flow_sequence_names_the_same_jobs_in_order(tmp):
    """A flow sequence decodes to the block sequence's list, in order.

    Six names, not one: a one-item sequence survives any reordering.
    """
    del tmp
    expected = ['changes', 'pycodestyle', 'pylint', 'pyright',
                'eslint', 'actionlint']
    flow = ('    needs: [changes, pycodestyle, pylint, pyright,'
            ' eslint, actionlint]\n')
    assert _job_needs(_tests_yml(), 'suites') == expected, 'shipped block'
    assert _planted_needs(flow) == expected, flow


def test_a_needs_mapping_is_refused_as_not_a_list_of_names(tmp):
    workflow = _real(tmp, _replaced(BLOCK_NEEDS, '    needs:\n'))
    message = _refuses(_job_needs, workflow, 'suites')
    assert 'needs is not a list of job names' in message, message

    for spelling in ('    needs: [[changes, suites], pylint]\n',
                     '    needs: [changes, {changes: true}]\n'):
        _refuses_needs(spelling)

    for spelling in ("    needs: {changes: 'true'}\n",
                     '    needs:\n      changes: true\n'):
        _refuses_needs(spelling)


def test_a_bare_empty_outputs_is_refused_as_not_a_mapping(tmp):
    """`outputs:` with nothing under it declares no output, and says so."""
    workflow = _real(tmp, _replaced(BLOCK_OUTPUTS, '    outputs:\n'))
    message = _refuses(_job_output_mapping, workflow, 'changes')
    assert 'outputs is not a mapping' in message, message


def test_a_bare_empty_strategy_elsewhere_still_finds_the_matrix_job(tmp):
    """A job whose strategy, steps or run is null runs nothing."""
    real = _tests_yml()
    matrix = _python_matrix(real)
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


RUN_PATH_SPELLINGS = (
    'python scripts/ci/install_lint_tools.py\n'
    'python scripts/ci/install_lint_tools.py\n'
    'python -m pytest daedalus_mcp/server.py --thresholds'
    ' ".github/ci-thresholds.json"\n'
    "run: 'scripts/ci/coverage_suites.py';\n"
    'python (scripts/ci/aggregate_gate.py),\n')
NOT_CANDIDATES = (
    ('$RUNNER_TEMP/probe', 'variable-led'),
    ('./actionlint', 'explicit relative'),
    ('../up/tool', 'parent relative'),
    ('dist/*.whl', 'glob'),
    ('/usr/bin/env', 'absolute'),
    ('FOO=x/y', 'assignment'),
    ('repos/$REPO/pulls', 'embedded variable'),
    ('https://example.com/a/b', 'scheme-led'),
    ('.github/workflows/', 'trailing slash'),
)
PLANTED_RUN_STEP = (
    'jobs:\n'
    '  suites:\n'
    '    steps:\n'
    '      - run: python scripts/ci/install_lint_tools.py\n'
    '      - run: python'
    ' scripts/ci/install_lint_tools_that_does_not_exist.py\n')


def test_a_run_naming_real_paths_yields_exactly_those_candidates(tmp):
    """Real spellings, quoted and edge-decorated; sorted, deduplicated."""
    del tmp
    assert run_path_candidates(RUN_PATH_SPELLINGS) == [
        '.github/ci-thresholds.json', 'daedalus_mcp/server.py',
        'scripts/ci/aggregate_gate.py', 'scripts/ci/coverage_suites.py',
        'scripts/ci/install_lint_tools.py']


def test_each_out_of_grammar_spelling_yields_no_candidates(tmp):
    """One rule rejects each spelling; that rule's death flips its entry."""
    del tmp
    for spelling, label in NOT_CANDIDATES:
        found = run_path_candidates(spelling)
        assert found == [], (label, spelling, found)
    combined = ' '.join(spelling for spelling, _label in NOT_CANDIDATES)
    assert run_path_candidates(combined) == [], run_path_candidates(combined)


def test_the_planted_step_is_refused_and_the_verdict_flips_on_resolution(tmp):
    """The issue's planted step, decoded and walked like the real gate."""
    del tmp
    jobs = jobs_mapping(PLANTED_RUN_STEP)
    assert jobs is not None, PLANTED_RUN_STEP
    run_text = '\n'.join(step.get('run', '')
                         for step in jobs['suites']['steps'])
    tracked = {'scripts/ci/install_lint_tools.py'}
    assert unresolved_run_paths(run_text, tracked) == [
        'scripts/ci/install_lint_tools_that_does_not_exist.py']
    tracked |= {'scripts/ci/install_lint_tools_that_does_not_exist.py'}
    assert unresolved_run_paths(run_text, tracked) == []


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
