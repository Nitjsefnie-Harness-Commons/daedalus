#!/usr/bin/env python3
"""The enumeration of guard arms, and the controls the row files hold.

`tests/_launch_arms.py` is the data: every arm of both analysers with
the state it is in, the evidence for that state, and the one clause a
sweep deleted to find out. This suite is what stops the table drifting
from the tree — a named row that no longer exists, a duplicated arm, an
arm whose line has moved — and it replays the sweep for one arm of each
state, so the table's own method is exercised.

It also carries what the two row files cannot express: three controls
asserting sites AND refusals, and the step ceiling for an arm whose
mutant does not stop.
"""
import ast
import hashlib
import importlib
import os
import re
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _arm_sweep import arm_sweep, cut_arm, cut_span  # noqa: E402
from _bound_site_rows import BOUND_SITE_ROWS  # noqa: E402
from _launch_arm_records import (ARM_NOTES, CRASH_CONTROLLED,  # noqa: E402
                                MARKER_NON_MEMBERS, ROW_UNCLAIMED,
                                SECONDARY_CONTROLLED, STEP_CEILING_HELD_BY)
from _launch_arms import (ARM_CONTROLS, DEAD, LAUNCH_ARMS,  # noqa: E402
                          REDUNDANT, STEP_CEILING_CONTROL, STATES)
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
    be there for the arm to be where the table says it is. The strip is
    the `': ...'` form, where the ellipsis stands in for a body.
    """
    return _collapsed(arm[ANCHOR]).removesuffix('...').rstrip()


def _from_line(rows, line, width):
    """The collapsed source from `line` on, for at least `width` chars.

    An anchor is not always inside the clause the sweep cuts: a
    `drop_stmt` arm's runs on into the next statement, a `boolop` arm's
    out to the end of the disjunction. So the span is the anchor's own
    length -- the check is that the line is where the text BEGINS.
    """
    taken, length = [], 0
    for row in rows[line - 1:]:
        taken.append(row)
        length += len(row) + 1
        if length >= width:
            break
    return ' '.join(taken)


def _arm(name):
    for arm in LAUNCH_ARMS:
        if arm[ID] == name:
            return arm
    raise AssertionError(f'{name} is not in the enumeration')


def _cut_signature(arm, source):
    """What this entry's own cut does to the live analyser.

    Two entries that cut the same clause leave the same file behind, so
    the signature IS the mutation, taken from the tree rather than from
    the table. A spec the cut refuses is keyed on the refusal instead,
    so a refused entry is still one clause and still distinct.
    """
    try:
        mutated, _, _ = cut_arm(source, arm[CUT])
    except Exception as error:  # the shape is the sweep's to report
        return (arm[FILE], arm[CUT], type(error).__name__, str(error))
    return (arm[FILE], hashlib.sha256(mutated.encode('utf-8')).hexdigest())


def test_the_enumeration_is_a_closed_list_with_no_holes(tmp):
    """Every arm is listed once, in one of three states, with evidence.

    A fourth state — "live and deliberately unpinned" — is what this
    table exists to make unrepresentable, so the states are closed and
    checked rather than documented.

    "Once" is the CLAUSE, not the name: a second row carrying one
    clause under a new name satisfies a check over names while
    inflating the denominator every count in the records and in
    `launch_arm_sweep` is taken over.
    """
    del tmp
    assert LAUNCH_ARMS, 'the enumeration is empty'
    names = [arm[ID] for arm in LAUNCH_ARMS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f'arms listed twice: {duplicates}'
    by_clause = {}
    for arm in LAUNCH_ARMS:
        key = (arm[FILE], arm[LINE], arm[CUT])
        assert key not in by_clause, (
            f'{arm[ID]} is the clause {by_clause[key]} already names: the '
            'same file, line and cut under a second name, and the arm count '
            'is the denominator every completeness claim divides by')
        by_clause[key] = arm[ID]
    sources = {name: (TESTS / name).read_text(encoding='utf-8')
               for name in {arm[FILE] for arm in LAUNCH_ARMS}}
    clauses = {}
    for arm in LAUNCH_ARMS:
        clauses.setdefault(_cut_signature(arm, sources[arm[FILE]]),
                           []).append(arm[ID])
    repeated = sorted(group for group in clauses.values() if len(group) > 1)
    assert not repeated, (
        'entries that cut one clause between them, so the enumeration '
        f'claims more arms than the analysers hold: {repeated}')
    outside = sorted({arm[STATE] for arm in LAUNCH_ARMS} - set(STATES))
    assert not outside, f'states outside the closed set: {outside}'
    blank = [arm[ID] for arm in LAUNCH_ARMS
             if not str(arm[WHAT]).strip() or not str(arm[EVIDENCE]).strip()]
    assert not blank, f'arms with no description or no evidence: {blank}'
    uncut = [arm[ID] for arm in LAUNCH_ARMS if not arm[CUT].strip()]
    assert not uncut, f'arms with no clause the sweep deleted: {uncut}'
    assert set(sources) == {'_launch_audit.py', '_argv_read.py'}, sources


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


def test_every_step_ceiling_arm_names_the_test_that_holds_it(tmp):
    """The step ceiling is not a row, so the record must name a real test.

    What holds these two arms bounds a STEP COUNT rather than a
    verdict, so the record names that test and this resolves it.
    """
    del tmp
    by_name = {arm[ID]: arm for arm in LAUNCH_ARMS}
    ceiling = {arm[ID] for arm in LAUNCH_ARMS
               if arm[EVIDENCE] == STEP_CEILING_CONTROL}
    assert set(STEP_CEILING_HELD_BY) == ceiling, (
        'STEP_CEILING_HELD_BY and the arms bound to the ceiling disagree: '
        f'{sorted(set(STEP_CEILING_HELD_BY) ^ ceiling)}')
    for name, holders in STEP_CEILING_HELD_BY.items():
        assert name in by_name, f'a holder recorded for no arm: {name}'
        assert by_name[name][STATE] == 'CONTROLLED', name
        assert holders, f'{name}: no test named'
        for holder in holders:
            path, _, test = holder.partition(':')
            assert (_util.ROOT / path).is_file(), f'{name}: no {path}'
            module = Path(path).stem
            assert test.startswith('test_'), f'{name}: not a test: {test}'
            found = getattr(importlib.import_module(module), test, None)
            assert callable(found), f'{name}: {module} has no {test}'
    assert STEP_CEILING_CONTROL not in ROW_LABELS | CONTROL_LABELS, (
        'the step ceiling is now a row label, so a ceiling arm can be '
        'held by the verdict table and this record is the wrong one')


def test_every_arm_is_still_in_the_analyser_it_was_classified_in(tmp):
    """The `line` column is the addressing scheme, so it is the checked one.

    Every one of the 150 `cut` specs is line-keyed and
    `tests/_arm_sweep.py` resolves it by `node.lineno == line`, so a
    moved line means the arm now cuts whichever clause landed there.
    The anchor text appears SOMEWHERE in the analyser whatever moves,
    so its presence is not the check; the line it starts at is.
    """
    del tmp
    rows = {}
    for name in {arm[FILE] for arm in LAUNCH_ARMS}:
        text = (TESTS / name).read_text(encoding='utf-8')
        rows[name] = [_collapsed(row) for row in text.splitlines()]
    # Sorted by line so the FIRST entry is the cause.
    stale = []
    for arm in LAUNCH_ARMS:
        anchor = _anchor(arm)
        line = arm[LINE]
        at = _from_line(rows[arm[FILE]], line, len(anchor))
        if not at.startswith(anchor):
            stale.append((arm[FILE], line,
                          f'{arm[FILE]}:{line} {arm[ID]} reads {at[:50]!r}'))
    stale.sort()
    named = [row for _, _, row in stale]
    # One insertion moves every arm below it, so the cause is the
    # first entry and the rest is the count.
    shown = named[:8] + ([f'(+{len(named) - 8} more)'] if len(named) > 8
                         else [])
    assert not stale, ('arms whose recorded line no longer carries their own '
                       'anchor, so the cut spec resolves another clause; the '
                       f'first is the cause: {shown}')


def test_every_arm_is_uniquely_addressed(tmp):
    """The line check cannot say WHICH arm it found, so identity is pinned.

    `test_every_arm_is_still_in_the_analyser_it_was_classified_in`
    reads the text at an arm's recorded line and asks whether it starts
    with that arm's anchor. Twenty anchors match at a second arm's line
    and twelve are shared outright, so re-pointing an arm at a line
    carrying a byte-identical anchor satisfies the check while its cut
    removes a neighbour's clause in another function.

    Two things close that. The identity the check runs on —
    (file, line, anchor, cut) — is asserted unique over the real table,
    and every entry's recorded line is asserted to fall inside the span
    its OWN cut removes, so the two columns cannot drift apart. The
    same-line pairs the table carries on purpose are named rather than
    left to be rediscovered: two arms on one line is a property here,
    because two arms cut two operands of one disjunction, and what
    separates them is the cut.
    """
    del tmp
    seen = {}
    for arm in LAUNCH_ARMS:
        key = (arm[FILE], arm[LINE], _anchor(arm), arm[CUT])
        assert key not in seen, (
            f'{arm[ID]} is addressed exactly as {seen[key]} is: file, line, '
            'anchor and cut alike, so the line check finds whichever the '
            'reader has in mind and the cut can remove the other clause')
        seen[key] = arm[ID]
    sources = {arm[FILE]: (TESTS / arm[FILE]).read_text(encoding='utf-8')
               for arm in LAUNCH_ARMS}
    for arm in LAUNCH_ARMS:
        span = cut_span(sources[arm[FILE]], arm[CUT])
        assert span is not None, f'{arm[ID]}: {arm[CUT]} removes no clause'
        assert span[0] <= arm[LINE] <= span[1], (
            f'{arm[ID]} records line {arm[LINE]}, which is not in the '
            f'{span[0]}-{span[1]} span its own cut {arm[CUT]} removes, so '
            'the two columns address different clauses')
    on_one_line = {}
    for arm in LAUNCH_ARMS:
        on_one_line.setdefault((arm[FILE], arm[LINE]), []).append(arm[CUT])
    for where, cuts in sorted(on_one_line.items()):
        assert len(set(cuts)) == len(cuts), (
            f'{where[0]}:{where[1]} carries {len(cuts)} arms whose cuts are '
            f'not distinct: {cuts}')
    shared = sorted(where for where, cuts in on_one_line.items()
                    if len(cuts) > 1)
    assert shared == [('_launch_audit.py', 526), ('_launch_audit.py', 549)], (
        'the same-line pairs the table carries, each two operands of one '
        f'disjunction; a new one is a decision, not an accident: {shared}')


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
    for name, note in ARM_NOTES.items():
        assert name in by_name, f'a note for no arm: {name}'
        assert len(note.split()) > 25, (
            f'{name}: a pointer, not the mechanism: {note!r}')
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
    spelling-independent marker for that is every `if`/`elif`/`while`/
    `return` header plus each disjunct of a multi-line condition. A
    clause the marker finds at a line the table does not list is an
    unstated hole in the one claim the table exists to make, so
    `MARKER_NON_MEMBERS` is the answer and this refuses a hole in it.
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
            continue
        assert state in ('MERGED', 'INERT'), f'{name}:{line}: state {state!r}'
        assert len(why) > 40, (
            f'{name}:{line}: a {state.lower()} reason, not a shrug')


def _functions(name):
    """`{name: (first line, last line)}` for every function in an analyser.

    The INNESTEST span wins where functions nest, so a lookup answers
    "which function is this line in" rather than "which functions cover
    it" — the one a reason has to name.
    """
    spans = {}
    for node in ast.walk(ast.parse((TESTS / name).read_text(
            encoding='utf-8'))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            held = spans.get(node.name)
            if held is None or node.lineno > held[0]:
                spans[node.name] = (node.lineno, node.end_lineno)
    return spans


def _enclosing(spans, line):
    holding = [name for name, (start, end) in spans.items()
               if start <= line <= end]
    return max(holding, key=lambda name: spans[name][0], default='')


def test_every_named_non_member_reason_names_the_function_it_is_in(tmp):
    """A reason that is merely long is not a reason.

    `len(why) > 40` cannot tell a true sentence from a plausible one,
    and three of these named a function the clause is not in. So every
    function a MERGED or INERT reason names is checked against the live
    AST — a reason may not name the wrong one — and every arm a MERGED
    reason places at a line is checked against the table, because an
    arm id at a line no arm sits on is the same false sentence.
    """
    del tmp
    files = {row[0] for row in MARKER_NON_MEMBERS}
    by_file = {name: ([arm for arm in LAUNCH_ARMS if arm[FILE] == name],
                      _functions(name))
               for name in files}
    for name, line, state, why in MARKER_NON_MEMBERS:
        if state == 'CONTROLLED':
            continue
        arms, functions = by_file[name]
        enclosing = _enclosing(functions, line)
        named = sorted(fn for fn in functions
                       if re.search(rf'\b{re.escape(fn)}\b', why))
        assert not set(named) - {enclosing}, (
            f'{name}:{line}: the reason names a function the clause is not '
            f'in: {sorted(set(named) - {enclosing})}; it is in {enclosing!r}')
        if state == 'MERGED':
            placed = [(int(at), arm_id) for at, arm_id in
                      re.findall(r':(\d+)\s+([a-z][\w-]*(?:\.[\w-]+)*)',
                                 why)]
            for at, arm_id in placed:
                assert any(arm[ID] == arm_id for arm in arms), (
                    f'{name}:{line}: the reason names {arm_id!r}, which is no '
                    f'arm of the enumeration')
                assert [arm[LINE] for arm in arms
                        if arm[ID] == arm_id] == [at], (
                    f'{name}:{line}: the reason places {arm_id!r} at :{at}, '
                    'and the table puts it elsewhere')


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
        if not isinstance(node, (ast.If, ast.While, ast.Return)):
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
