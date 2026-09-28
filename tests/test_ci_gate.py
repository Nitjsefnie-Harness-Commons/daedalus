#!/usr/bin/env python3
"""The gating-workflow predicate both waiters read, on its own.

`ci_wait.py` refuses with exit 4 on a run set with no gating workflow and
`watch_all.py` keeps its hold on one; both ask ci_gate, so the controls that
hold the predicate in place live beside the predicate rather than in either
caller. A caller can only be as right as the thing it asks, and both of them
would be wrong together if this were a copy.
"""
import ast
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
# Aliased to the names the ci_wait suites call them by, so the shared
# builder is the only thing this suite's call sites see.
from _ci_wait_fixtures import (  # noqa: E402
    _ci_wait_run as _run,
    _ci_wait_clock as _Clock,
    _frozen_ci_wait_clock as _frozen_wait_clock)

SOURCE = (_util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
          / 'ci_gate.py')


def _ci_gate():
    return _util.load(SOURCE, 'ci_gate_contract')


def _gate_run(name, conclusion='success'):
    """One workflow run, as both waiters read it."""
    return {'name': name, 'status': 'completed', 'conclusion': conclusion}


def test_a_gating_run_is_present(tmp):
    del tmp
    mod = _ci_gate()
    runs = [_gate_run('gate freshness'), _gate_run('tests')]
    assert mod.missing_required(runs) == []
    assert mod.missing_required([_gate_run('tests')]) == []


def test_no_gating_run_names_what_is_missing(tmp):
    del tmp
    mod = _ci_gate()
    runs = [_gate_run('gate freshness'), _gate_run('CodeQL - Code Quality')]
    assert mod.missing_required(runs) == ['tests']
    assert mod.missing_required([]) == ['tests']


def test_the_name_matches_exactly(tmp):
    """`Tests` and `test` are different workflows. A case difference or a
    decorated spelling treated as the gate would reinstate the false green
    this predicate exists to remove, under a new spelling."""
    del tmp
    mod = _ci_gate()
    for name in ('Tests', 'test', 'TESTS', 'tests ', 'unit tests'):
        assert mod.missing_required([_gate_run(name)]) == ['tests'], name


def test_a_conclusion_is_irrelevant_to_the_predicate(tmp):
    """Both callers judge conclusions against rules of their own, and this
    one must not pre-empt them: a red gating run is present, so it is their
    answer to report, not an absence to wait on."""
    del tmp
    mod = _ci_gate()
    for conclusion in ('success', 'failure', 'cancelled', 'skipped'):
        assert mod.missing_required([_gate_run('tests', conclusion)]) == [], (
            conclusion)


def test_a_superset_requirement_names_each_missing_workflow(tmp):
    del tmp
    mod = _ci_gate()
    wanted = frozenset({'tests', 'audit'})
    assert mod.missing_required(
        [_gate_run('tests')], required=wanted) == ['audit']
    assert mod.missing_required(
        [_gate_run('tests'), _gate_run('audit')], required=wanted) == []
    assert mod.missing_required([], required=wanted) == ['audit', 'tests']


def test_an_empty_requirement_is_satisfied_by_every_run_set(tmp):
    """The argument is a no-op, not a rule that refuses everything - which
    is what makes it usable as the default `verdict()` carries."""
    del tmp
    mod = _ci_gate()
    assert mod.missing_required([]) == ['tests']
    assert mod.missing_required([], required=frozenset()) == []
    assert mod.missing_required([_gate_run('gate freshness')],
                                required=frozenset()) == []


def _spells_a_required_name(node, wanted):
    """Whether a set or frozenset literal names a required workflow."""
    literals = None
    if isinstance(node, ast.Set):
        literals = node
    elif (isinstance(node, ast.Call)
            and getattr(node.func, 'id', None) in ('set', 'frozenset')
            and node.args and isinstance(node.args[0], ast.Set)):
        literals = node.args[0]
    if literals is None:
        return False
    return bool({item.value for item in literals.elts
                 if isinstance(item, ast.Constant)
                 and isinstance(item.value, str)} & wanted)


def _is_an_alias(node):
    """Whether a definition's value is a plain reference to the authority.

    `ci_wait.REQUIRED_WORKFLOWS = ci_gate.REQUIRED_WORKFLOWS` is the same
    object under a second name, which is what keeps this tool's public
    constant working after the extraction. A copy computes its own value,
    and that is the one this control refuses.
    """
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return False
    value = node.value
    return (isinstance(value, ast.Attribute)
            and getattr(value.value, 'id', None) == 'ci_gate')


def test_the_expectation_has_exactly_one_definition(tmp):
    """The control that answers Task 2's question, which import identity
    could not: a caller that grew its own copy of the expectation.

    Identity is satisfied by the import system - two modules that merely
    `import ci_gate` hold the same object whether or not either one calls
    it - so it passes for a value-identical copy, and for a caller that
    stopped calling the predicate altogether. Enumerating DEFINITIONS is
    the one form that sees a copy written under a definition's name, and
    an alias bound to the authority is not a copy.

    Read from source with `ast`, not by importing: an import would collapse
    the very thing being counted.

    And the control's own premise is asserted rather than assumed: a rename
    of either name would leave this filtering for a name nothing defines,
    enumerating everything and matching nothing, which is a control that
    passes while measuring nothing. That is the same failure as I2 one
    rename later, and the shape `tests/_unconsolidated_names.py` already
    uses for its own table - a row naming no live site is a refusal.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    watched = ('REQUIRED_WORKFLOWS', 'missing_required')
    declared = _declared_names(SOURCE)
    missing = [name for name in watched if name not in declared]
    assert not missing, (
        f'ci_gate.py defines none of {missing}, so this control would '
        f'enumerate every definition and match none of them - a green run '
        f'that measures nothing; declare the expectation under the name '
        f'this control watches, or teach it the new one')
    found = []
    for path in sorted(skill.iterdir()):
        if path.suffix != '.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in tree.body:
            name = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                name = getattr(node.targets[0], 'id', None)
            elif isinstance(node, ast.FunctionDef):
                name = node.name
            if name not in watched:
                continue
            if name == 'missing_required' or not _is_an_alias(node):
                found.append(f'{path.name}:{node.lineno} {name}')
    # BY NAME, not by line: an expected list carrying `first + 3` made a
    # blank line inserted between the two definitions a red whose message
    # was a list rather than a reason, and editing the `3` is the first
    # thing the next author would do. Both names are already in hand.
    named = [entry.split(' ', 1)[1] for entry in found]
    assert named == ['REQUIRED_WORKFLOWS', 'missing_required'], found
    assert {entry.split(':')[0] for entry in found} == {'ci_gate.py'}, found


def _declared_names(path):
    """Every top-level name the module declares, whatever its kind."""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(getattr(target, 'id', None)
                         for target in node.targets)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_the_required_names_are_spelled_in_exactly_one_module(tmp):
    """The same drift under a name the control above would miss.

    A copy need not call itself `missing_required` - the shape the review
    planted was an expression inside a caller's own body. What every copy
    has in common is that it spells the workflow names out again, so this
    looks for the names inside a set or frozenset literal and requires one
    file to carry them.
    """
    del tmp
    wanted = set(_ci_gate().REQUIRED_WORKFLOWS)
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    spelled = []
    for path in sorted(skill.iterdir()):
        if path.suffix != '.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        # One entry per FILE: `frozenset({'tests'})` is both a Call and the
        # Set it wraps, and the same file spelling it twice is still one
        # place the expectation is written down.
        if any(_spells_a_required_name(node, wanted)
               for node in ast.walk(tree)):
            spelled.append(path.name)
    assert spelled == ['ci_gate.py'], spelled


def test_each_caller_reaches_the_predicate_through_ci_gate(tmp):
    """The behavioural half, and the one that sees a deleted call.

    A source-level control cannot see an absence: deleting the predicate's
    use from a caller removes a definition rather than adding one, and
    spells no names. Planting a recorder on the module's `ci_gate` and
    requiring both callers to go through it catches that, and catches a
    caller's own copy for the same reason - neither of them would call it.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_reaches_gate')
    hold = _util.load(skill / 'watch_all.py', 'watch_all_reaches_gate')

    def _asked_and_answered(caller, call):
        """(did it call the predicate, what did it answer) for one caller."""
        seen = []

        def _recorder(runs, *args, **kwargs):
            seen.append(list(runs))
            return []

        real = caller.ci_gate.missing_required
        setattr(caller.ci_gate, 'missing_required', _recorder)
        try:
            answer = call(caller)
            asked = bool(seen)
        finally:
            setattr(caller.ci_gate, 'missing_required', real)
        return asked, answer

    # A recorder answering "nothing missing" is what makes the answer
    # assertion bite: with the call in place the verdict is the predicate's
    # own, and without it the caller answered for itself.
    asked, answer = _asked_and_answered(
        wait, lambda m: m.verdict([_gate_run('tests')]))
    assert asked, 'ci_wait never asked ci_gate'
    assert answer == ('acceptable', []), answer
    asked, answer = _asked_and_answered(
        hold, lambda m: m._settled([_gate_run('tests')]))
    assert asked, 'watch_all never asked ci_gate'
    assert answer is True, answer


def _refusing_wait(caller, runs):
    """(exit code, the line it printed) for a wait that reaches its refusal.

    The head is made CONFLICTING so the wait refuses at once rather than
    waiting out a grace, and no clock really advances: the bound is read
    through `_frozen_ci_wait_clock`, so a timeout here would be a value
    rather than a margin.
    """
    setattr(caller, 'runs_on', lambda repo, sha: runs)
    setattr(caller, 'prs_on', lambda repo, sha: [
        {'number': 1, 'state': 'OPEN', 'mergeable': 'CONFLICTING',
         'mergeStateStatus': 'DIRTY', 'headRefOid': sha}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(caller, _Clock()), contextlib.redirect_stderr(err):
        code = caller.wait('o/r', 'a' * 40, 60, 600, out, grace=300)
    return code, out.getvalue()


def test_every_reader_answers_over_the_judged_set(tmp):
    """The ANSWER the three readers give, not the set each one passed.

    The control this replaces read the shape of the call - which run ids
    reached the predicate - and could not do better: the producer names
    every run of a workflow alike, so on the data it emits the raw reading
    and the judged reading are one fact and no answer tells them apart. A
    call shape is the weaker thing in any case, since a caller can hand
    over exactly the ids a recorder wants and still be asking the wrong
    question.

    So the fixture is the one the removed control called unproducible -
    two runs of ONE workflow, the older named `tests` and the newer named
    something else - and what is pinned here is the RULE rather than a
    shape the producer emits. A workflow's `name:` changing in its own
    YAML is all it takes to make this set real; until then the control is
    a guard, and a guard passes on the tree it was written against by
    construction, so the defect was planted in ci_gate.py to watch it fail.

    They are meant to agree (issue #1262). A superseded run's name must
    not satisfy the gate on its own, which is what issue #1249 established
    for conclusions; the nearest precedent is #1223, where the hold read a
    settled green matrix with the gating workflow silently absent.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    mod = _util.load(skill / 'ci_gate.py', 'ci_gate_one_judged_set')
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_one_judged_set')
    hold = _util.load(skill / 'watch_all.py', 'watch_all_one_judged_set')
    runs = [
        _run(1, 'failure', '2026-09-20T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='gate freshness'),
    ]
    assert mod.missing_required(runs) == ['tests']
    assert [run['id'] for run in mod._judged(runs)] == [2]
    # Asked of the raw list and of the set the filter left, the predicate
    # answers alike: a caller that pre-filters cannot move the answer, and
    # one that does not cannot either.
    assert mod.missing_required(mod._judged(runs)) == ['tests']
    assert wait.verdict(runs) == ('incomplete', [])
    assert wait._missing(runs) == ['tests']
    absent = hold._settled(runs)
    assert isinstance(absent, hold.ci_gate.GateAbsent), absent
    assert absent.missing == ('tests',), absent
    # And the refusal built from that answer names the gate, rather than
    # printing a doubled space where the name belongs (issue #839).
    code, printed = _refusing_wait(wait, runs)
    assert code == 4, printed
    assert 'no tests run on' in printed, printed


FILTER_NAMES = frozenset(
    {'_judged', '_superseded', '_workflow_of', '_started_key'})


def test_the_filter_is_reached_through_ci_gate(tmp):
    """One filter, and the edge that reaches it - not its behaviour.

    A copy pasted back into a caller is the drift this branch exists to
    end, and it survives every other control: a behaviourally identical
    private `_judged` in `ci_wait.py` leaves the 81 pre-existing tests in
    the four suites that read these modules green. The two halves below
    are the tripwire the chokepoint needs. The first refuses a second
    definition anywhere in the skill, counting only definitions - the
    import that IS the edge is not one, which is why a correct tree lists
    one holder. The second pins the binding, because `wait.ci_gate` is the
    module `ci_wait` imported, so this compares the filter in use with the
    filter owned rather than two separately loaded copies of one file.

    What it does not see: a copy pasted under a name none of these four
    carries. That is the standing limit of a control that watches names.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_one_filter')
    holders = [path.name for path in sorted(skill.iterdir())
               if path.suffix == '.py'
               and FILTER_NAMES & _declared_names(path)]
    assert holders == ['ci_gate.py'], holders
    assert wait._judged is wait.ci_gate._judged
    assert wait._superseded is wait.ci_gate._superseded


def test_both_waiters_read_this_one_predicate(tmp):
    """Kept, and no longer the control that carries the weight.

    These assertions hold for any two modules that import the name, so
    they cannot see a caller that grew a copy or stopped calling the
    predicate - the three controls above are the ones that do. What is
    left here is the weaker property, still worth pinning: both callers
    reach the same module object rather than each resolving `ci_gate`
    somewhere of its own.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    mod = _ci_gate()
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_gate_owner')
    hold = _util.load(skill / 'watch_all.py', 'watch_all_gate_owner')
    assert wait.ci_gate is hold.ci_gate
    assert wait.ci_gate.missing_required is hold.ci_gate.missing_required
    assert wait.REQUIRED_WORKFLOWS == mod.REQUIRED_WORKFLOWS
    assert hold.ci_gate.REQUIRED_WORKFLOWS == mod.REQUIRED_WORKFLOWS
    assert wait.ci_gate.REQUIRED_WORKFLOWS == mod.REQUIRED_WORKFLOWS


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cigate_')


if __name__ == '__main__':
    raise SystemExit(main())
