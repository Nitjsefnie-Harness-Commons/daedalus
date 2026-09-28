#!/usr/bin/env python3
"""The enumeration of guard arms, and the controls the row files hold.

`tests/_launch_arms.py` is the data: every arm of both analysers with
the state it is in, the evidence for that state, and the one clause a
sweep deleted to find out. This suite is what stops the table drifting
from the tree — a named row that no longer exists, a duplicated arm, an
arm whose text has left the analyser — and it replays the sweep for one
arm of each state, so the table's own method is exercised rather than
merely described.

It also carries the arms the two row files cannot express: three
controls asserting sites AND refusals where a `LAUNCH_REFUSAL_ROW` is
built to hold exactly one refusal, and one step ceiling for the arm
whose mutant does not answer wrong but does not stop.
"""
import ast
import os
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _arm_sweep import arm_sweep  # noqa: E402
from _bound_site_rows import BOUND_SITE_ROWS  # noqa: E402
from _launch_arms import (ARM_CONTROLS,  # noqa: E402
                          CRASH_CONTROLLED, DEAD, LAUNCH_ARMS,
                          MARKER_NON_MEMBERS, REDUNDANT, ROW_UNCLAIMED,
                          SECONDARY_CONTROLLED, STEP_CEILING_CONTROL,
                          STATES)
from _launch_audit import bound_sites, launch_refusals  # noqa: E402
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402
from _step_ceiling import within_step_ceiling  # noqa: E402

TESTS = Path(__file__).resolve().parent

# (id, file, line, cut, anchor, what, state, evidence). Final keeps each a
# literal, so an arm read through one is that column's own type; bound to a
# plain int it reads as the union of the row's, `str | int`.
ID: Final = 0
FILE: Final = 1
LINE: Final = 2
CUT: Final = 3
ANCHOR: Final = 4
WHAT: Final = 5
STATE: Final = 6
EVIDENCE: Final = 7

ROW_LABELS = ({label for label, _, _ in BOUND_SITE_ROWS}
              | {label for label, _, _ in LAUNCH_REFUSAL_ROWS})
CONTROL_LABELS = {label for label, _, _, _ in ARM_CONTROLS}

# One arm per state, and one of the two the row files cannot hold, so the
# sweep is replayed in every shape the table claims for it.
SWEEP_SAMPLE = ('ch.namedexpr-in-bound', 'res.call-attribute-subprocess',
                'pf.in-bound', 'ra.else')


def _collapsed(text):
    return ' '.join(text.split())


def _anchor(arm):
    """The arm's own text, less the ellipsis a truncated anchor ends on.

    An anchor is a PREFIX of the arm's block, so the table costs one
    line per arm instead of three, and the prefix is what has to still
    be there for the arm to be where the table says it is.
    """
    return _collapsed(arm[ANCHOR]).removesuffix('...')


def _arm(name):
    for arm in LAUNCH_ARMS:
        if arm[ID] == name:
            return arm
    raise AssertionError(f'{name} is not in the enumeration')


def test_the_enumeration_is_a_closed_list_with_no_holes(tmp):
    """Every arm is listed once, in one of three states, with evidence.

    A fourth state — "live and deliberately unpinned" — is what this
    table exists to make unrepresentable, so the states are closed and
    checked rather than documented.
    """
    del tmp
    assert LAUNCH_ARMS, 'the enumeration is empty'
    names = [arm[ID] for arm in LAUNCH_ARMS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f'arms listed twice: {duplicates}'
    outside = sorted({arm[STATE] for arm in LAUNCH_ARMS} - set(STATES))
    assert not outside, f'states outside the closed set: {outside}'
    blank = [arm[ID] for arm in LAUNCH_ARMS
             if not str(arm[WHAT]).strip() or not str(arm[EVIDENCE]).strip()]
    assert not blank, f'arms with no description or no evidence: {blank}'
    uncut = [arm[ID] for arm in LAUNCH_ARMS if not arm[CUT].strip()]
    assert not uncut, f'arms with no clause the sweep deleted: {uncut}'
    files = {arm[FILE] for arm in LAUNCH_ARMS}
    assert files == {'_launch_audit.py', '_argv_read.py'}, files


def test_every_controlled_arm_names_a_row_or_a_control_that_exists(tmp):
    """A CONTROLLED entry is only worth what its evidence resolves to.

    The two row files, `ARM_CONTROLS` and the step ceiling are the whole
    set of things that can fail when an arm is deleted, so an evidence
    string naming none of them is a claim no test can check.
    """
    del tmp
    known = ROW_LABELS | CONTROL_LABELS | {STEP_CEILING_CONTROL}
    unknown = sorted({arm[EVIDENCE] for arm in LAUNCH_ARMS
                      if arm[STATE] == 'CONTROLLED'
                      and arm[EVIDENCE] not in known})
    assert not unknown, f'controlled arms naming no row or control: {unknown}'
    unused = sorted(CONTROL_LABELS - {arm[EVIDENCE] for arm in LAUNCH_ARMS})
    assert not unused, f'controls no arm names: {unused}'
    stepped = sorted(arm[ID] for arm in LAUNCH_ARMS
                     if arm[EVIDENCE] == STEP_CEILING_CONTROL)
    assert set(stepped) == {'fx.skip-registered', 'mr.while-guard'}, stepped
    # The step ceiling exists for a mutant that does not answer WRONG, it
    # does not stop, so every arm bound to it is one whose sweep reports
    # no verdict at all. An arm that DOES answer has a row to hold it,
    # and reaching for the ceiling instead would be a control that can
    # only fail on a hang.
    answered = sorted(arm[ID] for arm in LAUNCH_ARMS
                      if arm[EVIDENCE] != STEP_CEILING_CONTROL
                      and arm[EVIDENCE] in ROW_LABELS | CONTROL_LABELS)
    assert 'fx.skip-registered' in stepped and 'mr.while-guard' in stepped, (
        f'the step ceiling is bound to {stepped}; it exists for a fixpoint '
        'or a loop that does not stop, so both of those arms should reach '
        f'for it, and none of {answered} answers without one')


def test_every_arm_is_still_in_the_analyser_it_was_classified_in(tmp):
    """The `anchor` column is a drift check, not decoration.

    A classified arm whose text has left the analyser is an arm the
    table is still describing, and reading it as current is how a
    successor is misled.
    """
    del tmp
    names = {arm[FILE] for arm in LAUNCH_ARMS}
    sources = {name: _collapsed((TESTS / name).read_text(encoding='utf-8'))
               for name in names}
    lengths = {name: len((TESTS / name).read_text(
        encoding='utf-8').splitlines()) for name in sources}
    missing = sorted(arm[ID] for arm in LAUNCH_ARMS
                     if _anchor(arm) not in sources[arm[FILE]])
    assert not missing, ('arms whose text is no longer in the analyser: '
                         f'{missing}')
    out_of_range = sorted(arm[ID] for arm in LAUNCH_ARMS
                          if not 0 < arm[LINE] <= lengths[arm[FILE]])
    assert not out_of_range, (
        f'arms whose line is outside its file: {out_of_range}')


def test_each_control_asserts_what_the_analyser_answers_today(tmp):
    """The arms no row file can hold, pinned on sites AND refusals.

    A `LAUNCH_REFUSAL_ROW` is built to hold exactly one refusal, and
    each of these shapes is the one that tells its arm apart only
    because the OTHER refusal moves. So the control states the whole
    answer, and deleting the arm moves it.
    """
    del tmp
    for label, source, sites, refusals in ARM_CONTROLS:
        assert bound_sites(source, label) == sites, label
        assert launch_refusals(source, label) == refusals, label


def test_the_sweep_reproduces_each_state_it_claims(tmp):
    """Deleting the arm moves a CONTROLLED arm's evidence and nothing else.

    The table's method, run in place: a CONTROLLED arm's own evidence
    label is among the verdicts that move, a REDUNDANT or DEAD arm moves
    none, and every cut removed text rather than nothing. So the three
    states are told apart by what the sweep does, which is the claim the
    whole enumeration rests on.

    A child that did not ANSWER is none of those. It moves no verdict
    because it produced none, so an empty `moved` from a hung or dead
    child is read here as the same thing as a mutant that changed
    nothing -- and the two arms in `SWEEP_SAMPLE` this cannot tell apart
    are exactly the two states the sweep exists to adjudicate.
    """
    arms = [_arm(name) for name in SWEEP_SAMPLE]
    findings = arm_sweep(Path(tmp), arms)
    for arm in arms:
        name, state, evidence = arm[ID], arm[STATE], arm[EVIDENCE]
        found = findings[name]
        assert found['removed'].strip(), f'{name}: the cut removed nothing'
        assert 'refused' not in found, f'{name}: {found["refused"]}'
        assert 'timed_out' not in found, (
            f'{name}: the child did not answer, so this run says nothing '
            'about the arm; an empty `moved` from a child that hung is '
            'not a mutant that changed nothing')
        if state == 'CONTROLLED':
            assert evidence in found['moved'], (
                f'{name}: deleting it left every verdict alone, so its own '
                f'evidence {evidence!r} does not control it; moved '
                f'{found["moved"]}')
            # The reverse of `CRASH_CONTROLLED`, over the arms this test
            # sweeps anyway: a sample is a sample, and this is the half
            # of the split a completeness claim would rest on.
            assert evidence not in found['crash'], (
                f'{name}: its evidence raised, so it belongs in '
                'CRASH_CONTROLLED and was left out of it')
        else:
            assert state in (REDUNDANT, DEAD), name
            assert not found['moved'], (
                f'{name}: recorded {state} but deleting it moved '
                f'{found["moved"]}')


def test_every_recorded_crash_is_the_one_the_sweep_reproduces(tmp):
    """A crash is a control, and a weaker one than a changed value.

    `CRASH_CONTROLLED` says which CONTROLLED arms go red because the
    analyser RAISED rather than because its own clause changed the
    answer, and this is the forward half of that: every name in it
    really does crash, on its own evidence, in the real tree. Without
    the check the set is a claim; with it, a control that stops being
    crash-held has to be taken out deliberately, which is the only way
    a reader learns the split moved.
    """
    arms = [_arm(name) for name in sorted(CRASH_CONTROLLED)]
    findings = arm_sweep(Path(tmp), arms)
    for arm in arms:
        name, evidence = arm[ID], arm[EVIDENCE]
        found = findings[name]
        assert 'refused' not in found, f'{name}: {found["refused"]}'
        assert 'timed_out' not in found, (
            f'{name}: the child did not answer, so its control is neither '
            'a changed value nor a crash and the recorded set is stale')
        assert evidence in found['crash'], (
            f'{name}: recorded as held by a raise, but its evidence '
            f'{evidence!r} did not raise; crash {found["crash"]}, moved '
            f'{found["moved"]}')
        # An arm can be held by a raise AND isolated by a control that
        # changes a value. Where it is, the value-changing one is named
        # in SECONDARY_CONTROLLED, so the crash set never reads as "this
        # arm has no control that isolates it" when it has one.
        by_value = sorted(label for label in found['moved']
                          if label not in found['crash'])
        unrecorded = sorted(set(by_value)
                            - set(SECONDARY_CONTROLLED.get(name, ())))
        assert not unrecorded, (
            f'{name}: it is also held by {by_value}, and {unrecorded} '
            'are not named in SECONDARY_CONTROLLED')


def test_the_crash_set_names_only_arms_that_are_controlled(tmp):
    """The set is a partition of the CONTROLLED arms, not a list.

    A REDUNDANT or DEAD arm in the set would claim a control for a
    verdict that is supposed to have none, and a name that is not in
    `LAUNCH_ARMS` at all would be a control nothing can re-derive.
    """
    del tmp
    by_name = {arm[ID]: arm for arm in LAUNCH_ARMS}
    unknown = sorted(CRASH_CONTROLLED - set(by_name))
    assert not unknown, f'crash set naming no arm: {unknown}'
    not_controlled = sorted(name for name in CRASH_CONTROLLED
                            if by_name[name][STATE] != 'CONTROLLED')
    assert not not_controlled, (
        f'crash set naming a non-CONTROLLED arm: {not_controlled}')
    known = ROW_LABELS | CONTROL_LABELS | {STEP_CEILING_CONTROL}
    for name, labels in SECONDARY_CONTROLLED.items():
        assert name in by_name, f'secondary control for no arm: {name}'
        assert by_name[name][STATE] == 'CONTROLLED', name
        assert by_name[name][EVIDENCE] not in labels, (
            f'{name}: its second control is the one already recorded as '
            'its evidence, so nothing is being added')
        missing = sorted(set(labels) - known)
        assert not missing, f'{name}: second control names nothing: {missing}'
    for label, holders in ROW_UNCLAIMED:
        assert label in ROW_LABELS, f'ROW_UNCLAIMED names no row: {label}'
        recorded = {arm[EVIDENCE] for arm in LAUNCH_ARMS}
        assert label not in recorded, (
            f'{label} is now some arm\'s recorded evidence, so it is not '
            'unclaimed and should be retired from ROW_UNCLAIMED')
        unknown = sorted(set(holders) - set(by_name))
        assert not unknown, f'{label}: holders name no arm: {unknown}'
        assert holders == tuple(sorted(holders)), (
            f'{label}: holders are not in the order a reader scans them')


def test_every_marker_clause_is_an_arm_or_a_named_non_member(tmp):
    """The closure claim is a granularity claim, so its granularity is checked.

    "Every arm of both analysers" reads as "every guard clause", and the
    spelling-independent marker for that is every `if`/`elif`/`while`
    header plus each disjunct of a multi-line condition. A reader who
    takes that marker and finds a clause at a line the table does not
    list has found an unstated hole in the one claim the table exists to
    make. `MARKER_NON_MEMBERS` is the answer, one line per clause, and
    this re-derives the marker so the answer cannot fall behind the
    analysers.
    """
    del tmp
    named = {(row[0], row[1]) for row in MARKER_NON_MEMBERS}
    for name in ('_launch_audit.py', '_argv_read.py'):
        clauses, spans = _marker(name)
        listed = {arm[LINE] for arm in LAUNCH_ARMS if arm[FILE] == name}
        for line, _kind in clauses:
            inside = any(start <= line <= end for start, end in spans)
            if line in listed or inside:
                continue
            assert (name, line) in named, (
                f'{name}:{line} is a guard clause the marker finds outside '
                f'every arm, and MARKER_NON_MEMBERS does not name it; '
                'non-members named: '
                f'{sorted(n for n in named if n[0] == name)}')
    known = ROW_LABELS | CONTROL_LABELS | {STEP_CEILING_CONTROL}
    for name, line, state, why in MARKER_NON_MEMBERS:
        clauses, spans = _marker(name)
        assert line in {c for c, _ in clauses}, (
            f'{name}:{line} is not a marker clause any more')
        assert not any(s <= line <= e for s, e in spans), (
            f'{name}:{line} is named a non-member but sits inside a listed '
            "arm's span, so it is that arm and not an exception")
        if state == 'CONTROLLED':
            assert why in known, (
                f'{name}:{line} is CONTROLLED and names nothing: {why}')
        else:
            assert state == 'MERGED', f'{name}:{line}: state {state!r}'
            assert len(why) > 40, f'{name}:{line}: a merge reason, not a shrug'


def _marker(name):
    """`([(line, kind)], [(arm line, span end)])` for one analyser.

    Spans come from the AST rather than the table, because the table
    records where an arm was classified and not how far it reaches.
    """
    tree = ast.parse((TESTS / name).read_text(encoding='utf-8'))
    ends = {}
    for node in ast.walk(tree):
        line = getattr(node, 'lineno', None)
        if line is not None:
            ends[line] = max(ends.get(line, 0),
                             getattr(node, 'end_lineno', None) or line)
    parent_of = {}
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, ast.AST):
                    parent_of[id(item)] = (parent, field)
    clauses = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.While)):
            continue
        parent, field = parent_of.get(id(node), (None, None))
        kind = ('elif' if field == 'orelse' and isinstance(parent, ast.If)
                else type(node).__name__.lower())
        clauses.append((node.lineno, kind))
        clauses.extend((operand.lineno, 'disjunct')
                       for sub in ast.walk(node)
                       if isinstance(sub, (ast.BoolOp, ast.Compare))
                       for operand in (list(sub.values)
                                       if isinstance(sub, ast.BoolOp)
                                       else [sub.left, *sub.comparators])
                       if getattr(operand, 'lineno', None)
                       and getattr(operand, 'end_lineno', None)
                       and operand.lineno != operand.end_lineno)
    arms = [arm for arm in LAUNCH_ARMS if arm[FILE] == name]
    return sorted(set(clauses)), [(arm[LINE],
                                   ends.get(arm[LINE], arm[LINE]))
                                  for arm in arms]


def test_the_fixpoint_stops_on_a_factory_it_has_already_registered(tmp):
    """`fx.skip-registered` is load-bearing by being a stop, not a value.

    Without it the launcher branch re-adds its name and re-sets `changed`
    on every pass, so the analysis does not answer wrong — it does not
    stop. A row asserts a verdict and there is none to assert, so the
    bound is a step COUNT taken in a child, which is what
    `_step_ceiling` is for.
    """
    del tmp
    source = ("import subprocess\n"
              "def probe(ns):\n"
              "    return ns['subprocess'].run(\n"
              "        ['git', 'status'], check=True, timeout=30)\n")
    sites, child = within_step_ceiling(source, 'launcher-factory')
    assert sites == [(3, 'git', 'timeout')], sites
    assert child != os.getpid(), 'the step count was taken in this process'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
