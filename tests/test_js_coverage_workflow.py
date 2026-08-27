#!/usr/bin/env python3
"""Executable contracts for JavaScript coverage capture and publication."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import job_mapping, step_mapping_scalar  # noqa: E402


_CAPTURE_KEY = 'NODE_V8_COVERAGE'
_CAPTURE_VALUE = '${{ github.workspace }}/.node-v8-coverage'
_CONSUMER_STEPS = (
    'Measure',
    'JavaScript coverage summary',
    'JavaScript coverage gate',
    'Work out the raise this run justifies',
)


def _workflow():
    return (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')


def _without_job_capture(workflow):
    line = f'      {_CAPTURE_KEY}: "{_CAPTURE_VALUE}"\n'
    assert workflow.count(line) == 1, workflow
    return workflow.replace(line, '', 1)


def _step_scoped_capture(workflow):
    workflow = _without_job_capture(workflow)
    measure = ('      - name: Measure\n'
               '        id: measure\n'
               '        env:\n')
    assert workflow.count(measure) == 1, workflow
    workflow = workflow.replace(
        measure,
        measure + f'          {_CAPTURE_KEY}: "{_CAPTURE_VALUE}"\n',
        1)
    for name in _CONSUMER_STEPS[1:]:
        marker = f'      - name: {name}\n'
        assert workflow.count(marker) == 1, (name, workflow)
        env = (f'        env:\n'
               f'          {_CAPTURE_KEY}: "{_CAPTURE_VALUE}"\n')
        workflow = workflow.replace(marker, marker + env, 1)
    return workflow


def _checkout_scoped_capture(workflow):
    workflow = _without_job_capture(workflow)
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
    for step in _CONSUMER_STEPS:
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


def test_capture_can_be_scoped_to_every_consumer_step(tmp):
    """Equivalent per-step capture scope must remain valid."""
    del tmp
    _assert_capture_environment(_step_scoped_capture(_workflow()))


def test_checkout_only_capture_scope_is_refused(tmp):
    """Checkout cannot pass its step environment to later consumers."""
    del tmp
    _assert_capture_refused(_checkout_scoped_capture(_workflow()))


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='jscoverageworkflow_')


if __name__ == '__main__':
    raise SystemExit(main())
