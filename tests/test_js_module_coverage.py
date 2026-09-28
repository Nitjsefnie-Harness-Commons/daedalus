#!/usr/bin/env python3
"""Contracts for the per-module JavaScript coverage ratchet."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import (  # noqa: E402
    _captured_main, _document, _git, _normalised)
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
import js_coverage  # noqa: E402

POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'js_module_coverage.py'
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'
WORKFLOW_SOURCE = ROOT / '.github' / 'workflows' / 'tests.yml'
MEMBER = 'js_coverage_baseline'


def _policy():
    return _util.load(POLICY_SOURCE, 'js_module_coverage_contract')


def _thresholds():
    return _util.load(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                      'js_module_thresholds_contract')


def _main(policy, argv):
    """Answer the policy, or say the argv was refused rather than exiting.

    An argparse SystemExit would otherwise end the whole suite run with a
    truncated report and no verdict for the tests after this one.
    """
    try:
        return _captured_main(policy, argv)
    except SystemExit as error:
        raise AssertionError(
            f'the policy exited {error.code} instead of answering: '
            f'{argv}') from error


def test_the_seed_names_only_tracked_javascript_that_still_has_code(tmp):
    del tmp
    baseline = _thresholds().js_coverage_baseline(_document())
    assert baseline, 'the ratchet records nothing, so it exempts everything'
    sources = js_coverage.tracked_sources(ROOT)
    for rel, count in baseline.items():
        assert rel in sources, rel
        assert count > 0, rel
        assert sources[rel].strip(), rel


def test_the_script_docstring_carries_each_printed_remedy(tmp):
    del tmp
    policy = _policy()
    doc = _normalised(policy.__doc__ or '')
    assert sorted(policy.REMEDY_FOR) == [
        'graduated', 'grown', 'missing', 'unrecorded']
    for kind, remedy in policy.REMEDY_FOR.items():
        assert _normalised(remedy) in doc, (kind, remedy)
    assert '--tighten' in doc, doc
    assert 'python3 scripts/ci/js_module_coverage.py' in doc, doc


def test_refused_kinds_carry_the_cover_or_stale_entry_remedy(tmp):
    del tmp
    policy = _policy()
    assert policy.REMEDY_FOR['grown'] == policy.UNCOVERED_REMEDY
    assert policy.REMEDY_FOR['unrecorded'] == policy.UNCOVERED_REMEDY
    assert policy.REMEDY_FOR['missing'] == policy.STALE_ENTRY_REMEDY
    assert policy.REMEDY_FOR['graduated'] == policy.STALE_ENTRY_REMEDY
    assert 'never raised by hand' in policy.UNCOVERED_REMEDY
    assert 'cover the uncovered lines' in policy.UNCOVERED_REMEDY


def test_the_gate_reuses_the_totals_gate_attribution(tmp):
    """The gate must reuse the totals gate's attribution, not a second copy.

    With no dump at all every tracked file is wholly uncovered, so the
    counts are exactly the totals gate's executable-line set.
    """
    counts = _policy().uncovered_counts(Path(tmp) / 'absent')
    assert set(counts) == set(js_coverage.tracked_sources(ROOT))
    assert counts['dashboard/sections/tabs.js'] > 0, counts


def test_a_file_at_or_under_its_record_passes(tmp):
    policy = _policy()
    found = policy.violations({'a.js': 3, 'b.js': 1}, {'a.js': 3, 'b.js': 5})
    assert not any(found.values()), found


def test_a_file_over_its_record_is_refused_with_both_numbers(tmp):
    del tmp
    policy = _policy()
    found = policy.violations({'tabs.js': 12},
                              {'tabs.js': 9})
    assert found['grown'] == {
        'tabs.js': (12, 9)}, found


def test_a_file_with_no_record_is_refused(tmp):
    del tmp
    policy = _policy()
    found = policy.violations({'dashboard/app.js': 4}, {})
    assert found['unrecorded'] == {'dashboard/app.js': 4}, found


def test_an_entry_for_a_file_that_is_gone_is_refused(tmp):
    del tmp
    policy = _policy()
    found = policy.violations({}, {'dashboard/sections/gone.js': 3})
    assert found['missing'] == ['dashboard/sections/gone.js'], found


def test_a_zeroed_entry_is_graduated_rather_than_kept(tmp):
    del tmp
    policy = _policy()
    found = policy.violations({'a.js': 0}, {'a.js': 4})
    assert found['graduated'] == ['a.js'], found


def test_tightened_lowers_a_file_that_lost_cover(tmp):
    del tmp
    policy = _policy()
    tightened = policy.tightened({'a.js': 9, 'b.js': 4},
                                 {'a.js': 3, 'b.js': 4})
    assert tightened == {'a.js': 3, 'b.js': 4}
    assert tightened is not None


def test_tightening_never_raises_or_adds_a_number(tmp):
    del tmp
    policy = _policy()
    baseline = {'a.js': 3}
    assert policy.tightened(baseline, {'a.js': 3}) is None
    assert policy.tightened(baseline, {'a.js': 7}) is None
    assert policy.tightened(
        baseline, {'a.js': 3, 'b.js': 5}) is None


def test_tightening_drops_a_zeroed_entry(tmp):
    del tmp
    policy = _policy()
    assert policy.tightened(
        {'a.js': 4, 'b.js': 2}, {'a.js': 0, 'b.js': 2}) == {'b.js': 2}


def test_tightening_preserves_an_entry_for_a_file_it_cannot_measure(tmp):
    del tmp
    policy = _policy()
    assert policy.tightened({'gone.js': 4}, {}) is None


def test_main_refuses_and_names_the_file_and_the_remedy(tmp):
    policy = _policy()
    thresholds = _thresholds()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data[MEMBER] = {'tabs.js': 1}
    thresholds.write(target, data)
    setattr(policy, 'uncovered_counts', lambda *a, **kw: {'tabs.js': 9})
    status, stdout, stderr = _main(
        policy, [str(Path(tmp) / 'absent'), '--thresholds', str(target)])
    assert status == 1
    assert stdout == ''
    assert 'tabs.js' in stderr, stderr
    assert '9' in stderr and '1' in stderr, stderr
    assert policy.UNCOVERED_REMEDY in stderr, stderr
    assert 'Traceback' not in stderr


def test_main_reports_a_clean_tree_on_stdout_only(tmp):
    policy = _policy()
    thresholds = _thresholds()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data[MEMBER] = {'tabs.js': 9}
    thresholds.write(target, data)
    setattr(policy, 'uncovered_counts', lambda *a, **kw: {'tabs.js': 9})
    status, stdout, stderr = _main(
        policy, [str(Path(tmp) / 'absent'), '--thresholds', str(target)])
    assert status == 0
    assert stdout == '1 tracked modules within the per-module policy\n'
    assert stderr == ''


def _js_repo(tmp, name, sources, baseline):
    """A tracked repository named by one hand-written V8 dump."""
    thresholds = _thresholds()
    repo = Path(tmp) / name
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    for rel in ('thresholds.py', 'js_lines.py', 'js_coverage.py',
                'js_module_coverage.py'):
        shutil.copy2(ROOT / 'scripts' / 'ci' / rel,
                     repo / 'scripts' / 'ci' / rel)
    for rel, text in sources.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    (repo / '.github').mkdir()
    data = _document()
    data[MEMBER] = baseline
    target = repo / '.github' / 'ci-thresholds.json'
    thresholds.write(target, data)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'fixture')
    return repo, target


def _dump(repo, records):
    directory = repo / 'coverage'
    directory.mkdir(exist_ok=True)
    (directory / 'coverage-1.json').write_text(
        json.dumps({'result': records}), encoding='utf-8')
    return directory


def _covered(text, lines):
    """A range covering the first ``lines`` physical lines of ``text``."""
    offsets = [0]
    for _ in range(text.count('\n')):
        offsets.append(text.index('\n', offsets[-1] + 1) + 1)
    return offsets[lines]


def _record(path, end_offset, count=1):
    return {
        'scriptId': '1',
        'url': path.as_uri(),
        'functions': [{'ranges': [{
            'startOffset': 0, 'endOffset': end_offset, 'count': count,
        }]}],
    }


def _run_tighten(target, repo):
    return subprocess.run(
        [sys.executable,
         str(repo / 'scripts' / 'ci' / 'js_module_coverage.py'),
         str(repo / 'coverage'), '--tighten', '--root', str(repo),
         '--thresholds', str(target)],
        cwd=str(repo), env=_util.child_coverage('scrub'),
        capture_output=True, text=True, timeout=120)


_SOURCE = 'const value = 1;\nconsole.log(value);\n'


def test_cli_tighten_writes_a_lower_number_and_keeps_the_calibration(tmp):
    thresholds = _thresholds()
    repo, target = _js_repo(
        tmp, 'tighten', {'extension/fixture.js': _SOURCE},
        {'extension/fixture.js': 2})
    _dump(repo, [_record(repo / 'extension' / 'fixture.js',
                         _covered(_SOURCE, 1))])
    before = thresholds.load(target)
    result = _run_tighten(target, repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    after = thresholds.load(target)
    assert after[MEMBER] == {'extension/fixture.js': 1}, after[MEMBER]
    assert after['coverage'] == before['coverage']


def test_cli_tighten_never_writes_a_number_above_the_record(tmp):
    thresholds = _thresholds()
    repo, target = _js_repo(
        tmp, 'no-raise', {'extension/fixture.js': _SOURCE},
        {'extension/fixture.js': 1})
    _dump(repo, [])
    before = target.read_bytes()
    result = _run_tighten(target, repo)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == 'no file lost an uncovered line\n', result.stdout
    assert target.read_bytes() == before
    assert thresholds.load(target)[MEMBER] == {'extension/fixture.js': 1}


def test_workflow_gates_per_module_coverage_and_tightens_it(tmp):
    del tmp
    workflow = WORKFLOW_SOURCE.read_text(encoding='utf-8')
    assert ('python scripts/ci/js_module_coverage.py "$NODE_V8_COVERAGE"'
            in workflow), workflow
    tighten = 'python scripts/ci/js_module_coverage.py --tighten'
    assert tighten in workflow, workflow
    assert workflow.index(
        'python scripts/ci/line_lengths.py --tighten') \
        < workflow.index(tighten)
    assert workflow.index(tighten) < workflow.index(
        'python scripts/ci/thresholds.py --check')
    assert '- name: JavaScript per-module coverage gate' in workflow


def _skill_decisions(path=SKILL_SOURCE):
    source = path.read_text(encoding='utf-8')
    text = _normalised(source)
    paragraph = _normalised(''.join(
        block for block in source.split('\n\n') if MEMBER in block))
    return {
        'owner': '.github/ci-thresholds.json' in text
        and MEMBER in text,
        'command': ('python3 scripts/ci/js_module_coverage.py --tighten'
                    in text),
        'remedy': 'uncovered' in paragraph and 'never raised' in paragraph,
        'reads_skill': 'tests/test_js_module_coverage.py' in text,
    }


def test_skill_names_owner_command_remedy_and_reader(tmp):
    del tmp
    decisions = _skill_decisions()
    assert all(decisions.values()), decisions


def test_skill_mutations_are_caught_independently(tmp):
    source = SKILL_SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('owner', MEMBER, 'js_coverage_table'),
        ('command',
         'python3 scripts/ci/js_module_coverage.py --tighten',
         'python3 scripts/ci/js_module_coverage.py --raise'),
        ('reads_skill', 'tests/test_js_module_coverage.py', 'no suite'),
        ('remedy', 'never raised by hand',
         'raised by hand when a test is slow'),
    )
    for name, old, new in mutations:
        path = Path(tmp) / f'{name}.md'
        path.write_text(source.replace(old, new), encoding='utf-8')
        assert not _skill_decisions(path)[name], name


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='jsmodulecoverage_')


if __name__ == '__main__':
    raise SystemExit(main())
