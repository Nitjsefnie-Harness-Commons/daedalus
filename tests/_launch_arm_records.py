"""What the arm table records ABOUT itself, kept out of the table.

`tests/_launch_arms.py` is the enumeration: every guard arm of the two
launch-audit analysers, in one of three states, with the evidence for
its state. This module is the rest -- the sets that qualify what that
enumeration means, and the mechanism note for each arm whose verdict a
reader would otherwise take on trust. The enumeration has a size
ceiling.

    CRASH_CONTROLLED      arms whose evidence goes red by RAISING
    NON_MEMBER_CRASH_HELD the CONTROLLED non-members held by a raise
    SECONDARY_CONTROLLED  a control holding an arm that is not its
                          recorded evidence
    MARKER_NON_MEMBERS    marker clauses outside every listed arm, and
                          what covers each
    ROW_UNCLAIMED         rows this branch added that no arm records
    CEILING_ARMS          the arms bound to the step ceiling
    STEP_CEILING_HELD_BY  the test holding each step-ceiling arm
    ARM_NOTES             why a particular verdict is what it is

`tests/test_launch_arms.py` checks all eight, and the table carries a
one-line pointer to the note at each arm it names.

The arm row's column order is here rather than beside the table, which
is at its size ceiling; `tests/_launch_arms.py` states it in prose.
"""
from typing import Final

from _launch_arms import LAUNCH_ARMS, STEP_CEILING_CONTROL

# (id, file, line, cut, anchor, what, state, evidence). Final keeps each
# a literal, so an arm read through one is that column's own type.
ID: Final = 0
FILE: Final = 1
LINE: Final = 2
CUT: Final = 3
ANCHOR: Final = 4
WHAT: Final = 5
STATE: Final = 6
EVIDENCE: Final = 7
# The CONTROLLED arms whose `evidence` goes red by RAISING rather than
# to another value. A crash is a real control, but a weaker one: it
# pins "the analyser must not raise on this shape", which a robustness
# change can satisfy while the arm's own clause stops deciding. So the
# subset is NAMED beside the count rather than folded into it: this
# set is the part of `evidence-does-not-control: 0` held by the
# absence of a crash.
#
# Derived by sweeping all 150 arms and reading, per arm, whether its own
# evidence moved to a value or to a `RAISED ...` string. Fourteen, and
# for every one the by-value set is empty -- the crash is the whole of
# its control. `mr.while-guard` is NOT here and is why this is not
# simply "every arm that raises": its mutant child does not ANSWER (the
# cut is the fixpoint that does not stop), so there is no verdict to
# classify. Its real control is the step ceiling and the row this table
# used to name is the second, weaker one; see SECONDARY_CONTROLLED.
#
# The suite checks each name here really crashes, and that the set
# partitions the CONTROLLED arms. COMPLETENESS -- that no OTHER arm is
# crash-held -- is AUDITABLE, not enforced: it needs the whole 150-arm
# sweep, and only a human running `python3 tests/_launch_arm_sweep.py`
# runs one. So a CONTROLLED arm whose evidence quietly stops controlling
# it is caught by nothing in CI.
CRASH_CONTROLLED = frozenset({
    'fw.empty', 'fw.resolve', 'ha.name-guard', 'hl.no-container',
    'mr.func-shape', 'mr.not-a-call', 'mr.target-not-name', 'norm.no-dot',
    'par.comprehension', 'pf.not-a-name', 'rc.guard', 'rs.follow',
    'rs.guard', 'rw.no-container',
})

# A CONTROLLED arm's `evidence` names the control that enters the arm
# that can actually FAIL. Where another control also holds the arm it is
# named here rather than left implicit. Three arms need it:
# `mr.while-guard`'s row reaches the guard through a KeyError on the
# ordinary path where the step ceiling enters the CYCLE the guard
# exists for; `rs.guard` is held by a raise AND by a value-changing
# control; `pf.origin-not-bound` moves both of its rows, of which the
# table records one.
SECONDARY_CONTROLLED = {
    'mr.while-guard': ('machinery-reached-by-assignment-is-unproved',),
    'rs.guard': ('import-module-name-bound-twice',),
    'pf.origin-not-bound': (
        'rebound-module-name-from-import-module-is-unplaced',),
}

# The closure claim is a GRANULARITY claim, so it is stated with one. The
# spelling-independent marker is every `if`/`elif`/`while`/`return` header
# plus each disjunct of a multi-line condition (the granularity the table
# already uses for :532 and :556), and a clause inside a listed arm's
# SPAN is that arm. `return` is in it because the table treats one as a
# member (`pf.fallthrough` is `drop_stmt` on one).
#
# Twenty-two clauses fall outside every arm, each named with what covers
# it: MERGED is a chain head whose every member IS a listed arm (five),
# CONTROLLED stands alone with a row holding it (ten, each measured to
# move a label and split between a changed value and a raise by
# NON_MEMBER_CRASH_HELD), and INERT is one whose deletion moves nothing
# (seven): a last statement, or one only caller reads for membership
# or truthiness.
#
# The two finer clauses sit INSIDE a listed arm: :556 is the `**`-unpack
# operand of `ub.not-bounded` (:555) and _argv_read.py:212 the `seen`
# operand of `rs.guard` (:202). The table splits :518 and :542 per
# operand and leaves these whole, a spelling difference, not a gap.
# `tests/test_launch_arms.py` re-derives it and refuses a non-member
# this tuple does not name.
MARKER_NON_MEMBERS = (
    ('_launch_audit.py', 53, 'CONTROLLED',
     'kwarg-receiver-shadowing-a-module-import-is-unproved'),
    ('_stdlib_read.py', 45, 'CONTROLLED',
     'a-clean-launch-emits-nothing'),
    ('_launch_audit.py', 79, 'INERT',
     "normalize returning its own argument; callers only test it"),
    ('_launch_audit.py', 88, 'INERT',
     "callee_of's `or None`; deleting its last statement returns "
     'the same value'),
    ('_launch_audit.py', 195, 'MERGED',
     'the ast.Import chain head; every member is listed, at :197 '
     'imp.subprocess-alias, :202 imp.machinery, :204 imp.dotted and '
     ':208 imp.plain'),
    ('_launch_audit.py', 230, 'CONTROLLED',
     'a-from-import-of-an-excluded-root-member-is-refused'),
    ('_launch_audit.py', 209, 'MERGED',
     'the ast.ImportFrom chain head; every member is listed, at :210 '
     'imp.from-subprocess, :218 imp.from-partial and :221 '
     'imp.from-import-module, and its else branch is inside its span'),
    ('_launch_audit.py', 292, 'CONTROLLED',
     'parameter-shadows-a-module-import-is-unproved'),
    ('_launch_audit.py', 295, 'CONTROLLED',
     'kwarg-receiver-shadowing-a-module-import-is-unproved'),
    ('_launch_audit.py', 316, 'CONTROLLED',
     'bound-name-called-bare-is-a-placed-launch'),
    ('_launch_audit.py', 375, 'CONTROLLED',
     'call-func-receiver-is-unresolved'),
    ('_launch_audit.py', 384, 'MERGED',
     'the head of a one-member chain; :386 ch.machinery-member is its '
     'only member and is listed'),
    ('_launch_audit.py', 561, 'INERT',
     'the False closing unplaced_bounded_call; its one caller '
     'only tests it for truth'),
    ('_launch_audit.py', 580, 'CONTROLLED',
     'ambiguous-name'),
    ('_launch_audit.py', 647, 'CONTROLLED',
     'aliased-machinery-member-call'),
    ('_launch_audit.py', 664, 'CONTROLLED',
     'a-clean-launch-emits-nothing'),
    # The chain limb stays in the analyser and is recorded here rather
    # than in the enumeration for the reason above; the root predicate
    # it reads moved to `_stdlib_read.py` with the rest of the standard
    # library the analyser reads, and its clauses are named beside it.
    ('_launch_audit.py', 499, 'CONTROLLED',
     'a-stdlib-dotted-import-member-is-a-proved-fixed-value'),
    ('_launch_audit.py', 501, 'CONTROLLED',
     'a-stdlib-dotted-import-member-is-a-proved-fixed-value'),
    ('_stdlib_read.py', 56, 'CONTROLLED',
     'a-stdlib-dotted-import-member-is-a-proved-fixed-value'),
    ('_stdlib_read.py', 58, 'CONTROLLED',
     'a-stdlib-dotted-import-member-is-a-proved-fixed-value'),
    ('_stdlib_read.py', 61, 'CONTROLLED',
     'a-stdlib-dotted-import-member-is-a-proved-fixed-value'),
    ('_stdlib_read.py', 62, 'INERT',
     "the None closing _dotted_stdlib_root; deleting its last "
     'statement returns the same value'),
    ('_argv_read.py', 65, 'INERT',
     "the None closing resolve_constant's recursion; deleting its "
     'last statement returns the same value'),
    ('_argv_read.py', 88, 'MERGED',
     'the ast.Name chain head of resolve_argv; every member is '
     'listed, at :90 ra.name-guard, :93 ra.follow, :96 ra.list and '
     ':98 ra.else'),
    ('_argv_read.py', 99, 'INERT',
     "the None closing resolve_argv's cap loop; deleting its "
     'last statement returns the same value'),
    ('_argv_read.py', 114, 'MERGED',
     'the ast.Name chain head of head_is_ambiguous; every member is '
     'listed, at :115 ha.name-guard, :117 ha.ambiguous and '
     ':119 ha.follow'),
    ('_argv_read.py', 123, 'INERT',
     'the False closing head_is_ambiguous; its one caller only '
     'tests it for truth'),
    ('_argv_read.py', 153, 'CONTROLLED',
     'a-clean-launch-emits-nothing'),
    ('_argv_read.py', 216, 'INERT',
     "the None closing resolve_string's recursion; deleting its "
     'last statement returns the same value'),
)

# The CONTROLLED non-members whose evidence goes red by RAISING rather
# than to another value; the other six are held by a value. A
# MEASUREMENT, not something the table yields: one evidence row is
# crash-held at one clause and value-held at another. The suite sweeps
# all ten and re-derives this set.
NON_MEMBER_CRASH_HELD = frozenset({
    '_argv_read.py:153', '_launch_audit.py:53', '_stdlib_read.py:45',
    '_launch_audit.py:375',
})

# The rows THIS BRANCH added that no arm RECORDS as its evidence. The
# forward direction -- every CONTROLLED arm names a real row -- is the
# one the table is load-bearing on; this is the other one, so a row
# meeting the table with no arm behind it is not left for a reader to
# resolve by guess. Holders are MEASURED, and there are several, none
# of which records the row: not rows nothing reaches, but a second
# control for an arm whose recorded evidence is something else.
# Rows predating the table are not listed; many serve the tree-wide
# rule. A suite asserts each name is still a real row no arm records,
# so naming one retires it here.
ROW_UNCLAIMED = (
    ('class-name-is-a-defined-name',
     ('fw.resolve', 'norm.no-dot', 'pf.not-safe', 'sink.gate',
      'ub.unproved')),
    ('another-receiver-shape-resolves-safe',
     ('norm.no-dot', 'pf.not-a-name', 'sink.gate', 'ub.unproved')),
)

# Why a particular verdict is what it is. An arm whose state a
# reader has to take on trust says so HERE, and the table carries a
# one-line pointer to it at the row.
ARM_NOTES = {
    'pf.origin-not-bound': '''
# Recorded CONTROLLED, and the mechanism, because the two halves of
# this return are asked about DIFFERENT names: `proved_fixed` is
# asked about the RECEIVER of the bounded call, `origin` about the
# top-level base of the callee of the value that receiver is bound
# to. So the `placed` bound `pf.in-bound` cites cannot decide it --
# :400 places a call whose receiver is a name in `bound` and never
# looks at what that name holds. The only name satisfying both
# `origin in safe_names` and `origin in bound` here is `subprocess`:
# the plain import puts it in `safe_names` (:207-209) and rebinding
# it to a subprocess-derived value (`derives` says yes at :93) puts
# it in `bound`, so this clause is the only thing refusing. Deleting
# it makes a bounded call through an unproved receiver vanish
# instead of being reported -- fail-open on the fail-closed arm. The
# class: any module that rebinds a name it also imported to a
# subprocess-derived value and then calls a result through a name.
    ''',
    'mr.while-guard': '''
# The step ceiling, not the row: the row enters this arm through a
# KeyError on the ordinary path, and the guard is for the CYCLE, which
# the step ceiling enters and the row does not.
    ''',
}


# `STEP_CEILING_CONTROL` is in neither row file, so an evidence string
# naming it resolves to nothing and cannot be checked against itself.
# What holds these two bounds a STEP COUNT rather than a verdict, so
# the test that does is named here and resolved against the tree.
STEP_CEILING_HELD_BY = {
    'fx.skip-registered': (
        'tests/test_launch_arms.py:'
        'test_the_fixpoint_stops_on_a_factory_it_has_already_registered',),
    'mr.while-guard': (
        'tests/test_repo_layout.py:'
        'test_a_cyclic_machinery_base_terminates_within_a_step_ceiling',),
}

# The arms bound to the step ceiling, derived from the table so the
# fact is written once. `STEP_CEILING_HELD_BY` is the record of what
# holds each, and the ceiling test checks this derivation against it.
CEILING_ARMS = {arm[ID] for arm in LAUNCH_ARMS
                if arm[EVIDENCE] == STEP_CEILING_CONTROL}
