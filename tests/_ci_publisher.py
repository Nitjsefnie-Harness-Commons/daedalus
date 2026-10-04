"""Scaffolding for the threshold-publisher controls in test_ci_ratchets.py.

Not a suite itself — run_tests.py only loads `test_*.py`. The publisher
half of that suite builds a throwaway checkout, stubs a coverage
measurement and runs the real workflow step against it; the scaffolding
for that is here so the suite holds controls rather than setup, and so
the derived-promotion control has room to arrive without pushing the
file over its size ceiling.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import _util
from _repo import ROOT
from _yamlsteps import complete_job_mapping
from _workflowrun import run_step

RATCHET_PATH = ROOT / 'scripts' / 'ci' / 'ratchet.py'
SIZE_PATH = ROOT / 'scripts' / 'ci' / 'size_baseline.py'
LINES_PATH = ROOT / 'scripts' / 'ci' / 'line_lengths.py'
JS_MODULE_PATH = ROOT / 'scripts' / 'ci' / 'js_module_coverage.py'
JS_COVERAGE_PATH = ROOT / 'scripts' / 'ci' / 'js_coverage.py'
JS_LINES_PATH = ROOT / 'scripts' / 'ci' / 'js_lines.py'
TESTS_LINES_PATH = ROOT / 'scripts' / 'ci' / 'tests_lines.py'

_WORKFLOW = ROOT / '.github' / 'workflows' / 'tests.yml'


def git(repo, *args):
    return subprocess.run(('git', '-C', str(repo)) + args, check=True,
                          capture_output=True, text=True,
                          env=_util.child_coverage('scrub'))


def seed_publisher_tree(repo, data):
    (repo / '.github').mkdir(parents=True)
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    (repo / 'tests').mkdir()
    document = json.dumps(data) if isinstance(data, dict) else data
    (repo / '.github' / 'ci-thresholds.json').write_text(
        document, encoding='utf-8')
    for path in (RATCHET_PATH, SIZE_PATH, LINES_PATH, JS_MODULE_PATH,
                 JS_COVERAGE_PATH, JS_LINES_PATH, TESTS_LINES_PATH,
                 ROOT / 'scripts' / 'ci' / 'thresholds.py'):
        shutil.copy2(path, repo / 'scripts' / 'ci' / path.name)
    (repo / 'tests' / 'test_mcp_server.py').write_text(
        'value = 1\n' * 1706, encoding='utf-8')
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'tests@example.invalid')
    git(repo, 'config', 'user.name', 'Tests')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')


def workflow_step(name):
    job = complete_job_mapping(
        _WORKFLOW.read_text(encoding='utf-8'), 'coverage')
    assert job is not None, 'the coverage job is not in tests.yml'
    return next(step for step in job['steps'] if step.get('name') == name)


def publisher_step():
    return workflow_step('Work out the raise this run justifies')


def publisher_commit_step():
    return workflow_step('Commit the raise')


def publisher_python(tmp, values, real_python=None):
    """A command shim that stubs only coverage measurement commands."""
    real = str(sys.executable) if real_python is None else real_python
    shim = Path(tmp) / 'bin'
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.mkdir()
    command = shim / 'python'
    command.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-m" ] && [ "$2" = "coverage" ]; then\n'
        '  printf "%s\\n" "$PYTHON_MEASURED"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$1" = "scripts/ci/js_coverage.py" ]; then\n'
        '  printf "%s\\n" "$JAVASCRIPT_MEASURED"\n'
        '  exit 0\n'
        'fi\n'
        'exec "$REAL_PYTHON" "$@"\n', encoding='utf-8')
    command.chmod(0o755)
    carried = dict(values)
    carried['REAL_PYTHON'] = real
    return shim, carried


def publisher_environment(tmp, values, writer_failure=False):
    shim, values = publisher_python(Path(tmp) / 'shim', values)
    environment = dict(os.environ)
    environment.update(values)
    environment['PATH'] = f'{shim}{os.pathsep}{environment["PATH"]}'
    output = Path(tmp) / 'github-output'
    summary = Path(tmp) / 'github-summary'
    output.touch()
    summary.touch()
    environment['GITHUB_OUTPUT'] = str(output)
    environment['GITHUB_STEP_SUMMARY'] = str(summary)
    if writer_failure:
        hook = Path(tmp) / 'sitecustomize.py'
        failure_flag = 'CI_THRESHOLDS_INJECT_REPLACE_FAILURE'
        hook.write_text(
            'import os\n'
            f'if os.environ.get("{failure_flag}") == "1":\n'
            '    def fail_replace(*args):\n'
            '        raise OSError("injected replace failure")\n'
            '    os.replace = fail_replace\n', encoding='utf-8')
        environment['PYTHONPATH'] = (
            f'{hook.parent}{os.pathsep}{environment.get("PYTHONPATH", "")}')
        environment['CI_THRESHOLDS_INJECT_REPLACE_FAILURE'] = '1'
    return environment, output, summary


def run_publisher_case(tmp, name, data, python_measured,
                       javascript_measured, *, raw=None,
                       writer_failure=False):
    repo = Path(tmp) / name
    repo.mkdir()
    seed_publisher_tree(repo, data)
    threshold_path = repo / '.github' / 'ci-thresholds.json'
    if raw is not None:
        threshold_path.write_bytes(raw)
        git(repo, 'add', str(threshold_path.relative_to(repo)))
        git(repo, 'commit', '-qm', 'malformed base')
    before = threshold_path.read_bytes()
    environment, output, summary = publisher_environment(
        Path(tmp) / f'{name}-environment', {
            'PYTHON_MEASURED': str(python_measured),
            'JAVASCRIPT_MEASURED': str(javascript_measured),
        }, writer_failure=writer_failure)
    result = run_step(repo, publisher_step(), environment,
                      workflow={}, job={})
    return repo, threshold_path, before, output, summary, result
