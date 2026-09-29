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
control already on that set, through `tests/_suite_jobs.py`, and the
three mechanisms a skipped tool can be answered by are read through
`tests/_lint_tool_mechanisms.py`.

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
from _actionlint import _job_step as _actionlint_job_step  # noqa: E402
from _lint_tool_mechanisms import (  # noqa: E402
    DECLARED_BY, INSTALLER_PATH, SHIPPED_BY_THE_IMAGE, _declared_tool_actions,
    _declared_tools, _mechanism_residue, _mechanism_share, _mechanism_shares,
    _runs_installer, _unjournalled)
from _lint_tool_roles import (  # noqa: E402
    BOTH_ON, GUARDED_ON, REQUIRED_ON, _PREAMBLE, _derive_tool_roles,
    _tool_roles)
from _suite_jobs import (  # noqa: E402
    NAMES, RUNNER, _actions_before, _declarations_before, _door_jobs,
    _executable_shape, _suite_step, _unclassifiable_steps)
from _wfgraph import _tests_yml  # noqa: E402

ROOT = _util.ROOT
# The name the shared installer writes what it installed under, and where
# the script lives. Both live with the mechanisms in
# `tests/_lint_tool_mechanisms.py`; the refusals below name them.
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
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


def _runner_doors():
    """Every job that FINDS its suites, and so can be reached by a new one."""
    return [door for door in _door_jobs() if door[3] == RUNNER]


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

    The four jobs that reach the suites all inherited `node` from the
    `ubuntu-latest` image, and nothing in this repository said so. That was
    a claim about a hosted image rather than about the job: the moment the
    image drops node, or a job moves to a runner that never had it, every
    suite that skips on it goes quiet on every leg at once and the matrix
    reports green having verified nothing.

    ORDER is the property, not membership, and it is measured against the
    first step that REACHES the suite tree rather than the first one that
    runs a suite in it. Those are not the same step: in `timed` the first is
    a `--help` probe that runs no suite at all, and the walk cannot tell
    `time_tests.py --help` from a run of it without encoding which
    invocations of which runner do what — the basename fingerprint the
    walk exists to refuse. So the boundary is conservative on purpose, and
    the message below says which step it is rather than claiming a suite
    ran there.

    A declaration is also a step that RUNS. `if:` is not statically
    knowable from a `uses:`, so a setup step behind a condition is refused
    rather than counted: a step that executes on no cell declares nothing,
    and counting it would be the same silent green this control exists
    against.

    The tool set is this mechanism's share of the derivation, asked of
    `_mechanism_share` the same way the installer control asks for its own.
    A control that read `DECLARED_BY` directly would ask a different
    question on a tree where the table names a tool no suite skips on; the
    closure control holds that table to the tree, and it is a different
    control for a different question.
    """
    del tmp
    for source, job, _runs, _mechanism in _runner_doors():
        workflow = _workflow_text(source)
        reach = _suite_step(workflow, job)
        assert reach is not None, (
            f'the {job} job in {source} is a door and no step in it reaches '
            'the suite tree any more; the two are read from one function '
            'and disagreeing means one of them is stale')
        before = _actions_before(workflow, job, reach[0])
        gated = sorted(f'{name} (if: {condition})' for name, condition
                       in _declarations_before(workflow, job, reach[0])
                       if condition)
        share = _mechanism_share(DECLARED_BY)
        missing = sorted(tool for tool in share
                         if DECLARED_BY[tool] not in before)
        assert not missing, (
            f'the {job} job in {source} reaches the suite tree, and its '
            f'first step that does is step {reach[0] + 1}, before which it '
            f'uses {sorted(before) or ["no action at all"]}'
            f'{", and skips the gated " + str(gated) if gated else ""}. '
            f'The suites that tree holds skip on '
            f'{", ".join(sorted(missing))}, which the shared installer does '
            f'not install, so add the step that declares it '
            f'({", ".join(DECLARED_BY[tool] for tool in missing)}) ABOVE '
            'that step. A declaration below it is not a declaration, '
            'because the walk treats any step that reaches the suite tree '
            'as the boundary — a step naming a runner counts even where it '
            'only asks one for its options — and the suites that follow '
            'would run on whatever the job happened to have.')


def test_no_exempted_tool_has_a_setup_action_somewhere_in_the_tree(tmp):
    """An image exemption is legal only where the tree offers no other way.

    The `node` entry this branch removed was not a stray exemption that
    happened to be misplaced. It was a control-shaped sentence — "the
    hosted images ship it and no workflow step installs it, so a suite
    that skips on node skips on every leg" — that made the uncontrolled
    state read as the rule permitting it. Restoring it verbatim satisfied
    every other control in this file, because an image exemption is a
    perfectly legal placement for a tool the tree has no way to declare.

    So the legality of that placement is now DERIVED rather than asserted.
    `_declared_tool_actions` reads every action name in every workflow, and
    a tool is exempt only when no action anywhere names it. `node` is
    declared by `actions/setup-node`, which the `eslint` job has used since
    before this branch existed, so the exemption stays refused no matter
    what the four suite jobs do — including if all four steps are reverted
    together. `git` stays exempt on the same evidence: no action in this
    repository names it.

    The prose reason each entry carries is now documentation of a fact
    something checks, rather than the thing making it legal.
    """
    del tmp
    declared = _declared_tool_actions()
    forbidden = sorted(
        (tool, sorted(found)) for tool, found in
        ((tool, declared.get(tool, ())) for tool in SHIPPED_BY_THE_IMAGE)
        if found)
    assert not forbidden, (
        'tools SHIPPED_BY_THE_IMAGE exempts that some action in this '
        f'repository already declares: {forbidden}. An exemption is legal '
        'only where the tree has no way to declare the tool at all, and the '
        'action that declares it is right there in the workflows. Either '
        'drop the exemption and let the control that holds the job to the '
        'declaration do its work, or delete the action — and the reason '
        'text on the entry is documentation, not the thing that makes it '
        'legal.')


def test_no_job_reaches_the_suites_through_a_file_the_walk_cannot_read(tmp):
    """The walk's own limit, named rather than assumed.

    `_runs_suites` reads Python, so a step naming a tracked `.sh` wrapper
    classifies to "not a runner" by exactly the path a Python file that
    genuinely is not a runner takes. The job then reaches nothing, leaves
    the door set, and every control that reads the door set goes green on
    a job that runs the suites by way of a shell script.

    This does not teach the control to read shell — that is a different
    task. It refuses the shape instead, which is the direction that errs
    toward red: a step running a file an interpreter would execute is a
    refusal, and a step naming a manifest is not, because a step that
    reads `pyproject.toml` runs no suite whatever the manifest says.

    The message carries the whole unclassifiable set, so the data-file
    entries that are legitimately here are visible in every refusal rather
    than only in this control's source.
    """
    del tmp
    residue = _unclassifiable_steps()
    executable = sorted((source, job, index + 1, path.name)
                        for source, job, index, path in residue
                        if _executable_shape(path))
    named = [(source, job, index + 1, _path.name)
             for source, job, index, _path in residue]
    assert not executable, (
        'steps running a tracked file the door walk cannot classify, so the '
        f'job reaches the suite tree by nothing this walk can see: '
        f'{executable}. The walk reads Python, so a wrapper in any other '
        'language is a step that reaches nothing here and a job that leaves '
        f'the door set in silence. Every unclassifiable step: {named}. '
        'Give the suites their own entry in scripts/ci/, or point the step '
        'at the runner directly — routing a suite run through a script is '
        'what this refusal is about.')


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


def test_a_mechanisms_share_never_carries_a_tool_the_mechanism_lacks(tmp):
    """The share a control indexes its own table with stays inside it.

    One function once returned the residue unioned with whichever share a
    caller named, so a caller asking what `DECLARED_BY` had to handle got
    the residue back too and then indexed `DECLARED_BY` with it. On this
    tree the residue is empty, so the union was a no-op and the promise was
    never exercised. Planting a real skip on a binary nothing installs —
    the exact change this file exists to catch — put that binary in the
    share and the declaration control died with `KeyError: 'gojq'`
    instead of refusing with a message.

    Driven through the same seam, so the crash case is a test rather than
    something found by hand.
    """
    del tmp
    source = [_PREAMBLE + 'if not shutil.which(TOOL):\n'
              '    _util.skip("no parser")\n']
    residue, _overlap, _stale = _mechanism_residue(source)
    assert residue == ['gojq'], (
        'the synthetic source puts no tool in the residue, so the shares '
        'below are measured against a tree where nothing is unanswered and '
        f'the case this test exists for cannot arise: {residue}')
    covered = set()
    for name, mechanism in _mechanism_shares():
        share = _mechanism_share(mechanism, source)
        assert not share - set(mechanism), (
            f'{name} is handed a tool it does not carry: '
            f'{sorted(share - set(mechanism))}. A control indexing that '
            'table with what it is given raises KeyError instead of '
            'refusing, on the one input a future tool takes')
        covered |= share
    derived, _present = _derive_tool_roles(source)
    assert covered | set(residue) == set(derived), (
        'the three shares and the residue do not partition the derived skip '
        f'set: covered={sorted(covered)}, residue={residue}, '
        f'skipped={sorted(derived)}. A tool in two shares is enforced by '
        'neither, and one in neither is enforced by nothing')


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
