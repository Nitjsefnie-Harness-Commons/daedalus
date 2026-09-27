"""Which reported launch sites the bounded-launch control keeps.

`tests/test_repo_layout.py` keeps a reported launch whose head the
analyser reads as `git` or `ambiguous`, and one the analyser refused to
place; every other reported launch is out of scope by that boundary, and
`tests/test_launch_control_boundary.py` is the control that holds the
dropped ones to being refused rather than merely unpolled.

Both suites have to decide the same question, so the rule is written once
here. A copy in the second consumer is worse than no shared rule at all:
widening it fails loudly, but narrowing it shrinks that control's
population and nothing reports it, which a planted one-token edit
measured at 139 checked sites dropping to 55 with both suites green.
"""


def control_keeps(head, kind):
    """Does the bounded-launch control keep the site this label names?

    `head` and `kind` are the analyser's own labels, read from
    `tests/_launch_audit.py`'s `bound_sites`, so a shape the analyser
    could not place is kept rather than dropped and no bounded launch
    reaches the tree without a refusal or an allowance row.
    """
    return head in ('git', 'ambiguous') or kind == 'unplaced'
