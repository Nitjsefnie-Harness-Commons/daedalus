#!/usr/bin/env python3
"""Contracts for the shared CI threshold document reader and writer."""
import json
import os
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


SCRIPT = ROOT / 'scripts' / 'ci' / 'thresholds.py'
DATA_PATH = ROOT / '.github' / 'ci-thresholds.json'


def _thresholds():
    return _util.load(SCRIPT, 'thresholds_contract')


def _valid():
    return {
        'schema_version': 1,
        'coverage': {
            'python': {'measured': 94.4, 'floor': 92.9},
            'javascript': {'measured': 35.5, 'floor': 34.0},
        },
        'module_size_baseline': {
            'tests/_pyroute.py': 738,
            'tests/_pyroute_state.py': 780,
            'tests/test_bridge_results.py': 1343,
            'tests/test_cli.py': 1238,
            'tests/test_mcp_server.py': 1706,
            'tests/test_tab_routing.py': 793,
        },
    }


def _write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=True), encoding='utf-8')


def _check(path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--check', '--thresholds', str(path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)


def _assert_refused(path, needle):
    result = _check(path)
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert needle in result.stderr, (needle, result.stderr)


def _restrictive_mode(platform_name):
    return 0o400 if platform_name == 'posix' else 0o600


def test_shipped_document_is_exact_and_cli_checkable(tmp):
    """The tracked migration is the one source of all calibration state."""
    del tmp
    thresholds = _thresholds()
    data = thresholds.load(DATA_PATH)
    assert thresholds.coverage(data, 'python') == (
        Decimal('94.4'), Decimal('92.9'))
    assert thresholds.coverage(data, 'javascript') == (
        Decimal('35.5'), Decimal('34.0'))
    assert thresholds.module_size_baseline(data) == _valid()[
        'module_size_baseline']
    result = _check(DATA_PATH)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_cli_prints_only_the_requested_floor(tmp):
    """The floor command is safe to use in a quoted shell substitution."""
    del tmp
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--coverage-floor', 'python',
         '--thresholds', str(DATA_PATH)], cwd=str(ROOT), capture_output=True,
        text=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == '92.9\n', result.stdout
    assert result.stderr == '', result.stderr


def test_required_and_unknown_fields_are_rejected(tmp):
    """Schema drift must fail closed instead of silently selecting a value."""
    path = Path(tmp) / 'thresholds.json'
    cases = []
    for key in ('schema_version', 'coverage', 'module_size_baseline'):
        value = _valid()
        del value[key]
        cases.append((value, f'missing field: {key}'))
    value = _valid()
    value['unknown'] = 1
    cases.append((value, 'unknown field: unknown'))
    value = _valid()
    value['schema_version'] = 2
    cases.append((value, 'unsupported schema_version: 2'))
    for value, needle in cases:
        _write_json(path, value)
        _assert_refused(path, needle)


def test_nested_required_and_unknown_fields_are_rejected(tmp):
    """Both language records and the baseline have exact member sets."""
    path = Path(tmp) / 'thresholds.json'
    for language in ('python', 'javascript'):
        value = _valid()
        del value['coverage'][language]['floor']
        _write_json(path, value)
        _assert_refused(path, f'missing field: coverage.{language}.floor')
        value = _valid()
        value['coverage'][language]['extra'] = 1
        _write_json(path, value)
        _assert_refused(path, f'unknown field: coverage.{language}.extra')
    value = _valid()
    del value['coverage']['python']
    _write_json(path, value)
    _assert_refused(path, 'missing coverage language: python')
    value = _valid()
    value['coverage']['ruby'] = {'measured': 1.0, 'floor': -0.5}
    _write_json(path, value)
    _assert_refused(path, 'unknown coverage language: ruby')


def test_duplicate_keys_are_rejected_before_mapping_construction(tmp):
    """A last-key-wins JSON parser could forge the accepted calibration."""
    path = Path(tmp) / 'duplicate.json'
    path.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"coverage":{"python":{"measured":94.4,"floor":92.9},'
        '"javascript":{"measured":35.5,"floor":34.0}},'
        '"module_size_baseline":{}}', encoding='utf-8')
    _assert_refused(path, 'duplicate JSON key: schema_version')
    path.write_text(
        '{"schema_version":1,"coverage":{"python":'
        '{"measured":94.4,"measured":95.0,"floor":93.5},'
        '"javascript":{"measured":35.5,"floor":34.0}},'
        '"module_size_baseline":{}}', encoding='utf-8')
    _assert_refused(path, 'duplicate JSON key: measured')


def test_coverage_numbers_are_finite_bounded_and_one_decimal(tmp):
    """Coverage values have an exact tenths representation and no booleans."""
    path = Path(tmp) / 'thresholds.json'
    values = (True, '94.4', 'NaN', 'Infinity', -0.1, 100.1, 94.44)
    for value in values:
        candidate = _valid()
        candidate['coverage']['python']['measured'] = value
        _write_json(path, candidate)
        _assert_refused(path, 'coverage.python.measured')
    for literal in ('NaN', 'Infinity', '-Infinity'):
        path.write_text(
            '{"schema_version":1,"coverage":{"python":'
            f'{{"measured":{literal},"floor":92.9}},'
            '"javascript":{"measured":35.5,"floor":34.0}},'
            '"module_size_baseline":{}}', encoding='utf-8')
        _assert_refused(path, 'non-finite JSON number')


def test_coverage_floor_is_strictly_lower_with_exact_gap(tmp):
    """The calibration contract rejects both a weak floor and a wrong gap."""
    path = Path(tmp) / 'thresholds.json'
    for measured, floor, needle in (
            (80.0, 80.0, 'floor must be below measured'),
            (80.0, 78.6, 'coverage.python calibration gap must be 1.5'),
            (80.0, 78.4, 'coverage.python calibration gap must be 1.5')):
        candidate = _valid()
        candidate['coverage']['python'] = {
            'measured': measured, 'floor': floor}
        _write_json(path, candidate)
        _assert_refused(path, needle)


def test_baseline_paths_and_counts_are_safe_and_positive(tmp):
    """Filesystem policy state cannot smuggle traversal or invalid counts."""
    path = Path(tmp) / 'thresholds.json'
    for unsafe in ('', '/absolute.py', r'..\\escape.py', '../escape.py',
                   'tests/../escape.py', 'tests/has:colon.py',
                   'tests/CON.py', 'tests/trailing. ', 'tests/a\x01.py'):
        candidate = _valid()
        candidate['module_size_baseline'] = {unsafe: 1}
        _write_json(path, candidate)
        _assert_refused(path, 'unsafe module path')
    for count in (True, 0, -1, 1.5, '10'):
        candidate = _valid()
        candidate['module_size_baseline'] = {'tests/x.py': count}
        _write_json(path, candidate)
        _assert_refused(path, 'module_size_baseline.tests/x.py')


def test_public_accessors_return_validated_data(tmp):
    """Accessor calls repeat the named language and baseline contracts."""
    del tmp
    thresholds = _thresholds()
    data = thresholds.load(DATA_PATH)
    assert thresholds.coverage(data, 'python') == (
        Decimal('94.4'), Decimal('92.9'))
    assert thresholds.module_size_baseline(data)['tests/test_cli.py'] == 1238
    try:
        thresholds.coverage(data, 'ruby')
    except ValueError as error:
        assert 'unknown coverage language: ruby' in str(error), error
    else:
        raise AssertionError('unknown language was accepted')


def test_invalid_candidate_never_touches_existing_destination(tmp):
    """Validation precedes the temporary file and replacement boundaries."""
    thresholds = _thresholds()
    target = Path(tmp) / 'thresholds.json'
    target.write_bytes(b'old bytes\n')
    before = target.stat()
    candidate = _valid()
    candidate['coverage']['python']['floor'] = 92.8
    try:
        thresholds.write(target, candidate)
    except ValueError as error:
        assert 'calibration gap must be 1.5' in str(error), error
    else:
        raise AssertionError('invalid candidate was written')
    assert target.read_bytes() == b'old bytes\n'
    assert target.stat().st_ino == before.st_ino
    assert not list(target.parent.glob(f'.{target.name}.*.tmp'))


def _write_failure(tmp, boundary):
    thresholds = _thresholds()
    target = Path(tmp) / f'{boundary}.json'
    target.write_bytes(b'previous\n')
    old_mode = stat.S_IMODE(target.stat().st_mode)
    real_fsync = thresholds.os.fsync
    real_replace = thresholds.os.replace
    real_fdopen = thresholds.os.fdopen
    if boundary == 'write':
        def fdopen(*args, **kwargs):
            handle = real_fdopen(*args, **kwargs)

            class Broken:
                def __enter__(self):
                    handle.__enter__()
                    return self

                def __exit__(self, *exit_args):
                    return handle.__exit__(*exit_args)

                def write(self, _bytes):
                    raise OSError('injected write failure')

                def flush(self):
                    return handle.flush()

                def fileno(self):
                    return handle.fileno()

            return Broken()
        thresholds.os.fdopen = fdopen
    elif boundary == 'flush':
        def fdopen(*args, **kwargs):
            handle = real_fdopen(*args, **kwargs)
            original_flush = handle.flush
            handle.flush = lambda: (_ for _ in ()).throw(
                OSError('injected flush failure'))
            del original_flush
            return handle
        thresholds.os.fdopen = fdopen
    elif boundary == 'replace':
        thresholds.os.replace = lambda *_args: (_ for _ in ()).throw(
            OSError('injected replace failure'))
    else:
        raise AssertionError(boundary)
    try:
        try:
            thresholds.write(target, _valid())
        except OSError as error:
            assert f'injected {boundary} failure' in str(error), error
        else:
            raise AssertionError(f'{boundary} failure was swallowed')
    finally:
        thresholds.os.fsync = real_fsync
        thresholds.os.replace = real_replace
        thresholds.os.fdopen = real_fdopen
    assert target.read_bytes() == b'previous\n'
    assert stat.S_IMODE(target.stat().st_mode) == old_mode
    assert not list(target.parent.glob(f'.{target.name}.*.tmp'))


def test_atomic_writer_preserves_old_bytes_for_each_failure_boundary(tmp):
    """Write, flush, and replace failures all leave no partial publication."""
    for boundary in ('write', 'flush', 'replace'):
        _write_failure(tmp, boundary)


def test_successful_render_is_deterministic_loadable_and_mode_stable(tmp):
    """Canonical bytes and destination permissions survive repeated writes."""
    thresholds = _thresholds()
    target = Path(tmp) / 'thresholds.json'
    target.write_bytes(b'legacy\n')
    os.chmod(target, _restrictive_mode(os.name))
    expected_mode = stat.S_IMODE(target.stat().st_mode)
    if os.name == 'posix':
        assert expected_mode == 0o400
    else:
        assert expected_mode & stat.S_IWRITE
    thresholds.write(target, _valid())
    first = target.read_bytes()
    assert first.endswith(b'\n'), first
    assert b'NaN' not in first
    assert thresholds.load(target)['coverage']['python']['measured'] \
        == Decimal('94.4')
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    thresholds.write(target, thresholds.load(target))
    assert target.read_bytes() == first
    assert not list(target.parent.glob(f'.{target.name}.*.tmp'))


def test_restrictive_mode_selection_keeps_windows_destination_writable(tmp):
    """The Windows request is writable while POSIX remains restrictive."""
    del tmp
    assert _restrictive_mode('posix') == 0o400
    windows_mode = _restrictive_mode('nt')
    assert windows_mode == 0o600
    assert windows_mode & stat.S_IWRITE


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cithresholds_')


if __name__ == '__main__':
    raise SystemExit(main())
