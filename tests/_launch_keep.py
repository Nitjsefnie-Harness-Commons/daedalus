"""What the bounded-launch control reads, and which sites of that it keeps.

Both decisions below belong to `tests/test_repo_layout.py`'s control and to
`tests/test_launch_control_boundary.py`, which holds the sites the first
one drops to being refused rather than merely unpolled. So each is written
once here: a copy in the second consumer narrows that control's population
with nothing to report it, and a plant measured how far. Widening the keep
rule — adding `non-git` to its heads — took the boundary control from 139
dropped sites to 55 with the whole tree green. Narrowing the prefilter the
same way, with `and 'git' in source`, took it to 70. Both figures count
sites the rule drops; both are this tree's, and each is reproducible by
making that one edit.

The second consumer must go on reading `in_launch_population` from here
rather than re-spelling it: a narrowed copy is a coverage hole in the
control it does not name, and that hole is silent in the shrinking
direction and loud in neither.
"""

# Every token the bounded-launch control inspects, as it is SPELLED in a
# source file. `timeout` is the keyword-argument name it compares a call
# against; `**` is the unpacking operator whose call carries no argument
# name at all. Python has no second spelling for either and `ast.parse`
# neither desugars nor injects either, so a file spelling neither cannot
# carry a call the analyser will classify as bounded — before
# `in_launch_population` dropped it and after. The drift pin that keeps
# that true is `tests/test_launch_audit_bounded_spelling.py`, which reads
# the analyser's own AST and fails when it bounds on a third token.
BOUND_SPELLINGS = ('timeout', '**')


def in_launch_population(name, source):
    """Does the bounded-launch control read this file at all?

    Every tracked Python file that spells at least one token the control
    inspects. The control calls a launch bounded when the call carries a
    `timeout=` keyword argument or a `**`-unpacked mapping — which is
    why this reads the tree on `BOUND_SPELLINGS` rather than filtering
    on what a module launches with.

    The `'subprocess' in source` test this replaces, and the hand-named
    module tuple before it, skipped 440 of the 631 tracked files then,
    and the analyser reported 92 `unplaced` sites in them, so a bounded
    launch that reached a module by any route other than a `subprocess`
    spelling was gone rather than checked-and-passed (#1038, #1155). That
    filter guessed at what a module launches with, which a module reaches
    by more than one route; this one filters on what the control actually
    inspects, and the two tokens are grammar rather than spelling. So it
    drops a file with no reachable site in it and keeps every file with
    one, and the `.py` half stays because widening a population silently
    is the failure #1155 was filed about.

    `source` is the second argument because both consumers already read
    the file to hand it over, and a parameter nothing reads is a
    signature that lies about what the control costs.
    """
    return name.endswith('.py') and any(
        spelling in source for spelling in BOUND_SPELLINGS)


def control_keeps(head, kind):
    """Does the bounded-launch control keep the site this label names?

    `head` and `kind` are the analyser's own labels, from
    `tests/_launch_audit.py`'s `bound_sites`, so a shape the analyser could
    not place is kept and no bounded launch reaches the tree without a
    refusal or an allowance row.
    """
    return head in ('git', 'ambiguous') or kind == 'unplaced'
