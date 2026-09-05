#!/usr/bin/env python3
"""Contracts for the shared CI threshold document reader and writer."""
import json
import os
import stat
import subprocess
import sys
from decimal import Decimal, localcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


SCRIPT = ROOT / 'scripts' / 'ci' / 'thresholds.py'
DATA_PATH = ROOT / '.github' / 'ci-thresholds.json'


def _thresholds():
    return _util.load(SCRIPT, 'thresholds_contract')


def _valid():
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))


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


def _assert_load_refused(path, needle):
    try:
        _thresholds().load(path)
    except ValueError as error:
        assert str(error).startswith(needle), str(error)
    else:
        raise AssertionError('invalid threshold input was accepted')


def _assert_document_contract(path):
    thresholds = _thresholds()
    data = thresholds.load(path)
    assert data['schema_version'] == 1
    assert set(data['coverage']) == {'python', 'javascript'}
    for language in ('python', 'javascript'):
        measured, floor = thresholds.coverage(data, language)
        assert floor < measured
        assert measured - floor == thresholds.CALIBRATION_GAP
        assert set(data['coverage'][language]) == {'measured', 'floor'}
    baseline = thresholds.module_size_baseline(data)
    assert baseline == data['module_size_baseline']
    assert all(count > 0 for count in baseline.values())
    result = _check(path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return data


def _assert_cli_floor(path):
    thresholds = _thresholds()
    expected = thresholds.coverage(thresholds.load(path), 'python')[1]
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--coverage-floor', 'python',
         '--thresholds', str(path)], cwd=str(ROOT), capture_output=True,
        text=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == f'{expected:.1f}\n', result.stdout
    assert result.stderr == '', result.stderr


def _restrictive_mode(platform_name):
    return 0o400 if platform_name == 'posix' else 0o600


def test_shipped_document_is_exact_and_cli_checkable(tmp):
    del tmp
    _assert_document_contract(DATA_PATH)


def test_cli_prints_only_the_requested_floor(tmp):
    """The floor command is safe to use in a quoted shell substitution."""
    _assert_cli_floor(DATA_PATH)


def test_required_and_unknown_fields_are_rejected(tmp):
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
        '"coverage":{"python":{"measured":80.0,"floor":78.5},'
        '"javascript":{"measured":81.0,"floor":79.5}},'
        '"module_size_baseline":{}}', encoding='utf-8')
    _assert_refused(path, 'duplicate JSON key: schema_version')
    path.write_text(
        '{"schema_version":1,"coverage":{"python":'
        '{"measured":80.0,"measured":81.0,"floor":79.5},'
        '"javascript":{"measured":81.0,"floor":79.5}},'
        '"module_size_baseline":{}}', encoding='utf-8')
    _assert_refused(path, 'duplicate JSON key: measured')


def test_coverage_numbers_are_finite_bounded_and_one_decimal(tmp):
    path = Path(tmp) / 'thresholds.json'
    values = (True, '80.0', 'NaN', 'Infinity', -0.1, 100.1, 80.04)
    for value in values:
        candidate = _valid()
        candidate['coverage']['python']['measured'] = value
        _write_json(path, candidate)
        _assert_refused(path, 'coverage.python.measured')
    for literal in ('NaN', 'Infinity', '-Infinity'):
        path.write_text(
            '{"schema_version":1,"coverage":{"python":'
            f'{{"measured":{literal},"floor":78.5}},'
            '"javascript":{"measured":81.0,"floor":79.5}},'
            '"module_size_baseline":{}}', encoding='utf-8')
        _assert_refused(path, 'non-finite JSON number')


def test_invalid_utf8_and_malformed_json_are_refused(tmp):
    path = Path(tmp) / 'invalid.json'
    path.write_bytes(b'\xff')
    _assert_load_refused(path, 'invalid thresholds JSON:')
    path.write_bytes(b'{')
    _assert_load_refused(path, 'invalid thresholds JSON:')


def test_in_memory_coverage_rejects_nonfinite_and_nonobject_values(tmp):
    del tmp
    thresholds = _thresholds()
    candidate = _valid()
    candidate['coverage']['python']['measured'] = Decimal('NaN')
    try:
        thresholds.normalise(candidate)
    except ValueError as error:
        assert str(error) == 'coverage.python.measured must be finite'
    else:
        raise AssertionError('non-finite in-memory coverage was accepted')
    candidate = _valid()
    candidate['coverage']['python'] = []
    try:
        thresholds.normalise(candidate)
    except ValueError as error:
        assert str(error) == 'coverage.python must be an object'
    else:
        raise AssertionError('non-object in-memory coverage was accepted')


def test_low_decimal_precision_is_a_clean_threshold_refusal(tmp):
    del tmp
    thresholds = _thresholds()
    with localcontext() as context:
        context.prec = 1
        try:
            thresholds.coverage_value(
                Decimal('80.0'), 'coverage.python.measured')
        except ValueError as error:
            assert str(error) == (
                'coverage.python.measured must have at most one decimal place')
        else:
            raise AssertionError('low precision accepted an invalid value')


def test_coverage_floor_is_strictly_lower_with_exact_gap(tmp):
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


def test_baseline_path_encoding_limits_are_refused(tmp):
    path = Path(tmp) / 'thresholds.json'
    for unsafe in ('tests/\ud800.py', 'tests/' + 'x' * 241 + '.py'):
        candidate = _valid()
        candidate['module_size_baseline'] = {unsafe: 1}
        _write_json(path, candidate)
        _assert_load_refused(path, 'unsafe module path')


def test_nonobject_baseline_and_missing_threshold_file_are_refused(tmp):
    path = Path(tmp) / 'thresholds.json'
    candidate = _valid()
    candidate['module_size_baseline'] = []
    _write_json(path, candidate)
    _assert_load_refused(path, 'module_size_baseline must be an object')
    _assert_load_refused(Path(tmp) / 'missing.json', 'cannot read thresholds:')


def test_public_accessors_return_validated_data(tmp):
    del tmp
    thresholds = _thresholds()
    data = thresholds.load(DATA_PATH)
    assert thresholds.coverage(data, 'python') == (
        data['coverage']['python']['measured'],
        data['coverage']['python']['floor'])
    assert thresholds.module_size_baseline(data) \
        == data['module_size_baseline']
    try:
        thresholds.coverage(data, 'ruby')
    except ValueError as error:
        assert 'unknown coverage language: ruby' in str(error), error
    else:
        raise AssertionError('unknown language was accepted')


def test_invalid_candidate_never_touches_existing_destination(tmp):
    thresholds = _thresholds()
    target = Path(tmp) / 'thresholds.json'
    target.write_bytes(b'old bytes\n')
    before = target.stat()
    candidate = _valid()
    candidate['coverage']['python']['floor'] = \
        candidate['coverage']['python']['measured']
    try:
        thresholds.write(target, candidate)
    except ValueError as error:
        assert 'floor must be below measured' in str(error), error
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
    for boundary in ('write', 'flush', 'replace'):
        _write_failure(tmp, boundary)


def test_successful_render_is_deterministic_loadable_and_mode_stable(tmp):
    thresholds = _thresholds()
    target = Path(tmp) / 'thresholds.json'
    target.write_bytes(b'legacy\n')
    os.chmod(target, _restrictive_mode(os.name))
    expected_mode = stat.S_IMODE(target.stat().st_mode)
    if os.name == 'posix':
        assert expected_mode == 0o400
    else:
        assert expected_mode & stat.S_IWRITE
    source = thresholds.load(DATA_PATH)
    thresholds.write(target, source)
    first = target.read_bytes()
    assert first.endswith(b'\n'), first
    assert b'NaN' not in first
    assert thresholds.load(target) == source
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    thresholds.write(target, thresholds.load(target))
    assert target.read_bytes() == first
    assert not list(target.parent.glob(f'.{target.name}.*.tmp'))


def test_shipped_document_lifecycle_accepts_real_policy_updates(tmp):
    thresholds = _thresholds()
    path = Path(tmp) / 'lifecycle.json'
    source = thresholds.load(DATA_PATH)
    thresholds.write(path, source)
    ratchet = _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py',
                         'thresholds_lifecycle_ratchet')
    recorded, _floor = thresholds.coverage(source, 'python')
    measured = min(
        recorded + ratchet.RAISE_HYSTERESIS + Decimal('0.1'),
        Decimal('100.0'))
    should_raise = measured - recorded > ratchet.RAISE_HYSTERESIS
    before = path.read_bytes()
    assert ratchet.main([
        '--language', 'python', '--measured', str(measured),
        '--thresholds', str(path)]) == 0
    updated = thresholds.load(path)
    if should_raise:
        assert path.read_bytes() != before
        assert updated['coverage']['python'] == {
            'measured': measured,
            'floor': measured - ratchet.CALIBRATION_GAP}
    else:
        assert path.read_bytes() == before
        assert updated == source
    after = path.read_bytes()
    assert ratchet.main([
        '--language', 'python', '--measured', str(measured),
        '--thresholds', str(path)]) == 0
    assert path.read_bytes() == after

    size_baseline = _util.load(
        ROOT / 'scripts' / 'ci' / 'size_baseline.py',
        'thresholds_lifecycle_size_baseline')
    updated = thresholds.load(path)
    baseline = thresholds.module_size_baseline(updated)
    if baseline:
        sizes = {rel: size_baseline.ceiling_for(rel) for rel in baseline}
        tightened = size_baseline.tightened(baseline, sizes)
        assert tightened == {}
    else:
        assert size_baseline.tightened(baseline, {}) is None
        tightened = {}
    updated['module_size_baseline'] = tightened
    thresholds.write(path, updated)
    original_path = DATA_PATH
    globals()['DATA_PATH'] = path
    try:
        test_shipped_document_is_exact_and_cli_checkable(tmp)
        test_cli_prints_only_the_requested_floor(tmp)
        test_public_accessors_return_validated_data(tmp)
    finally:
        globals()['DATA_PATH'] = original_path
    loaded = thresholds.load(path)
    expected_measured = measured if should_raise else recorded
    assert loaded['coverage']['python'] == {
        'measured': expected_measured,
        'floor': expected_measured - ratchet.CALIBRATION_GAP}
    assert loaded['module_size_baseline'] == tightened
    assert thresholds.load(DATA_PATH) == source


def _run_lifecycle_against(tmp, path):
    original_path = DATA_PATH
    globals()['DATA_PATH'] = path
    try:
        test_shipped_document_lifecycle_accepts_real_policy_updates(tmp)
    finally:
        globals()['DATA_PATH'] = original_path


def test_lifecycle_accepts_an_already_raised_calibration(tmp):
    thresholds = _thresholds()
    source = thresholds.load(DATA_PATH)
    source['coverage']['python'] = {
        'measured': Decimal('96.0'), 'floor': Decimal('94.5')}
    path = Path(tmp) / 'already-raised.json'
    thresholds.write(path, source)
    ratchet = _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py',
                         'thresholds_already_raised_ratchet')
    before = path.read_bytes()
    assert ratchet.main([
        '--language', 'python', '--measured', '96.0',
        '--thresholds', str(path)]) == 0
    assert path.read_bytes() == before
    _run_lifecycle_against(tmp, path)


def test_lifecycle_accepts_the_finite_coverage_ceiling(tmp):
    thresholds = _thresholds()
    source = thresholds.load(DATA_PATH)
    source['coverage']['python'] = {
        'measured': Decimal('100.0'), 'floor': Decimal('98.5')}
    path = Path(tmp) / 'coverage-ceiling.json'
    thresholds.write(path, source)
    ratchet = _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py',
                         'thresholds_coverage_ceiling_ratchet')
    before = path.read_bytes()
    assert ratchet.main([
        '--language', 'python', '--measured', '100.0',
        '--thresholds', str(path)]) == 0
    assert path.read_bytes() == before
    _run_lifecycle_against(tmp, path)


def test_lifecycle_accepts_a_fully_tightened_empty_baseline(tmp):
    thresholds = _thresholds()
    source = thresholds.load(DATA_PATH)
    size_baseline = _util.load(
        ROOT / 'scripts' / 'ci' / 'size_baseline.py',
        'thresholds_empty_baseline_size_baseline')
    baseline = thresholds.module_size_baseline(source)
    if baseline:
        sizes = {rel: size_baseline.ceiling_for(rel) for rel in baseline}
        tightened = size_baseline.tightened(baseline, sizes)
        assert tightened == {}
    else:
        assert size_baseline.tightened(baseline, {}) is None
        tightened = {}
    source['module_size_baseline'] = tightened
    path = Path(tmp) / 'empty-baseline.json'
    thresholds.write(path, source)
    _run_lifecycle_against(tmp, path)


def test_restrictive_mode_selection_keeps_windows_destination_writable(tmp):
    del tmp
    assert _restrictive_mode('posix') == 0o400
    windows_mode = _restrictive_mode('nt')
    assert windows_mode == 0o600
    assert windows_mode & stat.S_IWRITE


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cithresholds_')


if __name__ == '__main__':
    raise SystemExit(main())
