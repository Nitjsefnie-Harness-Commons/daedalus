#!/usr/bin/env python3
"""Executable contracts for coverage and module-size ratchets."""
import contextlib, io
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ci_imports import (  # noqa: E402
    package_closure, package_edges, package_selectors)
from _ci_publisher import (  # noqa: E402
    git as _git, publisher_commit_step as _publisher_commit_step,
    publisher_python as _publisher_python,
    publisher_step as _publisher_step,
    run_publisher_case as _run_publisher_case,
    seed_publisher_tree as _seed_publisher_tree)
from _ghexpr import evaluate_if  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wffixtures import _value_error  # noqa: E402
from _workflowrun import run_step  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

sys.path[:0] = [str(ROOT), str(ROOT / 'scripts' / 'ci')]


THRESHOLDS_PATH = ROOT / '.github' / 'ci-thresholds.json'
RATCHET_PATH = ROOT / 'scripts' / 'ci' / 'ratchet.py'


def _thresholds():
    return _util.load(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                      'ratchet_thresholds')


def _ratchet():
    return _util.load(RATCHET_PATH, 'ratchet_contract')


def _ratchet_document(python=(80.0, 78.5), javascript=(35.5, 34.0)):
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
        'long_line_baseline': {},
        'type_error_baseline': {},
        'js_coverage_baseline': {},
        'tests_line_baseline': 1706,
    }


def _copy_thresholds(tmp, data=None):
    thresholds = _thresholds()
    path = Path(tmp) / 'ci-thresholds.json'
    thresholds.write(path, _ratchet_document() if data is None else data)
    return path


def _run_ratchet(tmp, language, measured, thresholds_path):
    return subprocess.run(
        [sys.executable, str(RATCHET_PATH), '--language', language,
         '--measured', str(measured), '--thresholds', str(thresholds_path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)


# Package-import entry points, and nothing else: a name earns a row by
# selecting on `__package__` itself or by importing down such an arm, which
# is what makes that arm reachable at all. A name that reaches nothing is a
# dead row, and the derivation below cannot see one.
PROMOTED_MODULES = (
    'scripts.ci.coverage_suites',
    'scripts.ci.js_module_coverage',
    'scripts.ci.line_lengths',
    'scripts.ci.ratchet',
    'scripts.ci.size_baseline',
    'scripts.ci.thresholds',
    'scripts.ci.tests_lines',
    'scripts.ci.type_error_baseline',
    'scripts.ci.workflow_yaml',
)


def test_promoted_modules_import_by_package(tmp):
    del tmp
    command = '; '.join(f'import {name}' for name in PROMOTED_MODULES)
    imported = subprocess.run(
        [sys.executable, '-c', command], cwd=str(ROOT), capture_output=True,
        text=True, timeout=60)
    assert imported.returncode == 0, (imported.stdout, imported.stderr)
    assert all(__import__(name, fromlist=['*'])
               for name in PROMOTED_MODULES)


def test_every_package_selector_is_reachable_by_a_package_import(_tmp):
    """A `__package__:` half nothing reaches is the half CI never runs.

    Derived from the tree, so a promotion that is deleted takes this with
    it. The requirement is REACHABILITY, not membership: a selector is
    satisfied by being promoted itself or by being imported by something
    that is, which is why `yamlanchor` needs no row of its own.
    """
    selectors = package_selectors(ROOT)
    assert selectors, 'nothing selects on __package__; this cannot be true'
    reached = package_closure(PROMOTED_MODULES, package_edges(ROOT))
    orphaned = sorted(set(selectors) - reached)
    assert not orphaned, (
        f'these select an import on __package__ and no package import '
        f'reaches them, so that half runs nowhere: {orphaned}')


def test_measurement_rejects_bool_bad_numeric_and_nonfinite(_tmp):
    class BadFloat(float):
        def __str__(self):
            return 'not-a-number'
    ratchet = _ratchet()
    cases = (
        (True, 'measured must be a finite JSON number'),
        (BadFloat(80.0), 'measured must be a finite JSON number'),
        (Decimal('NaN'), 'measured must be finite'))
    for value, message in cases:
        assert _value_error(
            lambda value=value: ratchet._measurement(value)) == message


def test_floor_for_and_update_refuse_unknown_language_before_mutation(_tmp):
    ratchet = _ratchet()
    assert ratchet.floor_for(Decimal('81.0')) == Decimal('79.5')
    data = {'not': 'a threshold document'}
    assert _value_error(
        lambda: ratchet.update(data, Decimal('81.6'), 'ruby')) \
        == 'unknown coverage language: ruby'
    assert data == {'not': 'a threshold document'}


def test_main_reports_invalid_measurement_without_writing(tmp):
    before = (path := _copy_thresholds(tmp)).read_bytes()
    ratchet = _ratchet()
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        status = ratchet.main(['--language', 'python', '--measured', 'nan',
                               '--thresholds', str(path)])
    assert status == 1
    assert stderr.getvalue() == 'measured must be finite\n'
    assert path.read_bytes() == before


def test_public_update_uses_recorded_measurement_high_water(tmp):
    ratchet = _ratchet()
    data = _ratchet_document()
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
    ratchet = _ratchet()
    data = _ratchet_document()
    candidate = ratchet.update(data, Decimal('81.6'), 'python')
    assert candidate is not None
    assert ratchet.update(candidate, Decimal('81.6'), 'python') is None


def test_measurement_above_recorded_value_raises(tmp):
    ratchet = _ratchet()
    data = _ratchet_document()
    raised = ratchet.update(data, Decimal('81.6'), 'python')
    assert raised['coverage']['python']['measured'] == Decimal('81.6')


def test_lower_measurements_never_lower_either_calibration(tmp):
    ratchet = _ratchet()
    data = _ratchet_document()
    for language in ('python', 'javascript'):
        assert ratchet.update(data, Decimal('0.0'), language) is None


def test_cli_updates_only_selected_language_and_rereads_latest_data(tmp):
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


def test_main_noop_then_raise_preserves_and_updates_thresholds(tmp):
    path = _copy_thresholds(tmp)
    before = path.read_bytes()
    ratchet = _ratchet()
    for measured, expected in (('80.1', 'no raise'), ('81.6', 'raised')):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = ratchet.main(['--language', 'python', '--measured',
                                   measured, '--thresholds', str(path)])
        assert status == 0
        assert expected in output.getvalue()
        if measured == '80.1':
            assert path.read_bytes() == before
    assert _thresholds().load(path)['coverage']['python']['floor'] \
        == Decimal('80.1')


def test_cli_rejects_the_retired_workflow_option(tmp):
    path = _copy_thresholds(tmp)
    result = subprocess.run(
        [sys.executable, str(RATCHET_PATH), '--language', 'python',
         '--measured', '81.6', '--workflow', str(path)], cwd=str(ROOT),
        capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert '--workflow' in result.stderr


def test_shipped_file_forcing_measurement_is_derived_and_capped(tmp):
    thresholds = _thresholds()
    source = thresholds.load(THRESHOLDS_PATH)
    ratchet = _ratchet()
    path = Path(tmp) / 'shipped-copy.json'
    for recorded in map(Decimal, ('94.4', '98.4', '98.5', '99.0')):
        source['coverage']['python'] = {
            'measured': recorded,
            'floor': recorded - thresholds.CALIBRATION_GAP}
        thresholds.write(path, source)
        forcing = min(recorded + ratchet.RAISE_HYSTERESIS + Decimal('0.1'),
                      Decimal('100.0'))
        result = _run_ratchet(tmp, 'python', forcing, path)
        assert result.returncode == 0, (result.stdout, result.stderr)
        actual = thresholds.load(path)
        if forcing - recorded > ratchet.RAISE_HYSTERESIS:
            assert actual['coverage']['python']['measured'] == forcing
            assert actual['coverage']['python']['floor'] \
                == forcing - thresholds.CALIBRATION_GAP
            assert 'raised' in result.stdout
        else:
            assert actual == source
            assert 'no raise' in result.stdout


def test_workflow_gate_steps_consume_the_threshold_floor(tmp):
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
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'reseed.py',
                 work / 'scripts' / 'ci' / 'reseed.py')
    (work / '.github' / 'ci-thresholds.json').write_text(
        json.dumps(_ratchet_document(python=(80.0, 78.5),
                                     javascript=(50.0, 48.5))),
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
    document = _ratchet_document(python=(82.0, 80.5), javascript=(50.0, 48.5))
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
    data = _ratchet_document()
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


def test_real_publisher_step_tightens_the_suite_tree_budget(tmp):
    """The no-op replays seed the budget at the measured number, so a tighten
    that wrote a wrong value on a real drop passes every one of them."""
    data = _ratchet_document()
    data['tests_line_baseline'] = 1707
    repo, path, _before, output, _summary, done = _run_publisher_case(
        tmp, 'publisher-tests-lines', data, '80.0', '35.5')
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'changed=true' in output.read_text(encoding='utf-8')
    after = _thresholds().load(path)
    assert after['tests_line_baseline'] == 1706
    assert after['coverage'] == data['coverage']
    assert after['module_size_baseline'] == data['module_size_baseline']
    assert _git(repo, 'diff', '--name-only').stdout.splitlines() == [
        '.github/ci-thresholds.json']


def test_real_publisher_step_combines_coverage_and_size_changes(tmp):
    data = _ratchet_document()
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
    raw = b'{"schema_version": 1}\r\n'
    result = _run_publisher_case(
        tmp, 'publisher-malformed', _ratchet_document(), '81.6', '35.5',
        raw=raw)
    repo, path, before, output, _summary, done = result
    assert done.returncode != 0
    assert 'changed=' not in output.read_text(encoding='utf-8')
    assert before.endswith(b'\r\n')
    assert path.read_bytes() == before == raw
    assert _git(repo, 'diff', '--name-only').stdout == ''


def test_real_publisher_step_writer_failure_is_fail_closed(tmp):
    result = _run_publisher_case(
        tmp, 'publisher-writer-failure', _ratchet_document(), '81.6', '35.5',
        writer_failure=True)
    repo, path, before, output, _summary, done = result
    assert done.returncode != 0
    assert 'injected replace failure' in done.stderr
    assert output.read_text(encoding='utf-8') == ''
    assert path.read_bytes() == before
    assert not list(path.parent.glob(f'.{path.name}.*.tmp'))
    assert _git(repo, 'diff', '--name-only').stdout == ''


def test_real_publisher_step_changed_summary_and_noop_outputs_are_exact(tmp):
    changed = _run_publisher_case(
        tmp, 'publisher-coverage', _ratchet_document(), '81.6', '35.5')
    _repo, _path, _before, changed_output, changed_summary, done = changed
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert changed_output.read_text(encoding='utf-8').splitlines() == [
        'changed=true', 'python_measured=81.6', 'javascript_measured=35.5']
    assert '### Recorded by this run' in changed_summary.read_text(
        encoding='utf-8')
    noop = _run_publisher_case(
        tmp, 'publisher-noop-matrix', _ratchet_document(), '80.0', '35.5')
    _repo, _path, _before, noop_output, noop_summary, done = noop
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert noop_output.read_text(encoding='utf-8') == 'changed=false\n'
    assert ('no raise; no module shrank, no file lost an over-limit '
            'line, no module lost an uncovered JavaScript line and '
            'the suite tree lost no line.'
            ) in noop_summary.read_text(encoding='utf-8')


def test_publisher_ratchet_and_commit_conditions_keep_authority_boundary(tmp):
    ratchet = _publisher_step()
    commit = _publisher_commit_step()
    assert "github.event_name == 'push'" in ratchet['if']
    assert "github.ref == 'refs/heads/main'" in ratchet['if']
    assert 'steps.measure.conclusion == \'success\'' in ratchet['if']
    # The step calls ONE shared script rather than repeating the push, so
    # what it still owns is the two arguments: which file the commit stages
    # and what the commit says it did. The push itself — the deploy key, the
    # pinned host key, HEAD:main, and telling a refusal from a concurrent
    # push — lives in that script and is controlled by executing it.
    assert 'scripts/ci/ratchet_push.py' in commit['run'], commit['run']
    assert '.github/ci-thresholds.json' in commit['run'], commit['run']
    assert "'ci: update CI ratchets'" in commit['run'], commit['run']
    push = (ROOT / 'scripts' / 'ci' / 'ratchet_push.py').read_text(
        encoding='utf-8')
    assert 'HEAD:main' in push
    assert 'GIT_SSH_COMMAND' in push
    assert '--force' not in push, push
    for event, ref, measured, status, expected in (
            ('push', 'refs/heads/main', 'success',
             {'success': True, 'failure': False, 'cancelled': False}, True),
            ('push', 'refs/heads/main', 'success',
             {'success': False, 'failure': True, 'cancelled': False}, True),
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
            ('true', 'key',
             {'success': False, 'failure': True, 'cancelled': False}, True),
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
    ratchet_failure = {
        'github': {'event_name': 'push', 'ref': 'refs/heads/main'},
        'steps': {'measure': {'conclusion': 'success'}},
        'status': {'success': False, 'failure': True, 'cancelled': False},
    }
    assert evaluate_if(ratchet, ratchet_failure) is True
    assert evaluate_if(
        ratchet.replace('!cancelled()', 'success()'),
        ratchet_failure) is False
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
    commit_failure = {
        'steps': {'ratchet': {'outputs': {'changed': 'true'}}},
        'env': {'RATCHET_SSH_KEY': 'key'},
        'status': {'success': False, 'failure': True, 'cancelled': False},
    }
    assert evaluate_if(commit['if'], commit_failure) is True
    assert evaluate_if(
        commit['if'].replace('!cancelled()', 'success()'),
        commit_failure) is False


def test_real_publisher_step_writes_one_valid_file_and_reports_changed(tmp):
    repo = Path(tmp) / 'publisher'
    repo.mkdir()
    _seed_publisher_tree(repo, _ratchet_document())
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
    assert 'thresholds valid' in done.stdout, done.stdout
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
    assert after['module_size_baseline'] == _ratchet_document()[
        'module_size_baseline']


def test_real_publisher_step_noop_reports_unchanged(tmp):
    repo = Path(tmp) / 'publisher-noop'
    repo.mkdir()
    _seed_publisher_tree(repo, _ratchet_document())
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
    # The noop holds at equality and at a budget below the seeded tree alike,
    # so only the check itself says which one the fixture is sitting at.
    budget = subprocess.run(
        [sys.executable, str(repo / 'scripts' / 'ci' / 'tests_lines.py'),
         '--thresholds', str(repo / '.github' / 'ci-thresholds.json')],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
        env=_util.child_coverage('scrub'))
    assert budget.returncode == 0, (budget.stdout, budget.stderr)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciratchets_')


if __name__ == '__main__':
    raise SystemExit(main())
