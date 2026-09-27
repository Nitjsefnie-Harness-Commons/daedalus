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


def test_each_caller_passes_the_set_it_wants_judged(tmp):
    """Which SET reaches the predicate, which the control above cannot see:
    it answers only whether the call happened, and both readings of
    `missing_required` make it.

    The property is a call shape rather than an answer, because on the data
    the producer emits the two shapes are the same fact: it names every run
    of a workflow alike, so a `tests` run existing and the newest `tests`
    run existing are one fact, and a filtered read of the set agrees with an
    unfiltered one. That is also why the fixture the removed control used is
    unproducible and cannot be restored - it gave one workflow two different
    run names. The recorder sees the set itself, so it needs no such
    fixture.

    Two runs of ONE workflow, so the filtered set is the newer alone. Both
    of ci_wait's calls must carry it - the verdict's own check, and the
    `_missing` the refusal names the gate from - or a superseded run's name
    can satisfy the gate on its own. watch_all's is asserted as it stands,
    so the divergence the two callers carry over the same question is
    visible rather than incidental; on the producer's data their answers
    agree, and whether they should is not settled here.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_gate_set')
    hold = _util.load(skill / 'watch_all.py', 'watch_all_gate_set')
    runs = [
        _run(1, 'failure', '2026-09-20T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='tests'),
    ]

    def _record(caller, call):
        seen = []

        def _recorder(runs, *args, **kwargs):
            seen.append([run['id'] for run in runs])
            return ['tests']

        real = caller.ci_gate.missing_required
        setattr(caller.ci_gate, 'missing_required', _recorder)
        try:
            call(caller)
        finally:
            setattr(caller.ci_gate, 'missing_required', real)
        return seen

    def _incomplete_wait(caller):
        """A wait that reaches the refusal, so `_missing` is called too."""
        clock = _Clock()
        setattr(caller, 'runs_on', lambda repo, sha: runs)
        setattr(caller, 'prs_on', lambda repo, sha: [
            {'number': 1, 'state': 'OPEN', 'mergeable': 'CONFLICTING',
             'mergeStateStatus': 'DIRTY', 'headRefOid': sha}])
        out, err = io.StringIO(), io.StringIO()
        with _frozen_wait_clock(caller, clock), contextlib.redirect_stderr(err):
            return caller.wait('o/r', 'a' * 40, 60, 600, out, grace=300)

    # The verdict's own check first, then the refusal's: a caller that
    # filtered in one and not the other is the hole this names, and the two
    # recorded calls are what says the wait reached the refusal at all.
    seen = _record(wait, lambda m: m.verdict(runs))
    assert seen == [[2]], seen
    seen = _record(wait, _incomplete_wait)
    assert seen == [[2], [2]], seen
    seen = _record(hold, lambda m: m._settled(runs))
    assert seen == [[1, 2]], seen


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
