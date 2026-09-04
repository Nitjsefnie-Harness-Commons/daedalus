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
from _ghexpr import evaluate_if  # noqa: E402
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
    command = '; '.join((
        'import scripts.ci.ratchet',
        'import scripts.ci.size_baseline',
        'import scripts.ci.thresholds',
    ))
    imported = subprocess.run(
        [sys.executable, '-c', command], cwd=str(ROOT), capture_output=True,
        text=True, timeout=60)
    assert imported.returncode == 0, (imported.stdout, imported.stderr)


def test_public_update_uses_recorded_measurement_high_water(tmp):
    """Only a gain beyond the measured high water raises the calibration."""
    del tmp
    ratchet = _ratchet()
    data = _document()
    for measured in (60.0, 78.5, 80.0, 80.1, 81.5):
        assert ratchet.update(data, Decimal(str(measured)), 'python') is None
    raised = ratchet.update(data, Decimal('81.6'), 'python')
    assert raised is not None
    assert raised['coverage']['python'] == {
        'measured': Decimal('81.6'), 'floor': Decimal('80.1')}
    assert raised['coverage']['javascript'] == {
        'measured': 35.5, 'floor': 34.0}
    assert raised['module_size_baseline'] == data['module_size_baseline']
    assert ratchet.update(raised, Decimal('81.6'), 'python') is None
    assert ratchet.update(raised, Decimal('81.7'), 'python') is None


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
    result = _run_ratchet(tmp, 'python', '80.1', path)
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
    forcing = min(
        recorded + _ratchet().RAISE_HYSTERESIS + Decimal('0.1'),
        Decimal('100.0'))
    path = Path(tmp) / 'shipped-copy.json'
    thresholds.write(path, source)
    result = _run_ratchet(tmp, 'python', forcing, path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    actual = thresholds.load(path)
    assert actual['coverage']['python']['measured'] == forcing


def test_issue_581_bounded_measurement_is_a_valid_noop(tmp):
    """A 100% shipped measurement cannot raise a 99.0% recorded high water."""
    del tmp
    ratchet = _ratchet()
    data = _document(python=(99.0, 97.5))
    assert ratchet.update(data, Decimal('100.0'), 'python') is None


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
    document = _document(python=(82.0, 80.5), javascript=(50.0, 48.5))
    (work / '.github' / 'ci-thresholds.json').write_text(
        json.dumps(document), encoding='utf-8')
    checked = subprocess.run(
        [sys.executable, str(work / 'scripts' / 'ci' / 'thresholds.py'),
         '--check', '--thresholds',
         str(work / '.github' / 'ci-thresholds.json')],
        cwd=work, capture_output=True, text=True, timeout=60,
        env=_util.child_coverage('scrub'))
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    bad = run_step(work, steps['Python coverage gate'], environment,
                   workflow={}, job={})
    assert bad.returncode != 0, (bad.stdout, bad.stderr)
    assert 'Coverage failure' in bad.stdout + bad.stderr
    assert 'calibration gap' not in bad.stdout + bad.stderr


def _git(repo, *args):
    return subprocess.run(('git', '-C', str(repo)) + args, check=True,
                          capture_output=True, text=True, timeout=60,
                          env=_util.child_coverage('scrub'))


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


def _publisher_commit_step():
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    job = complete_job_mapping(workflow, 'coverage')
    return next(step for step in job['steps']
                if step.get('name') == 'Commit the raise')


def _publisher_python(tmp, values, real_python=None):
    """Create a command shim that stubs only coverage measurement commands."""
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


def _publisher_environment(tmp, values, writer_failure=False):
    shim, values = _publisher_python(Path(tmp) / 'shim', values)
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


def _run_publisher_case(tmp, name, data, python_measured, javascript_measured,
                        *, raw=None, writer_failure=False):
    repo = Path(tmp) / name
    repo.mkdir()
    _seed_publisher_tree(repo, data)
    threshold_path = repo / '.github' / 'ci-thresholds.json'
    if raw is not None:
        threshold_path.write_text(raw, encoding='utf-8')
        _git(repo, 'add', str(threshold_path.relative_to(repo)))
        _git(repo, 'commit', '-qm', 'malformed base')
    before = threshold_path.read_bytes()
    environment, output, summary = _publisher_environment(
        Path(tmp) / f'{name}-environment', {
            'PYTHON_MEASURED': str(python_measured),
            'JAVASCRIPT_MEASURED': str(javascript_measured),
        }, writer_failure=writer_failure)
    result = run_step(repo, _publisher_step(), environment,
                      workflow={}, job={})
    return repo, threshold_path, before, output, summary, result


def test_publisher_python_carries_posix_and_windows_paths_without_embedding(
        tmp):
    """The shim must not let Git Bash reinterpret a native path."""
    windows_path = 'C:' + '\\' + 'hostedtoolcache' + '\\' + 'Python' \
        + '\\' + '3.14' + '\\' + 'python.exe'
    shim, values = _publisher_python(
        Path(tmp) / 'windows-shim', {}, real_python=windows_path)
    script = (shim / 'python').read_text(encoding='utf-8')
    assert 'exec "$REAL_PYTHON" "$@"' in script
    assert windows_path not in script
    assert values['REAL_PYTHON'] == windows_path
    native_shim, native_values = _publisher_python(
        Path(tmp) / 'native-shim', {})
    native_environment = dict(os.environ)
    native_environment.update(native_values)
    native = subprocess.run(
        [_util.workflow_bash(), str(native_shim / 'python'), '-c',
         'print("shim works")'],
        env=_util.child_coverage('scrub', native_environment),
        capture_output=True, text=True, timeout=60)
    assert native.returncode == 0, (native.stdout, native.stderr)
    assert native.stdout == 'shim works\n'


def test_real_publisher_step_size_only_preserves_calibrations(tmp):
    """The decoded producer publishes a size shrink without coverage drift."""
    data = _document()
    data['module_size_baseline']['tests/test_mcp_server.py'] = 1707
    result = _run_publisher_case(
        tmp, 'publisher-size', data, '80.0', '35.5')
    repo, path, before, output, summary, done = result
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'changed=true' in output.read_text(encoding='utf-8')
    assert '### Recorded by this run' in summary.read_text(encoding='utf-8')
    after = _thresholds().load(path)
    assert after['coverage'] == data['coverage']
    assert after['module_size_baseline']['tests/test_mcp_server.py'] == 1706
    assert path.read_bytes() != before
    assert _git(repo, 'diff', '--name-only').stdout.splitlines() == [
        '.github/ci-thresholds.json']
    assert after['module_size_baseline']['tests/test_cli.py'] == 1238


def test_real_publisher_step_combines_coverage_and_size_changes(tmp):
    """The decoded producer retains both independent updates in one file."""
    data = _document()
    data['module_size_baseline']['tests/test_mcp_server.py'] = 1707
    result = _run_publisher_case(
        tmp, 'publisher-combined', data, '81.6', '35.5')
    repo, path, _before, output, _summary, done = result
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'changed=true' in output.read_text(encoding='utf-8')
    after = _thresholds().load(path)
    assert after['coverage']['python'] == {
        'measured': Decimal('81.6'), 'floor': Decimal('80.1')}
    assert after['coverage']['javascript'] == data['coverage']['javascript']
    assert after['module_size_baseline']['tests/test_mcp_server.py'] == 1706
    assert after['module_size_baseline']['tests/test_cli.py'] == 1238
    assert _git(repo, 'diff', '--name-only').stdout.splitlines() == [
        '.github/ci-thresholds.json']


def test_real_publisher_step_rejects_malformed_data_without_publishing(tmp):
    """A malformed document fails closed before any output or diff."""
    raw = '{"schema_version": 1}\n'
    result = _run_publisher_case(
        tmp, 'publisher-malformed', _document(), '81.6', '35.5', raw=raw)
    repo, path, before, output, _summary, done = result
    assert done.returncode != 0
    assert 'changed=' not in output.read_text(encoding='utf-8')
    assert path.read_bytes() == before == raw.encode('utf-8')
    assert _git(repo, 'diff', '--name-only').stdout == ''


def test_real_publisher_step_writer_failure_is_fail_closed(tmp):
    """A real writer boundary failure leaves bytes and staging unchanged."""
    result = _run_publisher_case(
        tmp, 'publisher-writer-failure', _document(), '81.6', '35.5',
        writer_failure=True)
    repo, path, before, output, _summary, done = result
    assert done.returncode != 0
    assert 'injected replace failure' in done.stderr
    assert output.read_text(encoding='utf-8') == ''
    assert path.read_bytes() == before
    assert not list(path.parent.glob(f'.{path.name}.*.tmp'))
    assert _git(repo, 'diff', '--name-only').stdout == ''


def test_real_publisher_step_changed_summary_and_noop_outputs_are_exact(tmp):
    """Changed and no-op decoded runs expose distinct output contracts."""
    changed = _run_publisher_case(
        tmp, 'publisher-coverage', _document(), '81.6', '35.5')
    _repo, _path, _before, changed_output, changed_summary, done = changed
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert changed_output.read_text(encoding='utf-8').splitlines() == [
        'changed=true', 'python_measured=81.6', 'javascript_measured=35.5']
    assert '### Recorded by this run' in changed_summary.read_text(
        encoding='utf-8')

    noop = _run_publisher_case(
        tmp, 'publisher-noop-matrix', _document(), '80.0', '35.5')
    _repo, _path, _before, noop_output, noop_summary, done = noop
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert noop_output.read_text(encoding='utf-8') == 'changed=false\n'
    assert 'no raise; no module shrank.' in noop_summary.read_text(
        encoding='utf-8')


def test_publisher_ratchet_and_commit_conditions_keep_authority_boundary(tmp):
    """Actual publisher conditions require main, change, green, and secret."""
    del tmp
    ratchet = _publisher_step()
    commit = _publisher_commit_step()
    assert "github.event_name == 'push'" in ratchet['if']
    assert "github.ref == 'refs/heads/main'" in ratchet['if']
    assert 'steps.measure.conclusion == \'success\'' in ratchet['if']
    assert 'git add .github/ci-thresholds.json' in commit['run']
    assert "git commit -m 'ci: update CI ratchets'" in commit['run']
    assert 'HEAD:main' in commit['run']
    assert 'GIT_SSH_COMMAND' in commit['run']
    assert '--force' not in commit['run']

    for event, ref, measured, status, expected in (
            ('push', 'refs/heads/main', 'success',
             {'success': True, 'failure': False, 'cancelled': False}, True),
            ('pull_request', 'refs/heads/main', 'success',
             {'success': True, 'failure': False, 'cancelled': False}, False),
            ('push', 'refs/heads/other', 'success',
             {'success': True, 'failure': False, 'cancelled': False}, False),
            ('push', 'refs/heads/main', 'failure',
             {'success': False, 'failure': True, 'cancelled': False}, False),
            ('push', 'refs/heads/main', 'success',
             {'success': True, 'failure': False, 'cancelled': True}, False),
    ):
        context = {
            'github': {'event_name': event, 'ref': ref},
            'steps': {'measure': {'conclusion': measured}},
            'status': status,
        }
        assert evaluate_if(ratchet['if'], context) is expected, context

    for changed, secret, status, expected in (
            ('true', 'key',
             {'success': True, 'failure': False, 'cancelled': False}, True),
            ('false', 'key',
             {'success': True, 'failure': False, 'cancelled': False}, False),
            ('true', '',
             {'success': True, 'failure': False, 'cancelled': False}, False),
            ('', 'key',
             {'success': False, 'failure': True, 'cancelled': False}, False),
            ('true', 'key',
             {'success': True, 'failure': False, 'cancelled': True}, False),
    ):
        context = {
            'steps': {'ratchet': {'outputs': {'changed': changed}}},
            'env': {'RATCHET_SSH_KEY': secret},
            'status': status,
        }
        assert evaluate_if(commit['if'], context) is expected, context


def test_publisher_condition_mutations_are_rejected(tmp):
    """Removing any authority or change guard flips a recorded control."""
    del tmp
    ratchet = _publisher_step()['if']
    commit = _publisher_commit_step()
    main_context = {
        'github': {'event_name': 'pull_request', 'ref': 'refs/heads/main'},
        'steps': {'measure': {'conclusion': 'success'}},
        'status': {'success': True, 'failure': False, 'cancelled': False},
    }
    ratchet_mutations = (
        ("github.event_name == 'push'", main_context),
        ("github.ref == 'refs/heads/main'", {
            **main_context,
            'github': {'event_name': 'push', 'ref': 'refs/heads/other'},
        }),
        ("steps.measure.conclusion == 'success'", {
            **main_context,
            'github': {'event_name': 'push', 'ref': 'refs/heads/main'},
            'steps': {'measure': {'conclusion': 'failure'}},
        }),
    )
    for removed, context in ratchet_mutations:
        mutated = ratchet.replace(removed, 'true')
        assert evaluate_if(mutated, context) is True
        assert evaluate_if(ratchet, context) is False

    cancelled = {
        'steps': {'ratchet': {'outputs': {'changed': 'true'}}},
        'env': {'RATCHET_SSH_KEY': 'key'},
        'status': {'success': True, 'failure': False, 'cancelled': True},
    }
    changed_false = dict(cancelled)
    changed_false['steps'] = {
        'ratchet': {'outputs': {'changed': 'false'}}}
    changed_false['status'] = {
        'success': True, 'failure': False, 'cancelled': False}
    changed_false['env'] = {'RATCHET_SSH_KEY': 'key'}
    missing_secret = dict(cancelled)
    missing_secret['env'] = {'RATCHET_SSH_KEY': ''}
    missing_secret['status'] = {
        'success': True, 'failure': False, 'cancelled': False}
    assert evaluate_if(commit['if'], cancelled) is False
    assert evaluate_if(commit['if'].replace('!cancelled() && ', ''),
                       cancelled) is True
    assert evaluate_if(commit['if'], changed_false) is False
    assert evaluate_if(
        commit['if'].replace("steps.ratchet.outputs.changed == 'true' && ",
                             ''), changed_false) is True
    assert evaluate_if(commit['if'], missing_secret) is False
    assert evaluate_if(
        commit['if'].replace("env.RATCHET_SSH_KEY != ''", 'true'),
        missing_secret) is True


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
        cwd=repo, capture_output=True, text=True, timeout=60,
        env=_util.child_coverage('scrub'))
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    after = _thresholds().load(repo / '.github' / 'ci-thresholds.json')
    assert after['coverage']['javascript'] == {
        'measured': Decimal('35.5'), 'floor': Decimal('34.0')}
    assert after['module_size_baseline'] == _document()[
        'module_size_baseline']


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
