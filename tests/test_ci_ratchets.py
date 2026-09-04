#!/usr/bin/env python3
"""Executable contracts for coverage and module-size ratchets."""
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _workflowrun import run_step  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


THRESHOLDS_PATH = ROOT / '.github' / 'ci-thresholds.json'
RATCHET_PATH = ROOT / 'scripts' / 'ci' / 'ratchet.py'
SIZE_PATH = ROOT / 'scripts' / 'ci' / 'size_baseline.py'


def _thresholds():
    return _util.load(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                      'ratchet_thresholds')


def _ratchet():
    return _util.load(RATCHET_PATH, 'ratchet_contract')


def _size():
    return _util.load(SIZE_PATH, 'size_contract')


def _document(python=(80.0, 78.5), javascript=(35.5, 34.0)):
    return {
        'schema_version': 1,
        'coverage': {
            'python': {'measured': python[0], 'floor': python[1]},
            'javascript': {
                'measured': javascript[0], 'floor': javascript[1]},
        },
        'module_size_baseline': {
            'tests/test_mcp_server.py': 1706,
            'tests/test_cli.py': 1238,
        },
    }


def _copy_thresholds(tmp, data=None):
    thresholds = _thresholds()
    path = Path(tmp) / 'ci-thresholds.json'
    thresholds.write(path, _document() if data is None else data)
    return path


def _run_ratchet(tmp, language, measured, thresholds_path):
    return subprocess.run(
        [sys.executable, str(RATCHET_PATH), '--language', language,
         '--measured', str(measured), '--thresholds', str(thresholds_path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)


def test_promoted_modules_import_by_package(tmp):
    """The ratchet and shared reader import without workflow YAML coupling."""
    del tmp
    imported = subprocess.run(
        [sys.executable, '-c',
         'import scripts.ci.ratchet; import scripts.ci.size_baseline; '
         'import scripts.ci.thresholds'], cwd=str(ROOT), capture_output=True,
        text=True, timeout=60)
    assert imported.returncode == 0, (imported.stdout, imported.stderr)


def test_public_update_uses_recorded_measurement_high_water(tmp):
    """A gain above the recorded measurement raises the calibration."""
    del tmp
    ratchet = _ratchet()
    data = _document()
    for measured in (60.0, 78.5, 80.0):
        assert ratchet.update(data, Decimal(str(measured)), 'python') is None
    raised = ratchet.update(data, Decimal('80.1'), 'python')
    assert raised is not None
    assert raised['coverage']['python'] == {
        'measured': Decimal('80.1'), 'floor': Decimal('78.6')}
    assert raised['coverage']['javascript'] == {
        'measured': 35.5, 'floor': 34.0}
    assert raised['module_size_baseline'] == data['module_size_baseline']


def test_rerunning_a_raise_is_idempotent(tmp):
    """A recorded raise is not repeated when the same result is rerun."""
    del tmp
    ratchet = _ratchet()
    data = _document()
    candidate = ratchet.update(data, Decimal('81.6'), 'python')
    assert candidate is not None
    assert ratchet.update(candidate, Decimal('81.6'), 'python') is None


def test_measurement_above_recorded_value_raises(tmp):
    """A new high measurement updates the selected calibration."""
    del tmp
    ratchet = _ratchet()
    data = _document()
    raised = ratchet.update(data, Decimal('81.6'), 'python')
    assert raised['coverage']['python']['measured'] == Decimal('81.6')


def test_lower_measurements_never_lower_either_calibration(tmp):
    """Coverage dips are observations, not permission to weaken the gate."""
    del tmp
    ratchet = _ratchet()
    data = _document()
    for language in ('python', 'javascript'):
        assert ratchet.update(data, Decimal('0.0'), language) is None


def test_cli_updates_only_selected_language_and_rereads_latest_data(tmp):
    """Sequential invocations retain the other language and complete map."""
    thresholds = _thresholds()
    path = _copy_thresholds(tmp)
    first = _run_ratchet(tmp, 'python', '81.6', path)
    assert first.returncode == 0, (first.stdout, first.stderr)
    second = _run_ratchet(tmp, 'javascript', '37.1', path)
    assert second.returncode == 0, (second.stdout, second.stderr)
    data = thresholds.load(path)
    assert data['coverage']['python'] == {
        'measured': Decimal('81.6'), 'floor': Decimal('80.1')}
    assert data['coverage']['javascript'] == {
        'measured': Decimal('37.1'), 'floor': Decimal('35.6')}
    assert data['module_size_baseline'] == {
        'tests/test_cli.py': 1238,
        'tests/test_mcp_server.py': 1706,
    }


def test_cli_noop_does_not_rewrite_or_refresh_measurement(tmp):
    """No-op ratchets leave bytes untouched, including recorded measured."""
    path = _copy_thresholds(tmp)
    before = path.read_bytes()
    result = _run_ratchet(tmp, 'python', '80.0', path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert path.read_bytes() == before
    assert 'no raise' in result.stdout


def test_cli_rejects_the_retired_workflow_option(tmp):
    """Workflow source is no longer an input or a second state authority."""
    path = _copy_thresholds(tmp)
    result = subprocess.run(
        [sys.executable, str(RATCHET_PATH), '--language', 'python',
         '--measured', '81.6', '--workflow', str(path)], cwd=str(ROOT),
        capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert '--workflow' in result.stderr


def test_shipped_file_forcing_measurement_is_derived_and_capped(tmp):
    """The real-file regression derives forcing and never invents >100."""
    thresholds = _thresholds()
    source = thresholds.load(THRESHOLDS_PATH)
    recorded, _floor = thresholds.coverage(source, 'python')
    forcing = recorded + Decimal('5.0')
    forcing = min(forcing, Decimal('100.0'))
    path = Path(tmp) / 'shipped-copy.json'
    thresholds.write(path, source)
    result = _run_ratchet(tmp, 'python', forcing, path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    actual = thresholds.load(path)
    assert actual['coverage']['python']['measured'] == forcing


def test_workflow_gate_steps_consume_the_threshold_floor(tmp):
    """Decoded real gate steps flip when only the copied floor changes."""
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    job = complete_job_mapping(workflow, 'coverage')
    assert job is not None
    steps = {step['name']: step for step in job['steps']
             if step.get('name') in ('Python coverage gate',
                                     'JavaScript coverage gate')}
    assert set(steps) == {'Python coverage gate', 'JavaScript coverage gate'}

    work = Path(tmp) / 'tree'
    work.mkdir()
    (work / '.github').mkdir()
    (work / 'scripts' / 'ci').mkdir(parents=True)
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                 work / 'scripts' / 'ci' / 'thresholds.py')
    (work / '.github' / 'ci-thresholds.json').write_text(
        json.dumps(_document(python=(80.0, 78.5), javascript=(50.0, 48.5))),
        encoding='utf-8')
    (work / 'fixture.py').write_text(
        '\n'.join(f'line_{index} = {index}' for index in range(10)) + '\n',
        encoding='utf-8')
    python_bin = work / 'bin'
    python_bin.mkdir()
    shutil.copy2(sys.executable, python_bin / 'python')
    import coverage
    cov = coverage.Coverage(data_file=str(work / '.coverage'))
    cov.start()
    cov.stop()
    cov.erase()
    cov = coverage.Coverage(data_file=str(work / '.coverage'))
    cov.get_data().add_lines({str(work / 'fixture.py'): set(range(1, 9))})
    cov.save()
    environment = dict(os.environ)
    environment['PATH'] = f'{python_bin}{os.pathsep}{environment["PATH"]}'
    good = run_step(work, steps['Python coverage gate'], environment,
                    workflow={}, job={})
    assert good.returncode == 0, (good.stdout, good.stderr)
    document = _document(python=(82.0, 80.5), javascript=(50.0, 49.0))
    (work / '.github' / 'ci-thresholds.json').write_text(
        json.dumps(document), encoding='utf-8')
    bad = run_step(work, steps['Python coverage gate'], environment,
                   workflow={}, job={})
    assert bad.returncode != 0, (bad.stdout, bad.stderr)


def _git(repo, *args):
    return subprocess.run(('git', '-C', str(repo)) + args, check=True,
                          capture_output=True, text=True, timeout=60)


def _seed_publisher_tree(repo, data):
    (repo / '.github').mkdir(parents=True)
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    (repo / 'tests').mkdir()
    _thresholds().write(repo / '.github' / 'ci-thresholds.json', data)
    for path in (RATCHET_PATH, SIZE_PATH,
                 ROOT / 'scripts' / 'ci' / 'thresholds.py'):
        shutil.copy2(path, repo / 'scripts' / 'ci' / path.name)
    (repo / 'tests' / 'test_mcp_server.py').write_text(
        'value = 1\n' * 1706, encoding='utf-8')
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'base')


def _publisher_step():
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    job = complete_job_mapping(workflow, 'coverage')
    return next(step for step in job['steps']
                if step.get('name') == 'Work out the raise this run justifies')


def _publisher_python(tmp, values):
    """Create a command shim that stubs only coverage measurement commands."""
    real = Path(sys.executable)
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
        f'exec {real} "$@"\n', encoding='utf-8')
    command.chmod(0o755)
    return shim, values


def test_real_publisher_step_writes_one_valid_file_and_reports_changed(tmp):
    """The decoded producer executes real ratchets and stages one JSON path."""
    repo = Path(tmp) / 'publisher'
    repo.mkdir()
    _seed_publisher_tree(repo, _document())
    shim, values = _publisher_python(Path(tmp) / 'shim', {
        'PYTHON_MEASURED': '81.6', 'JAVASCRIPT_MEASURED': '35.5'})
    env = dict(os.environ)
    env.update(values)
    env['PATH'] = f'{shim}{os.pathsep}{env["PATH"]}'
    output = Path(tmp) / 'github-output'
    summary = Path(tmp) / 'github-summary'
    output.touch()
    summary.touch()
    env['GITHUB_OUTPUT'] = str(output)
    env['GITHUB_STEP_SUMMARY'] = str(summary)
    done = run_step(repo, _publisher_step(), env, workflow={}, job={})
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'changed=true' in output.read_text(encoding='utf-8')
    assert _git(repo, 'diff', '--name-only').stdout.splitlines() == [
        '.github/ci-thresholds.json']
    checked = subprocess.run(
        [sys.executable, str(repo / 'scripts' / 'ci' / 'thresholds.py'),
         '--check', '--thresholds',
         str(repo / '.github' / 'ci-thresholds.json')],
        cwd=repo, capture_output=True, text=True, timeout=60)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)


def test_real_publisher_step_noop_reports_unchanged(tmp):
    """A run below both high waters creates no publishable diff."""
    repo = Path(tmp) / 'publisher-noop'
    repo.mkdir()
    _seed_publisher_tree(repo, _document())
    shim, values = _publisher_python(Path(tmp) / 'shim-noop', {
        'PYTHON_MEASURED': '80.0', 'JAVASCRIPT_MEASURED': '35.5'})
    env = dict(os.environ)
    env.update(values)
    env['PATH'] = f'{shim}{os.pathsep}{env["PATH"]}'
    output = Path(tmp) / 'github-output-noop'
    summary = Path(tmp) / 'github-summary-noop'
    output.touch()
    summary.touch()
    env['GITHUB_OUTPUT'] = str(output)
    env['GITHUB_STEP_SUMMARY'] = str(summary)
    done = run_step(repo, _publisher_step(), env, workflow={}, job={})
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'changed=false' in output.read_text(encoding='utf-8')
    assert _git(repo, 'diff', '--name-only').stdout == ''


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciratchets_')


if __name__ == '__main__':
    raise SystemExit(main())
