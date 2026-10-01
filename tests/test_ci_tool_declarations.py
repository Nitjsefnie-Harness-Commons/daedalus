#!/usr/bin/env python3
"""A tool no job installs is DECLARED by a step, and this file holds that.

Three mechanisms answer a tool a suite under `tests/` skips on: the shared
installer installs one, a setup step declares another, and a claim about
the runner image exempts a third. `tests/test_ci_lint_tools.py` holds the
installer's half — the installer step, the tools it declares, the lint-tool
install — and this file the other two, because a control filed under a
name that does not match its subject is one a reader will not look for.
The split is of subject, not of mechanism: the three tables and the
recognisers that read what a job runs are in
`tests/_lint_tool_mechanisms.py`, and the door walk in
`tests/_suite_jobs.py`, so no assertion about which mechanism answers a
tool is written twice.

The control that reads the job's build pin rather than its declaration —
the installer's `ACTIONLINT_VERSION` and release base against what the
`Install actionlint` step downloads — is the installer's, and stays in
`tests/test_ci_lint_tools.py` with the rest of that half. Every assertion
it makes is about what the installer installs; the job is the other side
of the comparison, not its subject.

Every control here is a guard: a green run proves the tree still matches
it and nothing more. The proof that each bites is a planted defect in a
real target — the `actions/setup-node` step removed from the `publish`
job of `release.yml`, the `node` exemption restored for a tool an action
in the tree already declares, a third `scripts/ci/` runner planted in a
workflow the door walk then has to classify, and a shell wrapper put in
front of a suite run so the walk cannot read it. Every workflow is read
now: `_door_jobs` globs `*.yml` and `*.yaml`, so no workflow is one
this file does not look at, and the bound on what the walk can see is
stated in `tests/_suite_jobs.py` where it lives.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _lint_tool_mechanisms import (  # noqa: E402
    DECLARED_BY, SHIPPED_BY_THE_IMAGE, _declared_tool_actions,
    _mechanism_residue, _mechanism_share, _mechanism_shares)
from _lint_tool_roles import _PREAMBLE, _derive_tool_roles  # noqa: E402
from _suite_jobs import (  # noqa: E402
    SCRIPT_PATH, _actions_before, _declarations_before, _INERT_SHAPES,
    _PATH_EXTENSIONS, _SCRIPT_SHAPES, _executable_shape, _runner_doors,
    _suite_step, _unclassifiable_steps)

ROOT = _util.ROOT


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
    runs a suite in it. Those are not the same step: a `--help` probe
    reaches the tree and runs no suite at all, and the walk cannot tell it
    from a run without encoding which invocations of which runner do what
    — the basename fingerprint the walk exists to refuse. So the boundary
    is conservative on purpose, and the message below says which step it
    is rather than claiming a suite ran there.

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
            f'{_declared_above(before, gated)}. '
            f'The suites that tree holds skip on '
            f'{", ".join(sorted(missing))}, which the shared installer does '
            f'not install, so add the step that declares it '
            f'({", ".join(DECLARED_BY[tool] for tool in missing)}) ABOVE '
            'that step. A declaration below it is not a declaration, '
            'because the walk treats any step that reaches the suite tree '
            'as the boundary — a step naming a runner counts even where it '
            'only asks one for its options — and the suites that follow '
            'would run on whatever the job happened to have.')


def _declared_above(before, gated):
    """What a job declares above the boundary, phrased so it is true of it.

    "no action at all" was a fallback for an empty `before`, and it is
    false of any job that reaches that state with a gated action to name:
    the message denied the job every action and then listed the gated ones
    in the next clause, so a reader had to read past the assertion to
    learn the opposite of what it claimed. A job whose every pre-boundary
    action is gated reaches that state — a gated action is not one the
    control counts — on any tree where the declaration above its boundary
    is not there, which is the tree this control's own refusal is
    about.
    """
    if before:
        named = f'uses {sorted(before)}'
    elif gated:
        named = 'uses no ungated action'
    else:
        named = 'uses no action at all'
    if gated:
        named += f' and skips the gated {gated}'
    return named


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


def test_the_exemption_predicate_splits_a_name_into_the_words_it_sets_up(
        tmp):
    """The word-split is the predicate, and this is the only pin on it.

    `_declared_tool_actions` maps an action to the tools it declares by
    splitting the last segment of its name into words, because that is
    where a setup action says what it sets up: `actions/setup-node`
    declares `node`. Every action in this tree except that one is a single
    word in its last segment, so it is the only input on which the
    word-split and the whole-segment reading disagree — and it is the
    action this branch's central claim turns on. Take the segment as one
    name instead, put `node` back in `SHIPPED_BY_THE_IMAGE`, empty
    `DECLARED_BY` and delete the setup steps the suite jobs carry, and
    every other control in this file passes over a tree whose workflows
    still use `actions/setup-node` and whose declarations carry no
    `node` at all.

    So what is pinned here is the disagreement, written as fixed names
    rather than as a second run of the same regex over the same string:
    each multi-word action in the tree is credited to the WORDS its last
    segment carries, the segment itself is credited to nothing, and no
    word that appears only in an action's owner or namespace is a tool at
    all. Those are the three readings a simplification of that one line
    reaches for — the segment, the whole name, and a special case for the
    action this branch happens to need — and every one of them is a name
    the derivation has to produce on its own.
    """
    del tmp
    declared = _declared_tool_actions()
    for action, words in (('actions/setup-node', ('setup', 'node')),
                          ('actions/setup-python', ('setup', 'python')),
                          ('actions/attest-build-provenance',
                           ('attest', 'build', 'provenance'))):
        credited = sorted(word for word, found in declared.items()
                          if action in {name for name, _w in found})
        uncredited = sorted(set(words) - set(credited))
        assert not uncredited, (
            f'{action} declares none of {uncredited}, so the derivation is '
            'not splitting an action name into the words that name the tools '
            f'it sets up: the words it credits that action are {credited}. '
            'A setup action is a declaration of the tool in its name, and '
            'this is the only assertion in the tree that says which part of '
            'the name carries it — read the segment as one name and `node` '
            'leaves the derived set, which is the whole exemption this '
            'control above turns on.')
    assert 'setup-node' not in declared, (
        'the derivation credits an action with its last segment spelled as '
        'one name, so `setup-node` is a tool the tree declares and `node` '
        'is not. A hyphen in a setup action joins the words of what it sets '
        'up; it does not rename the tool, and a segment-spelled key is a '
        f'key no suite skips on: {sorted(declared)}.')
    owner_words = sorted(word for word in ('actions', 'github')
                         if word in declared)
    assert not owner_words, (
        f'the derivation credits the owner and namespace segments of an '
        f'action name with tools: {owner_words}. A tool is a binary a suite '
        'skips on, and an action names one in its LAST segment; reading the '
        'whole name makes an exemption for a tool nobody installs illegal '
        f'on the evidence of a word no step declares: {sorted(declared)}.')


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
    than only in this control's source, and it names the universe it
    speaks for: `_PATH_EXTENSIONS`, the extensions the walk's own path
    pattern resolves. A tracked file in any other language is a step the
    walk never resolves, so this refusal is not the answer for it and says
    so rather than implying the set is every file an interpreter can run.
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
        f'{executable}. The walk reads Python, so a step running one of '
        'these reaches nothing here and a job that reaches the suites that '
        f'way leaves the door set in silence. Every unclassifiable step: '
        f'{named}. Give the suites their own entry in scripts/ci/, or point '
        'the step at the runner directly — routing a suite run through a '
        'script is what this refusal is about. What it cannot answer for '
        'is a tracked file the walk never resolves: the universe is '
        f'_PATH_EXTENSIONS ({", ".join(_PATH_EXTENSIONS)}), and a wrapper '
        'in any other language names nothing this walk can see.')


def test_every_extension_the_walk_resolves_is_classified_exactly_once(tmp):
    """The residue's universe is the path pattern's, and stays that way.

    `_unclassifiable_steps` resolves a step's named file through
    `SCRIPT_PATH` and then has to say whether that file is one an
    interpreter runs. The two lists are the same fact read twice — which
    extensions the walk resolves, and which of them a step runs — so they
    are held to each other here, with the readable third (`.py`, the one
    the walk parses) between them. An extension added to the pattern
    without being classified would leave the residue control silent about
    a file it can now see, which is the shape that reached the walk with
    `.ps1`: a real authoring choice on the `windows-latest` leg that no
    control in this file could answer for.
    """
    del tmp
    resolved = {f'.{extension}' for extension in _PATH_EXTENSIONS}
    unresolvable = sorted(extension for extension in resolved
                          if not SCRIPT_PATH.search(f'ci/tool{extension}'))
    assert not unresolvable, (
        f'_PATH_EXTENSIONS names {unresolvable} and the path pattern does '
        'not resolve it, so the two disagree about what this walk can see '
        'and a control reading one of them is reading a set the other does '
        f'not have: {sorted(resolved)}')
    classes = ({'.py'}, set(_SCRIPT_SHAPES), set(_INERT_SHAPES))
    claims = {shape: sum(shape in group for group in classes)
              for shape in resolved}
    twice = sorted(shape for shape, count in claims.items() if count > 1)
    assert not twice, (
        f'an extension is claimed by more than one class: {twice}. The '
        'residue control reads a file as either one a step runs or one a '
        'step reads, and a file both of those is a shape whose verdict no '
        'single reading of it can be trusted for')
    unclassified = sorted(shape for shape, count in claims.items()
                          if not count)
    assert not unclassified, (
        f'the walk resolves {unclassified} and nothing says what a step '
        f'naming one of those files is: {sorted(resolved)} is classified '
        f'as run by an interpreter ({sorted(_SCRIPT_SHAPES)}), read as a '
        f'manifest ({sorted(_INERT_SHAPES)}), or read as Python (.py), and '
        'an extension outside all three is a step the residue control sees '
        'and cannot judge.')


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
    subject). Modelled on the door control in
    `tests/test_ci_lint_tools.py`, which is the same residue-versus-derived
    question over the job set.
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
    defect both lint-tool files are for, and it reaches this control only
    through the recognisers — so it is driven through the `sources` seam,
    which is what that seam is for, rather than by planting a file in
    `tests/`.
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
    the exact change these two files exist to catch — put that binary in
    the share and the declaration control died with `KeyError: 'gojq'`
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='citooldecls_')


if __name__ == '__main__':
    raise SystemExit(main())
