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
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _git, _normalised  # noqa: E402
from _wfgraph import _job_names  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

ROOT = _util.ROOT
sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
SCRIPT = ROOT / 'scripts' / 'ci' / 'type_error_baseline.py'
THRESHOLDS_SOURCE = ROOT / '.github' / 'ci-thresholds.json'
# A second binding of the path _ratchet_fixture holds, kept rather than
# imported: the reserved set's python limb is definitions-only, so this
# Assign is invisible to the control that would settle the question.
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'
WORKFLOW_DIR = ROOT / '.github' / 'workflows'
CONFIG_NAME = 'pyrightconfig.tests.json'
RATCHET_RUN = 'python3 scripts/ci/type_error_baseline.py'
# Both runners discover suites by glob, so either one puts this suite in the
# job's scope whether or not the job names it.
SUITE_RUNNERS = ('run_tests.py', 'coverage_suites.py')
INSTALL_DEV = 'pip install -r requirements-dev.txt'
# The step every suite-running job carries, and the name the installer writes
# what it installed under. The name is read back by the control below, so it
# is the whole contract between the script and the test that checks it.
LINT_INSTALLER = 'python scripts/ci/install_lint_tools.py'
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
INSTALLER_SOURCE = ROOT / 'scripts' / 'ci' / 'install_lint_tools.py'
# A job reaches the suite tree either by naming a runner or by naming a suite
# file. SUITE_RUNNERS recognises the first; the second is the door a third
# spelling walks through, and it is the one this file has to account for.
SUITE_DOOR = re.compile(
    r'tests/test_|run_tests[.]py|coverage_suites[.]py|time_tests[.]py'
    r'|pytest')
# The doors that are not a sanctioned runner, and what each one is. A route
# added without being named here is red until a human judges it, which is the
# property no membership test over runner basenames can have on its own.
SUITE_DOORS = {
    ('tests.yml', 'timed'):
        'scripts/ci/time_tests.py globs tests/test_*.py and runs each',
    ('timed-timings.yml', 'refresh'):
        'five suites are run by path rather than through a runner',
}


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


def _thresholds_document(baseline=None):
    data = _thresholds().load(THRESHOLDS_SOURCE)
    data['module_size_baseline'] = {}
    data['long_line_baseline'] = {}
    data['type_error_baseline'] = {} if baseline is None else dict(baseline)
    return data


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


def _job_runs(workflow, job):
    """The ordered `run:` values of every step in one workflow job."""
    mapping = complete_job_mapping(workflow, job)
    assert mapping is not None, f'the workflow has no {job} job'
    return [step.get('run', '') for step in mapping['steps']]


def _workflow_jobs(marker):
    """Every job in every workflow with a step whose `run:` names `marker`.

    Read off what the jobs run, not off a list of their names: a job that
    globs the suites belongs to this control whichever workflow file it was
    added to, and a control that could only see one file was exactly the
    defect a third job in a second file walked past.
    """
    found = []
    for source in sorted(WORKFLOW_DIR.glob('*.yml')):
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            runs = _job_runs(workflow, job)
            if any(marker in run for run in runs):
                found.append((source.name, job, runs))
    return found


def _suite_jobs():
    """Every job in every workflow that runs a suite runner."""
    return [entry for runner in SUITE_RUNNERS
            for entry in _workflow_jobs(runner)]


def _door_jobs():
    """`(workflow, job)` for every job whose `run:` reaches the suite tree.

    The complement of `_suite_jobs`, and deliberately not the same walk: that
    one reads a substring of each `run:` and globs `*.yml`, while the claim
    this supports is about everything the substring cannot see — a suite file
    named by path, a third runner, a workflow the runner list never imagined.
    Both extensions, because a `.yaml` workflow is a workflow.
    """
    sources = sorted(WORKFLOW_DIR.glob('*.yml')) + sorted(
        WORKFLOW_DIR.glob('*.yaml'))
    found = set()
    for source in sources:
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            if any(SUITE_DOOR.search(run) for run in _job_runs(workflow, job)):
                found.add((source.name, job))
    return found


def _tool_name(node):
    """The tool a `shutil.which` call names, or None for any other call."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'which'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'shutil'):
        return None
    argument = node.args[0] if node.args else None
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _dotted_or_bare_name(node):
    """The bare name a call or a raise names, or ''."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ''


def _skips(body):
    """Whether the statements in an `if` body skip rather than fail."""
    names = ('skip', 'skipTest', 'SkipTest')
    for statement in body:
        for part in ast.walk(statement):
            call = isinstance(part, ast.Call)
            if call and _dotted_or_bare_name(part) in names:
                return True
            if isinstance(part, ast.Raise):
                name = _dotted_or_bare_name(part.exc)
                if name in names or name.endswith('Skipped'):
                    return True
    return False


def _none_test(test, name, missing):
    """Whether `test` asks whether `name` is `missing` (None or not)."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return not missing and _none_test(test.operand, name, True)
    if isinstance(test, ast.BoolOp):
        return any(_none_test(value, name, missing) for value in test.values)
    if isinstance(test, ast.Name):
        return test.id == name
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name):
        return (test.left.id == name and len(test.comparators) == 1
                and isinstance(test.ops[0], (ast.Is, ast.IsNot, ast.Eq))
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None)
    return False


def _tool_roles():
    """`(skipped, present)` tool names, read off the suites' own source.

    A suite that SKIPS on a missing tool has decided the machine may lack it;
    one that ASSERTS it, or RUNS it without asking, has decided it may not —
    and a suite that cannot do its work without the binary fails loudly when
    it is gone. A tool in both is already covered by a control that reports a
    failure rather than a skip, so a job that does not install it cannot
    report green having checked nothing about it. A tool in neither is not a
    requirement the tree states at all.
    """
    skipped, present = set(), set()
    for source in sorted((ROOT / 'tests').rglob('*.py')):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        parent = {child: node for node in ast.walk(tree)
                  for child in ast.iter_child_nodes(node)}
        present |= _command_words(tree)
        for node in ast.walk(tree):
            tool = _tool_name(node)
            if tool is not None:
                _role_of_lookup(tree, parent, node, tool, skipped, present)
    return skipped, present


_SUBPROCESS = ('run', 'Popen', 'check_call', 'check_output')


def _command_words(tree):
    """Tools a suite runs as a command, asking nothing about their absence."""
    words = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = _dotted_or_bare_name(node)
        if not (callee in _SUBPROCESS and _names_subprocess(node.func)):
            continue
        argv = node.args[0]
        if isinstance(argv, (ast.List, ast.Tuple)) and argv.elts:
            first = argv.elts[0]
            named = isinstance(first, ast.Constant)
            if named and isinstance(first.value, str):
                words.add(first.value)
    return words


def _names_subprocess(func):
    """Whether a call goes through the subprocess module, aliased or not."""
    if isinstance(func, ast.Attribute):
        return (isinstance(func.value, ast.Name)
                and func.value.id.rsplit('.', 1)[-1] == 'subprocess')
    return isinstance(func, ast.Name) and func.id.rsplit('.', 1)[-1] in (
        'subprocess', 'sp')


def _role_of_lookup(tree, parent, node, tool, skipped, present):
    """Record how the suite that wrote this `which` call treats absence."""
    current = node
    while current in parent:
        current = parent[current]
        if isinstance(current, ast.If) and node in ast.walk(current.test):
            (skipped if _skips(current.body) else present).add(tool)
            return
        if isinstance(current, ast.Assert) and node in ast.walk(current.test):
            present.add(tool)
            return
        if isinstance(current, ast.stmt):
            if (isinstance(current, ast.Assign)
                    and isinstance(current.targets[0], ast.Name)):
                _role_of_binding(tree, current.targets[0].id, tool, skipped,
                                 present)
            return


def _role_of_binding(tree, name, tool, skipped, present):
    """Record how the suite guards the tool bound to `name`."""
    parts = list(ast.walk(tree))
    if any(_skips(part.body) for part in parts
           if isinstance(part, ast.If) and _none_test(part.test, name, True)):
        skipped.add(tool)
    if any(_none_test(part.test, name, False) for part in parts
           if isinstance(part, ast.Assert)):
        present.add(tool)


def _declared_tools():
    """The tool set the shared installer says it installs."""
    return frozenset(_util.load(INSTALLER_SOURCE, 'lint_installer').TOOLS)


def _unjournalled():
    """Skip-guarded tools that no suite treats as unconditionally present.

    The `requires=` channel on `_util.runner` is the other machine-readable
    one, and it is not read here: its only value in the tree is the prose
    string `'Chromium and Node'`, so a control that demanded a job install
    that string would be asserting something no job can satisfy and no plant
    could falsify. A tool named in prose is a requirement, not a binary.
    """
    skipped, present = _tool_roles()
    return skipped - present


def _require_resolvable(tools):
    """Every recorded tool must resolve on PATH, or this fails.

    A skip here would be the defect this control exists to close: the runner
    reports a skip as a pass, so a broken install would read as a clean gate.
    """
    missing = [tool for tool in tools if shutil.which(tool) is None]
    assert not missing, (
        f'the installer recorded {", ".join(missing)} in ${LINT_TOOLS_ENV}, '
        f'and {", ".join(missing)} does not resolve on PATH. A suite that '
        'skips on one of them skips silently here, so this is a failure and '
        'not a skip: fix the install step, not this control')


def test_the_ratchet_runs_in_ci_after_the_checker_is_installed(tmp):
    """CI measures the test-tree scope, and does so once the checker exists."""
    del tmp
    found = _workflow_jobs(RATCHET_RUN)
    assert found, (
        f'no workflow job runs {RATCHET_RUN!r}; without this step nothing '
        'measures the second type-checker scope, and a reorder or a careless '
        'merge that drops it reproduces exactly issue 983 with CI still '
        'green')
    for source, job, runs in found:
        install = next(
            (index for index, run in enumerate(runs)
             if INSTALL_DEV in run), None)
        assert install is not None, (
            f'the {job} job in {source} no longer installs the pinned type '
            'checker, so the ratchet has no pyright to run')
        assert runs.index(RATCHET_RUN) > install, (
            f'the {job} job in {source} runs {RATCHET_RUN!r} before the type '
            'checker is installed, so the step would fail for want of '
            'pyright rather than measure anything')


def test_every_suite_running_job_installs_the_toolchain_it_drives(tmp):
    """This suite drives the real ``pyright`` binary, so the jobs that run
    it install the file that pins pyright.

    ``needs:`` sequences jobs; it does not share an environment between them,
    so the lint and type-check jobs already installing requirements-dev.txt
    leaves the suite jobs without pyright however green those jobs are.
    """
    del tmp
    found = [entry for runner in SUITE_RUNNERS
             for entry in _workflow_jobs(runner)]
    assert found, (
        'no workflow job runs a suite runner, so this control has found '
        'nothing to check and would pass on a tree that runs no suites at '
        'all')
    for source, job, runs in found:
        assert any(INSTALL_DEV in run for run in runs), (
            f'the {job} job in {source} runs a suite runner, which discovers '
            'this suite and drives the real pyright binary, but the job '
            f'never installs {INSTALL_DEV!r} that pins it; on a runner '
            'without pyright already cached the suite cannot find the tool '
            'it is written to exercise')


def test_every_suite_running_job_installs_the_tools_its_suites_may_skip_on(
        tmp):
    """A suite that skips on a missing binary skips on every leg, always.

    `needs:` sequences jobs without sharing an environment between them, so
    the job that lints the workflows hands the suite jobs nothing. An
    installer step in one of them leaves the others skipping silently, which
    is the shape issue 1353 was: the suites that drive actionlint and
    shellcheck reported green on every leg and verified nothing.
    """
    del tmp
    found = _suite_jobs()
    # Not vacuous: an enumeration that finds nothing is the same green a
    # correct one does not produce, so the set has to be bigger than one.
    assert len(found) >= 2, (
        f'only {len(found)} job(s) run a suite runner, so this control is '
        'reading a set too small to be the whole set of them: '
        f'{[(source, job) for source, job, _ in found]}')
    declared = ', '.join(sorted(_declared_tools()))
    unjournalled = sorted(_unjournalled() - _declared_tools())
    assert not unjournalled, (
        'the suites skip on '
        f'{", ".join(unjournalled)} and nothing treats them as present, so '
        'a suite-running job that has not installed them skips in silence '
        f'on every leg; scripts/ci/install_lint_tools.py declares '
        f'{declared}, and the next binary a suite skips on has to be added '
        'there')
    for source, job, runs in found:
        assert any(LINT_INSTALLER in run for run in runs), (
            f'the {job} job in {source} runs a suite runner, which discovers '
            'every suite by glob, and the suites it discovers skip on '
            f'{declared} when they are absent; the job never runs '
            f'{LINT_INSTALLER!r}, so on a runner without them those suites '
            'skip instead of running and the job reports green')


def test_the_installer_declares_a_tool_and_the_suites_state_one(tmp):
    """Both halves of the derivation are read, so neither can read empty."""
    del tmp
    skipped, present = _tool_roles()
    assert skipped, (
        'no suite skips on a missing tool, so the tool set this control '
        'derives from tests/ is empty and the control is satisfied by a set '
        'with nothing in it; has the skip idiom moved?')
    assert _declared_tools(), (
        f'{LINT_INSTALLER!r} declares no tools, so it installs nothing and '
        'every job that runs it gains a step and no tool')
    assert present, (
        'no suite treats a tool as unconditionally present either, so the '
        'half that decides a tool needs no install cannot have anything to '
        'exclude, and every tool would be demanded of every job')


def test_no_job_reaches_the_suites_by_a_door_this_control_does_not_name(tmp):
    """The job set is closed: the sanctioned runners, plus a named residue.

    Membership in `_suite_jobs` is a substring test over two runner
    basenames, so a job that reaches the suite tree by any other mechanism is
    invisible to it — and a control that cannot see a job cannot hold it to
    anything. Naming the doors that exist turns the enumeration into a claim
    about the complement: a new route is red until somebody says what it is.
    """
    del tmp
    sanctioned = {(source, job) for source, job, _ in _suite_jobs()}
    residue = _door_jobs() - sanctioned
    named = set(SUITE_DOORS)
    assert residue == named, (
        f'doors into the suite tree that SUITE_DOORS does not name: '
        f'{sorted(residue - named)}; named doors that are gone: '
        f'{sorted(named - residue)}. The jobs reaching the suites are the '
        'ones naming a sanctioned runner plus the ones SUITE_DOORS lists; '
        'name the new door there with what it is, and judge whether it has '
        'to install what the suites skip on')


def test_every_tool_the_installer_recorded_resolves_on_path(tmp):
    """What the installer says it installed has to be there afterwards.

    Gated on the variable the installer writes, so this reaches the three
    suite jobs and nowhere else; off a CI leg the installer has not run and
    there is no subject to check. A leg where it DID run and the binary is
    still missing fails below rather than skipping — the whole defect is a
    check that reported success having verified nothing.
    """
    del tmp
    recorded = os.environ.get(LINT_TOOLS_ENV)
    if recorded is None:
        return
    _require_resolvable(tuple(tool for tool in recorded.split(',') if tool))


def test_a_recorded_tool_path_cannot_find_is_reported_as_a_failure(tmp):
    """The failure branch of the check above, reached on every run.

    The gate above only runs where the installer ran, so nothing else
    exercises what it does when a tool is missing. Driving it here is what
    makes that branch tested rather than merely written.
    """
    del tmp
    absent = 'daedalus-no-such-tool'
    try:
        _require_resolvable((absent,))
    except AssertionError as error:
        assert absent in str(error), error
    else:
        raise AssertionError(
            f'{absent!r} does not resolve on PATH and the check passed it; '
            'the check is the last thing standing between a broken install '
            'and a green suite job')


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
                         _thresholds_document({'tests/typed.py': 1}))
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
                         _thresholds_document())
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
    repo, target = _repo(
        tmp, 'zero', {'tests/typed.py': _clean()}, _thresholds_document(),
        config=_config(exclude=('tests',)))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _policy().SCOPE_REMEDY in red.stderr, red.stderr
    assert 'within the type-error policy' not in red.stdout, red.stdout


def test_a_run_with_no_tracked_test_module_at_all_is_unanalysed(tmp):
    """The empty scope, not a mismatched one: nothing is tracked and
    nothing is analysed, so the counts agree and only ``expected == 0``
    can tell a clean run from a gate pointed at no test tree at all."""
    repo, target = _repo(tmp, 'empty', {}, _thresholds_document())
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
                          'helper.py': _clean()}, _thresholds_document())
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
        _thresholds_document(), config=_config(exclude=('tests/skipped.py',)))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _policy().SCOPE_REMEDY in red.stderr, red.stderr
    assert 'within the type-error policy' not in red.stdout, red.stdout


def test_a_baseline_entry_naming_a_gone_file_is_missing(tmp):
    repo, target = _repo(tmp, 'missing', {'tests/kept.py': _clean()},
                         _thresholds_document({'tests/gone.py': 3}))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'missing' in red.stderr, red.stderr
    assert _policy().STALE_ENTRY_REMEDY in red.stderr, red.stderr


def test_a_baseline_entry_whose_file_is_clean_is_graduated(tmp):
    repo, target = _repo(tmp, 'graduated', {'tests/kept.py': _clean()},
                         _thresholds_document({'tests/kept.py': 1}))
    red = _gate(repo, target)
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'graduated' in red.stderr, red.stderr
    assert _policy().STALE_ENTRY_REMEDY in red.stderr, red.stderr


def test_the_success_line_states_the_analysed_count(tmp):
    repo, target = _repo(tmp, 'count', {'tests/a.py': _clean(),
                                        'tests/b.py': _clean()},
                         _thresholds_document())
    green = _gate(repo, target)
    assert green.returncode == 0, (green.stdout, green.stderr)
    expected = '2 test modules analysed, within the type-error policy\n'
    assert green.stdout == expected, green.stdout


def test_tighten_lowers_drops_zeroed_and_leaves_raised(tmp):
    repo, target = _repo(
        tmp, 'tighten',
        {'tests/a.py': _typed(2), 'tests/b.py': _clean(),
         'tests/c.py': _typed(4)},
        _thresholds_document(
            {'tests/a.py': 5, 'tests/b.py': 3, 'tests/c.py': 1}))
    done = _gate(repo, target, '--tighten')
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'tightened the type-error baseline' in done.stdout, done.stdout
    after = _thresholds().load(target)['type_error_baseline']
    assert after == {'tests/a.py': 2, 'tests/c.py': 1}, after


def test_tighten_reports_nothing_moved(tmp):
    repo, target = _repo(tmp, 'steady', {'tests/a.py': _typed(2)},
                         _thresholds_document({'tests/a.py': 2}))
    done = _gate(repo, target, '--tighten')
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert 'no test module lost a type error' in done.stdout, done.stdout


def test_tighten_refuses_a_broken_scope_and_writes_nothing(tmp):
    """--tighten may not record a baseline measured by a broken scope."""
    repo, target = _repo(
        tmp, 'tighten-scope', {'tests/typed.py': _typed(2)},
        _thresholds_document({'tests/typed.py': 5}),
        config=_config(exclude=('tests',)))
    before = target.read_bytes()
    red = _gate(repo, target, '--tighten')
    assert red.returncode != 0, (red.stdout, red.stderr)
    assert 'unanalysed' in red.stderr, red.stderr
    assert _normalised(_policy().SCOPE_REMEDY) in _normalised(red.stderr), \
        red.stderr
    assert target.read_bytes() == before


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
