"""The workflows whose absence is a refusal, and what that absence is.

Two waiters read the same run list and have to answer the same question of
it: was the workflow that gates the merge dispatched on this commit at all?
`ci_wait.py` refuses with exit 4 when it was not (issue #1217), and
`watch_all.py` keeps its hold rather than releasing a batch whose gating
matrix was never created (issue #1223). Two copies of the expectation would
be two mechanisms wearing one name, so the expectation, the predicate, the
set the predicate is asked of and the answer an absent gate gets live here
and both callers reach them through it.

They are meant to AGREE, and until issue #1262 they did not: `ci_wait` asked
the question of the set the newest-run-per-workflow filter left, and
`watch_all` asked it of the set it was handed. The two readings coincide only
while every run of a workflow carries the same `name`, which the producer
guarantees today and a `name:` edit in the workflow's own YAML ends. The
filter therefore lives here and `missing_required` applies it itself, so a
caller passing the raw list and a caller passing the filtered list get one
answer; the reading that survives is the one issue #1249 established for
conclusions - a superseded run's name does not satisfy the gate on its own.
The nearest precedent is #1223, where the hold read a settled green matrix
with the gating workflow silently absent.

It is a module of its own for a third reason: the callers sit near the
500-line production ceiling the size policy enforces, and an expectation
that lives inside a caller is an expectation the other caller can drift
from.

A run satisfies a requirement by its `name`, exactly. `Tests` and `test` are
different workflows, and treating either as the gate would reinstate the
false green this exists to remove. A conclusion is irrelevant to the
predicate: both callers judge conclusions against rules of their own, and a
required workflow that is present and red is a failure, not an absence.
Those rules are the two callers' alone, and they do not agree. Both ask the
gate question of the set below; the wait judges the conclusion question over
that set, while the hold judges it over the raw runs - which is what
`watch_all.py`'s own docstring says is deliberately not shared.
"""

from datetime import datetime, timezone

REQUIRED_WORKFLOWS = frozenset({'tests'})
OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _workflow_of(run):
    """The workflow a run belongs to: its id, or its path when id is absent."""
    return run.get('workflow_id') or run.get('path')


def _started_key(run):
    """(start, id): the instant the run began, tie-broken by numeric id."""
    text = run.get('run_started_at') or run.get('created_at')
    stamp = OLDEST
    if text:
        try:
            stamp = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
        except ValueError:
            stamp = OLDEST
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp, int(run.get('id') or 0)


def superseded(run, runs):
    """True when a strictly newer run of the same workflow exists.

    A run naming no workflow - neither an id nor a path, which `gh_client`
    emits when both are null - is never superseded: there is nothing to
    group it on, so grouping it would let one unidentified run clear
    another's conclusion.
    """
    mine = _workflow_of(run)
    if not mine:
        return False
    started = _started_key(run)
    return any(_workflow_of(other) == mine and _started_key(other) > started
               for other in runs)


def judged(runs):
    """The runs a verdict reads: each workflow's newest run, and no other.

    A workflow's verdict is the one GitHub's required-check status reports
    for it, so a cancelled remnant of a re-run and a FAILED run the re-run
    then cleared are both out whatever they concluded (issue #1249). The
    grouping is by workflow id with the path standing in, never by the
    run's name.
    """
    return [run for run in runs if not superseded(run, runs)]


def missing_required(runs, *, required=REQUIRED_WORKFLOWS):
    """The required workflow names no run the judged set carries, sorted.

    The judged set is computed here rather than handed in, so the answer is
    a property of the run list: a caller cannot reach a different one by
    passing a different set. The filter is idempotent - a run the filter
    kept has no newer sibling left to displace it - which is what lets a
    caller that filters first still get this answer.

    An empty `required` is satisfied by every run list, which is what makes
    the argument a no-op rather than a rule that refuses everything.
    """
    return sorted(required - {run.get('name') for run in judged(runs)})


class GateAbsent:
    """A run set that concluded and never carried the gating workflow.

    The fourth answer, beside True (settled), False (runs still open) and
    None (nothing to judge). Its own type rather than a falsy value, so it
    can never be read as one of the other three by an `is` comparison, and
    it carries the names so a refusal can name the workflow instead of
    falling back on "unknown" - the shape issue #839 was filed about.

    What constructs it, exactly: `missing_required` above answers with the
    missing NAMES, and `watch_all._settled` turns those names into this
    value. Nothing here raises it, and `ci_wait.py` never names it. One
    caller builds it and the other recognises it by `isinstance` - so it is
    named here and imported, not re-declared beside its recogniser.
    """

    def __init__(self, missing):
        self.missing = tuple(missing)

    def __repr__(self):
        return f'gate absent: {", ".join(self.missing)}'
