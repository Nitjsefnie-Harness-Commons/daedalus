#!/usr/bin/env python3
"""Executable contracts for JavaScript coverage capture and publication."""
import ast
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_names  # noqa: E402
from _yamlread import job_mapping, step_mapping_scalar  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402


_CAPTURE_KEY = 'NODE_V8_COVERAGE'
_CAPTURE_VALUE = '${{ github.workspace }}/.node-v8-coverage'
_JOB_CAPTURE_LINE = f'      {_CAPTURE_KEY}: "{_CAPTURE_VALUE}"\n'
_STEP_CAPTURE_LINE = f'          {_CAPTURE_KEY}: "{_CAPTURE_VALUE}"\n'
_STEP_START = re.compile(r'^      - ', re.MULTILINE)
_CONSUMER_ID = re.compile(
    r'^        id: (?:measure|ratchet)[ \t]*$', re.MULTILINE)
_SUPPORTED_MATRIX = {
    'os': ['ubuntu-latest', 'windows-latest', 'macos-latest'],
    'python': ['3.13'],
}
_PTH_CREATION_COMMAND = (
    'python -c "from pathlib import Path; import sysconfig; Path( '
    "sysconfig.get_paths()['purelib'], 'coverage-subprocess.pth').write_text( "
    "'import coverage; coverage.process_startup()\\n', encoding='ascii')\"")


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')


def _matrix_job(workflow):
    matches = []
    for name in _job_names(workflow):
        job = complete_job_mapping(workflow, name)
        matrix = job.get('strategy', {}).get('matrix')
        measures = [
            step for step in job.get('steps', [])
            if 'coverage_suites.py' in step.get('run', '')
        ]
        if matrix == _SUPPORTED_MATRIX and measures:
            matches.append((name, job))
    assert len(matches) == 1, matches
    return matches[0]


def _pth_program(command):
    argv = shlex.split(command)
    assert len(argv) == 3 and argv[:2] == ['python', '-c'], argv
    writes = [
        node for node in ast.walk(ast.parse(argv[2]))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'write_text'
    ]
    assert len(writes) == 1, writes
    program = writes[0].args[0]
    assert isinstance(program, ast.Constant)
    assert isinstance(program.value, str)
    return program.value


def _coverage_bounds(workflow):
    start_marker = '\n  coverage:\n'
    end_marker = '\n  diff-coverage:\n'
    before, found, rest = workflow.partition(start_marker)
    assert found, workflow
    body, found, _after = rest.partition(end_marker)
    assert found, workflow
    start = len(before) + len(start_marker)
    return start, start + len(body)


def _coverage_steps(workflow):
    job_start, job_end = _coverage_bounds(workflow)
    steps_marker = '    steps:\n'
    steps_start = workflow.find(steps_marker, job_start, job_end)
    assert steps_start >= 0, workflow[job_start:job_end]
    steps_start += len(steps_marker)
    matches = list(_STEP_START.finditer(workflow, steps_start, job_end))
    steps = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) \
            else job_end
        first_line = workflow[start:workflow.find('\n', start)]
        prefix = '      - name: '
        name = first_line[len(prefix):] if first_line.startswith(prefix) \
            else None
        steps.append((start, end, name))
    return steps


def _consumer_steps(workflow):
    consumers = []
    javascript = 'python scripts/ci/js_coverage.py "$NODE_V8_COVERAGE"'
    for start, end, name in _coverage_steps(workflow):
        step = workflow[start:end]
        if _CONSUMER_ID.search(step) or javascript in step:
            assert name is not None, step
            consumers.append((start, end, name))
    assert len(consumers) == 4, consumers
    return consumers


def _rename_measure_step(workflow):
    matches = []
    for start, end, name in _coverage_steps(workflow):
        if re.search(r'^        id: measure[ \t]*$',
                     workflow[start:end], re.MULTILINE):
            matches.append((start, end, name))
    assert len(matches) == 1, matches
    start, end, current = matches[0]
    assert current is not None, workflow[start:end]
    temporary = 'Temporary measure label'
    if current == temporary:
        temporary = 'Alternate measure label'
    step = workflow[start:end]
    first, newline, rest = step.partition('\n')
    assert newline, step
    assert first == f'      - name: {current}', first
    renamed = f'      - name: {temporary}\n{rest}'
    return workflow[:start] + renamed + workflow[end:]


def _without_job_capture(workflow):
    job_start, job_end = _coverage_bounds(workflow)
    steps = workflow.find('    steps:\n', job_start, job_end)
    assert steps >= 0, workflow[job_start:job_end]
    prefix = workflow[job_start:steps]
    assert prefix.count(_JOB_CAPTURE_LINE) <= 1, prefix
    prefix = prefix.replace(_JOB_CAPTURE_LINE, '', 1)
    return workflow[:job_start] + prefix + workflow[steps:]


def _with_step_capture(step):
    if _STEP_CAPTURE_LINE in step:
        assert step.count(_STEP_CAPTURE_LINE) == 1, step
        return step
    assert not re.search(
        rf'^          {re.escape(_CAPTURE_KEY)}:', step, re.MULTILINE)
    env = '        env:\n'
    if env in step:
        assert step.count(env) == 1, step
        return step.replace(env, env + _STEP_CAPTURE_LINE, 1)
    line_end = step.index('\n') + 1
    return (step[:line_end] + env + _STEP_CAPTURE_LINE
            + step[line_end:])


def _without_step_capture(step):
    count = step.count(_STEP_CAPTURE_LINE)
    assert count <= 1, step
    if not count:
        return step
    lines = step.splitlines(keepends=True)
    capture = lines.index(_STEP_CAPTURE_LINE)
    env = max(
        index for index in range(capture)
        if lines[index] == '        env:\n')
    del lines[capture]
    has_value = False
    for line in lines[env + 1:]:
        raw = line.rstrip('\r\n')
        stripped = raw.lstrip(' ')
        if not stripped or stripped.startswith('#'):
            continue
        if len(raw) - len(stripped) <= 8:
            break
        has_value = True
        break
    if not has_value:
        del lines[env]
    return ''.join(lines)


def _map_consumer_steps(workflow, transform):
    for start, end, _name in reversed(_consumer_steps(workflow)):
        step = transform(workflow[start:end])
        workflow = workflow[:start] + step + workflow[end:]
    return workflow


def _step_scoped_capture(workflow):
    workflow = _without_job_capture(workflow)
    return _map_consumer_steps(workflow, _with_step_capture)


def _checkout_scoped_capture(workflow):
    workflow = _without_job_capture(workflow)
    workflow = _map_consumer_steps(workflow, _without_step_capture)
    before, marker, coverage = workflow.partition('\n  coverage:\n')
    assert marker, workflow
    checkout = '      - uses: actions/checkout@'
    head, found, rest = coverage.partition(checkout)
    assert found, coverage
    line, newline, rest = rest.partition('\n')
    assert newline, coverage
    env = (f'        env:\n'
           f'          {_CAPTURE_KEY}: "{_CAPTURE_VALUE}"\n')
    return before + marker + head + found + line + newline + env + rest


def _assert_capture_environment(workflow):
    job_env = job_mapping(workflow, 'coverage', 'env') or {}
    for _start, _end, step in _consumer_steps(workflow):
        step_value = step_mapping_scalar(
            workflow, 'coverage', step, 'env', _CAPTURE_KEY)
        effective = (step_value if step_value is not None
                     else job_env.get(_CAPTURE_KEY))
        assert effective == _CAPTURE_VALUE, (step, effective)


def _assert_capture_refused(workflow):
    try:
        _assert_capture_environment(workflow)
    except AssertionError:
        return
    raise AssertionError('broken capture environment was accepted')


def test_coverage_job_publishes_distinct_python_and_javascript_metrics(tmp):
    """Dropping capture, labels, either XML, or either gate must fail."""
    del tmp
    workflow = _workflow()
    _assert_capture_environment(workflow)
    coverage = workflow.split('\n  coverage:\n', 1)[1]
    coverage = coverage.split('\n  diff-coverage:\n', 1)[0]
    assert '- name: Python coverage summary' in coverage, coverage
    assert '- name: Python coverage gate' in coverage, coverage
    assert '- name: JavaScript coverage summary' in coverage, coverage
    assert '- name: JavaScript coverage gate' in coverage, coverage
    assert "echo '### Python coverage'" in coverage, coverage
    assert "echo '### JavaScript coverage'" in coverage, coverage
    assert 'Python statement coverage and JavaScript physical code-line' \
        in coverage, coverage
    assert 'coverage are separate metrics; do not add them together.' \
        in coverage, coverage
    upload = coverage.partition('name: coverage-xml')[2]
    upload = upload.partition('if-no-files-found: error')[0]
    assert re.search(r'^\s+coverage\.xml\s*$', upload, re.MULTILINE), upload
    assert re.search(r'^\s+javascript-coverage\.xml\s*$',
                     upload, re.MULTILINE), upload


def _assert_coverage_artifact_flow(workflow):
    matrix_name, matrix_job = _matrix_job(workflow)
    measure = [step for step in matrix_job['steps']
               if 'coverage_suites.py' in step.get('run', '')]
    assert len(measure) == 1, measure
    assert measure[0]['run'] == (
        'python scripts/ci/coverage_suites.py --require-all')

    uploads = [step for step in matrix_job['steps']
               if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert len(uploads) == 2, uploads
    assert {step['with']['name'] for step in uploads} == {
        'coverage-data-${{ matrix.os }}'}, uploads
    javascript = [step for step in uploads
                  if '.node-v8-coverage' in step['with']['path']]
    assert len(javascript) == 1, uploads
    assert "matrix.os == 'ubuntu-latest'" in javascript[0]['if']
    python_only = [step for step in uploads if step not in javascript]
    assert len(python_only) == 1, uploads
    assert python_only[0]['with']['path'].strip() == '.coverage.*'
    assert "matrix.os != 'ubuntu-latest'" in python_only[0]['if']
    for step in uploads:
        assert step['with']['include-hidden-files'] == 'true', step
        assert step['with']['if-no-files-found'] == 'error', step

    job_env = matrix_job.get('env', {})
    assert _CAPTURE_KEY not in job_env, job_env
    capture = [step for step in matrix_job['steps']
               if _CAPTURE_KEY in step.get('run', '')]
    assert len(capture) == 1, capture
    assert capture[0]['if'] == "${{ matrix.os == 'ubuntu-latest' }}"
    assert capture[0]['run'] == (
        'echo "NODE_V8_COVERAGE=$GITHUB_WORKSPACE/.node-v8-coverage" '
        '>> "$GITHUB_ENV"')

    coverage = complete_job_mapping(workflow, 'coverage')
    assert matrix_name in coverage['needs']
    aggregate = complete_job_mapping(workflow, 'aggregate')
    assert matrix_name in aggregate['needs']
    body = workflow.split('\n  coverage:\n', 1)[1]
    body = body.split('\n  diff-coverage:\n', 1)[0]
    assert 'pattern: coverage-data-*' in body, body
    assert 'path: coverage-data' in body, body
    assert 'merge-multiple: false' in body, body
    assert 'python scripts/ci/coverage_combine.py coverage-data/' in body, body
    assert ('coverage-data/coverage-data-ubuntu-latest/.node-v8-coverage'
            in body), body
    assert 'coverage_suites.py' not in body, body


def test_coverage_combines_measurements_from_every_supported_os(tmp):
    """The final gates must consume one artifact from each OS leg."""
    del tmp
    _assert_coverage_artifact_flow(_workflow())


def test_matrix_job_identifier_is_not_a_contract(tmp):
    """A consistent private job rename must preserve the dataflow guard."""
    del tmp
    workflow = _workflow()
    name, _job = _matrix_job(workflow)
    renamed = workflow.replace(name, 'platform-coverage-measurements')
    assert renamed != workflow
    _assert_coverage_artifact_flow(renamed)


def test_subprocess_startup_program_starts_coverage(tmp):
    """The generated .pth program must start coverage in a real child."""
    _name, matrix_job = _matrix_job(_workflow())
    creators = [step for step in matrix_job['steps']
                if 'coverage-subprocess.pth' in step.get('run', '')]
    assert len(creators) == 1, creators
    command = creators[0]['run']
    program = _pth_program(command)
    workdir = Path(tmp) / 'tree'
    workdir.mkdir()
    env = _util.coverage_free_environment(os.environ)
    env['COVERAGE_PROCESS_START'] = str(ROOT / 'pyproject.toml')
    env['COVERAGE_FILE'] = str(Path(tmp) / '.coverage')
    probe = program + 'assert coverage.Coverage.current() is not None\n'
    result = subprocess.run(
        [sys.executable, '-c', probe], cwd=workdir,
        env=_util.child_coverage('keep', env, cwd=workdir),
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert command == _PTH_CREATION_COMMAND, command


def test_capture_can_be_scoped_to_every_consumer_step(tmp):
    """Equivalent per-step capture scope must remain valid."""
    del tmp
    step_scoped = _step_scoped_capture(_workflow())
    _assert_capture_environment(step_scoped)
    _assert_capture_environment(_step_scoped_capture(step_scoped))


def test_capture_fixture_uses_step_identity_not_display_name(tmp):
    """A runtime-neutral Measure rename must not constrain the fixture."""
    del tmp
    renamed = _rename_measure_step(_workflow())
    _assert_capture_environment(_step_scoped_capture(renamed))


def test_checkout_only_capture_scope_is_refused(tmp):
    """Checkout cannot pass its step environment to later consumers."""
    del tmp
    _assert_capture_refused(_checkout_scoped_capture(_workflow()))
    step_scoped = _step_scoped_capture(_workflow())
    _assert_capture_refused(_checkout_scoped_capture(step_scoped))


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='jscoverageworkflow_')


if __name__ == '__main__':
    raise SystemExit(main())
