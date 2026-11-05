#!/usr/bin/env python3
"""Contracts for the shared CI threshold document reader and writer."""
import json
import os
import stat
import subprocess
import sys
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _ratchet_fixture  # noqa: E402
from _repo import ROOT  # noqa: E402


sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


SCRIPT = ROOT / 'scripts' / 'ci' / 'thresholds.py'
DATA_PATH = ROOT / '.github' / 'ci-thresholds.json'
# The CLI children run outside the coverage paths.
_CHILD_ENV = _util.child_coverage('scrub')
# One list names both the document key and the reader call that must agree.
_BASELINE_ACCESSORS = ('long_line_baseline', 'type_error_baseline',
                       'js_coverage_baseline')


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
    _assert_refused_by(lambda: _thresholds().load(path), needle)


def _assert_normalise_refused(thresholds, candidate, needle):
    _assert_refused_by(
        lambda: thresholds.normalise(candidate), needle)


def _assert_refused_by(read, needle):
    try:
        read()
    except ValueError as error:
        assert str(error).startswith(needle), str(error)
    else:
        raise AssertionError(f'accepted input that should say {needle!r}')


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
    for member in _BASELINE_ACCESSORS:
        baseline = getattr(thresholds, member)(data)
        assert baseline == data.get(member, {}), member
        assert all(count > 0 for count in baseline.values()), member
    assert thresholds.tests_line_baseline(data) == data.get(
        'tests_line_baseline')
    try:
        thresholds.coverage(data, 'ruby')
    except ValueError as error:
        assert 'unknown coverage language: ruby' in str(error), error
    else:
        raise AssertionError('unknown language was accepted')
    result = _check(path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return data


def _restrictive_mode(platform_name):
    return 0o400 if platform_name == 'posix' else 0o600


def test_shipped_document_is_exact_and_cli_checkable(tmp):
    _assert_document_contract(DATA_PATH)
    for member in _BASELINE_ACCESSORS:
        absent = _valid()
        absent.pop(member, None)
        path = Path(tmp) / f'{member}.json'
        _write_json(path, absent)
        _assert_document_contract(path)


def test_absent_maps_remain_valid_in_derived_fixtures(tmp):
    data = _valid()
    for member in _BASELINE_ACCESSORS:
        data.pop(member, None)
    path = Path(tmp) / 'without-maps.json'
    _write_json(path, data)
    with (patch.dict(globals(), DATA_PATH=path),
          patch.object(_ratchet_fixture, 'THRESHOLDS_SOURCE', path)):
        test_shipped_document_is_exact_and_cli_checkable(tmp)
        test_absent_budget_round_trips_and_preserves_other_validation(tmp)
        test_tests_line_budget_tightens_a_drop_and_never_raises(tmp)


def test_coverage_floor_cli_emits_the_loaded_numeric_floor(tmp):
    del tmp
    thresholds = _thresholds()
    for language in ('python', 'javascript'):
        expected = thresholds.coverage(thresholds.load(DATA_PATH), language)[1]
        done = subprocess.run(
            [sys.executable, str(SCRIPT), '--coverage-floor', language],
            capture_output=True, text=True, timeout=60, env=_CHILD_ENV)
        assert done.returncode == 0, done.stderr
        assert Decimal(done.stdout.strip()) == expected, done.stdout


def test_required_and_unknown_fields_are_rejected(tmp):
    """Every absent and every extra member, at both levels, in one table."""
    path = Path(tmp) / 'thresholds.json'
    cases = []
    for key in ('schema_version', 'coverage'):
        value = _valid()
        del value[key]
        cases.append((value, f'missing field: {key}'))
    for language in ('python', 'javascript'):
        value = _valid()
        del value['coverage'][language]['floor']
        cases.append((value, f'missing field: coverage.{language}.floor'))
        value = _valid()
        value['coverage'][language]['extra'] = 1
        cases.append((value, f'unknown field: coverage.{language}.extra'))
    value = _valid()
    del value['coverage']['python']
    cases.append((value, 'missing coverage language: python'))
    value = _valid()
    value['unknown'] = 1
    cases.append((value, 'unknown field: unknown'))
    value = _valid()
    value['coverage']['ruby'] = {'measured': 1.0, 'floor': -0.5}
    cases.append((value, 'unknown coverage language: ruby'))
    value = _valid()
    value['schema_version'] = 2
    cases.append((value, 'unsupported schema_version: 2'))
    for value, needle in cases:
        _write_json(path, value)
        _assert_refused(path, needle)


def test_duplicate_keys_are_rejected_before_mapping_construction(tmp):
    """A last-key-wins JSON parser could forge the accepted calibration."""
    path = Path(tmp) / 'duplicate.json'
    path.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"coverage":{"python":{"measured":80.0,"floor":78.5},'
        '"javascript":{"measured":81.0,"floor":79.5}}}', encoding='utf-8')
    _assert_refused(path, 'duplicate JSON key: schema_version')
    path.write_text(
        '{"schema_version":1,"coverage":{"python":'
        '{"measured":80.0,"measured":81.0,"floor":79.5},'
        '"javascript":{"measured":81.0,"floor":79.5}}}',
        encoding='utf-8')
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
            '"javascript":{"measured":81.0,"floor":79.5}}}',
            encoding='utf-8')
        _assert_refused(path, 'non-finite JSON number')


def test_invalid_utf8_and_malformed_json_are_refused(tmp):
    path = Path(tmp) / 'invalid.json'
    path.write_bytes(b'\xff')
    _assert_load_refused(path, 'invalid thresholds JSON:')
    path.write_bytes(b'{')
    _assert_load_refused(path, 'invalid thresholds JSON:')


def test_load_base_drops_members_this_loader_does_not_define(tmp):
    """A base document was written by the base revision's own loader.

    The direction guard reads the base document with this loader, so the
    base reading must accept a document still carrying a member this
    loader has retired; the current document keeps the strict reading.
    """
    path = Path(tmp) / 'base.json'
    base = _valid()
    base['retired_baseline'] = {'tests/gone.py': 10}
    _write_json(path, base)
    without = Path(tmp) / 'without.json'
    _write_json(without, {key: value for key, value in base.items()
                          if key != 'retired_baseline'})
    loaded = _thresholds().load_base(path)
    assert 'retired_baseline' not in loaded
    assert loaded == _thresholds().load(without)
    _assert_load_refused(path, 'unknown field: retired_baseline')


def test_in_memory_coverage_rejects_nonfinite_and_nonobject_values(tmp):
    """These two never round-trip through JSON, so they reach `normalise`."""
    del tmp
    thresholds = _thresholds()
    candidate = _valid()
    candidate['coverage']['python']['measured'] = Decimal('NaN')
    _assert_normalise_refused(
        thresholds, candidate, 'coverage.python.measured must be finite')
    candidate = _valid()
    candidate['coverage']['python'] = []
    _assert_normalise_refused(
        thresholds, candidate, 'coverage.python must be an object')


def test_low_decimal_precision_is_a_clean_threshold_refusal(tmp):
    del tmp
    thresholds = _thresholds()
    with localcontext() as context:
        context.prec = 1
        _assert_refused_by(
            lambda: thresholds.coverage_value(
                Decimal('80.0'), 'coverage.python.measured'),
            'coverage.python.measured must have at most one decimal place')


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
                   'tests/CON.py', 'tests/trailing. ', 'tests/a\x01.py',
                   'tests/\ud800.py', 'tests/' + 'x' * 241 + '.py'):
        candidate = _valid()
        candidate['long_line_baseline'] = {unsafe: 1}
        _write_json(path, candidate)
        _assert_refused(path, 'unsafe module path')
    for member in _BASELINE_ACCESSORS:
        for count in (True, 0, -1, 1.5, '10'):
            candidate = _valid()
            candidate[member] = {'tests/x.py': count}
            _write_json(path, candidate)
            _assert_refused(path, f'{member}.tests/x.py')
        candidate = _valid()
        candidate[member] = {'tests/has:colon.py': 1}
        _write_json(path, candidate)
        _assert_refused(path, 'unsafe module path')


def test_nonobject_baseline_and_missing_threshold_file_are_refused(tmp):
    path = Path(tmp) / 'thresholds.json'
    for member in _BASELINE_ACCESSORS:
        candidate = _valid()
        candidate[member] = []
        _write_json(path, candidate)
        _assert_load_refused(path, f'{member} must be an object')
    _assert_load_refused(Path(tmp) / 'missing.json', 'cannot read thresholds:')


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
    real_replace = thresholds.os.replace
    real_fdopen = thresholds.os.fdopen
    if boundary == 'replace':
        thresholds.os.replace = lambda *_args: (_ for _ in ()).throw(
            OSError('injected replace failure'))
    else:
        def fdopen(*args, **kwargs):
            handle = real_fdopen(*args, **kwargs)
            setattr(handle, boundary, lambda *_args: (_ for _ in ()).throw(
                OSError(f'injected {boundary} failure')))
            return handle
        thresholds.os.fdopen = fdopen
    try:
        try:
            thresholds.write(target, _valid())
        except OSError as error:
            assert f'injected {boundary} failure' in str(error), error
        else:
            raise AssertionError(f'{boundary} failure was swallowed')
    finally:
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cithresholds_')


if __name__ == '__main__':
    raise SystemExit(main())
