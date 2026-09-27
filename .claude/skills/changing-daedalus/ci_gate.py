"""The workflows whose absence is a refusal, and the one predicate over runs.

Two waiters read the same run list and have to answer the same question of
it: was the workflow that gates the merge dispatched on this commit at all?
`ci_wait.py` refuses with exit 4 when it was not (issue #1217), and
`watch_all.py` keeps its hold rather than releasing a batch whose gating
matrix was never created (issue #1223). Two copies of the expectation would
be two mechanisms wearing one name, so the expectation and the predicate
live here and both callers import them.

It is a module of its own for a third reason: both callers sit near the
500-line production ceiling the size policy enforces, and an expectation
that lives inside a caller is an expectation the other caller can drift
from.

A run satisfies a requirement by its `name`, exactly. `Tests` and `test` are
different workflows, and treating either as the gate would reinstate the
false green this exists to remove. A conclusion is irrelevant to the
predicate: both callers judge conclusions against rules of their own, and a
required workflow that is present and red is a failure, not an absence.
"""

REQUIRED_WORKFLOWS = frozenset({'tests'})


def missing_required(runs, *, required=REQUIRED_WORKFLOWS):
    """The required workflow names no run in `runs` carries, sorted.

    An empty `required` is satisfied by every run list, which is what makes
    the argument a no-op rather than a rule that refuses everything.
    """
    return sorted(required - {run.get('name') for run in runs})
