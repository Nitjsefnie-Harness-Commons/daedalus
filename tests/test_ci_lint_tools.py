#!/usr/bin/env python3
"""The binaries a suite skips on must be installed in every job that runs it.

A suite that shells out to a real binary SKIPS when the binary is absent,
and a skip is a pass to every runner and every aggregate. So a job without
the binary is green having verified nothing, silently, on every leg — the
shape issue 1353 was for actionlint and shellcheck.

Which jobs run a suite is derived from what the jobs run; which tools the
suites can skip on is derived from the suites' own source. Nothing in
either direction is maintained by hand except the residue one control
names, and that is a claim about the complement rather than another list
to keep in step. The job derivation is shared with the control already on
that set, through `tests/_suite_jobs.py`.

Every control here is a guard: a green run proves the tree still matches
it and nothing more. The proof that it bites is a planted defect in a
real target — the installer step removed from a real suite job, a skip
arm added to a real suite for a binary nothing installs, and a suite
job planted in a workflow this file never reads.
"""
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _suite_jobs import (  # noqa: E402
    SUITE_RUNNERS, WORKFLOW_DIR, _ordered_job_runs, _workflow_jobs)
from _lint_tool_roles import (  # noqa: E402
    _derive_tool_roles, _tool_roles)
from _wfgraph import _job_names  # noqa: E402

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


def _suite_jobs():
    """Every job in every workflow that runs a suite runner."""
    return [entry for runner in SUITE_RUNNERS
            for entry in _workflow_jobs(runner)]


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
                   for run in _ordered_job_runs(workflow, job)):
                found.add((source.name, job))
    return found


def _declared_tools():
    """The tool set the shared installer says it installs."""
    return frozenset(_util.load(INSTALLER_SOURCE, 'lint_installer').TOOLS)


# Ways a suite says a binary may be missing, one per answer to the same
# question. Every one of them SKIPS or RETURNS on absence, and every skip
# message names the parser rather than the binary, so none of them is
# visible to the channel that matches a tool named in a message: a
# derivation that reads these is reading the guard, not the wording.
GUARDED_ON = {
    'inline identity':
        'if shutil.which(TOOL) is None:\n    _util.skip("no parser")',
    'inline truthiness':
        'if not shutil.which(TOOL):\n    _util.skip("no parser")',
    'inline not-identity':
        'if shutil.which(TOOL) is not None:\n    return',
    'bound identity':
        'found = shutil.which(TOOL)\nif found is None:\n'
        '    _util.skip("no parser")',
    'bound truthiness':
        'found = shutil.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'bound equality':
        'found = shutil.which(TOOL)\nif found == None:\n'
        '    _util.skip("no parser")',
    'bound inequality':
        'found = shutil.which(TOOL)\nif found != None:\n'
        '    _util.skip("no parser")',
    'bound membership':
        'found = shutil.which(TOOL)\nif found in (None,):\n'
        '    _util.skip("no parser")',
    'bound conjunction':
        'found = shutil.which(TOOL)\nif found is None or not extra:\n'
        '    _util.skip("no parser")',
    'aliased import':
        'found = sh.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'annotated constant':
        'found = shutil.which(NAMED)\nif not found:\n'
        '    _util.skip("no parser")',
    'constant bound in a function':
        'def probe():\n    local = TOOL\n    found = shutil.which(local)\n'
        '    if not found:\n        _util.skip("no parser")\n',
    'command that cannot be started':
        'try:\n    subprocess.run([TOOL, "--version"], check=True)\n'
        'except FileNotFoundError:\n    _util.skip("no parser")',
    'aliased command that cannot be started':
        'try:\n    sp.run([TOOL, "--version"], check=True)\n'
        'except OSError:\n    _util.skip("no parser")',
}
_PREAMBLE = ('import shutil\nimport subprocess\nimport shutil as sh\n'
             'import subprocess as sp\nTOOL = "gojq"\nNAMED: str = "gojq"\n')


def test_every_spelling_of_an_absence_guard_is_read_as_one_operation(tmp):
    """No suite is invisible because its author spelled the guard differently.

    Driven through the derivation rather than planted in `tests/`, because
    the question is what the recognisers read and every spelling would
    otherwise be a tracked change. One of these is the two-step
    truthiness guard that reads as a PRESENCE test — a suite skipping on a
    binary no job installs — and the control missed it for a whole wave.
    """
    del tmp
    for label, body in GUARDED_ON.items():
        skipped, present = _derive_tool_roles([_PREAMBLE + body + '\n'])
        assert 'gojq' in skipped, (
            f'the {label} spelling puts no tool in the skip set, so a suite '
            'written that way is invisible to this control and its binary '
            f'needs no install: skipped={sorted(skipped)}, '
            f'present={sorted(present)}')


REQUIRED_ON = {
    'inline assert':
        'assert shutil.which(TOOL), "the parser runs the fixture"',
    'bound assert':
        'found = shutil.which(TOOL)\n'
        'assert found, "the parser runs the fixture"',
    'a command run without asking':
        'subprocess.run([TOOL, "--version"], check=True)',
}


def test_an_asserted_tool_is_a_requirement_and_not_a_skip(tmp):
    """The other half of the distinction, so the fix is not one-sided.

    The two-step idiom every helper in the tree uses is
    `found = which(X); if not found: skip`. Reading anything that mentions
    the name as a skip would file `assert found, '...'` as a tool the
    suites tolerate the absence of, and demand an install for a binary
    every job already has.
    """
    del tmp
    for label, body in REQUIRED_ON.items():
        skipped, present = _derive_tool_roles([_PREAMBLE + body + '\n'])
        assert 'gojq' in present, (
            f'the {label} shape puts no tool in the present set: '
            f'skipped={sorted(skipped)}, present={sorted(present)}')


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
