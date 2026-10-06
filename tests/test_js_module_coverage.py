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
from js_lines import code_lines  # noqa: E402

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


def test_unrecorded_carries_its_own_remedy_naming_both_actions(tmp):
    """A file with no record cannot be told to cover its lines and stop.

    `unrecorded` is the one kind whose subject is a module the record has
    never seen, and the only one with no entry to lower: `tightened()`
    iterates the record, so --tighten provably cannot create the entry
    the refusal demands. Sharing `grown`'s remedy there would print a bar
    no reader can meet at admission time -- 100% line coverage on a brand
    new module, which this repository's own norm is far below -- and
    would contradict the bootstrap the seed itself used. So the remedy
    must name the reviewed hand edit, and it must be its own constant so
    that editing one cannot silently edit the other.
    """
    del tmp
    policy = _policy()
    assert policy.REMEDY_FOR['unrecorded'] == policy.UNRECORDED_REMEDY
    assert policy.UNRECORDED_REMEDY != policy.UNCOVERED_REMEDY
    assert 'at its measured uncovered count' in policy.UNRECORDED_REMEDY
    assert 'reviewed diff' in policy.UNRECORDED_REMEDY
    assert 'never adds one' in policy.UNRECORDED_REMEDY
    assert 'cover the uncovered lines' in policy.UNRECORDED_REMEDY


def test_tightening_still_cannot_add_the_entry_the_remedy_names(tmp):
    """The remedy says a reviewer adds the entry, not that --tighten does.

    Both halves have to hold: the prose may tell a reader to add the
    entry in their diff, and the tool must still be incapable of adding
    it on its own. If tightening could add, a run on main would admit
    whatever coverage happened to be measured that day.
    """
    del tmp
    policy = _policy()
    assert policy.tightened({}, {'new.js': 4}) is None
    assert policy.tightened({'a.js': 2}, {'a.js': 2, 'new.js': 4}) is None


def test_the_gate_reuses_the_totals_gate_attribution(tmp):
    """The gate must reuse the totals gate's attribution, not a second copy.

    With no dump at all every tracked file is wholly uncovered, so the
    counts are exactly the totals gate's executable-line set.
    """
    counts = _policy().tracked_uncovered_counts(Path(tmp) / 'absent')
    assert set(counts) == set(js_coverage.tracked_sources(ROOT))
    assert counts['dashboard/sections/tabs.js'] > 0, counts


def test_violations_reports_each_kind_and_nothing_on_a_clean_pair(tmp):
    """The whole four-key dict, so each filter's negative space is driven.

    `ok.js` is the cell that decides the `unrecorded` filter: a tracked
    file the run covered completely measures 0, and 0 is not a missing
    record. Dropping the `count > 0` guard from that filter is the repair
    a plausible fix reaches for, it leaves every other case here passing,
    and it makes the real gate refuse all fifteen fully covered modules on
    every run — so the clean pair below must carry a zero-count file that
    has no record.
    """
    del tmp
    policy = _policy()
    baseline = {'a.js': 2, 'b.js': 1, 'gone.js': 3, 'done.js': 1}
    counts = {'a.js': 3, 'b.js': 1, 'done.js': 0,
              'new.js': 1, 'ok.js': 0}
    found = policy.violations(counts, baseline)
    assert found == {
        'grown': {'a.js': (3, 2)},
        'unrecorded': {'new.js': 1},
        'missing': ['gone.js'],
        'graduated': {'done.js': 0},
    }, found
    clean = policy.violations({'a.js': 2, 'b.js': 0}, {'a.js': 2})
    assert not any(clean.values()), clean
    assert sorted(clean) == ['graduated', 'grown', 'missing', 'unrecorded']


def test_every_tracked_module_satisfies_the_per_module_policy(tmp):
    """`violations()` over the repository's own JavaScript, not a fixture.

    A suite run has no V8 dump, so the count each file carries here is
    read back from the record: a recorded file stands at its recorded
    number, and a file with no record stands at 0, which is what a run
    that covered it completely measured. Running it over the real
    thirty-eight files is what puts the fifteen fully covered modules
    into the negative space of `unrecorded` as fifteen zero-count entries
    with no record — the cell a fixture of four invented names never
    reaches, and the one that decides whether `unrecorded` still
    distinguishes 0 from a missing record. That is what these four
    assertions earn here, and the `code_lines` loop below earns the rest:
    every tracked module has an executable line, so a file nothing can
    cover is never quietly treated as a file that needs no record.

    What they do NOT establish is anything about the record's own
    content, and the construction is why: `counts` is the record unioned
    with the tracked set, so every recorded key is present (nothing can
    be `missing`), every recorded file stands at its own recorded number
    (nothing can be `grown`), and `_baseline` refuses a zero count (so
    nothing can be `graduated`). Those three still discriminate a
    mutation of the filters themselves — `>` against `>=`, `not in`
    against `in` — which is why they stay. The refusals against real
    record content belong to the other two tests and are not duplicated
    here: an entry naming a file the tree does not ship is caught by
    `test_the_seed_names_only_tracked_javascript_that_still_has_code`,
    and each kind's own shape is caught by the whole-dict assertion
    above. A recorded count that is merely wrong is not catchable here at
    all; the measurement lives in the coverage job, which is the only
    place the dumps exist.
    """
    del tmp
    policy = _policy()
    baseline = _thresholds().js_coverage_baseline(_document())
    assert baseline, 'the ratchet records nothing, so it exempts everything'
    sources = js_coverage.tracked_sources(ROOT)
    counts = dict.fromkeys(sources, 0) | dict(baseline)
    found = policy.violations(counts, baseline)
    assert not found['missing'], found['missing']
    assert not found['graduated'], found['graduated']
    assert not found['unrecorded'], found['unrecorded']
    assert not found['grown'], found['grown']
    for rel, text in sources.items():
        assert code_lines(text, rel), rel


def test_tightened_lowers_a_file_that_lost_cover(tmp):
    del tmp
    policy = _policy()
    tightened = policy.tightened({'a.js': 9, 'b.js': 4},
                                 {'a.js': 3, 'b.js': 4})
    assert tightened == {'a.js': 3, 'b.js': 4}
    assert tightened is not None


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
    setattr(policy, 'tracked_uncovered_counts',
            lambda *a, **kw: {'tabs.js': 9})
    status, stdout, stderr = _main(
        policy, [str(Path(tmp) / 'absent'), '--thresholds', str(target)])
    assert status == 1
    assert stdout == ''
    assert 'tabs.js' in stderr, stderr
    assert '9' in stderr and '1' in stderr, stderr
    assert policy.UNCOVERED_REMEDY in stderr, stderr
    assert 'Traceback' not in stderr


def _tighten(policy, target, counts):
    """`--tighten` against a thresholds document of the caller's own."""
    setattr(policy, 'tracked_uncovered_counts',
            lambda *a, **kw: counts)
    return _main(policy, [str(target.parent / 'absent'), '--tighten',
                          '--thresholds', str(target)])


def test_main_tighten_writes_what_it_measured_and_says_so(tmp):
    """`--tighten` rewrites the record from the counts it was handed.

    The document written is the caller's own, in a temporary tree: the
    repository's `.github/ci-thresholds.json` is never this gate's output,
    and a control that pointed `--thresholds` at it would make a unit test
    an editor of the policy it is asserting about.
    """
    policy, thresholds = _policy(), _thresholds()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data[MEMBER] = {'tabs.js': 9, 'done.js': 4}
    thresholds.write(target, data)
    status, stdout, stderr = _tighten(
        policy, target, {'tabs.js': 3, 'done.js': 0})
    assert status == 0, (stdout, stderr)
    assert stdout == 'tightened the per-module coverage baseline\n', stdout
    assert stderr == '', stderr
    assert thresholds.load(target)[MEMBER] == {'tabs.js': 3}, stderr


def test_main_tighten_says_nothing_was_lowered_and_writes_nothing(tmp):
    """A record with nothing to lower is reported, and left exactly as it was.

    The bytes are compared rather than the parsed document: a `--tighten`
    that rewrote the file with an identical document would still move the
    mtime, and a ratchet whose output is committed does not want that.
    """
    policy, thresholds = _policy(), _thresholds()
    target = Path(tmp) / 'thresholds.json'
    data = _document()
    data[MEMBER] = {'tabs.js': 9}
    thresholds.write(target, data)
    before = target.read_bytes()
    status, stdout, stderr = _tighten(policy, target, {'tabs.js': 9})
    assert status == 0, (stdout, stderr)
    assert stdout == 'no file lost an uncovered line\n', stdout
    assert stderr == '', stderr
    assert target.read_bytes() == before


def test_main_reports_a_thresholds_document_it_could_not_read(tmp):
    """An unreadable record is refused by what it says, not by a traceback.

    The path is asserted as `repr()` because the refusal interpolates the
    `OSError`, and an `OSError` spells its filename quoted and escaped: on
    Windows every backslash in it appears twice, so the raw spelling is not
    in the message at all.
    """
    policy = _policy()
    absent = Path(tmp) / 'no-such-thresholds.json'
    status, stdout, stderr = _main(
        policy, [str(Path(tmp) / 'absent'), '--thresholds', str(absent)])
    assert status == 1
    assert stdout == '', stdout
    assert repr(str(absent)) in stderr, stderr
    assert 'cannot read thresholds' in stderr, stderr
    assert 'Traceback' not in stderr, stderr


def _js_repo(tmp, name, sources, baseline):
    """A tracked repository named by one hand-written V8 dump."""
    thresholds = _thresholds()
    repo = Path(tmp) / name
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    for rel in ('thresholds.py', 'reseed.py', 'js_lines.py',
                'js_coverage.py', 'js_module_coverage.py'):
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
        'admission': 'at its measured uncovered count' in paragraph
        and 'reviewed diff' in paragraph
        and 'never add one' in paragraph
        and 'it will not' in paragraph,
        'reads_skill': 'tests/test_js_module_coverage.py' in text,
    }


def test_skill_names_owner_command_remedy_admission_and_reader(tmp):
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
        ('admission', 'at its measured',
         'at whatever count the next run measures'),
        ('admission', 'never add one', 'adds one on the next run'),
        ('admission', 'pick it up; it will not', 'pick it up; it will'),
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
