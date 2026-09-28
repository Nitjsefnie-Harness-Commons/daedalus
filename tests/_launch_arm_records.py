"""What the arm table records ABOUT itself, kept out of the table.

`tests/_launch_arms.py` is the enumeration: every guard arm of the two
launch-audit analysers, in one of three states, with the evidence for
its state. This module is the rest -- the four sets that qualify what
that enumeration means, and the mechanism note for each arm whose
verdict a reader would otherwise have to take on trust. They are one
concern and not the enumeration, and the enumeration has a size ceiling
to keep.

    CRASH_CONTROLLED      arms whose evidence goes red by RAISING
    SECONDARY_CONTROLLED  a control that holds an arm without being its
                          recorded evidence
    MARKER_NON_MEMBERS    guard clauses the spelling-independent marker
                          finds outside every listed arm, and what
                          covers each
    ROW_UNCLAIMED         rows this branch added that no arm records
    ARM_NOTES             why a particular verdict is what it is

`tests/test_launch_arms.py` checks all five, and the table carries a
one-line pointer to the note at each arm it names.
"""
# The CONTROLLED arms whose `evidence` goes red by RAISING rather than
# to another value. A crash is a real control -- the suite goes red --
# but a weaker one: it pins "the analyser must not raise on this
# shape", which a robustness change can satisfy while the arm's own
# clause stops deciding. So the subset is NAMED beside the count rather
# than folded into it: `evidence-does-not-control: 0` means the named
# evidence's rendered verdict changes, and this set is the part of that
# held by the absence of a crash.
#
# Derived by sweeping all 150 arms and reading, per arm, whether its own
# evidence moved to a value or to a `RAISED ...` string. Fourteen, and
# for every one the by-value set is empty -- the crash is the whole of
# its control. `mr.while-guard` is NOT here and is why this is not
# simply "every arm that raises": its mutant child does not ANSWER (the
# cut is the fixpoint that does not stop), so there is no verdict to
# classify. Its real control is the step ceiling and the row this table
# used to name is the second, weaker one; see SECONDARY_CONTROLLED.
CRASH_CONTROLLED = frozenset({
    'fw.empty', 'fw.resolve', 'ha.name-guard', 'hl.no-container',
    'mr.func-shape', 'mr.not-a-call', 'mr.target-not-name', 'norm.no-dot',
    'par.comprehension', 'pf.not-a-name', 'rc.guard', 'rs.follow',
    'rs.guard', 'rw.no-container',
})

# A CONTROLLED arm's `evidence` names the control that enters the arm
# that can actually FAIL. Where another control also holds the arm it is
# named here, rather than left implicit in the gap a reader of that one
# line would not know about. Three arms need it: `mr.while-guard`'s row
# reaches the guard through a KeyError on the ordinary path where the
# step ceiling enters the CYCLE the guard exists for; `rs.guard` is held
# by a raise AND by a value-changing control; and `pf.origin-not-bound`
# moves both of its rows, of which the table records one.
SECONDARY_CONTROLLED = {
    'mr.while-guard': ('machinery-reached-by-assignment-is-unproved',),
    'rs.guard': ('import-module-name-bound-twice',),
    'pf.origin-not-bound': (
        'rebound-module-name-from-import-module-is-unplaced',),
}

# "Every arm of both analysers" is a GRANULARITY claim, so it is stated
# with its granularity. The spelling-independent marker is every
# `if`/`elif`/`while`/`return` header plus each disjunct of a multi-line
# condition (which is the granularity the table already uses for :524 and
# :549), and a clause inside a listed arm's SPAN is that arm rather than
# an exception. `return` is in the marker because the table treats it as
# a member: `pf.fallthrough` is `drop_stmt` on a `return True` and
# `rw.no-container` on a `return None`, so a fallthrough return is a
# clause by this table's own practice.
#
# Twenty-two clauses fall outside every arm, and each is named here with
# what covers it: MERGED is a chain head whose every member IS a listed
# arm (five), CONTROLLED is one that stands alone with a row holding it
# (ten, all measured to move a label -- six to a value, and :53, :68,
# :153 and :383 by a raise), and INERT is a clause whose deletion moves
# no verdict at all (seven), with the reason why. The seven are the four
# `return None` that close a function, where falling off the end returns
# the same value, and the three whose only caller reads them for
# membership or truthiness, where the implicit `None` reads the same.
#
# The two finer clauses sit INSIDE a listed arm: :567 is the `**`-unpack
# operand of `ub.not-bounded` (:566) and _argv_read.py:212 the `seen`
# operand of `rs.guard` (:203). The table splits :524 and :549 per
# operand and leaves these whole, a spelling difference, not a gap.
# `tests/test_launch_arms.py` re-derives the marker and refuses a
# non-member this tuple does not name.
MARKER_NON_MEMBERS = (
    ('_launch_audit.py', 53, 'CONTROLLED',
     'kwarg-receiver-shadowing-a-module-import-is-unproved'),
    ('_launch_audit.py', 68, 'CONTROLLED',
     'a-clean-launch-emits-nothing'),
    ('_launch_audit.py', 95, 'INERT',
     "normalize's own return of its argument; every caller of it "
     'membership-tests the result or compares it to a literal, so the '
     'implicit None a deletion leaves reads the same and no row moves'),
    ('_launch_audit.py', 104, 'INERT',
     "callee_of's documented `or None`; it is the function's last "
     'statement, so deleting it returns the same value and no row moves'),
    ('_launch_audit.py', 211, 'MERGED',
     'the ast.Import chain head; every member is listed, at :213 '
     'imp.subprocess-alias, :218 imp.machinery, :220 imp.dotted and '
     ':223 imp.plain'),
    ('_launch_audit.py', 224, 'MERGED',
     'the ast.ImportFrom chain head; every member is listed, at :225 '
     'imp.from-subprocess, :233 imp.from-partial and :236 '
     'imp.from-import-module, and its else branch is inside its span'),
    ('_launch_audit.py', 300, 'CONTROLLED',
     'parameter-shadows-a-module-import-is-unproved'),
    ('_launch_audit.py', 303, 'CONTROLLED',
     'kwarg-receiver-shadowing-a-module-import-is-unproved'),
    ('_launch_audit.py', 324, 'CONTROLLED',
     'bound-name-called-bare-is-a-placed-launch'),
    ('_launch_audit.py', 383, 'CONTROLLED',
     'call-func-receiver-is-unresolved'),
    ('_launch_audit.py', 392, 'MERGED',
     'the head of a one-member chain; :394 ch.machinery-member is its '
     'only member and is listed'),
    ('_launch_audit.py', 572, 'INERT',
     'the False that closes unplaced_bounded_call; its one caller tests '
     'it for truth, so the implicit None a deletion leaves reads the '
     'same and no row moves'),
    ('_launch_audit.py', 591, 'CONTROLLED',
     'ambiguous-name'),
    ('_launch_audit.py', 658, 'CONTROLLED',
     'aliased-machinery-member-call'),
    ('_launch_audit.py', 675, 'CONTROLLED',
     'a-clean-launch-emits-nothing'),
    ('_argv_read.py', 65, 'INERT',
     "the None that closes resolve_constant's recursion; it is the "
     "function's last statement, so deleting it returns the same value "
     'and no row moves'),
    ('_argv_read.py', 88, 'MERGED',
     'the ast.Name chain head of head_is_ambiguous; every member is '
     'listed, at :90 ha.name-guard, :93 ha.follow, :96 ha.list and '
     ':98 ha.else'),
    ('_argv_read.py', 99, 'INERT',
     "the None that closes head_is_ambiguous's cap loop; it is the "
     'function\'s last statement, so deleting it returns the same value '
     'and no row moves'),
    ('_argv_read.py', 114, 'MERGED',
     'the ast.Name chain head of resolve_string; every member is '
     'listed, at :115 rs.guard, :117 rs.ambiguous and :119 rs.follow'),
    ('_argv_read.py', 123, 'INERT',
     'the False that closes head_is_ambiguous; its one caller tests it '
     'for truth, so the implicit None a deletion leaves reads the same '
     'and no row moves'),
    ('_argv_read.py', 153, 'CONTROLLED',
     'a-clean-launch-emits-nothing'),
    ('_argv_read.py', 216, 'INERT',
     "the None that closes resolve_string's recursion; it is the "
     "function's last statement, so deleting it returns the same value "
     'and no row moves'),
)

# The rows THIS BRANCH added that no arm RECORDS as its evidence. The
# forward direction -- every CONTROLLED arm names a real row -- is the
# one the table is load-bearing on; this is the other one, so a row
# meeting the table with no arm behind it is not left for a reader to
# resolve by guess. Holders are MEASURED (sweeping every arm for which
# sweep moved the label) and there are several, none of which records
# the row: these are not rows nothing reaches, but rows that are a
# second control for an arm whose recorded evidence is something else.
# Rows predating the table are not listed -- many serve the tree-wide
# rule directly. A suite asserts each name here is still a real row that
# still no arm records, so naming one retires it here.
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
# :408 places a call whose receiver is a name in `bound` and never
# looks at what that name holds. The only name satisfying both
# `origin in safe_names` and `origin in bound` here is `subprocess`:
# the plain import puts it in `safe_names` (:222-223) and rebinding
# it to a subprocess-derived value (`derives` says yes at :109) puts
# it in `bound`, so this clause is the only thing refusing. Deleting
# it makes a bounded call through an unproved receiver vanish
# instead of being reported -- fail-open on the fail-closed arm. The
# class: any module that rebinds a name it also imported to a
# subprocess-derived value and then calls a result through a name.
    ''',
    'mr.while-guard': '''
# The step ceiling, not the row: the row enters this arm through a
# KeyError on the ordinary path, and the guard is for the CYCLE,
# which the step ceiling enters and the row does not. The row still
# holds the arm; SECONDARY_CONTROLLED says so.
    ''',
}
