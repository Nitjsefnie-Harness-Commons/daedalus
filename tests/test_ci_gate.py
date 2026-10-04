#!/usr/bin/env python3
"""The gating-workflow predicate `ci_wait.py` reads, on its own.

`ci_wait.py` refuses with exit 4 on a run set with no gating workflow, and
it asks ci_gate rather than deciding, so the controls that hold the predicate
in place live beside the predicate rather than in the caller. A caller can
only be as right as the thing it asks, and it would be wrong if this were a
copy.
"""
import ast
import contextlib
import io
import symtable
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
# Aliased to the names the ci_wait suites call them by, so the shared
# builder is the only thing this suite's call sites see.
from _ci_wait_fixtures import (  # noqa: E402
    _ci_wait_run as _run,
    _ci_wait_clock as _Clock,
    _ci_wait_state as _state,
    _frozen_ci_wait_clock as _frozen_wait_clock)

SOURCE = (_util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
          / 'ci_gate.py')


def _ci_gate():
    return _util.load(SOURCE, 'ci_gate_contract')


def _gate_run(name, conclusion='success'):
    """One workflow run, as both waiters read it."""
    return {'name': name, 'status': 'completed', 'conclusion': conclusion}


def _published_check(conclusion='success'):
    """One published `gate freshness` check run, as `ci_wait` reads it.

    A head whose publisher has written nothing is a state of its own
    since issue 1360, so a control about the WORKFLOW gate states this
    one green rather than leaving the answer ambiguous.
    """
    return {'id': 7, 'name': 'gate freshness', 'status': 'completed',
            'conclusion': conclusion, 'completed_at': '2026-09-20T10:10:00Z',
            'html_url': 'https://github.com/o/r/runs/7'}


# Which call anchored each stamp the subject parsed, in parse order.
#
# `replace(tzinfo=utc)` ANCHORS a zone-less stamp to UTC and `astimezone()`
# CONVERTS it into whatever zone the runner is in. On a runner that runs in
# UTC the two are the same instant, so no ordering the predicate produces
# can tell them apart -- which is why this record exists rather than a wider
# pair of stamps, and why GitHub-hosted runners, which are UTC, are the ones
# a control reading only the verdict cannot catch a defect in.
_ANCHORS = []


class _AnchoredStamp(datetime):
    """A parsed stamp that remembers the call that anchored it.

    A real `datetime` rather than a stand-in object, so every comparison
    and every tuple the predicate goes on to build behaves as it would
    without the fixture: what is read back is the anchor, not a fake
    leaking into the verdict.
    """

    def replace(self, *args, **kwargs):
        _ANCHORS.append('replace')
        return datetime.replace(self, *args, **kwargs)

    def astimezone(self, tz=None):
        _ANCHORS.append('astimezone')
        return datetime.astimezone(self, tz)


class _ParsingDatetime(datetime):
    """`datetime` as `_started_key` parses with, and the anchor on record.

    Swapped on the subject's own module global for the length of one
    control, so every other control keeps parsing with the real class.
    """

    @classmethod
    def fromisoformat(cls, date_string):
        parsed = datetime.fromisoformat(date_string)
        return _AnchoredStamp(parsed.year, parsed.month, parsed.day,
                              parsed.hour, parsed.minute, parsed.second,
                              parsed.microsecond, parsed.tzinfo)


@contextlib.contextmanager
def _recording_anchors(mod):
    """The subject's parses, watched, with the anchors they chose."""
    del _ANCHORS[:]
    saved = mod.datetime
    mod.datetime = _ParsingDatetime
    try:
        yield _ANCHORS
    finally:
        mod.datetime = saved
        del _ANCHORS[:]


def test_a_gating_run_is_present(tmp):
    del tmp
    mod = _ci_gate()
    runs = [_gate_run('gate freshness'), _gate_run('tests')]
    assert mod.missing_required(runs) == []
    assert mod.missing_required([_gate_run('tests')]) == []


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

    Read from source with `symtable`, not by importing: an import would
    collapse the very thing being counted.

    And the control's own premise is asserted rather than assumed: a rename
    of either name would leave this filtering for a name nothing defines,
    enumerating everything and matching nothing, which is a control that
    passes while measuring nothing. That is the same failure as I2 one
    rename later, and the shape `tests/_unconsolidated_names.py` already
    uses for its own table - a row naming no live site is a refusal.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    watched = frozenset({'REQUIRED_WORKFLOWS', 'missing_required'})
    declared = _module_names(SOURCE)
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
        # `missing_required` is exempt from no alias and no import:
        # `test_each_caller_reaches_the_predicate_through_ci_gate` needs
        # a caller to reach it through `ci_gate`, and one that binds the
        # name itself reds there. `REQUIRED_WORKFLOWS` is exempt, being
        # the same object under a second name.
        for file, name in _module_declarations(path, watched,
                                               always=('missing_required',)):
            found.append(f'{file} {name}')
    # BY NAME, not by line: an expected list carrying `first + 3` made a
    # blank line inserted between the two definitions a red whose message
    # was a list rather than a reason, and editing the `3` is the first
    # thing the next author would do. Both names are already in hand, and
    # a line number would go stale on the next edit above the definition.
    named = [entry.split(' ', 1)[1] for entry in found]
    assert named == ['REQUIRED_WORKFLOWS', 'missing_required'], found
    assert {entry.split(' ')[0] for entry in found} == {'ci_gate.py'}, found


def _module_names(path):
    """Every name `path` binds at module scope, from the language's own view.

    `symtable` compiles the source and hands back the scopes the
    interpreter itself builds, so the answer is the domain of the language
    rather than a list of node types someone remembered: a binding form
    added to the language is in the domain the day it is. A name bound
    inside a `def` is a local, and a class-body binding is a class
    attribute; neither binds the module's namespace and neither is
    reported.
    """
    return set(symtable.symtable(path.read_text(encoding='utf-8'),
                                 str(path), 'exec').get_identifiers())


def _module_declarations(path, watched, *, always=()):
    """(file, name) for every definition of `watched` `path` makes itself.

    One enumeration, shared with the filter control below: two readings of
    one property in one file is how the two came to disagree.

    A module-scope binding is a DEFINITION unless it was only imported, or
    it is the plain alias `_is_an_alias` recognises - and `always` names
    the bindings no exemption reaches. The alias is the only form the AST
    is asked about, because it is the only form that predicate matches; a
    binding form it does not find is not exempt, which is the direction
    that refuses rather than passes.
    """
    source = path.read_text(encoding='utf-8')
    table = symtable.symtable(source, str(path), 'exec')
    names = set(table.get_identifiers())
    aliases = dict(_module_assigns(
        ast.parse(source, filename=str(path)).body))
    found = []
    for name in sorted(watched & names):
        symbol = table.lookup(name)
        imported_only = (symbol.is_imported()
                         and not (symbol.is_assigned()
                                  or symbol.is_namespace()
                                  or symbol.is_declared_global()))
        node = aliases.get(name)
        if name not in always and (imported_only or _is_an_alias(node)):
            continue
        found.append((path.name, name))
    return found


def _module_assigns(statements):
    """(name, node) for every `name = ...` bound at module scope.

    The only question the AST is asked here, and the one form that answers
    it: a plain alias lives in an `ast.Assign`, so a binding form this does
    not match cannot be one. The recursion stops at a `def` or a `class`,
    whose bodies are not the module's scope - a class-body alias must not
    exempt a module-scope definition - and nowhere else can an assignment
    statement sit, so descending finds nothing more. That is why
    `ast.Lambda` is absent: a walrus in a lambda body is a `NamedExpr`.
    """
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in statements:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node
        elif not isinstance(node, scopes):
            yield from _module_assigns(ast.iter_child_nodes(node))


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
    requiring the caller to go through it catches that, and catches a
    caller's own copy for the same reason - it would not call it.

    `ci_wait.py` is the only caller this repository ships: the watcher's
    hold that read the predicate beside it is gone from the tree, so the
    property pinned here is the one that survives rather than the one it
    replaced.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_reaches_gate')

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
        wait, lambda m: m.verdict([_gate_run('tests')], [_published_check()]))
    assert asked, 'ci_wait never asked ci_gate'
    assert answer == ('acceptable', []), answer


def _refusing_wait(caller, runs):
    """(exit code, the line it printed) for a wait that reaches its refusal.

    The head is made CONFLICTING so the wait refuses at once rather than
    waiting out a grace, and no clock really advances: the bound is read
    through `_frozen_wait_clock`, so a timeout here would be a value
    rather than a margin.
    """
    setattr(caller, 'ci_on', lambda repo, sha: _state(runs))
    setattr(caller, 'prs_on', lambda repo, sha: [
        {'number': 1, 'state': 'OPEN', 'mergeable': 'CONFLICTING',
         'mergeStateStatus': 'DIRTY', 'headRefOid': sha}])
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(caller, _Clock()), contextlib.redirect_stderr(err):
        code = caller.wait('o/r', 'a' * 40, 60, 600, out, grace=300,
                           required_checks=frozenset())
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
    runs = [
        _run(1, 'failure', '2026-09-20T10:00:00Z', name='tests'),
        _run(2, 'success', '2026-09-20T10:05:00Z', name='gate freshness'),
    ]
    assert mod.missing_required(runs) == ['tests']
    assert [run['id'] for run in mod.judged(runs)] == [2]
    # Asked of the raw list and of the set the filter left, the predicate
    # answers alike: a caller that pre-filters cannot move the answer, and
    # one that does not cannot either.
    assert mod.missing_required(mod.judged(runs)) == ['tests']
    assert wait.verdict(runs, [], required_checks=frozenset()) == (
        'incomplete', [])
    # The absent gates as the refusal spells them, and this control is
    # about the workflow one: `tests/test_ci_wait_published.py` is
    # where a head missing BOTH is the subject.
    assert wait._missing(runs, [], required_checks=frozenset()) == [
        'no tests run']
    # And the refusal built from that answer names the gate, rather than
    # printing a doubled space where the name belongs (issue #839).
    code, printed = _refusing_wait(wait, runs)
    assert code == 4, printed
    assert 'no tests run on' in printed, printed


FILTER_NAMES = frozenset(
    {'judged', 'superseded', '_workflow_of', '_started_key'})


def test_no_caller_declares_a_filter_of_its_own(tmp):
    """That the filter is ci_gate's and not a copy - not that it is reached.

    A copy pasted back into a caller is the drift this branch exists to
    end, and it survives every other control: a behaviourally identical
    `judged` in `ci_wait.py` leaves every suite that reads these modules
    green but this one.

    The searched domain is this skill's own directory and nothing wider: a
    `*.py` module beside `ci_gate.py`. It cannot bound the other copies.
    `scripts/ci/aggregate_gate.py` carries its own versions of the filter
    - `_workflow_of` and `_started_key` with bodies identical to
    `ci_gate.py`'s, and `superseded` under its own signature - and a copy
    pasted in any module outside this directory is green here. That is
    issue #1260, which this branch leaves open on purpose: `scripts/ci/`
    is gate-defining, and an edit there turns every open pull request's
    `gate freshness` check red on merge.

    Within that domain the first half refuses a module-scope definition
    of any name `FILTER_NAMES` watches outside `ci_gate`, whatever nests
    it, and accepts an alias bound to the authority - the pattern
    `ci_wait.py` already uses for `REQUIRED_WORKFLOWS`, and what
    `_is_an_alias` is for. The exemption is narrow twice over, and both
    narrowings refuse rather than pass. `_is_an_alias` matches an
    `ast.Assign` whose value is `ci_gate`'s attribute and nothing else,
    so a walrus alias (`judged = (w := ci_gate.judged)`) is refused too.
    And an import-form alias (`import os as judged`) is someone else's
    object under a misleading name rather than a second filter; the
    second half is what catches that, and only in `ci_wait.py`, the one
    module here that binds the watched names at all - in every other
    module of this directory the same line is green.

    What it does not establish: that a caller REACHES the filter.
    Deleting the filter import and leaving the call sites dangling is
    green here, because `getattr`'s default cannot tell an unbound name
    from an attribute read; the controls beside this one are what catch
    that, as is every case in `test_ci_wait.py` that reaches the verdict.
    Nor can it see a copy pasted under a name none of the four carries,
    which is the standing limit of watching names.
    """
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_one_filter')
    owners = {}
    for path in sorted(skill.iterdir()):
        if path.suffix != '.py':
            continue
        for file, name in _module_declarations(path, FILTER_NAMES):
            owners.setdefault(file, []).append(name)
    assert sorted(owners) == ['ci_gate.py'], (
        f'only ci_gate.py may declare a filter name; searched every *.py in '
        f'{skill.name} and found them declared in {sorted(owners)}')
    assert sorted(owners['ci_gate.py']) == sorted(FILTER_NAMES), owners
    for name in sorted(FILTER_NAMES):
        owned = getattr(wait.ci_gate, name)
        assert getattr(wait, name, owned) is owned, (
            f'ci_wait binds its own {name}; the filter must be ci_gate\'s')


def test_an_unreadable_timestamp_ranks_as_the_absent_one(tmp):
    """What the ordering does with a stamp it cannot parse.

    The fallback is not merely "something early" - it is the SAME instant
    a run carrying no timestamp at all reads as, so the two hold one
    position in the order and the numeric id alone decides between them.
    A fallback with an instant of its own would order a garbled stamp
    against an absent one, which is an ordering no producer emits and no
    reader can audit.

    Asked of `superseded` rather than of the judged set, because the
    judged set keeps whichever run carries the higher id either way and
    so cannot say WHICH of the two it kept.
    """
    del tmp
    mod = _ci_gate()
    garbled = _run(10, 'cancelled', 'not a timestamp')
    absent = _run(9, 'success', None, name='tests')
    assert mod.superseded(garbled, [garbled, absent]) is False, garbled['id']
    assert mod.superseded(absent, [garbled, absent]) is True, absent['id']
    garbled = _run(9, 'cancelled', 'not a timestamp')
    absent = _run(10, 'success', None, name='tests')
    assert mod.superseded(garbled, [garbled, absent]) is True, garbled['id']
    assert mod.superseded(absent, [garbled, absent]) is False, absent['id']


def test_a_naive_timestamp_is_read_as_utc(tmp):
    """The other half of the same read, and the one that makes the two
    comparable at all.

    A stamp without a zone is the SAME instant as the same stamp written
    with `Z`, so the id alone decides between the two - and a naive stamp
    compared against an aware one is the `TypeError` this arm removes.
    Deleting it does not change an answer, it ends the comparison with an
    exception, which is why what is asserted here is the equality of the
    two positions and not a verdict about a head.

    The second pair says the read is UTC rather than the runner's own
    zone: a stamp an hour earlier in UTC is earlier whoever is carrying
    it, and on a runner west of Greenwich a local-zone read would order
    the pair the other way. On a runner IN Greenwich that pair cannot see
    the difference at all -- `astimezone()` and `replace(tzinfo=utc)` are
    the same instant there -- so this control reads what the subject did
    with a zone-less stamp, which is the premise the pair depends on and
    which is the same on every host.
    """
    del tmp
    mod = _ci_gate()
    naive = _run(9, 'cancelled', '2026-09-20T10:00:00')
    utc = _run(10, 'success', '2026-09-20T10:00:00Z', name='tests')
    with _recording_anchors(mod) as anchors:
        earlier = _run(11, 'success', '2026-09-20T09:00:00Z')
        assert mod.superseded(naive, [naive, utc]) is True, naive['id']
        assert mod.superseded(utc, [naive, utc]) is False, utc['id']
        assert mod.superseded(naive, [naive, earlier]) is False, naive['id']
        assert mod.superseded(earlier, [naive, earlier]) is True, earlier['id']
        # Anchored, not converted: an `astimezone()` reads a zone-less stamp
        # in the runner's own zone, which is UTC here and on every
        # GitHub-hosted runner, so the ordering above answers the same
        # either way and the control would be green with the defect in
        # place.
        assert set(anchors) == {'replace'}, sorted(set(anchors))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cigate_')


if __name__ == '__main__':
    raise SystemExit(main())
