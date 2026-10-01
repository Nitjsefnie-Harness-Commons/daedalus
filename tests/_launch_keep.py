"""What the bounded-launch control reads, and which sites of that it keeps.

Both decisions below belong to `tests/test_repo_layout.py`'s control, which
is the one consumer left: it walks the whole tracked tree on the first and
keeps or drops each site on the second, so the two are written once here
and read from here. A second copy would narrow that control's population
with nothing to report it, and the hole would be silent in the shrinking
direction.

The figures the two rules were tuned against — 139 dropped sites narrowed to
55, then 70 — were measured before #1408 and none has been re-measured
since; the prefilter they were taken against is not in this file, so none
is reproducible by making that one edit. What the rules below yield on the
current tree is what `tests/test_repo_layout.py` computes as it runs, so
read the answer there rather than from a number written here.
"""


def in_launch_population(name, source):
    """Does the bounded-launch control read this file at all?

    Every tracked Python file, with no filter. `timeout=` and a
    `**`-unpacked mapping are the two limbs of a bounded call with grammar
    behind them, and they are not the whole of what the analyser bounds on,
    so no spelling of the source can stand in for the control's own
    inspection — a filter that tries is a second guess at it, and it fails
    silently. The `'subprocess' in source` test this replaces skipped 440 of
    the 631 tracked files, and the analyser reports 92 `unplaced` sites in
    them, so a bounded launch that reached a module by any route other than
    a `subprocess` spelling was gone rather than checked-and-passed (#1038,
    #1155). A text filter spelling those two tokens was tried and taken
    back out (#1408): the analyser also bounds a call on any keyword the
    stdlib does not take, so `timout=30` is a site, and PEP 3131 normalises
    a fullwidth `timeout` to the ASCII spelling in `ast.keyword.arg` from a
    file whose own text never spells it. Both fixtures are in
    `tests/test_harness_launch_bounds.py`, and the cost of the walk
    over the whole tree is paid in the analyser.

    `source` is still the second argument because both consumers already
    read the file to hand it over, and a parameter nothing reads is a
    signature that lies about what the control costs.
    """
    del source
    return name.endswith('.py')


def control_keeps(head, kind):
    """Does the bounded-launch control keep the site this label names?

    `head` and `kind` are the analyser's own labels, from
    `tests/_launch_audit.py`'s `bound_sites`, so a shape the analyser could
    not place is kept and no bounded launch reaches the tree without a
    refusal or an allowance row.
    """
    return head in ('git', 'ambiguous') or kind == 'unplaced'
