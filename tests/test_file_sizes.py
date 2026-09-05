#!/usr/bin/env python3
"""Contracts for the JSON-owned module-size policy and its ratchet."""
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'size_baseline.py'
THRESHOLDS_SOURCE = ROOT / '.github' / 'ci-thresholds.json'
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'


def _policy():
    return _util.load(POLICY_SOURCE, 'size_baseline_contract')


def _thresholds():
    return _util.load(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                      'size_thresholds_contract')


def _document():
    return _thresholds().load(THRESHOLDS_SOURCE)


def _captured_main(policy, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(stdout),
          contextlib.redirect_stderr(stderr)):
        status = policy.main(argv)
    return status, stdout.getvalue(), stderr.getvalue()


def test_every_tracked_module_satisfies_the_size_policy(tmp):
    """No tracked file grows past its record or its unexcused ceiling."""
    del tmp
    policy = _policy()
    baseline = _thresholds().module_size_baseline(_document())
    found = policy.violations(policy.tracked_sizes(), baseline)
    assert not found['grown'], found['grown']
    assert not found['over'], found['over']


def test_baseline_names_only_files_that_still_need_it(tmp):
    """Missing and graduated entries remain visible for manual cleanup."""
    del tmp
    policy = _policy()
    baseline = _thresholds().module_size_baseline(_document())
    found = policy.violations(policy.tracked_sizes(), baseline)
    assert not found['missing'], found['missing']
    assert not found['graduated'], found['graduated']


def test_the_ceilings_are_below_everything_they_excuse(tmp):
    """A baseline is meaningful only when it exceeds its file ceiling."""
    del tmp
    policy = _policy()
    baseline = _thresholds().module_size_baseline(_document())
    for rel, recorded in baseline.items():
        assert recorded > policy.ceiling_for(rel), (rel, recorded)


def test_tightened_returns_a_new_mapping_for_a_shrunk_file(tmp):
    """The JSON mapping follows an over-ceiling file down exactly."""
    del tmp
    policy = _policy()
    baseline = {'server.py': 2624}
    sizes = {'server.py': 2000}
    tightened = policy.tightened(baseline, sizes)
    assert tightened == {'server.py': 2000}
    assert tightened is not baseline


def test_tightening_never_raises_or_adds_a_number(tmp):
    """No-shrink, growth, and unbaselined files are untouched."""
    del tmp
    policy = _policy()
    baseline = {'server.py': 2624}
    assert policy.tightened(baseline, {'server.py': 2624}) is None
    assert policy.tightened(baseline, {'server.py': 2625}) is None
    assert policy.tightened(
        baseline, {'server.py': 2624, 'tests/new.py': 900}) is None


def test_tightening_graduates_a_file_under_its_ceiling(tmp):
    """A stale exception is removed once the file needs no exception."""
    del tmp
    policy = _policy()
    baseline = {'server.py': 2624, 'tests/small.py': 939}
    sizes = {'server.py': 400, 'tests/small.py': 939}
    assert policy.tightened(baseline, sizes) == {'tests/small.py': 939}


def test_tightening_graduates_at_the_inclusive_ceiling(tmp):
    """A file exactly at its ceiling no longer needs a baseline exception."""
    del tmp
    policy = _policy()
    baseline = {'tests/small.py': 900}
    assert policy.tightened(
        baseline, {'tests/small.py': policy.TEST_CEILING}) == {}


def test_tightening_preserves_an_entry_for_a_missing_file(tmp):
    """Deleting source never silently rewrites policy state."""
    del tmp
    policy = _policy()
    baseline = {'tests/gone.py': 900}
    assert policy.tightened(baseline, {}) is None


def test_main_reports_clean_and_all_violation_modes(tmp):
    """The direct CLI reports every kind and de-duplicates its remedies."""
    thresholds = _thresholds()
    policy = _policy()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data['module_size_baseline'] = {}
    thresholds.write(target, data)
    original = policy.tracked_sizes
    policy.tracked_sizes = lambda: {'server.py': 10}
    try:
        status, stdout, stderr = _captured_main(
            policy, ['--thresholds', str(target)])
        assert status == 0
        assert stdout == '1 tracked modules within the size policy\n'
        assert stderr == ''

        data['module_size_baseline'] = {
            'tests/grown.py': 700,
            'tests/graduated.py': 900,
            'tests/missing.py': 900,
        }
        thresholds.write(target, data)
        policy.tracked_sizes = lambda: {
            'tests/grown.py': 701,
            'tests/graduated.py': 700,
            'tests/over.py': 701,
        }
        status, stdout, stderr = _captured_main(
            policy, ['--thresholds', str(target)])
    finally:
        policy.tracked_sizes = original
    assert status == 1
    assert stdout == ''
    for detail in (
            "grown: {'tests/grown.py': (701, 700)}",
            "over: {'tests/over.py': (701, 700)}",
            "missing: ['tests/missing.py']",
            "graduated: {'tests/graduated.py': (700, 700)}"):
        assert detail in stderr, stderr
    assert stderr.count(policy.GROWTH_REMEDY) == 1
    assert stderr.count(policy.STALE_ENTRY_REMEDY) == 1


def test_main_tighten_noop_then_changes_only_selected_member(tmp):
    """Tightening preserves a no-op byte-for-byte and lowers one entry."""
    thresholds = _thresholds()
    policy = _policy()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data['module_size_baseline'] = {
        'tests/changed.py': 901,
        'tests/steady.py': 902,
    }
    thresholds.write(target, data)
    original = policy.tracked_sizes
    try:
        policy.tracked_sizes = lambda: {
            'tests/changed.py': 901,
            'tests/steady.py': 902,
        }
        before = target.read_bytes()
        status, stdout, stderr = _captured_main(
            policy, ['--tighten', '--thresholds', str(target)])
        assert status == 0
        assert stdout == 'no module shrank below its recorded size\n'
        assert stderr == ''
        assert target.read_bytes() == before

        policy.tracked_sizes = lambda: {
            'tests/changed.py': 850,
            'tests/steady.py': 902,
        }
        before = thresholds.load(target)
        status, stdout, stderr = _captured_main(
            policy, ['--tighten', '--thresholds', str(target)])
    finally:
        policy.tracked_sizes = original
    assert status == 0
    assert stdout == 'tightened the module size baseline\n'
    assert stderr == ''
    after = thresholds.load(target)
    assert after['module_size_baseline'] == {
        'tests/changed.py': 850,
        'tests/steady.py': 902,
    }
    assert after['coverage'] == before['coverage']


def test_main_reports_threshold_load_failures_without_traceback(tmp):
    """Missing, unreadable, and malformed threshold inputs fail closed."""
    policy = _policy()
    missing = Path(tmp) / 'missing.json'
    unreadable = Path(tmp) / 'unreadable'
    unreadable.mkdir()
    invalid = Path(tmp) / 'invalid.json'
    invalid.write_bytes(b'{')
    for target, marker in (
            (missing, 'cannot read thresholds:'),
            (unreadable, 'cannot read thresholds:'),
            (invalid, 'invalid thresholds JSON:')):
        status, stdout, stderr = _captured_main(
            policy, ['--thresholds', str(target)])
        assert status == 1
        assert stdout == ''
        assert stderr.startswith(marker + ' '), stderr
        assert stderr.count('\n') == 1
        assert 'Traceback' not in stderr


def _size_fixture(tmp, files, baseline, name):
    """Create a tracked tree for one real size-policy CLI invocation."""
    thresholds = _thresholds()
    repo = Path(tmp) / name
    (repo / '.github').mkdir(parents=True)
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    for rel, count in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('line = 1\n' * count, encoding='utf-8')
    shutil.copy2(POLICY_SOURCE, repo / 'scripts' / 'ci' / 'size_baseline.py')
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                 repo / 'scripts' / 'ci' / 'thresholds.py')
    data = _document()
    data['module_size_baseline'] = baseline
    target = repo / '.github' / 'ci-thresholds.json'
    thresholds.write(target, data)
    commands = (
        ('git', 'init', '-q'),
        ('git', 'config', 'user.email', 'tests@example.invalid'),
        ('git', 'config', 'user.name', 'Tests'),
        ('git', 'add', '.'),
        ('git', 'commit', '-qm', 'base'),
    )
    for command in commands:
        subprocess.run(command, cwd=repo,
                       env=_util.child_coverage('scrub'), check=True)
    return repo, target


def test_cli_tighten_changes_json_member_and_preserves_calibration(tmp):
    """The real CLI atomically lowers one controlled fixture entry."""
    policy = _policy()
    thresholds = _thresholds()
    selected = 'tests/shrinking.py'
    current = policy.TEST_CEILING + 1
    repo, target = _size_fixture(
        tmp, {selected: current}, {selected: current + 1}, 'one-line-shrink')
    before = thresholds.load(target)
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    after = thresholds.load(target)
    live = policy.tracked_sizes(repo)[selected]
    assert after['module_size_baseline'] == {selected: live}
    assert after['coverage'] == before['coverage']


def test_real_cli_no_shrink_and_missing_entry_report_without_write(tmp):
    """A controlled no-op preserves a missing entry and its document bytes."""
    policy = _policy()
    thresholds = _thresholds()
    stable = 'tests/stable.py'
    missing = 'tests/missing.py'
    current = policy.TEST_CEILING + 1
    repo, target = _size_fixture(
        tmp, {stable: current}, {stable: current, missing: current},
        'no-shrink')
    before = target.read_bytes()
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no module shrank below its recorded size\n'
    assert result.stderr == ''
    assert target.read_bytes() == before
    assert thresholds.load(target)['module_size_baseline'] == {
        stable: current, missing: current}


def _run_tighten(target, script=POLICY_SOURCE, cwd=ROOT):
    return subprocess.run(
        [sys.executable, str(script), '--tighten', '--thresholds',
         str(target)], cwd=str(cwd), env=_util.child_coverage('scrub'),
        capture_output=True, text=True, timeout=60)


def test_real_cli_tighten_already_empty_baseline_is_noop(tmp):
    """An already-empty valid baseline remains byte-for-byte unchanged."""
    policy = _policy()
    selected = 'tests/unbaselined.py'
    current = policy.ceiling_for(selected)
    repo, target = _size_fixture(
        tmp, {selected: current}, {}, 'empty-baseline')
    before = target.read_bytes()
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no module shrank below its recorded size\n'
    assert result.stderr == ''
    assert target.read_bytes() == before


def test_real_cli_tighten_shrink_writes_only_the_lowered_member(tmp):
    """The copied real CLI records one shrink and preserves other state."""
    policy = _policy()
    thresholds = _thresholds()
    selected = 'tests/shrinking.py'
    steady = 'tests/steady.py'
    current = policy.TEST_CEILING + 1
    repo, target = _size_fixture(
        tmp, {selected: current, steady: current},
        {selected: current + 1, steady: current}, 'shrink')
    before = thresholds.load(target)
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'tightened the module size baseline\n'
    after = thresholds.load(target)
    assert after['coverage'] == before['coverage']
    live = policy.tracked_sizes(repo)
    assert after['module_size_baseline'] == {
        selected: live[selected], steady: live[steady]}


def test_real_cli_tighten_graduates_last_entry_to_empty(tmp):
    """The copied real CLI graduates the final entry into an empty map."""
    policy = _policy()
    thresholds = _thresholds()
    selected = 'tests/graduating.py'
    ceiling = policy.ceiling_for(selected)
    repo, target = _size_fixture(
        tmp, {selected: ceiling}, {selected: ceiling + 1}, 'graduation')
    before = thresholds.load(target)
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'tightened the module size baseline\n'
    after = thresholds.load(target)
    live = policy.tracked_sizes(repo)[selected]
    assert live == ceiling
    assert after['module_size_baseline'] == {}
    assert after['coverage'] == before['coverage']


def test_real_cli_tighten_growth_reports_without_upward_write(tmp):
    """The copied real CLI never records a controlled grown module upward."""
    policy = _policy()
    selected = 'tests/growing.py'
    current = policy.TEST_CEILING + 2
    repo, target = _size_fixture(
        tmp, {selected: current}, {selected: current - 1}, 'growth')
    before = target.read_bytes()
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no module shrank below its recorded size\n'
    assert target.read_bytes() == before


def test_real_cli_tighten_over_ceiling_unbaselined_file_reports_without_write(
        tmp):
    """An unbaselined fixture over its ceiling cannot be added."""
    policy = _policy()
    thresholds = _thresholds()
    data = _document()
    data['module_size_baseline'] = {}
    selected = 'tests/over_ceiling.py'
    current = policy.TEST_CEILING + 1
    repo, target = _size_fixture(
        tmp, {selected: current}, data['module_size_baseline'],
        'over-ceiling-tree')
    before = target.read_bytes()
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no module shrank below its recorded size\n'
    assert target.read_bytes() == before
    after = thresholds.load(target)
    assert selected not in after['module_size_baseline']
    assert after['coverage'] == data['coverage']
    assert after['module_size_baseline'] == data['module_size_baseline']


def test_real_cli_tighten_missing_entry_reports_without_deleting_it(tmp):
    """A genuinely missing fixture entry remains for manual deletion."""
    policy = _policy()
    thresholds = _thresholds()
    data = _document()
    missing = 'tests/genuinely_missing_task3_entry.py'
    data['module_size_baseline'] = {
        missing: policy.TEST_CEILING + 1}
    repo, target = _size_fixture(tmp, {}, data['module_size_baseline'],
                                 'missing-entry')
    before = target.read_bytes()
    result = _run_tighten(
        target, repo / 'scripts' / 'ci' / 'size_baseline.py', repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no module shrank below its recorded size\n'
    assert target.read_bytes() == before
    after = thresholds.load(target)
    assert after['module_size_baseline'] == data['module_size_baseline']
    assert after['coverage'] == data['coverage']


def _normalised(text):
    text = text.lower().replace("n't", ' not')
    return ' '.join(text.split())


def _skill_decisions(path=SKILL_SOURCE):
    """Read each operator decision independently from the tracked skill."""
    text = _normalised(path.read_text(encoding='utf-8'))
    return {
        'owner': '.github/ci-thresholds.json' in text
        and 'module_size_baseline' in text,
        'command': 'python3 scripts/ci/size_baseline.py --tighten' in text,
        'growth': ('relocate the code into a new module' in text
                   and 'shrinking' in text),
        'manual_delete': ('entry naming a file that is gone is deleted by hand'
                          in text),
        'reads_skill': 'tests/test_file_sizes.py' in text,
        'no_old_owner': "size_baseline.py's table" not in text,
        'no_stale_reader_claim': 'no test reads this file' not in text,
    }


def _assert_skill_decision(name):
    decisions = _skill_decisions()
    assert decisions[name], f'skill decision missing: {name}'


def test_skill_names_json_owner_independently(tmp):
    del tmp
    _assert_skill_decision('owner')


def test_skill_names_real_shrink_command_independently(tmp):
    del tmp
    _assert_skill_decision('command')


def test_skill_names_relocation_or_shrinking_as_growth_remedy(tmp):
    del tmp
    _assert_skill_decision('growth')


def test_skill_names_manual_deletion_only_for_gone_files(tmp):
    del tmp
    _assert_skill_decision('manual_delete')


def test_skill_removes_retired_owner_and_false_reader_claim(tmp):
    del tmp
    decisions = _skill_decisions()
    assert decisions['no_old_owner'], decisions
    assert decisions['no_stale_reader_claim'], decisions
    assert decisions['reads_skill'], decisions


def test_skill_mutations_are_caught_independently(tmp):
    """Breaking one operator decision cannot hide behind the other three."""
    source = SKILL_SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('owner', '.github/ci-thresholds.json', 'scripts/ci/size_baseline.py'),
        ('command', 'python3 scripts/ci/size_baseline.py --tighten',
         'python3 .github/ci-thresholds.json'),
        ('growth', 'relocate the code into a new module',
         'raise the recorded number'),
        ('manual_delete',
         'entry naming a file that is gone is deleted by\nhand',
         '--tighten removes every stale entry'),
    )
    for name, old, new in mutations:
        path = Path(tmp) / f'{name}.md'
        path.write_text(source.replace(old, new), encoding='utf-8')
        assert not _skill_decisions(path)[name], name


def test_skill_pressure_scenarios_name_the_operator_action(tmp):
    """The prose gives an action for growth, shrinkage, and deletion."""
    del tmp
    text = _normalised(SKILL_SOURCE.read_text(encoding='utf-8'))
    assert 'recorded number is never raised' in text
    assert 'shrinking is recorded with ' \
        '`python3 scripts/ci/size_baseline.py --tighten`' \
        in text
    assert 'entry naming a file that is gone is deleted by hand' in text


def test_script_docstring_carries_each_printed_remedy(tmp):
    """Refusals and operator documentation remain one policy contract."""
    del tmp
    policy = _policy()
    doc = _normalised(policy.__doc__ or '')
    for kind, remedy in policy.REMEDY_FOR.items():
        assert _normalised(remedy) in doc, (kind, remedy)


def test_refused_kinds_print_the_correct_remedy(tmp):
    """The four violation kinds retain their distinct clearing actions."""
    del tmp
    policy = _policy()
    over = policy.TEST_CEILING + 1
    baseline = {'tests/gone.py': over, 'tests/big.py': over}
    sizes = {'tests/big.py': over + 1, 'tests/other.py': over}
    found = policy.violations(sizes, baseline)
    assert sorted(kind for kind, detail in found.items() if detail) == [
        'grown', 'missing', 'over']
    assert 'relocate the code into a new module' in policy.GROWTH_REMEDY
    assert 'deleted by hand' in policy.STALE_ENTRY_REMEDY


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='filesizes_')


if __name__ == '__main__':
    raise SystemExit(main())
