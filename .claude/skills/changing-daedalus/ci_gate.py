"""The workflows whose absence is a refusal, and what that absence is.

Two waiters read the same run list and have to answer the same question of
it: was the workflow that gates the merge dispatched on this commit at all?
`ci_wait.py` refuses with exit 4 when it was not (issue #1217), and
`watch_all.py` keeps its hold rather than releasing a batch whose gating
matrix was never created (issue #1223). Two copies of the expectation would
be two mechanisms wearing one name, so the expectation, the predicate and
the answer an absent gate gets live here and both callers import them.

It is a module of its own for a third reason: both callers sit at or near
the 500-line production ceiling the size policy enforces, and an
expectation that lives inside a caller is an expectation the other caller
can drift from.

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


class GateAbsent:
    """A run set that concluded and never carried the gating workflow.

    The fourth answer, beside True (settled), False (runs still open) and
    None (nothing to judge) - and it belongs here rather than in a caller
    because the predicate below raises it and both callers must recognise
    it. Its own type rather than a falsy value, so it can never be read as
    one of the other three by an `is` comparison, and it carries the names
    so a refusal can name the workflow instead of falling back on
    "unknown" - the shape issue #839 was filed about, reached by whichever
    caller is asking.

    `missing_required` answers with a list and every caller turns it into
    whatever its own contract says; this is the one answer both contracts
    share, so it is named here and imported, never re-declared.
    """

    def __init__(self, missing):
        self.missing = tuple(missing)

    def __repr__(self):
        return f'gate absent: {", ".join(self.missing)}'
