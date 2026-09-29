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
real target — the installer step removed from a real suite job, the setup
step removed from a real suite job, a skip arm added to a real suite for a
binary nothing installs, and a third suite runner planted in a workflow
the door walk then has to classify. Every workflow is read now:
`_door_jobs` globs `*.yml` and `*.yaml`, so no workflow is one this file
does not look at, and the bound on what the walk can see is stated in
`tests/_suite_jobs.py` where it lives.

THREE mechanisms answer a tool the suites skip on, and the controls below
are split across them so each asks about its own: the shared installer
installs one, a setup step declares another, and a claim about the runner
image excuses a third. The closure control holds the three to the derived
set, so the next tool a suite skips on reaches one of them rather than a
fourth hand-written exemption — which is what `node` was, and the reason
the `node` entry cannot come back.
"""
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _lint_tool_roles import (  # noqa: E402
    _derive_tool_roles, _tool_roles)
from _actionlint import _job_step as _actionlint_job_step  # noqa: E402
from _suite_jobs import (  # noqa: E402
    NAMES, RUNNER, _actions_before, _door_jobs, _suite_step)
from _wfgraph import _tests_yml  # noqa: E402

ROOT = _util.ROOT
# The shared installer's path, and the name it writes what it installed
# under. That name is the whole contract between the script and the test
# reading it back, so the two spell it once each and nowhere else.
#
# The path, NOT the command that runs it. A job's install step is correct
# because it runs the installer, and `suites`, `coverage-matrix` and
# `publish` reach the installer through a root checkout while `timed` checks
# its two trees out into subdirectories and reaches it as
# `head/scripts/ci/install_lint_tools.py`. A constant holding the whole
# invocation pinned that second spelling, so the correct path turned the
# control red with a message arguing for the revert — this branch's own
# lesson arriving for the third time, after the tool set and the door set.
# Matching the path with a substring covers both, because the prefixed form
# ends with the root-relative one.
#
# WHAT THIS CANNOT SEE, both directions, because a reader deciding how far
# to trust a green run needs the bounds rather than the claim:
#
# - It cannot tell a step that RUNS the installer from a comment quoting
#   the path inside a `run:` block. That error runs toward a red control
#   naming a comment, which is a cheap edit.
# - It cannot tell a step that runs the installer from a step that NAMES
#   it and would fail at runtime. A path that does not resolve in the job's
#   own directory layout is not a step that runs the installer, and this
#   substring is green on it. That error runs the OTHER way — toward a
#   green control over a broken job — and it is not hypothetical: this
#   branch shipped exactly that, when `timed` named a root-relative script
#   in a job that checks its trees out into subdirectories, and the
#   control stayed green through two review rounds on the strength of this
#   assertion. N1 fixed the instance; nothing in the tree catches the
#   class, and no spelling of this assertion can, because the information
#   that is missing is whether the file exists from where the step runs.
#   The control that would need it is a workflow control resolving each
#   `run:` path against a checkout layout, which does not exist here.
INSTALLER_PATH = 'scripts/ci/install_lint_tools.py'
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
INSTALLER_SOURCE = ROOT / INSTALLER_PATH
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
#
# `actions/checkout` is not a declaration of git: it is a client of it, and
# an action that runs git is not a step that says a machine may lack it.
SHIPPED_BY_THE_IMAGE = {
    'git': 'every job here checks out through actions/checkout, which '
           'runs git, and the hosted images ship it',
}
# Tools a suite skips on that no job installs through
# `scripts/ci/install_lint_tools.py`, and the setup action a job has to
# carry to DECLARE it. A third mechanism beside the installer's TOOLS and
# the image claim, because a tool the runtime distributes is provisioned by
# a step rather than by an install list, and a control that asked the
# installer to install it would be asking for a mechanism this repository
# does not use.
#
# The action NAME, never the pinned commit: which action a job names is a
# property of the job, while the commit it is pinned at is a property of the
# workflow, and a table holding the SHA would have to be edited on every
# dependabot bump to keep saying the same thing. The pin every declaration
# has to use is a different control's, on a family of actions.
DECLARED_BY = {
    'node': 'actions/setup-node',
}


def _runs_installer(run):
    """Whether a step's command runs the shared installer, from any tree.

    The path is a suffix of every working spelling of it — a job that
    checks the repository out under `head/` runs
    `python head/scripts/ci/install_lint_tools.py` — so matching the path
    asks the property the control means rather than the command one job
    happened to use.
    """
    return INSTALLER_PATH in run


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
#
# THIS IS A LIST OF WITNESSES, NOT AN ENUMERATION, and the control that
# reads it says so where a reader meets it. The name of the test that
# drives it says "every spelling", and that is a claim this table cannot
# make: adding a fifteenth shape turns it red, which is the point — a
# recogniser that cannot see a shape nobody thought of fails nothing —
# but it also means the table's real job is to hold one witness per way
# the recogniser's STRUCTURE can fail, so that a structural change which
# drops an arm is red rather than silent. The shapes still open, recorded
# so the next round does not re-derive them, and kept in step with what
# `tests/_lint_tool_roles._lookup_target_names` says it cannot read:
#
# - a `which` over a loop variable, where the tool is never a CANDIDATE at
#   all, so no guard-shape fix reaches it;
# - `os.popen`;
# - a string argv under `shell=True`, where the whole command is one
#   string rather than an argv element;
# - a locally named skip helper whose name is neither `skip*` nor
#   `*Skipped`, which is a class bound — the recogniser matches call
#   NAMES, not call intent — rather than one defect;
# - a multi-target assignment, `a = b = which(t)` and the comma-separated
#   `a = b, c = which(t), None` beside it, which are now READ and are rows
#   in the table below rather than open shapes. They were open until this
#   round, and the previous list did not say so, which is the failure this
#   paragraph exists to stop repeating: a disclosure a reader consults has
#   to name what the recogniser actually declines.
#
# And one shape that is named because it CANNOT RUN, so handling it would
# be handling a program nobody can execute: `(a, b) = c = which(t)` unpacks
# a path string into two names, which is a ValueError. It binds nothing,
# and `_lookup_target_names` says so in the same words rather than leaving
# a reader to assume the silence was an oversight.
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
    'annotated binding of the result':
        'found: str = shutil.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'tuple-unpacked binding of the result':
        'found, _rest = shutil.which(TOOL), None\nif not found:\n'
        '    _util.skip("no parser")',
    'second slot of a tuple-unpacked binding':
        'def probe():\n    _first, found = None, shutil.which(TOOL)\n'
        '    if not found:\n        _util.skip("no parser")\n',
    'second name of a chained assignment':
        'first = found = shutil.which(TOOL)\nif not found:\n'
        '    _util.skip("no parser")',
    'first name of a chained assignment':
        'first = found = shutil.which(TOOL)\nif not first:\n'
        '    _util.skip("no parser")',
    'tuple target in a comma-separated target list':
        'whole = first, rest = shutil.which(TOOL), None\n'
        'if not first:\n    _util.skip("no parser")',
    'name target in a comma-separated target list':
        'whole = first, rest = shutil.which(TOOL), None\n'
        'if not whole:\n    _util.skip("no parser")',
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
    the question is what the recognisers read and every shape would
    otherwise be a tracked change. One of these is the two-step
    truthiness guard that reads as a PRESENCE test — a suite skipping on a
    binary no job installs — and the control missed it for a whole wave.

    The NAME says "every spelling" and `GUARDED_ON` does not deliver
    that: it is a list of witnesses, one per way the recogniser's
    structure can fail, and the list is evidence rather than the gate.
    Adding a shape turns this red, which is exactly what a witness is
    for — a recogniser that cannot see a shape nobody thought of fails
    nothing — but a reader must not take the name as a completeness claim
    the table does not carry. The shapes still open, and the reason each
    is not a row here, are named beside the table.
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


def _unjournalled(sources=None, share=()):
    """The tools a suite may run without, minus the mechanisms that answer.

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

    There are now THREE mechanisms rather than one, and each has its own
    control, so this asks the question for all of them or for one: a caller
    that names no `share` gets the residue no mechanism answers, and a
    caller that names a mechanism's share gets back what is left of the
    residue for that mechanism alone. Without the split the installer
    control would demand a job install `node`, which is not a question it
    can be answered on — setup-node is a different mechanism, and a control
    asking the wrong one reads either a defect or the mechanism working.

    The `requires=` channel on `_util.runner` is the other machine-readable
    one, and it is not read here: its only value in the tree is the prose
    string `'Chromium and Node'`, so a control that demanded a job install
    that string would be asserting something no job can satisfy and no plant
    could falsify. A tool named in prose is a requirement, not a binary.
    """
    skipped, _present = (_tool_roles() if sources is None
                         else _derive_tool_roles(sources))
    return ((skipped - _declared_tools() - set(SHIPPED_BY_THE_IMAGE)
             - set(DECLARED_BY)) | set(share))


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
    unjournalled = sorted(_unjournalled())
    assert not unjournalled, (
        f'the suites under tests/ skip on {", ".join(unjournalled)}, and '
        'no job installs them, so a suite-running job on a runner without '
        'them skips in silence on every leg; '
        f'scripts/ci/install_lint_tools.py declares {declared}, and the '
        'next binary a suite skips on has to be added there, or declared '
        'in DECLARED_BY with the setup action a job must carry, or listed '
        'in SHIPPED_BY_THE_IMAGE with the reason no job has to install it')
    for source, job, runs, _mechanism in found:
        assert any(_runs_installer(run) for run in runs), (
            f'the {job} job in {source} finds its suites by discovery, so a '
            'suite added tomorrow reaches it whatever it is called; the '
            f'suites it finds skip on {_unjournalled_sentence()}, and the '
            f'job never runs {INSTALLER_PATH!r}, so on a runner without '
            'them those suites skip instead of running and the job reports '
            'green')


def _workflow_text(source):
    """The tracked workflow of a derived door, read fresh."""
    return (ROOT / '.github' / 'workflows' / source).read_text(
        encoding='utf-8')


def test_every_suite_running_job_declares_the_tools_it_does_not_install(tmp):
    """A tool the installer does not install is declared by a step, first.

    The four jobs that reach the suites all inherit `node` from the
    `ubuntu-latest` image, and nothing in this repository says so. That is a
    claim about a hosted image rather than about the job: the moment the
    image drops node, or a job moves to a runner that never had it, every
    suite that skips on it goes quiet on every leg at once and the matrix
    reports green having verified nothing.

    ORDER is the property, not membership. A `setup-node` step after
    `python run_tests.py` declares nothing — the suites have already skipped
    by then — and a set-membership check passes it, so the check is written
    against the steps that come BEFORE the first step reaching the suites.
    Which step that is comes from `_suite_step`, the same walk the door
    derivation uses, so the two cannot disagree about it.

    The tool set is this mechanism's share of the derivation, asked of
    `_unjournalled` the same way the installer control asks for its own. A
    control that read `DECLARED_BY` directly would ask a different question
    on a tree where the table names a tool no suite skips on, and would
    demand a declaration for it; the closure control is what holds that
    table to the tree, and it is a different control for a different
    question.
    """
    del tmp
    for source, job, _runs, _mechanism in _runner_doors():
        workflow = _workflow_text(source)
        reach = _suite_step(workflow, job)
        assert reach is not None, (
            f'the {job} job in {source} is a door and no step in it reaches '
            'the suites any more; the two are read from one function and '
            'disagreeing means one of them is stale')
        before = _actions_before(workflow, job, reach[0])
        share = _unjournalled(share=DECLARED_BY)
        missing = sorted(tool for tool in share
                         if DECLARED_BY[tool] not in before)
        assert not missing, (
            f'the {job} job in {source} finds its suites by discovery, and '
            f'its first suite-running step is step {reach[0] + 1}, before '
            f'which it uses {sorted(before) or "no action at all"}. The '
            f'suites it finds skip on {", ".join(sorted(missing))}, which '
            f'the shared installer does not install, so add the step that '
            'declares it '
            f'({", ".join(DECLARED_BY[tool] for tool in missing)}) ABOVE that '
            'step. A declaration placed after it is not a declaration: the '
            'suites have already skipped by then.')


def _mechanism_shares():
    """`(name, tools)` for each mechanism a skipped tool can be answered by."""
    return (('scripts/ci/install_lint_tools.py',
             frozenset(_declared_tools())),
            ('SHIPPED_BY_THE_IMAGE', frozenset(SHIPPED_BY_THE_IMAGE)),
            ('DECLARED_BY', frozenset(DECLARED_BY)))


def _mechanism_residue(sources=None):
    """`(residue, overlap, stale)` for the mechanisms and the skip set.

    A tool the suites skip on is answered by exactly one mechanism, and
    this is the question that holds the three of them to it: answered by
    none, answered by more than one, or held in a share the tree no longer
    derives. `sources` is the seam the derivation already has, so a suite
    that skips on a tool no mechanism answers is asked about without planting
    a file in `tests/`; over a synthetic source only the residue arm means
    anything, because a module that skips on one tool leaves every other
    mechanism's entry looking stale.
    """
    skipped, _present = (_tool_roles() if sources is None
                         else _derive_tool_roles(sources))
    shares = _mechanism_shares()
    answered = {tool for _name, tools in shares for tool in tools}
    residue = sorted(skipped - answered)
    overlap = sorted(tool for tool in skipped
                     if sum(tool in tools for _name, tools in shares) > 1)
    stale = sorted((name, sorted(tools - skipped))
                   for name, tools in shares if tools - skipped)
    return residue, overlap, stale


def test_every_skipped_tool_is_answered_by_exactly_one_mechanism(tmp):
    """A tool the suites skip on reaches a mechanism, or this is not a set.

    Three mechanisms answer a skipped tool: the shared installer installs
    it, a step declares it, or a claim about the runner image says the image
    has it. Nothing keeps a fourth tool from being waved through with a
    hand-written exemption, which is exactly what `node` was — an entry
    whose reason read "the hosted images ship it and no workflow step
    installs it", so the control stated the defect as the rule that permitted
    it and a suite skipping on node was green on every leg by construction.

    The three failure shapes are the ones a reader can act on: a tool no
    mechanism answers (add it to one), a tool two of them claim (one of the
    two is redundant, and while both stand neither is checked), and an entry
    for a tool the tree no longer skips on (the claim outlived its
    subject). Modelled on the door control above, which is the same
    residue-versus-derived question over the job set.
    """
    del tmp
    residue, overlap, stale = _mechanism_residue()
    assert not residue and not overlap and not stale, (
        'tools the suites skip on that the mechanisms do not account for '
        f'between them: {residue}; tools claimed by more than one mechanism, '
        f'so none of them is the one being enforced: {overlap}; entries in a '
        f'mechanism that no suite skips on any more: {stale}. Every skipped '
        'tool is installed by scripts/ci/install_lint_tools.py, declared by a '
        'setup step in DECLARED_BY, or named in SHIPPED_BY_THE_IMAGE with the '
        'reason the image has it — exactly one of the three, and a new one '
        'goes in the table that mechanism owns rather than in a new table.')


def test_the_derivation_reaches_the_mechanism_closure_not_only_the_table(tmp):
    """The other half of the control above: a tool no mechanism answers.

    The tree on this run is answered by all three mechanisms, so a green
    residue here says nothing about whether the DERIVATION would notice a
    fourth tool. A suite that skips on a binary nothing installs is the
    defect the whole file is for, and it reaches this control only through
    the recognisers — so it is driven through the `sources` seam, which is
    what that seam is for, rather than by planting a file in `tests/`.
    """
    del tmp
    source = [_PREAMBLE + 'if not shutil.which(TOOL):\n'
              '    _util.skip("no parser")\n']
    residue, overlap, stale = _mechanism_residue(source)
    assert residue == ['gojq'], (
        'a suite that skips on gojq puts no tool in the residue, so the '
        'closure this control reads is not over the derived skip set and a '
        'binary nothing installs reads as answered: '
        f'residue={residue}, overlap={overlap}, stale={stale}')


def _unjournalled_sentence():
    """What the suites actually skip on, for a message that must be true.

    The message this replaces named the installer's declaration and called
    it the derived set. On this tree the two differ: the derived set is
    empty until 1307's suites land, so the control was asserting in its own
    output that the suites it discovers skip on actionlint and shellcheck,
    which no suite here does.
    """
    unjournalled = sorted(_unjournalled())
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
        f'{INSTALLER_PATH!r} declares no tools, so it installs nothing and '
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

    Gated on the variable the installer writes, so this reaches the `suites`,
    `coverage-matrix` and `publish` jobs in `tests.yml` and `release.yml`,
    and the `timed` job in `tests.yml`, and nowhere else: those four are
    the jobs whose steps run `python scripts/ci/install_lint_tools.py`.
    Off a CI leg the installer has not run and there is no subject to
    check. A leg where it DID run and the binary is still missing fails
    below rather than skipping — the whole defect is a check that reported
    success having verified nothing.

    The early return is a SKIP carrying its reason, not a bare pass. A run
    log has to distinguish "ran, and every tool resolved" from "did not
    run", and a control whose subject is this branch's whole motivation
    cannot be the one that reports nothing about itself.
    """
    del tmp
    recorded = os.environ.get(LINT_TOOLS_ENV)
    if recorded is None:
        _util.skip(
            f'${LINT_TOOLS_ENV} is unset, so the installer has not run on '
            'this leg and there is nothing recorded to resolve; the static '
            'halves of this file still gate')
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


def test_the_installed_build_is_the_one_the_actionlint_job_pins(tmp):
    """The job's pin and the installer's are one build, and this says so.

    Both spell `ACTIONLINT_VERSION` in a different file, and a pin spelled
    twice drifts — the reason the installer reads shellcheck's version out
    of `requirements-test.txt` instead. Its own comment says why this one
    keeps two.

    What makes the drift SILENT is this suite's own subject. The
    workflow-lint suites read the JOB's pin, compare it against the
    installed binary, and SKIP on a mismatch: with the fork build on PATH
    and the job pin reverted to `1.7.12`, test_ci_workflows reports 31/33
    with exit 0, two of them skipping. "Two" is the two whose VERDICT the
    mismatch decides, not the two that reach the binary: three tests launch
    actionlint under a divergence, and one of the three
    (`test_a_lint_run_without_shellcheck_is_skipped`) runs a full lint and
    never reaches the version arm at all. A skip is a pass to every runner
    and every aggregate, so the disagreement is pinned here, where it is an
    assertion failure.
    """
    del tmp
    installer = _util.load(INSTALLER_SOURCE, 'lint_installer_pins')
    job = _tests_yml()
    pinned = re.findall(r'^\s*ACTIONLINT_VERSION:\s*(.*?)\s*(?:#.*)?$',
                        job, re.MULTILINE)
    assert len(pinned) == 1, pinned
    assert pinned[0].strip('\'"') == installer.ACTIONLINT_VERSION, (
        'the actionlint job pins '
        f'{pinned[0].strip(chr(39) + chr(34))} and the installer installs '
        f'{installer.ACTIONLINT_VERSION}; the workflow-lint suites SKIP when '
        'the two disagree, so a divergence here is a green run that linted '
        'nothing')
    # The URL is a SUBSTRING of the job's, not the other way round, so this
    # is one arm and not a disjunction: the job spells the release base and
    # appends `/v${ACTIONLINT_VERSION}/...`, so its own line contains the
    # installer's RELEASE. An `or <org> in job` beside it would be
    # satisfied by that same occurrence whichever way this points.
    #
    # The base the step NAMES, not the file and not merely the step's text:
    # a whole-file substring is satisfied by a comment quoting the old URL,
    # and a step-scoped one is satisfied too, because the run block is a
    # `>-` scalar that keeps its `#` lines. Both were planted, and both left
    # this green with the two bases genuinely diverged. What is compared is
    # the base on a non-comment line, and exactly one of them.
    #
    # ADMITTED SUBSET, and what it cannot see. This reads LINES, not shell.
    # A base ASSEMBLED FROM PARTS or split across a continuation is refused
    # rather than seen: neither puts the whole base on one line, so this
    # over-refuses a shape it might have accepted. A base consumed through a
    # variable the control does handle — the whole base on one line, read
    # once, is exactly what it looks for. What it cannot see is the residue
    # a plant confirmed: a step that names the base correctly on one line
    # and then fetches elsewhere through another variable. It is not a
    # silent pass in practice — the pinned sha256 fails on bytes that are
    # not the ones verified — so what this bounds is the JOB, not the
    # download. Red is the safe direction, and a line-based reading cannot
    # do better than this without a shell evaluator.
    downloaded = [line.strip() for line
                  in _actionlint_job_step('Install actionlint').splitlines()
                  if not line.lstrip().startswith('#')]
    bases = {line for line in downloaded if 'releases/download' in line}
    assert len(bases) == 1, (
        f'the install step names {len(bases)} release bases, and this '
        f'control reads one line rather than a shell: {sorted(bases)}')
    assert installer.RELEASE in bases.pop(), (
        f'the install step names a release base that is not '
        f'{installer.RELEASE!r}, so the checksum table the installer '
        'verifies belongs to a different release than the job downloads: a '
        'match on the version alone would install one build and lint with '
        'another')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='linttools_')


if __name__ == '__main__':
    raise SystemExit(main())
