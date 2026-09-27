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
"""


def in_launch_population(name, source):
    """Does the bounded-launch control read this file at all?

    This is the prefilter the issue calls unsound, which is why it is named
    rather than left as a literal at each walk: a skipped site is gone, not
    checked-and-passed.
    """
    return name.endswith('.py') and 'subprocess' in source


def control_keeps(head, kind):
    """Does the bounded-launch control keep the site this label names?

    `head` and `kind` are the analyser's own labels, from
    `tests/_launch_audit.py`'s `bound_sites`, so a shape the analyser could
    not place is kept and no bounded launch reaches the tree without a
    refusal or an allowance row.
    """
    return head in ('git', 'ambiguous') or kind == 'unplaced'
