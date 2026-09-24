#!/usr/bin/env python3
"""Contracts for the test-tree type-error ratchet and its own gate.

This is a guard, so a green run proves only that the seeded baseline still
matches the tree. The proof that the gate catches anything is a planted
defect in a real target: each violation kind below is reached by a genuine
``pyright`` run over a committed miniature tree, and every plant is sized
past the operand the comparison reads. The real-tree direction — a module
with no type error does not trip ``over`` or ``grown`` — is pinned by the
same runs, not asserted on faith.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

ROOT = _util.ROOT
sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
SCRIPT = ROOT / 'scripts' / 'ci' / 'type_error_baseline.py'
THRESHOLDS_SOURCE = ROOT / '.github' / 'ci-thresholds.json'
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'
WORKFLOW_SOURCE = ROOT / '.github' / 'workflows' / 'tests.yml'
CONFIG_NAME = 'pyrightconfig.tests.json'
RATCHET_RUN = 'python3 scripts/ci/type_error_baseline.py'


def _thresholds():
    return _util.load(ROOT / 'scripts' / 'ci' / 'thresholds.py',
                      'type_error_thresholds')


def _policy():
    return _util.load(SCRIPT, 'type_error_policy')


def _config(include=('tests',), exclude=()):
    return json.dumps({
        'typeCheckingMode': 'basic',
        'pythonVersion': '3.13',
        'include': list(include),
        'exclude': list(exclude),
        'reportMissingModuleSource': 'none',
        'pythonPlatform': 'All',
    }, indent=2) + '\n'


def _document(baseline=None):
    data = _thresholds().load(THRESHOLDS_SOURCE)
    data['module_size_baseline'] = {}
    data['long_line_baseline'] = {}
    data['type_error_baseline'] = {} if baseline is None else dict(baseline)
    return data


def _git(repo, *args):
    subprocess.run(('git', '-C', str(repo)) + args, check=True,
                   capture_output=True, env=_util.child_coverage('scrub'))


def _repo(tmp, name, files, document, config=None):
    repo = Path(tmp) / name
    (repo / '.github').mkdir(parents=True)
    (repo / 'tests').mkdir(parents=True)
    for rel, content in files.items():
        (repo / rel).write_text(content, encoding='utf-8')
    (repo / CONFIG_NAME).write_text(
        config if config is not None else _config(), encoding='utf-8')
    target = repo / '.github' / 'ci-thresholds.json'
    _thresholds().write(target, document)
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'base')
    return repo, target


def _gate(repo, target, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--root', str(repo),
         '--thresholds', str(target), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        env=_util.child_coverage('scrub'))


def _typed(count):
    """A test module carrying exactly ``count`` assignable-type errors."""
    return ''.join(
        f'value_{index}: int = "text"\n' for index in range(count))


def _clean():
    return 'x = 1\n'


def _write(repo, rel, content):
    (repo / rel).write_text(content, encoding='utf-8')


def _job_runs(job):
    """The ordered `run:` values of every step in a named workflow job."""
    workflow = WORKFLOW_SOURCE.read_text(encoding='utf-8')
    mapping = complete_job_mapping(workflow, job)
    assert mapping is not None, f'the workflow has no {job} job'
    steps = mapping['steps']
    return [step.get('run', '') for step in steps]


def _pyright_job_runs():
    """The ordered `run:` values of the workflow's pyright job's steps."""
    return _job_runs('pyright')


def test_the_ratchet_runs_in_ci_after_pyright_is_installed(tmp):
    """CI measures the test-tree scope, and does so once the checker exists."""
    del tmp
    runs = _pyright_job_runs()
    assert RATCHET_RUN in runs, (
        f'the pyright job no longer runs {RATCHET_RUN!r}; without this step '
        'nothing measures the second type-checker scope, and a reorder or a '
        'careless merge that drops it reproduces exactly issue 983 with CI '
        'still green')
    install = next(
        (index for index, run in enumerate(runs)
         if 'pip install -r requirements-dev.txt' in run), None)
    assert install is not None, (
        'the pyright job no longer installs the pinned type checker, so the '
        'ratchet has no pyright to run')
    assert runs.index(RATCHET_RUN) > install, (
        f'{RATCHET_RUN!r} runs before the type checker is installed, so the '
        'step would fail for want of pyright rather than measure anything')


def test_the_suite_jobs_install_the_toolchain_the_suites_drive(tmp):
    """This suite drives the real ``pyright`` binary, so the jobs that run
    it install the file that pins pyright.

    ``needs:`` sequences jobs; it does not share an environment between them,
    so the lint and type-check jobs already installing requirements-dev.txt
    leaves the suite jobs without pyright however green those jobs are.
    """
    del tmp
    for job in ('suites', 'coverage-matrix'):
        runs = _job_runs(job)
        assert any('pip install -r requirements-dev.txt' in run
                   for run in runs), (
            f'the {job} job runs this suite, which drives the real pyright '
            'binary, but never installs requirements-dev.txt that pins it; '
            'on a runner without pyright already cached the suite cannot '
            'find the tool it is written to exercise')


def test_the_key_format_is_forward_slash_on_every_host(tmp):
    """A backslash-bearing diagnostic path yields git's spelling of the key.

    ``git ls-files`` spells every tracked path with forward slashes on every
    host, so the diagnostic set must agree with that spelling. A ``str(Path)``
    rendering separates on the host's own separator instead, which is why a
    plain nested-path assertion is not enough to catch it: on Linux the two
    spellings coincide, so only a backslash-bearing input — wrong on every
    host — is red before the fix and green after it.
    """
    del tmp
    policy = _policy()
    root = Path('C:/repo') if os.name == 'nt' else Path('/repo')
    nested = 'tests/sub/deep/foo.py'
    assert policy._rel_key(f'{root}/{nested}', root) == nested
    windows_spelling = f'{root}\\{nested}'.replace('/', '\\')
    assert policy._rel_key(windows_spelling, root) == nested, (
        'a diagnostic path spelled with backslashes must reduce to the same '
        'forward-slash key git ls-files emits, on every host, or the tracked '
        'set and the diagnostic set disagree on Windows for every entry')


def test_a_type_error_in_a_baselined_file_is_grown(tmp):
    repo, target = _repo(tmp, 'grown', {'tests/typed.py': _typed(1)},
                         _document({'tests/typed.py': 1}))
    green = _gate(repo, target)
    assert green.returncode == 0, (green.stdout, green.stderr)
    # The recorded operand is 1; the plant makes the real count 2.
    _write(repo, 'tests/typed.py', _typed(2))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'grown' in red.stderr, red.stderr
    assert _policy().FIX_REMEDY in red.stderr, red.stderr
    _write(repo, 'tests/typed.py', _typed(1))
    restored = _gate(repo, target)
    assert restored.returncode == 0, (restored.stdout, restored.stderr)
    assert 'within the type-error policy' in restored.stdout


def test_only_a_type_error_in_a_new_test_file_is_over(tmp):
    repo, target = _repo(tmp, 'over', {'tests/clean.py': _clean()},
                         _document())
    _write(repo, 'tests/new.py', 'y = 2\n')
    _git(repo, 'add', 'tests/new.py')
    clean = _gate(repo, target)
    assert clean.returncode == 0, (clean.stdout, clean.stderr)
    _write(repo, 'tests/new.py', _typed(1))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'over' in red.stderr, red.stderr
    assert _policy().FIX_REMEDY in red.stderr, red.stderr


def test_a_run_analysing_no_file_is_unanalysed(tmp):
    """The exact shape of the original defect: the tree is excluded."""
    repo, target = _repo(tmp, 'zero', {'tests/typed.py': _clean()},
                         _document(), config=_config(exclude=('tests',)))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _policy().SCOPE_REMEDY in red.stderr, red.stderr
    assert 'within the type-error policy' not in red.stdout, red.stdout


def test_a_run_with_no_tracked_test_module_at_all_is_unanalysed(tmp):
    """The empty scope, not a mismatched one: nothing is tracked and
    nothing is analysed, so the counts agree and only ``expected == 0``
    can tell a clean run from a gate pointed at no test tree at all."""
    repo, target = _repo(tmp, 'empty', {}, _document())
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _policy().SCOPE_REMEDY in red.stderr, red.stderr
    assert 'within the type-error policy' not in red.stdout, red.stdout


def test_an_unreadable_config_is_named_in_the_refusal(tmp):
    """pyright still parses a report when its config could not be read.

    The fallback scope then analyses a different set of files, so the count
    mismatch is the observation, and pyright's own complaint — the cause —
    is what the gate surfaces next to it rather than discarding.
    """
    repo, target = _repo(tmp, 'unreadable',
                         {'tests/typed.py': _clean(),
                          'helper.py': _clean()}, _document())
    (repo / CONFIG_NAME).unlink()
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert 'could not be read' in red.stderr, red.stderr
    assert _policy().SCOPE_REMEDY in red.stderr, red.stderr


def test_a_report_without_a_summary_is_a_refusal_not_a_traceback(tmp):
    """A summary-less report fails through the ValueError path, not a
    KeyError traceback out of ``report['summary']['filesAnalyzed']``."""
    del tmp
    policy = _policy()
    original = getattr(policy, '_pyright_report')
    setattr(policy, '_pyright_report',
            lambda root: ({'generalDiagnostics': []}, ''))
    try:
        policy.analyse(ROOT)
    except ValueError as error:
        assert 'analysed-count' in str(error), error
    else:
        raise AssertionError('a report with no summary must raise ValueError')
    finally:
        setattr(policy, '_pyright_report', original)


def test_a_scope_missing_a_tracked_module_is_a_mismatch(tmp):
    repo, target = _repo(
        tmp, 'mismatch',
        {'tests/kept.py': _clean(), 'tests/skipped.py': _clean()},
        _document(), config=_config(exclude=('tests/skipped.py',)))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _policy().SCOPE_REMEDY in red.stderr, red.stderr
    assert 'within the type-error policy' not in red.stdout, red.stdout


def test_a_baseline_entry_naming_a_gone_file_is_missing(tmp):
    repo, target = _repo(tmp, 'missing', {'tests/kept.py': _clean()},
                         _document({'tests/gone.py': 3}))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'missing' in red.stderr, red.stderr
    assert _policy().STALE_ENTRY_REMEDY in red.stderr, red.stderr


def test_a_baseline_entry_whose_file_is_clean_is_graduated(tmp):
    repo, target = _repo(tmp, 'graduated', {'tests/kept.py': _clean()},
                         _document({'tests/kept.py': 1}))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'graduated' in red.stderr, red.stderr
    assert _policy().STALE_ENTRY_REMEDY in red.stderr, red.stderr


def test_the_success_line_states_the_analysed_count(tmp):
    repo, target = _repo(tmp, 'count', {'tests/a.py': _clean(),
                                        'tests/b.py': _clean()},
                         _document())
    green = _gate(repo, target)
    assert green.returncode == 0, (green.stdout, green.stderr)
    expected = '2 test modules analysed, within the type-error policy\n'
    assert green.stdout == expected, green.stdout


def test_tighten_lowers_drops_zeroed_and_leaves_raised(tmp):
    repo, target = _repo(
        tmp, 'tighten',
        {'tests/a.py': _typed(2), 'tests/b.py': _clean(),
         'tests/c.py': _typed(4)},
        _document({'tests/a.py': 5, 'tests/b.py': 3, 'tests/c.py': 1}))
    done = _gate(repo, target, '--tighten')
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'tightened the type-error baseline' in done.stdout, done.stdout
    after = _thresholds().load(target)['type_error_baseline']
    assert after == {'tests/a.py': 2, 'tests/c.py': 1}, after


def test_tighten_reports_nothing_moved(tmp):
    repo, target = _repo(tmp, 'steady', {'tests/a.py': _typed(2)},
                         _document({'tests/a.py': 2}))
    done = _gate(repo, target, '--tighten')
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'no test module lost a type error' in done.stdout, done.stdout


def test_tighten_refuses_a_broken_scope_and_writes_nothing(tmp):
    """--tighten may not record a baseline measured by a broken scope."""
    repo, target = _repo(
        tmp, 'tighten-scope', {'tests/typed.py': _typed(2)},
        _document({'tests/typed.py': 5}), config=_config(exclude=('tests',)))
    before = target.read_bytes()
    red = _gate(repo, target, '--tighten')
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _normalised(_policy().SCOPE_REMEDY) in _normalised(red.stderr), \
        red.stderr
    assert target.read_bytes() == before


def _normalised(text):
    return ' '.join(text.lower().split())


def test_each_kind_carries_its_own_remedy_in_the_mapping(tmp):
    """The mapping's shape, pinned independently of what it is printed with.

    The docstring pin reads its expectation out of ``REMEDY_FOR`` — the very
    mapping that produced the print — so it cannot fail for a remedy that is
    merely present somewhere in the docstring.  This one names which text
    belongs to which kind, and the real-CLI tests above name which text each
    refusal actually carries.
    """
    del tmp
    policy = _policy()
    assert policy.REMEDY_FOR['unanalysed'] == policy.SCOPE_REMEDY
    assert policy.REMEDY_FOR['grown'] == policy.FIX_REMEDY
    assert policy.REMEDY_FOR['over'] == policy.FIX_REMEDY
    assert policy.REMEDY_FOR['missing'] == policy.STALE_ENTRY_REMEDY
    assert policy.REMEDY_FOR['graduated'] == policy.STALE_ENTRY_REMEDY
    assert 'never raised by hand' in policy.FIX_REMEDY
    assert 'fix the type error' in policy.FIX_REMEDY
    assert 'deleted by hand' in policy.STALE_ENTRY_REMEDY
    assert 'stale entry' in policy.STALE_ENTRY_REMEDY
    assert 'pyrightconfig.tests.json' in policy.SCOPE_REMEDY


def test_script_docstring_carries_each_printed_remedy(tmp):
    del tmp
    policy = _policy()
    doc = _normalised(policy.__doc__ or '')
    for kind, remedy in policy.REMEDY_FOR.items():
        assert _normalised(remedy) in doc, (kind, remedy)


def _skill_decisions(path=SKILL_SOURCE):
    raw = path.read_text(encoding='utf-8')
    # Isolate the type-error paragraph, so a phrase the other ratchet
    # paragraphs share cannot satisfy this one's decisions for it. The
    # anchor is stable under every mutation below.
    block = next((part for part in raw.split('\n\n')
                  if 'pyrightconfig.tests.json' in part), '')
    paragraph = _normalised(block)
    return {
        'owner': ('.github/ci-thresholds.json' in paragraph
                  and 'type_error_baseline' in paragraph),
        'command': ('python3 scripts/ci/type_error_baseline.py --tighten'
                    in paragraph),
        'growth': 'fix the type error' in paragraph,
        'manual_delete': ('entry naming a file that is gone is removed by '
                          'hand' in paragraph),
        'reads_skill': 'tests/test_type_errors.py' in paragraph,
    }


def test_skill_names_the_state_owner_and_tighten_command(tmp):
    del tmp
    decisions = _skill_decisions()
    assert decisions['owner'], decisions
    assert decisions['command'], decisions
    assert decisions['growth'], decisions
    assert decisions['manual_delete'], decisions
    assert decisions['reads_skill'], decisions


def test_skill_mutations_are_caught_independently(tmp):
    source = SKILL_SOURCE.read_text(encoding='utf-8')
    mutations = (
        ('owner', 'type_error_baseline', 'type_error_table'),
        ('command', 'python3 scripts/ci/type_error_baseline.py --tighten',
         'python3 .github/ci-thresholds.json --tighten'),
        ('growth', 'fix the type error', 'raise the recorded number'),
        ('manual_delete',
         'entry naming a file that is gone\nis removed by hand',
         '--tighten removes every stale entry'),
        ('reads_skill', 'tests/test_type_errors.py',
         'tests/test_type_errors_absent.py'),
    )
    for name, old, new in mutations:
        path = Path(tmp) / f'{name}.md'
        path.write_text(source.replace(old, new), encoding='utf-8')
        assert not _skill_decisions(path)[name], name


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='typeerrors_')


if __name__ == '__main__':
    raise SystemExit(main())
