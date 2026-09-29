#!/usr/bin/env python3
"""The binaries a suite skips on must be installed in every job that runs it.

A suite that shells out to a real binary SKIPS when the binary is absent, and
a skip is a pass to every runner and every aggregate. So a job without the
binary is green having verified nothing, silently, on every leg — which is
the shape issue 1353 was for actionlint and shellcheck.

Both halves of that sentence are controls here. Which jobs run a suite is
derived from what the jobs run, not from a list of their names; which tools
the suites can skip on is derived from the suites' own source, not from a
list. Nothing in either direction is maintained by hand except the residue
the last control names, and that is a claim about the complement rather than
another list to keep in step.

The derivation is not extended here: `_workflow_jobs` and `SUITE_RUNNERS`
come from `test_type_errors.py`, whose control on the same job set already
uses them, so the two controls cannot disagree about which jobs run suites.

Every control is a guard, so a green run proves the tree still matches it and
nothing more. The proof that it bites is a planted defect in a real target:
the installer step removed from a real suite job, a skip arm added to a real
suite for a binary nothing installs, and a suite-running job planted in a
workflow this file never reads.
"""
import ast
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from test_type_errors import (  # noqa: E402
    SUITE_RUNNERS, WORKFLOW_DIR, _workflow_jobs)

ROOT = _util.ROOT
# The step every suite-running job carries, and the name the installer writes
# what it installed under. That name is the whole contract between the script
# and the test reading it back, so the two spell it once each and nowhere
# else.
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
_SUBPROCESS = ('run', 'Popen', 'check_call', 'check_output')
_SKIP_NAMES = ('skip', 'skipTest', 'SkipTest')


def _suite_jobs():
    """Every job in every workflow that runs a suite runner."""
    return [entry for runner in SUITE_RUNNERS
            for entry in _workflow_jobs(runner)]


def _job_runs(workflow, job):
    """The ordered `run:` values of every step in one workflow job."""
    mapping = complete_job_mapping(workflow, job)
    assert mapping is not None, f'the workflow has no {job} job'
    return [step.get('run', '') for step in mapping['steps']]


def _door_jobs():
    """`(workflow, job)` for every job whose `run:` reaches the suite tree.

    The complement of `_suite_jobs`, and deliberately not the same walk: that
    one reads a substring of each `run:` and globs `*.yml`, while the claim
    this supports is about everything that substring cannot see — a suite
    file named by path, a third runner, a `.yaml` workflow the runner list
    never imagined. Both extensions, because a `.yaml` workflow is a
    workflow.
    """
    sources = sorted(WORKFLOW_DIR.glob('*.yml')) + sorted(
        WORKFLOW_DIR.glob('*.yaml'))
    found = set()
    for source in sources:
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            if any(SUITE_DOOR.search(run)
                   for run in _job_runs(workflow, job)):
                found.add((source.name, job))
    return found


def _constants(tree):
    """The module's own `NAME = 'literal'` bindings, so a `which` resolves.

    A tool named through a module constant is still the tool the suite
    requires, and reading only literal arguments would make the derivation
    blind to the spelling half the tree uses.
    """
    bound = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            bound[node.targets[0].id] = node.value.value
    return bound


def _tool_name(node, bound):
    """The tool a `shutil.which` call names, or None for any other call."""
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'which'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'shutil'):
        return None
    argument = node.args[0] if node.args else None
    if isinstance(argument, ast.Name):
        return bound.get(argument.id)
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
    """Whether the statements in an `if` body skip rather than fail.

    The SKIP is the distinguishing property, not the lookup: most of the
    tree's `shutil.which` uses are `assert node, '...'`, which state a
    requirement the jobs already meet. Reading a skip as an assertion — or
    the reverse — is what makes a control like this one either demand a
    package manager install git, or miss the next binary entirely.
    """
    for statement in body:
        for part in ast.walk(statement):
            if isinstance(part, ast.Call):
                if _dotted_or_bare_name(part) in _SKIP_NAMES:
                    return True
            elif isinstance(part, ast.Raise):
                name = _dotted_or_bare_name(part.exc)
                if name in _SKIP_NAMES or name.endswith('Skipped'):
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


def _command_words(tree):
    """Tools a suite runs as a command, asking nothing about their absence."""
    words = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _dotted_or_bare_name(node) not in _SUBPROCESS:
            continue
        if not _names_subprocess(node.func):
            continue
        argv = node.args[0]
        if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
            continue
        first = argv.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
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


def _skip_texts(tree):
    """Every literal a skip call or a skip raise renders.

    A skip that NAMES the tool it is skipping on is a skip on that tool,
    whatever plumbing carries the lookup to the skip. Reading only the
    `which()` binding's own guard misses the common shape where the lookup
    goes into a dict and the guard reads it out in another function, and a
    control that misses that reads green having checked nothing — the whole
    shape issue 1353 is.
    """
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted_or_bare_name(node)
        elif isinstance(node, ast.Raise):
            name = _dotted_or_bare_name(node.exc)
        else:
            continue
        if name in _SKIP_NAMES or name.endswith('Skipped'):
            texts.extend(part.value for part in ast.walk(node)
                         if isinstance(part, ast.Constant)
                         and isinstance(part.value, str))
    return texts


_ROLES = []


def _tool_roles():
    """`(skipped, present)` tool names, read off the suites' own source.

    Read once per process: the answer is a property of the tree, and three
    controls asking the same question should not parse 340 modules six times
    between them.
    """
    if not _ROLES:
        _ROLES.append(_derive_tool_roles())
    return _ROLES[0]


def _derive_tool_roles():
    """The two tool sets, one pass to enumerate and one to classify.

    A suite that SKIPS on a missing tool has decided the machine may lack it;
    one that ASSERTS it, or RUNS it without asking, has decided it may not —
    and a suite that cannot do its work without the binary fails loudly when
    it is gone. A tool in both is already covered by a control that reports a
    failure rather than a skip, so a job that does not install it cannot
    report green having checked nothing about it. A tool in neither is not a
    requirement the tree states at all.

    Two passes, and the second re-parses rather than holding every module's
    tree at once: a skip can only be matched against the tools the tree
    names, and 340 trees is more to hold than a 4 GB cap should be asked
    for.
    """
    sources = sorted((ROOT / 'tests').rglob('*.py'))
    candidates = set()
    for source in sources:
        tree = ast.parse(source.read_text(encoding='utf-8'))
        bound = _constants(tree)
        candidates |= {name for name in
                       (_tool_name(node, bound) for node in ast.walk(tree))
                       if name}
    skipped, present = set(), set()
    for source in sources:
        tree = ast.parse(source.read_text(encoding='utf-8'))
        bound = _constants(tree)
        present |= _command_words(tree)
        for text in _skip_texts(tree):
            skipped |= {tool for tool in candidates
                        if re.search(rf'\b{re.escape(tool)}\b', text)}
        parent = {child: node for node in ast.walk(tree)
                  for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            tool = _tool_name(node, bound)
            if tool is not None:
                _role_of_lookup(tree, parent, node, tool, skipped, present)
    return skipped, present


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


def test_every_suite_running_job_installs_the_tools_its_suites_may_skip_on(
        tmp):
    """A suite that skips on a missing binary skips on every leg, always.

    `needs:` sequences jobs without sharing an environment between them, so
    the job that lints the workflows hands the suite jobs nothing. An
    installer step in one of them leaves the others skipping silently, which
    is the shape issue 1353 was.
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
        f'the suites skip on {", ".join(unjournalled)} and nothing treats '
        'them as present, so a suite-running job that has not installed them '
        'skips in silence on every leg; scripts/ci/install_lint_tools.py '
        f'declares {declared}, and the next binary a suite skips on has to '
        'be added there')
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
        'doors into the suite tree that SUITE_DOORS does not name: '
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='linttools_')


if __name__ == '__main__':
    raise SystemExit(main())
