#!/usr/bin/env python3
"""Contracts for the JSON-owned line-length policy and its ratchet."""
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
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'line_lengths.py'
THRESHOLDS_SOURCE = ROOT / '.github' / 'ci-thresholds.json'
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'
WORKFLOW_SOURCE = ROOT / '.github' / 'workflows' / 'tests.yml'


def _policy():
    return _util.load(POLICY_SOURCE, 'line_lengths_contract')


def _thresholds():
    return _util.load(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                      'line_thresholds_contract')


def _document():
    return _thresholds().load(THRESHOLDS_SOURCE)


def _captured_main(policy, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(stdout),
          contextlib.redirect_stderr(stderr)):
        status = policy.main(argv)
    return status, stdout.getvalue(), stderr.getvalue()


def _git(repo, *args):
    subprocess.run(('git', '-C', str(repo)) + args, check=True,
                   capture_output=True, env=_util.child_coverage('scrub'))


def _line_fixture(tmp, files, baseline, name):
    """A committed repository of ``files`` (rel -> bytes) with a document."""
    thresholds = _thresholds()
    repo = Path(tmp) / name
    (repo / '.github').mkdir(parents=True)
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for source in (POLICY_SOURCE, ROOT / 'scripts' / 'ci' / 'thresholds.py'):
        shutil.copy2(source, repo / 'scripts' / 'ci' / source.name)
    data = _document()
    data['long_line_baseline'] = baseline
    target = repo / '.github' / 'ci-thresholds.json'
    thresholds.write(target, data)
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'base')
    return repo, target


def _lines(*texts):
    return ''.join(texts).encode('utf-8')


def test_limit_is_seventy_nine_characters(tmp):
    del tmp
    assert _policy().LINE_LIMIT == 79


def test_counting_is_by_character_not_byte(tmp):
    policy = _policy()
    limit = policy.LINE_LIMIT
    repo, _target = _line_fixture(tmp, {
        'multibyte_ok.py': _lines('é' * limit, '\n'),
        'multibyte_over.py': _lines('é' * (limit + 1), '\n'),
        'crlf_ok.py': _lines('x' * limit, '\r\n', 'y' * limit, '\r\n'),
        'crlf_over.py': _lines('x' * (limit + 1), '\r\n'),
        'unterminated_over.py': _lines('x' * (limit + 1)),
        'tab_ok.py': _lines('\t' * limit, '\n'),
        'bare_cr_is_content.py': _lines('x' * limit, '\r\r\n'),
        'trailing_cr_is_content.py': _lines('x' * limit, '\r'),
        'two_over.py': _lines('x' * limit, '\n', 'x' * 200, '\n',
                              'x' * 80, '\n'),
    }, {}, 'characters')
    counts = policy.tracked_long_lines(repo)
    scripts = {'scripts/ci/line_lengths.py', 'scripts/ci/thresholds.py'}
    assert scripts <= set(counts)
    assert {rel: count for rel, count in counts.items()
            if rel not in scripts} == {
        'multibyte_ok.py': 0,
        'multibyte_over.py': 1,
        'crlf_ok.py': 0,
        'crlf_over.py': 1,
        'unterminated_over.py': 1,
        'tab_ok.py': 0,
        'bare_cr_is_content.py': 1,
        'trailing_cr_is_content.py': 1,
        'two_over.py': 2,
    }


def test_counting_covers_tracked_python_only(tmp):
    policy = _policy()
    over = _lines('x' * 80, '\n')
    repo, _target = _line_fixture(tmp, {
        'tracked.py': over,
        'notes.txt': over,
        'examples/sample.py': over,
        'deep/nested/module.py': over,
    }, {}, 'tracked')
    (repo / 'untracked.py').write_bytes(over)
    counts = policy.tracked_long_lines(repo)
    assert 'notes.txt' not in counts
    assert 'untracked.py' not in counts
    assert counts['tracked.py'] == 1
    assert counts['examples/sample.py'] == 1
    assert counts['deep/nested/module.py'] == 1


def test_violations_reports_each_kind_and_nothing_on_a_clean_pair(tmp):
    del tmp
    policy = _policy()
    baseline = {'a.py': 2, 'b.py': 1, 'gone.py': 3, 'done.py': 1}
    counts = {'a.py': 3, 'b.py': 1, 'done.py': 0, 'new.py': 1, 'ok.py': 0}
    found = policy.violations(counts, baseline)
    assert found == {
        'grown': {'a.py': (3, 2)},
        'over': {'new.py': 1},
        'missing': ['gone.py'],
        'graduated': ['done.py'],
    }
    clean = policy.violations({'a.py': 2, 'b.py': 0}, {'a.py': 2})
    assert not any(clean.values()), clean
    assert sorted(clean) == ['graduated', 'grown', 'missing', 'over']


def test_tightened_lowers_and_drops_at_zero(tmp):
    del tmp
    policy = _policy()
    baseline = {'a.py': 3, 'b.py': 2, 'c.py': 1}
    counts = {'a.py': 1, 'b.py': 2, 'c.py': 0}
    lowered = policy.tightened(baseline, counts)
    assert lowered == {'a.py': 1, 'b.py': 2}
    assert lowered is not baseline
    assert baseline == {'a.py': 3, 'b.py': 2, 'c.py': 1}


def test_tightening_never_raises_or_adds_a_number(tmp):
    del tmp
    policy = _policy()
    baseline = {'a.py': 2}
    assert policy.tightened(baseline, {'a.py': 2}) is None
    assert policy.tightened(baseline, {'a.py': 3}) is None
    assert policy.tightened(baseline, {'a.py': 2, 'new.py': 5}) is None
    assert policy.tightened({}, {'new.py': 5}) is None


def test_tightening_preserves_an_entry_for_a_missing_file(tmp):
    del tmp
    policy = _policy()
    assert policy.tightened({'gone.py': 2}, {}) is None
    assert policy.tightened({'gone.py': 2, 'a.py': 2}, {'a.py': 1}) == {
        'gone.py': 2, 'a.py': 1}


def _normalised(text):
    return ' '.join(text.lower().split())


def test_script_docstring_carries_each_printed_remedy(tmp):
    del tmp
    policy = _policy()
    doc = _normalised(policy.__doc__ or '')
    assert sorted(policy.REMEDY_FOR) == [
        'graduated', 'grown', 'missing', 'over']
    for kind, remedy in policy.REMEDY_FOR.items():
        assert _normalised(remedy) in doc, (kind, remedy)


def test_refused_kinds_carry_wrapping_or_stale_entry_remedies(tmp):
    del tmp
    policy = _policy()
    assert policy.REMEDY_FOR['grown'] == policy.WRAP_REMEDY
    assert policy.REMEDY_FOR['over'] == policy.WRAP_REMEDY
    assert policy.REMEDY_FOR['missing'] == policy.STALE_ENTRY_REMEDY
    assert policy.REMEDY_FOR['graduated'] == policy.STALE_ENTRY_REMEDY
    assert 'never raised by hand' in policy.WRAP_REMEDY
    assert 'wrap' in policy.WRAP_REMEDY
    assert 'deleted by hand' in policy.STALE_ENTRY_REMEDY


def test_main_reports_clean_and_all_violation_modes(tmp):
    thresholds = _thresholds()
    policy = _policy()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data['long_line_baseline'] = {}
    thresholds.write(target, data)
    original = policy.tracked_long_lines
    policy.tracked_long_lines = lambda: {'server.py': 0, 'cli.py': 0}
    try:
        status, stdout, stderr = _captured_main(
            policy, ['--thresholds', str(target)])
        assert status == 0
        assert stdout == '2 tracked modules within the line-length policy\n'
        assert stderr == ''

        data['long_line_baseline'] = {
            'tests/grown.py': 1,
            'tests/graduated.py': 4,
            'tests/missing.py': 2,
        }
        thresholds.write(target, data)
        policy.tracked_long_lines = lambda: {
            'tests/grown.py': 2,
            'tests/graduated.py': 0,
            'tests/over.py': 3,
        }
        status, stdout, stderr = _captured_main(
            policy, ['--thresholds', str(target)])
    finally:
        policy.tracked_long_lines = original
    assert status == 1
    assert stdout == ''
    for detail in (
            "grown: {'tests/grown.py': (2, 1)}",
            "over: {'tests/over.py': 3}",
            "missing: ['tests/missing.py']",
            "graduated: ['tests/graduated.py']"):
        assert detail in stderr, stderr
    assert stderr.count(policy.WRAP_REMEDY) == 1
    assert stderr.count(policy.STALE_ENTRY_REMEDY) == 1
    assert 'Traceback' not in stderr


def test_main_tighten_noop_then_changes_only_selected_member(tmp):
    thresholds = _thresholds()
    policy = _policy()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data['long_line_baseline'] = {
        'tests/changed.py': 3,
        'tests/steady.py': 2,
    }
    thresholds.write(target, data)
    original = policy.tracked_long_lines
    try:
        policy.tracked_long_lines = lambda: {
            'tests/changed.py': 3,
            'tests/steady.py': 2,
        }
        before = target.read_bytes()
        status, stdout, stderr = _captured_main(
            policy, ['--tighten', '--thresholds', str(target)])
        assert status == 0
        assert stdout == 'no file lost an over-limit line\n'
        assert stderr == ''
        assert target.read_bytes() == before

        policy.tracked_long_lines = lambda: {
            'tests/changed.py': 1,
            'tests/steady.py': 2,
        }
        status, stdout, stderr = _captured_main(
            policy, ['--tighten', '--thresholds', str(target)])
    finally:
        policy.tracked_long_lines = original
    assert status == 0
    assert stdout == 'tightened the long-line baseline\n'
    assert stderr == ''
    after = thresholds.load(target)
    assert after['long_line_baseline'] == {
        'tests/changed.py': 1,
        'tests/steady.py': 2,
    }
    assert after['coverage'] == data['coverage']
    assert after['module_size_baseline'] == data['module_size_baseline']


def test_every_tracked_module_satisfies_the_line_policy(tmp):
    del tmp
    policy = _policy()
    baseline = _thresholds().long_line_baseline(_document())
    found = policy.violations(policy.tracked_long_lines(), baseline)
    assert not found['grown'], found['grown']
    assert not found['over'], found['over']


def test_baseline_names_only_files_that_still_need_it(tmp):
    del tmp
    policy = _policy()
    baseline = _thresholds().long_line_baseline(_document())
    found = policy.violations(policy.tracked_long_lines(), baseline)
    assert not found['missing'], found['missing']
    assert not found['graduated'], found['graduated']


def _run_cli(repo, *args, target=None):
    if target is None:
        target = repo / '.github' / 'ci-thresholds.json'
    return subprocess.run(
        [sys.executable, str(repo / 'scripts' / 'ci' / 'line_lengths.py'),
         *args, '--thresholds', str(target)],
        cwd=str(repo), env=_util.child_coverage('scrub'),
        capture_output=True, text=True, timeout=60)


def _over(count):
    return _lines(*(['x' * 80, '\n'] * count))


def test_real_cli_reports_clean_tree(tmp):
    policy = _policy()
    repo, target = _line_fixture(
        tmp, {'a.py': _over(2), 'b.py': _lines('y = 1\n')},
        {'a.py': 2}, 'clean')
    before = target.read_bytes()
    result = _run_cli(repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == (
        f'{len(policy.tracked_long_lines(repo))} tracked modules within '
        'the line-length policy\n')
    assert result.stderr == ''
    assert target.read_bytes() == before


def test_real_cli_refuses_each_kind_with_its_remedy(tmp):
    policy = _policy()
    cases = (
        ('grown', {'a.py': _over(3)}, {'a.py': 2},
         "grown: {'a.py': (3, 2)}", policy.WRAP_REMEDY),
        ('over', {'a.py': _over(1)}, {},
         "over: {'a.py': 1}", policy.WRAP_REMEDY),
        ('missing', {'a.py': _over(1)}, {'a.py': 1, 'gone.py': 1},
         "missing: ['gone.py']", policy.STALE_ENTRY_REMEDY),
        ('graduated', {'a.py': _lines('y = 1\n')}, {'a.py': 1},
         "graduated: ['a.py']", policy.STALE_ENTRY_REMEDY),
    )
    for kind, files, baseline, detail, remedy in cases:
        repo, target = _line_fixture(tmp, files, baseline, kind)
        before = target.read_bytes()
        result = _run_cli(repo)
        assert result.returncode == 1, (kind, result.stdout, result.stderr)
        assert result.stdout == '', (kind, result.stdout)
        assert result.stderr == f'{detail}\n{remedy}\n', (kind, result.stderr)
        assert target.read_bytes() == before


def test_real_cli_tighten_noop_writes_nothing(tmp):
    repo, target = _line_fixture(
        tmp, {'a.py': _over(2), 'b.py': _over(1)},
        {'a.py': 2, 'gone.py': 1}, 'noop')
    before = target.read_bytes()
    untouched = target.stat()
    result = _run_cli(repo, '--tighten')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no file lost an over-limit line\n'
    assert result.stderr == ''
    assert target.read_bytes() == before
    after = target.stat()
    assert after.st_ino == untouched.st_ino
    assert after.st_mtime_ns == untouched.st_mtime_ns


def test_real_cli_tighten_shrink_rewrites_only_the_lowered_member(tmp):
    thresholds = _thresholds()
    repo, target = _line_fixture(
        tmp, {'a.py': _over(1), 'b.py': _over(3), 'c.py': _over(0)},
        {'a.py': 3, 'b.py': 2, 'c.py': 1, 'gone.py': 4}, 'shrink')
    before = thresholds.load(target)
    result = _run_cli(repo, '--tighten')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'tightened the long-line baseline\n'
    assert result.stderr == ''
    expected = dict(before)
    expected['long_line_baseline'] = {'a.py': 1, 'b.py': 2, 'gone.py': 4}
    rendered = Path(tmp) / 'expected.json'
    thresholds.write(rendered, expected)
    assert target.read_bytes() == rendered.read_bytes()
    assert not list(target.parent.glob(f'.{target.name}.*.tmp'))


def test_real_cli_reports_threshold_load_failure_without_traceback(tmp):
    repo, target = _line_fixture(tmp, {'a.py': _over(0)}, {}, 'broken')
    target.write_bytes(b'{')
    (repo / 'unreadable').mkdir()
    failures = (
        (target, 'invalid thresholds JSON: '),
        (repo / 'unreadable', 'cannot read thresholds: '),
        (repo / 'missing.json', 'cannot read thresholds: '),
    )
    for path, marker in failures:
        result = _run_cli(repo, target=path)
        assert result.returncode == 1, (path, result.stdout, result.stderr)
        assert result.stdout == ''
        assert result.stderr.startswith(marker), (path, result.stderr)
        assert result.stderr.count('\n') == 1, (path, result.stderr)
        assert 'Traceback' not in result.stderr


def test_real_cli_names_an_undecodable_file_without_traceback(tmp):
    repo, _target = _line_fixture(
        tmp, {'bad.py': b'x = 1\n\xff\n', 'ok.py': _over(0)}, {},
        'undecodable')
    result = _run_cli(repo)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == ''
    assert result.stderr.startswith('bad.py: '), result.stderr
    assert result.stderr.count('\n') == 1, result.stderr
    assert 'Traceback' not in result.stderr


def _skill_decisions(path=SKILL_SOURCE):
    source = path.read_text(encoding='utf-8')
    text = _normalised(source)
    paragraph = _normalised(''.join(
        block for block in source.split('\n\n')
        if 'long_line_baseline' in block))
    return {
        'owner': '.github/ci-thresholds.json' in text
        and 'long_line_baseline' in text,
        'command': 'python3 scripts/ci/line_lengths.py --tighten' in text,
        'remedy': 'wrap' in paragraph and 'never raised' in paragraph,
        'reads_skill': 'tests/test_line_lengths.py' in text,
    }


def test_skill_names_owner_command_remedy_and_reader(tmp):
    del tmp
    decisions = _skill_decisions()
    assert all(decisions.values()), decisions


def test_skill_mutations_are_caught_independently(tmp):
    source = SKILL_SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('owner', 'long_line_baseline', 'line_length_table'),
        ('command', 'python3 scripts/ci/line_lengths.py --tighten',
         'python3 scripts/ci/line_lengths.py --raise'),
        ('reads_skill', 'tests/test_line_lengths.py', 'no suite'),
        ('remedy', 'a number is never raised by hand',
         'a number may be raised by hand'),
    )
    for name, old, new in mutations:
        path = Path(tmp) / f'{name}.md'
        path.write_text(source.replace(old, new), encoding='utf-8')
        assert not _skill_decisions(path)[name], name


def test_workflow_tightens_long_lines_before_the_document_check(tmp):
    del tmp
    workflow = WORKFLOW_SOURCE.read_text(encoding='utf-8')
    size = workflow.index('python scripts/ci/size_baseline.py --tighten')
    lines = workflow.index('python scripts/ci/line_lengths.py --tighten')
    check = workflow.index('python scripts/ci/thresholds.py --check')
    assert size < lines < check
    assert 'no module shrank.' not in workflow


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='linelengths_')


if __name__ == '__main__':
    raise SystemExit(main())
