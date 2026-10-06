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
from _ci_reseed_fixtures import (  # noqa: E402
    _ci_reseed_budget_repo, _ci_reseed_commit, _ci_reseed_merge,
    _ci_reseed_module, _ci_reseed_order_subject, _ci_reseed_repo,
    _ci_reseed_rowless, _ci_reseed_shallow)
from _ratchet_fixture import _git, _normalised  # noqa: E402


sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


SCRIPT = ROOT / 'scripts' / 'ci' / 'thresholds.py'
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'tests_lines.py'
DATA_PATH = ROOT / '.github' / 'ci-thresholds.json'
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'
# The budget fixtures' children run outside the coverage paths.
_CHILD_ENV = _util.child_coverage('scrub')
# One list names both the document key and the reader call that must agree.
_BASELINE_ACCESSORS = ('module_size_baseline', 'long_line_baseline',
                       'type_error_baseline', 'js_coverage_baseline')


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
        lambda: thresholds.normalise(candidate, False), needle)


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
        assert baseline == data[member], member
        assert all(count > 0 for count in baseline.values()), member
    assert thresholds.tests_line_baseline(data) == data['tests_line_baseline']
    try:
        thresholds.coverage(data, 'ruby')
    except ValueError as error:
        assert 'unknown coverage language: ruby' in str(error), error
    else:
        raise AssertionError('unknown language was accepted')
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
    """Every absent and every extra member, at both levels, in one table."""
    path = Path(tmp) / 'thresholds.json'
    cases = []
    for key in ('schema_version', 'coverage', 'module_size_baseline',
                'long_line_baseline', 'type_error_baseline',
                'js_coverage_baseline', 'tests_line_baseline'):
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
        candidate['module_size_baseline'] = {unsafe: 1}
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
    before = path.read_bytes()
    assert ratchet.main([
        '--language', 'python', '--measured', str(measured),
        '--thresholds', str(path)]) == 0
    updated = thresholds.load(path)
    assert path.read_bytes() != before
    assert updated['coverage']['python'] == {
        'measured': measured,
        'floor': measured - ratchet.CALIBRATION_GAP}
    after = path.read_bytes()
    assert ratchet.main([
        '--language', 'python', '--measured', str(measured),
        '--thresholds', str(path)]) == 0
    assert path.read_bytes() == after

    _assert_document_contract(path)
    _assert_cli_floor(path)
    loaded = thresholds.load(path)
    assert loaded['coverage'] == {
        'python': {'measured': measured,
                   'floor': measured - ratchet.CALIBRATION_GAP},
        'javascript': source['coverage']['javascript']}
    assert thresholds.load(DATA_PATH) == source


def test_lifecycle_accepts_a_calibration_the_ratchet_cannot_move(tmp):
    """A measured above the hysteresis, and the 100.0 ceiling: both are
    calibrations the ordinary raise must leave byte-identical."""
    thresholds = _thresholds()
    ratchet = _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py',
                         'thresholds_at_ceiling_ratchet')
    for measured in (Decimal('96.0'), Decimal('100.0')):
        source = thresholds.load(DATA_PATH)
        source['coverage']['python'] = {
            'measured': measured, 'floor': measured - ratchet.CALIBRATION_GAP}
        path = Path(tmp) / f'at-{measured}.json'
        thresholds.write(path, source)
        before = path.read_bytes()
        assert ratchet.main([
            '--language', 'python', '--measured', f'{measured}',
            '--thresholds', str(path)]) == 0
        assert path.read_bytes() == before
        updated = thresholds.load(path)
        assert updated['coverage']['python'] == {
            'measured': measured,
            'floor': measured - ratchet.CALIBRATION_GAP}


def _tests_files(first, second):
    return {'tests/first.py': b'x = 1\n' * first,
            'tests/second.py': b'x = 1\n' * second}


def _mixed_tests_files(python_lines, note_lines):
    """A tests/ tree whose shape the pathspec and `-I` have to name."""
    return {'tests/first.py': b'x = 1\n' * python_lines,
            'tests/notes.md': b'note\n' * note_lines,
            'tests/blob.bin': b'\x00\xff\xfe binary\n' * 40}


def _run_lines_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / 'scripts' / 'ci' / 'tests_lines.py'),
         *args, '--thresholds', str(repo / '.github' / 'ci-thresholds.json')],
        cwd=str(repo), env=_CHILD_ENV, capture_output=True, text=True,
        timeout=60)


def test_tests_line_budget_is_a_positive_integer_that_round_trips(tmp):
    """A document that loses the key on rewrite is worse than no key."""
    thresholds = _thresholds()
    data = thresholds.load(DATA_PATH)
    assert data['tests_line_baseline'] > 0
    target = Path(tmp) / 'round-trip.json'
    thresholds.write(target, data)
    assert thresholds.load(target)['tests_line_baseline'] \
        == data['tests_line_baseline']
    for value in (True, 0, -1, 1.5, '10'):
        candidate = _valid()
        candidate['tests_line_baseline'] = value
        path = Path(tmp) / 'bad.json'
        _write_json(path, candidate)
        _assert_refused(path, 'tests_line_baseline')


def test_tests_line_budget_fails_naming_both_numbers_and_the_remedy(tmp):
    repo, _target = _ci_reseed_budget_repo(
        tmp, _tests_files(5, 4), 8, 'grown')
    done = _run_lines_cli(repo)
    assert done.returncode != 0, (done.stdout, done.stderr)
    assert '8' in done.stderr and '9' in done.stderr, done.stderr
    remedy = _util.load(POLICY_SOURCE, 'tests_lines_growth').GROWTH_REMEDY
    assert _normalised(remedy) in _normalised(done.stderr), done.stderr


def test_tests_line_budget_tightens_a_drop_and_never_raises(tmp):
    """Compared as `load` normalises it, not raw JSON: a raw float and
    the Decimal it becomes are not equal."""
    repo, target = _ci_reseed_budget_repo(tmp, _tests_files(4, 3), 20, 'drop')
    source = _thresholds().load(target)
    done = _run_lines_cli(repo, '--tighten')
    assert done.returncode == 0, (done.stdout, done.stderr)
    after = _thresholds().load(target)
    assert after['tests_line_baseline'] == 7
    for member in ('coverage', *_BASELINE_ACCESSORS):
        assert after[member] == source[member], member
    for budget in (7, 2):
        other, other_target = _ci_reseed_budget_repo(
            tmp, _tests_files(4, 3), budget, f'steady-{budget}')
        before = other_target.read_bytes()
        done = _run_lines_cli(other, '--tighten')
        assert done.returncode == 0, (done.stdout, done.stderr)
        assert other_target.read_bytes() == before


def test_the_budget_counts_every_tracked_text_file_under_tests(tmp):
    """The counting definition gets its own oracle: a non-`.py` text
    file (which a `tests/*.py` pathspec drops), a binary one (which a
    missing `-I` counts) and files outside `tests/`."""
    repo, _target = _ci_reseed_budget_repo(
        tmp, _mixed_tests_files(4, 3), 7, 'mixed')
    policy = _util.load(POLICY_SOURCE, 'tests_lines_definition')
    assert policy.tracked_test_lines(repo) == 7
    done = _run_lines_cli(repo)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert '7' in done.stdout, done.stdout


def test_the_workflow_tightens_the_tests_budget_before_the_check(tmp):
    del tmp
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    tighten = workflow.index('python scripts/ci/tests_lines.py --tighten')
    check = workflow.index('python scripts/ci/thresholds.py --check')
    assert tighten < check


def test_the_real_tests_tree_is_within_its_recorded_line_budget(tmp):
    del tmp
    thresholds = _thresholds()
    recorded = thresholds.tests_line_baseline(thresholds.load(DATA_PATH))
    policy = _util.load(POLICY_SOURCE, 'tests_lines_real_tree')
    assert policy.tracked_test_lines() <= recorded, recorded
    done = subprocess.run(
        [sys.executable, str(POLICY_SOURCE),
         '--thresholds', str(DATA_PATH)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (done.stdout, done.stderr)


def test_the_reseed_verdict_is_a_required_bool_buying_only_absence(tmp):
    """The verdict is the tree's answer, asked at one named place."""
    thresholds = _thresholds()
    reseed = _ci_reseed_module()
    strict = thresholds.normalise(_valid(), False)
    _assert_refused_by(lambda: thresholds.normalise(_valid(), None),
                       'reseed_in_flight must be a bool')
    _assert_refused_by(lambda: thresholds.normalise(_valid(), 'yes'),
                       'reseed_in_flight must be a bool')
    assert thresholds.normalise(_valid(), True) == strict
    missing = _ci_reseed_rowless()
    _assert_refused_by(
        lambda: thresholds.normalise(missing, False),
        'missing field: tests_line_baseline')
    windowed = thresholds.normalise(missing, True)
    assert 'tests_line_baseline' not in windowed
    for member in ('schema_version', 'coverage', *_BASELINE_ACCESSORS):
        assert windowed[member] == strict[member], member
    target = Path(tmp) / 'window.json'
    thresholds.write(target, missing, True)
    assert 'tests_line_baseline' not in target.read_text(encoding='utf-8')
    assert thresholds.load(target, True) == windowed
    _assert_refused_by(
        lambda: thresholds.write(target, missing),
        'missing field: tests_line_baseline')
    assert reseed.MARKER == '[tests-line-re-seed]'
    assert thresholds._SCALAR_FIELDS == (reseed._SCALAR_FIELD,)
    bad_schema = _ci_reseed_rowless()
    bad_schema['schema_version'] = 2
    bad_floor = _ci_reseed_rowless()
    bad_floor['coverage']['python']['floor'] = \
        bad_floor['coverage']['python']['measured']
    unknown = _ci_reseed_rowless()
    unknown['unknown'] = 1
    bad_row = _valid()
    bad_row['tests_line_baseline'] = 'ten'
    for candidate, needle in (
            (bad_schema, 'unsupported schema_version: 2'),
            (bad_floor, 'coverage.python.floor must be below measured'),
            (unknown, 'unknown field: unknown'),
            (bad_row, 'tests_line_baseline must be a JSON number')):
        _assert_refused_by(
            lambda doc=candidate: thresholds.normalise(doc, True), needle)


def test_the_walk_reads_the_marker_through_bounded_ancestry(tmp):
    """The marker survives commits landing on the delete, a GitHub merge,
    and the visit cap; a present row and a shallow boundary close it."""
    reseed = _ci_reseed_module()
    repo = _ci_reseed_repo(tmp, 'walk')
    _ci_reseed_commit(repo, f'base {reseed.MARKER}', row=False)
    reseed.clear_cache()
    assert reseed.in_flight(repo) is True
    _ci_reseed_commit(repo, 'a publisher lands', row=False)
    reseed.clear_cache()
    assert reseed.in_flight(repo) is True
    _ci_reseed_commit(repo, f'seed {reseed.MARKER}', row=True)
    reseed.clear_cache()
    assert reseed.in_flight(repo) is False

    _ci_reseed_merge(repo, reseed.MARKER)
    reseed.clear_cache()
    assert reseed.in_flight(repo) is True

    for fillers, expected in ((9, True), (10, False)):
        bound = _ci_reseed_repo(tmp, f'bound-{fillers}')
        _ci_reseed_commit(bound, f'delete {reseed.MARKER}', row=False)
        for _ in range(fillers):
            _ci_reseed_commit(bound, 'filler', row=False)
        reseed.clear_cache()
        assert reseed.in_flight(bound) is expected


def test_the_walk_visits_a_deep_marked_lineage_before_the_cap(tmp):
    """Parent order decides: the marked limb hangs six deep behind a
    ten-commit first-parent limb."""
    reseed = _ci_reseed_module()
    repo = _ci_reseed_order_subject(tmp, reseed.MARKER)
    reseed.clear_cache()
    assert reseed.in_flight(repo) is True


def test_the_walk_fails_closed_and_the_loader_gates_the_same_answer(tmp):
    reseed = _ci_reseed_module()
    clone = _ci_reseed_shallow(tmp, reseed.MARKER)
    reseed.clear_cache()
    assert reseed.in_flight(clone) is False
    document = clone / '.github' / 'ci-thresholds.json'
    done = subprocess.run(
        [sys.executable, str(clone / 'scripts' / 'ci' / 'thresholds.py'),
         '--check', '--thresholds', str(document)],
        cwd=str(clone), env=_CHILD_ENV, capture_output=True, text=True,
        timeout=60)
    assert done.returncode != 0, (done.stdout, done.stderr)
    assert 'missing field: tests_line_baseline' in done.stderr, done.stderr

    real_run = reseed.subprocess.run
    reseed.subprocess.run = lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError('no git'))
    try:
        answer = reseed.in_flight(Path('/nowhere'))
    finally:
        reseed.subprocess.run = real_run
    reseed.clear_cache()
    assert answer is False

    broken = _ci_reseed_repo(tmp, 'broken')
    (broken / '.github' / 'ci-thresholds.json').write_bytes(b'{')
    _git(broken, 'add', '-A')
    _git(broken, 'commit', '-qm', 'broken')
    assert not reseed._field_present(broken, 'HEAD')


def test_the_window_skips_the_line_gate_and_the_tighten(tmp):
    repo, target = _ci_reseed_budget_repo(
        tmp, _tests_files(4, 3), None, 'window')
    before = target.read_bytes()
    done = _run_lines_cli(repo)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 're-seed window' in done.stdout, done.stdout
    tighten = _run_lines_cli(repo, '--tighten')
    assert tighten.returncode == 0, (tighten.stdout, tighten.stderr)
    assert target.read_bytes() == before


def test_the_accessor_and_the_ratchet_thread_the_verdict(tmp):
    del tmp
    thresholds = _thresholds()
    assert thresholds.tests_line_baseline(_ci_reseed_rowless(), True) is None
    assert thresholds.tests_line_baseline(
        _valid()) == _valid()['tests_line_baseline']
    ratchet = _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py',
                         'thresholds_window_ratchet')
    raised = ratchet.update(_ci_reseed_rowless(), '99.0', 'python', True)
    assert raised is not None
    assert 'tests_line_baseline' not in raised
    _assert_refused_by(
        lambda: ratchet.update(_ci_reseed_rowless(), '99.0', 'python', False),
        'missing field: tests_line_baseline')


def _skill_decisions(path=SKILL_SOURCE):
    raw = path.read_text(encoding='utf-8')
    # Isolate the tests/-budget paragraph from the other ratchets'.
    block = next((part for part in raw.split('\n\n')
                  if 'tests_line_baseline' in part), '')
    paragraph = _normalised(block)
    return {
        'owner': ('.github/ci-thresholds.json' in paragraph
                  and 'tests_line_baseline' in paragraph),
        'command': ('python3 scripts/ci/tests_lines.py --tighten'
                    in paragraph),
        'growth': 'pays for growth by deleting' in paragraph,
        'merge_ref': 'merge ref' in paragraph,
        'reads_skill': 'tests/test_ci_thresholds.py' in paragraph,
    }


def test_skill_names_the_state_owner_and_tighten_command(tmp):
    """Each decision asserted true, then its phrase mutated once and
    the decision asserted false, so neither phase is a green no-op."""
    source = SKILL_SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('owner', 'tests_line_baseline', 'tests_line_table'),
        ('command', 'python3 scripts/ci/tests_lines.py --tighten',
         'python3 .github/ci-thresholds.json --tighten'),
        ('growth', 'pays for growth by deleting',
         'pays for growth by re-baselining'),
        ('merge_ref', 'its MERGE ref', 'its HEAD ref'),
        ('reads_skill', 'tests/test_ci_thresholds.py',
         'tests/test_ci_thresholds_absent.py'),
    )
    for name, old, new in mutations:
        assert _skill_decisions()[name], name
        path = Path(tmp) / f'{name}.md'
        path.write_text(source.replace(old, new), encoding='utf-8')
        assert not _skill_decisions(path)[name], name


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cithresholds_')


if __name__ == '__main__':
    raise SystemExit(main())
