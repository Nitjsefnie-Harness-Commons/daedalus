"""The workflows whose absence is a refusal, and what that absence is.

One waiter reads the run list and has to answer the question of it: was
the workflow that gates the merge dispatched on this commit at all?
`ci_wait.py` refuses with exit 4 when it was not (issue #1217), and a
watcher once kept its hold rather than releasing a batch whose gating
matrix was never created (issue #1223). Two copies of the expectation would
be two mechanisms wearing one name, so the expectation, the predicate, the
set the predicate is asked of and the answer an absent gate gets live here.
The caller reaches what it uses through this module, and `ci_wait` binds
the expectation and the filter and calls the predicate.

They are meant to AGREE, and until issue #1262 they did not: one asked the
question of the set the newest-run-per-workflow filter left, and one of the
set it was handed. The two readings coincide only while every run of a
workflow carries the same `name`, which the producer guarantees today and a
`name:` edit in the workflow's own YAML ends. The filter therefore lives
here and `missing_required` applies it itself, so a caller passing the raw
list and a caller passing the filtered list get one answer; the reading
that survives is the one issue #1249 established for conclusions - a
superseded run's name does not satisfy the gate on its own. The nearest
precedent is #1223, where a hold read a settled green matrix with the gating
workflow silently absent.

It is a module of its own for a third reason: the callers sit near the
500-line production ceiling the size policy enforces, and an expectation
that lives inside a caller is an expectation the other caller can drift
from.

A run satisfies a requirement by its `name`, exactly. `Tests` and `test` are
different workflows, and treating either as the gate would reinstate the
false green this exists to remove. A conclusion is irrelevant to the
predicate: the caller judges conclusions by rules of its own, and a
required workflow that is present and red is a failure, not an absence.
Those rules are the caller's alone. The gate question is asked of the set
below; the wait judges the conclusion question over that set, where a
watcher's hold once judged it over the raw runs - deliberately not shared,
and the reason the filter is applied here.

A PUBLISHED CHECK-RUN is a gate that is not a workflow run, and this
repository has one. The `gate freshness` workflow's own run concludes
`success` on every head whatever it published, because publishing the
verdict IS that run's job: `scripts/ci/gate_freshness.py` writes a check run
of its own through the Checks API onto each pull-request head. The rulesets
read that check, so the run is the publisher and the check is the gate, and
a waiter that reads only runs certifies a head whose gate is red (issue
#1360). The check arrives in a check suite belonging to no workflow run at
all, which is why it is invisible to a run-shaped read and reachable only
through the suites' own `checkRuns`.

A published check is read by NAME, exactly, like a workflow: `Gate
freshness` is a different check and cannot satisfy this one.

The DIRECTION RULE is here too, because it is the same question with a
caller's own facts added: which gates is this invocation held to, and what
does a refusal say about the ones it was not told about. A waiter KNOWS
this repository's gates and is only GUESSING about another's, so a named
gate may only make it STRICTER here and only REPLACE the set there, and a
name it invented is required nowhere at all.

The predicate is shared rather than copied, so a second reader finds one
definition.
"""

from datetime import datetime, timezone

DEFAULT_REPO = 'Nitjsefnie-Harness-Commons/daedalus'
REQUIRED_WORKFLOWS = frozenset({'tests'})
PUBLISHED_CHECKS = frozenset({'gate freshness'})
# What an acceptable conclusion is, for the two predicates here.
# `ci_wait`'s is an alias of this; `gh_client` spells its own, for a
# reason its comment gives, and a control holds the two equal.
ACCEPTABLE = frozenset({'success', 'neutral', 'skipped'})
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


def red_published(checks, *, required=PUBLISHED_CHECKS):
    """The required check runs whose conclusion is not acceptable.

    Unlike `missing_required` this reads a conclusion, because a red
    published verdict is a FAILURE and not an absence - the same reason
    the workflow predicate does not, and the same reason the caller still
    judges its own runs' conclusions: a workflow the caller required and
    this one required together must not answer the same question twice.

    A check that has NOT concluded cannot be red, so both halves are
    asked: a caller's waiting guard sits before this predicate, and a
    running check of the same name beside a red one would otherwise
    answer `waiting` and the red one would never be read.

    The whole check is returned rather than its name, because the caller
    prints its offenders through one loop and a name alone would have to
    be looked up again to find the conclusion and the URL.
    """
    return [check for check in checks
            if check.get('name') in required
            and check.get('status') == 'completed'
            and check.get('conclusion') not in ACCEPTABLE]


def missing_published(checks, *, required=PUBLISHED_CHECKS):
    """The required check names no check run carries, sorted.

    The mirror of `missing_required` over the other shape of gate, and
    asked of the raw list: there is no filter to apply, because a check
    run is its own verdict and the newest one IS it. A publisher PATCHes
    the run it POSTed rather than adding a second one.
    """
    return sorted(required - {check.get('name') for check in checks})


def is_default_repo(repo):
    """Whether `repo` names the repository whose gates these are.

    Case-insensitive over the whole `owner/name`, and asked of the RESOLVED
    value rather than of whether a flag was passed: spelling this
    repository out in full names the same repository. Only the comparison
    is normalised, so a query still goes out as the caller spelled it.
    """
    return repo.lower() == DEFAULT_REPO.lower()


def required_workflows(repo, named):
    """The workflow names an invocation is held to.

    A direction, and deliberately not a symmetric one: a flag may only
    make a waiter STRICTER where it already knows the gate, and may only
    REPLACE a requirement where it was guessing. Here the caller's names
    are UNIONED with `REQUIRED_WORKFLOWS` - which puts the `tests`
    protection out of reach of any argument, and with it the false green of
    issue #1217 - while on another repository they replace a guess. Each
    name is stripped, so `' ci '` is the requirement `ci` rather than a
    name no run carries and no report can spell legibly (issue #1320).
    """
    if not named:
        return REQUIRED_WORKFLOWS
    names = frozenset(name.strip() for name in named)
    if is_default_repo(repo):
        return REQUIRED_WORKFLOWS | names
    return names


def required_published(repo):
    """The published check runs an invocation is held to.

    The same asymmetry, and with no flag to carry it: `gate freshness` is a
    NAME this repository's publisher chose, so it is a fact here and a
    guess everywhere else.
    """
    return PUBLISHED_CHECKS if is_default_repo(repo) else frozenset()


def gate_note(repo, named):
    """The note a missing-gate report carries, or nothing at all.

    Both facts it turns on are the caller's, and only `main` holds them.
    It is empty whenever a gate was named, because a caller who has stated
    one is not asking what is checked by default; and empty on this
    repository under every spelling, because there the answer is not a
    guess. `is_default_repo` answers the repository half for this and for
    `required_workflows`, and the names are rendered from the constant, so
    the note and the set cannot drift apart.
    """
    if named or is_default_repo(repo):
        return ''
    names = ', '.join(sorted(REQUIRED_WORKFLOWS))
    return (f'  only {names} is checked by default; --required NAME states '
            'the workflow that gates another repository')


class GateAbsent:
    """A run set that concluded and never carried the gating workflow.

    The fourth answer, beside True (settled), False (runs still open) and
    None (nothing to judge). Its own type rather than a falsy value, so it
    can never be read as one of the other three by an `is` comparison, and
    it carries the names so a refusal can name the workflow instead of
    falling back on "unknown" - the shape issue #839 was filed about.

    What constructs it, exactly: `missing_required` above answers with the
    missing NAMES, and a caller turned those names into this value. No
    caller in this tree does so any more, and nothing here raises it, so
    the class is carried with no builder; `ci_wait.py` never names it.
    Removing it is a code change and this branch changes comments and
    docstrings here only, so the honest state is recorded rather than
    fixed.
    """

    def __init__(self, missing):
        self.missing = tuple(missing)

    def __repr__(self):
        return f'gate absent: {", ".join(self.missing)}'
