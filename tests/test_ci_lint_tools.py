#!/usr/bin/env python3
"""The binaries a suite skips on must be installed in every job that runs it.

A suite that shells out to a real binary SKIPS when the binary is absent,
and a skip is a pass to every runner and every aggregate. So a job without
the binary is green having verified nothing, silently, on every leg — the
shape issue 1353 was for actionlint and shellcheck.

Which jobs reach a suite is derived from what the jobs' steps are given;
which tools the suites can skip on is derived from the suites' own source.
Nothing in either direction is maintained by hand except the residue one
control names, and that is a claim about the complement rather than
another list to keep in step. The job derivation is shared with the
control already on that set, through `tests/_suite_jobs.py`.

Every control here is a guard: a green run proves the tree still matches
it and nothing more. The proof that it bites is a planted defect in a
real target — the installer step removed from a real suite job, a skip
arm added to a real suite for a binary nothing installs, and a suite
job planted in a workflow this file never reads.
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _lint_tool_roles import (  # noqa: E402
    _derive_tool_roles, _tool_roles)
from _suite_jobs import NAMES, RUNNER, _door_jobs  # noqa: E402

ROOT = _util.ROOT
# The step every suite-running job carries, and the name the installer writes
# what it installed under. That name is the whole contract between the script
# and the test reading it back, so the two spell it once each and nowhere
# else.
LINT_INSTALLER = 'python scripts/ci/install_lint_tools.py'
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
INSTALLER_SOURCE = ROOT / 'scripts' / 'ci' / 'install_lint_tools.py'
# The doors that reach the suites without FINDING them: a fixed list of
# suite paths written in the step, so a suite added tomorrow cannot walk
# through one of these at all. What each entry owes is that its list stays
# true — the suites it names are the suites it runs — and nothing about
# installing tools, because nothing new can arrive through it.
#
# This is the residue of a DERIVED set, not a second copy of it. Every
# other route is either a `RUNNER` door, which the control below holds to
# the installer step, or a door this walk cannot see at all: a step whose
# reach is decided by a composite action or a container entry point has no
# source in this repository to read. That bound is the walk's, stated here
# where the table meets it.
SUITE_DOORS = {
    ('timed-timings.yml', 'refresh'):
        'five suites are run by path rather than through a runner',
}
# Tools the suites skip on that no job installs, named here with a reason
# each rather than derived. The derivation cannot supply this list: it read
# the tree's own `present` set as covering a tool, and `present` is an
# assertion the tree may never reach — the jq assert in
# `tests/_coverage_comment_publication.py` sits behind a stub-environment
# condition that is false on every ordinary run, which is how a suite
# skipping on a binary nothing installs read green. Each entry is a claim
# about the runner image, written down so a human can check it and change
# it, and no derivation can be asked to confirm it.
SHIPPED_BY_THE_IMAGE = {
    'git': 'every job here checks out through actions/checkout, which '
           'runs git, and the hosted images ship it',
    'node': 'the hosted images ship it and no workflow step installs it, '
            'so a suite that skips on node skips on every leg; that is a '
            'property of the image, and it is the reason this entry exists',
}


def _runner_doors():
    """Every job that FINDS its suites, and so can be reached by a new one."""
    return [door for door in _door_jobs() if door[3] == RUNNER]


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
# A tool the tree both insists on and tolerates the absence of. Nothing in
# the tree is written this way today; two independent plants were, and both
# read green, which is what these cases are here to refuse.
BOTH_ON = {
    'asserted elsewhere, skipped here':
        'def required():\n    assert shutil.which(TOOL), "the parser is '
        'the fixture"\n\n\n'
        'def optional():\n    found = shutil.which(TOOL)\n'
        '    if not found:\n        _util.skip("no parser")\n',
    'run unguarded, skipped when it cannot start':
        'def unguarded():\n    subprocess.run([TOOL, "--version"], '
        'check=True)\n\n\n'
        'def optional():\n    try:\n'
        '        subprocess.run([TOOL, "--format", "json"], check=True)\n'
        '    except FileNotFoundError:\n        _util.skip("no parser")\n',
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


def test_a_tool_the_tree_also_asserts_is_still_required(tmp):
    """A skip is a skip, whatever another function says about the binary.

    The required set was once `skipped - present`, on the argument that a
    tool in both is covered by a control that fails rather than skips. That
    argument is one the derivation cannot check — it cannot tell an assert
    the tree always reaches from one behind a condition that is false on
    every ordinary run — and two reviews drove a false green through it: a
    `which('jq')` + skip arm grew the skip set, jq was in the present set
    because of the assert behind `needs_jq_stub`, and the control stayed
    green with no job installing jq. The second case is the sharper one,
    because the old derivation read the FileNotFoundError skip as a tool
    the tree had DECIDED may not be absent: it inverted the site.
    """
    del tmp
    for label, body in BOTH_ON.items():
        source = [_PREAMBLE + body + '\n']
        skipped, present = _derive_tool_roles(source)
        assert 'gojq' in _unjournalled(source), (
            f'the {label} shape leaves gojq out of the required set: '
            f'skipped={sorted(skipped)}, present={sorted(present)}')


def _unjournalled(sources=None):
    """The tools a suite may run without, minus what the runner image brings.

    The property is *a tool the suites can skip on*, so the required set is
    the skip set itself. The earlier narrowing subtracted the tools the tree
    treats as present, on the argument that a tool in both is already
    covered by a control which fails rather than skips — and that argument
    is false of the code as written, because the derivation could not tell
    an assert the tree always reaches from one it never does. Two reviews
    drove a false green through it: a suite skipping on `jq` grew the skip
    set, `jq` was in the present set because of an assert behind a stub
    environment that is off on every ordinary run, and the control stayed
    green with no job installing `jq`. So the subtraction is gone, and
    what genuinely needs no install is named below with a reason per entry.

    The `requires=` channel on `_util.runner` is the other machine-readable
    one, and it is not read here: its only value in the tree is the prose
    string `'Chromium and Node'`, so a control that demanded a job install
    that string would be asserting something no job can satisfy and no plant
    could falsify. A tool named in prose is a requirement, not a binary.
    """
    skipped, _present = (_tool_roles() if sources is None
                         else _derive_tool_roles(sources))
    return skipped - set(SHIPPED_BY_THE_IMAGE)


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

    The job set is the DERIVED one, not the two sanctioned runner
    basenames. A job that reaches the suites by any other discovery
    mechanism is a job a suite added tomorrow walks through, and a control
    that cannot see it cannot hold it to anything.
    """
    del tmp
    found = _runner_doors()
    # Not vacuous: an enumeration that finds nothing is the same green a
    # correct one does not produce, so the set has to be bigger than one.
    assert len(found) >= 2, (
        f'only {len(found)} job(s) find their suites, so this control is '
        'reading a set too small to be the whole set of them: '
        f'{[(source, job) for source, job, _, _ in found]}')
    declared = ', '.join(sorted(_declared_tools()))
    unjournalled = sorted(_unjournalled() - _declared_tools())
    assert not unjournalled, (
        f'the suites under tests/ skip on {", ".join(unjournalled)}, and '
        'no job installs them, so a suite-running job on a runner without '
        'them skips in silence on every leg; '
        f'scripts/ci/install_lint_tools.py declares {declared}, and the '
        'next binary a suite skips on has to be added there, or listed in '
        'SHIPPED_BY_THE_IMAGE with the reason no job has to install it')
    for source, job, runs, _mechanism in found:
        assert any(LINT_INSTALLER in run for run in runs), (
            f'the {job} job in {source} finds its suites by discovery, so a '
            'suite added tomorrow reaches it whatever it is called; the '
            f'suites it finds skip on {_unjournalled_sentence()}, and the '
            f'job never runs {LINT_INSTALLER!r}, so on a runner without '
            'them those suites skip instead of running and the job reports '
            'green')


def _unjournalled_sentence():
    """What the suites actually skip on, for a message that must be true.

    The message this replaces named the installer's declaration and called
    it the derived set. On this tree the two differ: the derived set is
    empty until 1307's suites land, so the control was asserting in its own
    output that the suites it discovers skip on actionlint and shellcheck,
    which no suite here does.
    """
    unjournalled = sorted(_unjournalled() - _declared_tools())
    return (', '.join(unjournalled) if unjournalled
            else 'no tool this control can currently derive')


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
        'no suite treats a tool as unconditionally required either, so the '
        'second half of the derivation is reading an empty set: the two '
        'channels together are what tells a binary a job must install from '
        'one the suite fails loudly without, and one of them going empty '
        'means the recogniser that fills it has stopped recognising')


def test_no_job_reaches_the_suites_by_a_door_this_control_does_not_name(tmp):
    """The job set is closed: what the walk derives, plus what it cannot.

    `_door_jobs` reads each step's resolved inputs and follows the tracked
    file a step runs, so a third `scripts/ci/` runner, a shell loop over
    `tests/*.py`, `unittest discover -s tests`, a `with:`-passed path and a
    `.yaml` workflow are all routes it sees. What it cannot see is a route
    whose reach lives outside this repository — a composite action, a
    container entry point — and that bound belongs in the table beside the
    doors it does see, not in a claim this control cannot make.

    The two kinds are judged differently, which is why they are two
    things. A `runner` door FINDS its suites, so a suite added tomorrow
    walks through it and the control above holds it to the installer step.
    A `names` door runs a list written in the step, so nothing new can
    arrive through it and the only thing owed is that the list stays true
    — which is what its reason says.
    """
    del tmp
    named_doors = {(source, job) for source, job, _runs, mechanism
                   in _door_jobs() if mechanism == NAMES}
    residue = named_doors - set(SUITE_DOORS)
    stale = set(SUITE_DOORS) - named_doors
    assert not residue and not stale, (
        'doors into the suite tree that reach the suites by naming them and '
        f'SUITE_DOORS does not account for: {sorted(residue)}; entries in '
        f'SUITE_DOORS that are no longer such a door: {sorted(stale)}. A '
        'door that FINDS its suites needs no entry — the control above '
        'holds it to the installer step. A door that NAMES them carries a '
        'fixed list, so name it here with that fact, and say what the list '
        'is, because nothing new can reach a job that does not glob.')


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
