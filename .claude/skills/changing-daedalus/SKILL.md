---
name: changing-daedalus
description: Use when changing anything in this repository - before running its suites, adding a tracked file, growing a size-baselined module, writing a regression test, judging a CI failure, or filing, claiming and labelling an issue.
---

# Changing Daedalus

What this repository does differently, and what it has already been burned by.
Everything general is left to the skill that owns it: follow the pointers
instead of expecting a summary, because a summary here is what stops the skill
being read.

| Situation | Where it is governed |
|---|---|
| Implementing any feature or fix | `superpowers:test-driven-development` |
| Multi-task implementation | Required SDD workflow below |
| Subagent dispatch | `agent-routing` |
| A bug, a test failure, or behaviour you cannot explain | `superpowers:systematic-debugging` |
| About to say something works, passes, or is done | `superpowers:verification-before-completion` |
| Writing a pull request or issue body | `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/issue.md` |
| Architecture, endpoints, exact field names | `AGENTS.md` |
| Piping a runner, a trailing status echo, an `|| true` fallback | `bash-harness-antipatterns` |
| Contribution conventions and the guard-per-layer rule | `CONTRIBUTING.md` |

For every feature or fix, regardless of size:

- **The lead dispatches the work; it never implements it in its own
  context.** Implementation goes to an `implementer` subagent and review to
  independent reviewers, because a lead that implements is biased toward
  its own code and will not catch its own mistakes. The lead verifies
  claims and orchestrates; it does not write the change.

For an implementation plan with independent tasks in the current session:

- **REQUIRED SUB-SKILL:** Use `superpowers:subagent-driven-development`.
- **REQUIRED PATCH:** Read `subagent-driven-development-patch` whenever that
  skill is loaded or run; it overrides the reviewer-model guidance and carries
  the lead-orchestrates rule.
- **REQUIRED ROUTING:** Read `agent-routing` before every subagent dispatch.

## Running the suites

**Run them bare** - `bash-harness-antipatterns` owns why, and it covers the
whole family (piping a runner, a trailing status echo, an `|| true` evidence
fallback). What it costs here specifically: a filtered run of a blank-line
truncation check dropped half of a two-line failure message and read as a
serious regression that did not exist, and roughly ten minutes went on a false
alarm manufactured by the filter.

**Do not run `run_tests.py`** - invoke the suites directly.

**Run `pylint` as a single invocation over all files.** Split it and the
cross-file duplicate check is silently skipped.

**Check added lines against 79 characters yourself.** `setup.cfg` sets
`max-line-length = 10000`, so pycodestyle will not catch a long line.

## Choosing which suites to run

Choose by what the change *touches*, not by what it is *about*. Six suites were
once chosen for a change because they "touch striping"; all six passed and all
twelve CI legs then failed on a suite that loads `server.py` by path.

- Changed a module's import surface or a shared helper? Enumerate every suite
  that imports the changed thing and run all of them.
- **Import-based enumeration cannot see a suite that scans rather than
  imports**, so it never proposes one and the gap is invisible.
  `tests/test_release_scan.py` reads every tracked file and refuses a
  non-allowlisted host or a machine-specific path; `tests/test_file_sizes.py`
  and `tests/test_repo_layout.py` read the tree the same way. Add every scanner
  to the list whenever a change adds or edits a **string** in a tracked file -
  a different trigger from changing an import surface. A fake hostname inside a
  new test has failed all twelve legs at once while every suite that imports
  the changed modules passed locally.

## Tests that actually pin something

`superpowers:test-driven-development` owns the RED-GREEN-REFACTOR cycle. These
are the parts it does not cover.

**A regression test you have not watched fail is not a regression test.**
`superpowers:verification-before-completion` owns the cycle - write, pass,
revert the fix, watch it fail, restore. What this repository adds is that
reverting a *patch* is not enough: reintroduce the defect into the real code
and record the failure, because two
ordinary fixes here shipped tests that passed against unfixed code - one
asserted on an unlink race microseconds wide, another pinned an extracted
helper thoroughly while passing with `server.py` fully reverted. Reading a test
reveals neither.

**When the change *is* a guard, green CI proves nothing.** A guard passes on
the tree it was written against by construction. For any change that adds or
alters a guard - an audit, a linter, a CI check, a ratchet - plant the defect
it exists to catch **in a real target**, prove the guard fails, restore, prove
it passes. A synthetic fixture shows what the guard thinks; only a real target
shows whether the guard and the runtime agree.

**Never assert a wall-clock margin.** It passes because the machine was fast
enough, never because the code is right, so it fails correct code on a loaded
runner - the same intermittency you were probably sent to remove, one level
down, in the test written to prevent it. Widening the bound makes the failure
rarer and harder to attribute.

- Prove a timing boundary by asserting what the code **did**. Where the
  boundary is an interaction - a join, a wait, a retry - stand in for the thing
  being waited on, record how it was called, and assert on that record.
- Where you genuinely must wait, wait **without** a bound. An unbounded wait
  cannot fail early on a slow machine, and the only thing it does not survive
  is a real deadlock, better surfaced as a hung job than hidden as an
  intermittent assertion.
- **A loop of N unsynchronised attempts is not a proof.** A thirty-iteration
  subprocess loop here passed 15/15 against a mutation that removed the very
  wait it existed to pin, while the same mutation lost real output in 2 of 100
  runs. Raising the iteration count is the wrong lever.
- The exception is a margin running the other way: an assertion that something
  did **not** happen inside an interval far shorter than it could possibly
  take. Headroom there weakens the test. Say which kind you have before you
  touch it.

## Repository mechanics

**`.gitignore` denies by default** and names back exactly what ships, so a new
file is invisible until you name it - see `CONTRIBUTING.md`. Regenerate with
`scripts/gen_gitignore.py`, and:

- **Never run it while unmerged paths exist.** `git ls-files` reports a
  conflicted path once per stage, so a mid-rebase run triplicates every
  conflicted entry: 135 tracked files with 2 conflicted produced
  `ok - 139 tracked files named`. Resolve, finish the rebase, then regenerate.
- **Verify by regenerating on a clean tree and asserting no diff**
  (`git diff --exit-code -- .gitignore`). That catches triplication, a missing
  entry and ordering drift together. Do not verify by exit status - the
  generator reported `ok - 140 tracked files named, none ignored` while the
  file was wrong, and both halves of that "ok" were true. Duplicate `!` entries
  are semantically inert, which is why they go unnoticed and why they break the
  one number that would otherwise reveal a genuinely missing entry.

**Nothing about a particular machine may reach the tree.**
`tests/test_release_scan.py` reads every tracked file and refuses a
non-allowlisted host, an absolute path under a private home or web root, or a
deployment URL - in a docstring and a usage example as readily as in code.
The scanner's own patterns are the specification; do not reproduce them
anywhere else in the tree, because a rule that quotes them trips them. It also refuses an empty tracked-file enumeration, because
a scan that came back empty is broken rather than clean.

**`scripts/ci/size_baseline.py` is not hand-edited, and a recorded number is
not raised.** Growth is not the way out; shrinking is, recorded with
`--tighten`. If a change would push a listed file past its number, relocate the
code into a new module. `tests/test_file_sizes.py` gates the same thing.
The script's own docstring says growth is allowed by editing the number in the
same commit - that is the older policy and reading it will teach you the wrong
one.

## Git and CI

**Count a branch's commits before asserting the count anywhere.** Run
`git rev-list --count <base>..HEAD`; never state a range from reading the tail
of a log. A wrong count does not merely mislead - it invites someone to make it
true. A brief here said "the six commits the branch owns" when the branch owned
23, and a `rebase (fixup)` collapsed 18 reviewed commits into one.

**Create a recovery anchor before any history rewrite** - tag the current head
before a rebase, reset or amend, and delete the tag only once the push is
confirmed. `git reset --hard <tag>` then restores the exact starting point.
Reflog works only until a second destructive operation lands on top of a wrong
one.

**When redoing work whose output was already verified, pin the tree rather
than the process.** If a correct result exists in a bad shape, tag it and make
tree equality the acceptance test: `git diff <verified-tag> HEAD` must be
empty. That proves a rewritten history reaches exactly the content that already
passed the gates, and it is one command instead of re-reading every conflict
resolution - 23 resolutions did not have to be re-reviewed here, only the tree
compared.

**When the base moves, rebase and re-verify against the rebased SHA.** All
pre-rebase test and CI evidence is stale. Re-fetch immediately before pushing
rather than once at the start: `main` has advanced 26 commits and then 4 more
inside a single session here. A rebase has two outcomes and you must establish
which one you are in - if `git rev-list --count HEAD..<saved-old-head>` is 0,
history was preserved and that containment is the proof; if it is not, commits
were replayed, so compare the old and new series with `git range-diff` and
account for every changed or unmatched commit.

**A check that gates an action goes in its own invocation, and you read it
before acting.** Printing a precondition beside the command it guards gates
nothing: a push whose "behind == 0" precondition printed `behind: 4` in the
same compound command went out anyway. Watch the shape of compound checks
generally - `git status --short && echo "(clean)"` prints `(clean)` next to a
list of modified files, because `&&` fires on exit status and `git status`
succeeds either way.

**Re-run a failing CI leg on the unchanged commit before calling it a flake.**
"The branch touches none of those files", "the suite calls the changed helper
zero times" and "it passes locally" are all arguments, none of them evidence.
Re-running the same SHA and watching it pass is what makes intermittency a
fact - and it earns its own issue. "Probably the known flake" is how a real
regression gets merged.

**Read the diff-coverage comment line by line.** It names the added lines no
test executed, and it is informational precisely so that nobody can point at a
threshold and stop thinking. For each line named, write down why its absence of
coverage is not a defect this change introduces - a guard a healthy run does
not reach, a path exercised only by a suite whose fabricated tree is
deliberately unmeasured, a file that runs as the measurement harness rather
than under it. If no such argument exists, that line is untested code the
change is adding, and it gets a test. Put the argument in the pull request
body, where a reviewer meets the same comment.

**Read the live Checks API before the first push, not after the first red.**
A shape pin over a workflow file cannot see where GitHub attaches check runs
or what it names them, and both differ by event and by job state: here a
`pull_request` run attaches its checks to the pull request's head commit,
never to the merge commit `github.sha` names - which on this repository
carries none at all - and a skipped matrix job creates one check run named
after the job's `name:` verbatim, template expression included. Query
`commits/<sha>/check-runs` for both the head and the merge commit and read
the names back before building anything that waits on or reads those names;
the expensive teacher is a wait that burns its whole bound on a commit
nothing ever checked.

**The `speed` suite may be ignored**, and issue 148 against it stays
deferred - do not fix it. It also outlasts every other check on a
head, so any "all checks concluded" condition must exclude it or it never
fires.

## Before and during a branch

**Check the already-open pull requests before picking work**, so you do not
collide with a branch that already has those files open.

**Open the pull request as a draft as soon as you have a commit, and push as
you go** rather than batching. CI here covers twelve legs across three
platforms, and the draft period exists so it catches problems early; a red
draft head is that working, not damage.

## Watching a pull request

This skill ships the tooling: `watch_all.py`, beside this file, with
`ci_watch.py` and `pr_comment_watch.py` as its children.

```
python3 -u .claude/skills/changing-daedalus/watch_all.py <pr-number> <branch>
```

**Arm that one aggregator, never a backgrounded shell loop and never the two
children separately.** Pushing early only buys something if the verdict is
read, and the feedback arrives while you are working on something else. A
shell `while true` poll never exits, so it never delivers anything.

**Run it with `--once` before arming it, every time, and trial it against an
open pull request with a live matrix.** A zero from a broken command and a
zero from an empty surface are the same zero. A merged pull request is the
trap that looks like the obvious target: its comment surface answers normally
while the CI child dies with `Branch not found (HTTP 404)`, because the branch
was deleted at merge - and that half-failure reads as a successful trial of
both. An earlier hand-written watcher here used `gh api --slurp`, which this
`gh` build does not support; every fetch failed to stderr, where a watcher
keeps it silent, and it would have sat quiet forever.

What the aggregator does, and why each part is load-bearing:

- **It batches** until neither child has emitted for a minute. A twelve-cell
  matrix finishing over two minutes is one thing happening, not twelve, and a
  watcher turns every line into its own interruption. `ci_watch.py` runs with
  `--debounce 0` underneath so the batching happens once rather than twice.
- **A success-only batch is held longer**, because a filling matrix goes quiet
  between cells and every partial tally is superseded by the next. A batch
  holding nothing but settled, actionless conclusions waits until something
  worth reading lands or until every check except `speed` has concluded. An
  unanswerable check-runs query keeps it holding rather than flushing: a failed
  query must never look like a settled matrix.
- **The hold is bounded** by `--max-hold` (default 600s). A push supersedes the
  SHA a batch names, and that SHA's runs may then never all reach `completed` -
  so without the cap a batch held across a force-push waits forever on a matrix
  nobody will finish, which is silence indistinguishable from a clean run. A
  cap rather than head-movement detection, because it also covers a deleted
  branch and a query that starts failing permanently.
- **It condenses**, because a watcher truncates a long event and a settled
  matrix runs past 70 lines - which is how a failure hides. Every non-success
  conclusion is named in full with its URL, successes and superseded runs
  collapse to a tally, comment bodies clip. Nothing is lost: each batch is
  appended untruncated to a log the emission names, and that log is keyed per
  pull request and branch rather than one fixed filename, so concurrent runs
  cannot interleave inside a single batch.

Both children fetch with `Cache-Control: no-cache` and `--paginate`, never
consult read/unread state, announce items that already existed when armed, keep
stderr off the event channel, and escalate consecutive poll failures to it -
because a watcher that has gone blind must not look like a quiet pull request.
CI announces success and failure alike, and re-resolves the branch head every
poll since a push moves it.

**A pull request has three comment surfaces, and a review is not a comment:**
`pulls/<N>/reviews`, `pulls/<N>/comments` (inline) and `issues/<N>/comments`
(the conversation). Read or unread is delivery bookkeeping, not evidence that a
thread has been dealt with.

**Never wait on `speed` to conclude** - see below.

This is not redundant with the local suites, which cover one platform: a
`.gitattributes` regression here passed every local suite and all eight Linux
and macOS cells, then failed all four Windows cells because `bash` there
resolves to the WSL launcher.

## Issues, claims and labels

**Claim every issue you work**, including one you filed yourself and are
closing from your own branch. `.github/workflows/claim.yml` assigns you when
you comment, and the comment body must be **exactly** the claim command after
trimming - surrounding prose makes it a sentence and it is ignored. Open,
unassigned issues only, never a pull request. `/unclaim` and `/release` are the
same command under two names, under the same exact-match rule.

**Do not read the claim back in the next breath.** The workflow runs on
`issue_comment`, so an immediate read returns empty `assignees` for a claim
that is about to succeed. That empty read is a race, not evidence - never
re-post on the strength of it. Check on your next natural touch of the issue;
the check still matters, because the workflow declines silently for a closed
issue, a pull request, a bot comment, or an inexact body.

**Release an issue your merge did not finish**, and note what remains in the
same comment. The ordering is load-bearing: `claim.yml` gates unclaim on the
issue being open, so an issue a merge keyword closed can never be unassigned
again. A pull request that does not finish its issue must therefore not carry a
closing keyword for it.

**Read the whole comment thread before designing anything.** The body is the
filing; the thread is what happened since. One issue here proposes claiming
queue files by rename, and its single comment records that exactly that
shipped, passed every Linux and macOS leg, failed `windows-latest` on 3.11,
3.12 and 3.13 with the symptom it was meant to remove, and was reverted.
Designing from the body alone reimplements the reverted attempt.

**Read every label and work out why each one is there.** Difficulty is the
label people reach for and the least informative of them - it is a filing-time
guess, while the rest records what the issue is and what has already happened
to it. An `area:` label says which surface it touches, and two of them say it
straddles a boundary. `blocked`, `wontfix`, `duplicate` or `question` say the
work is not yours to start. An `actual difficulty` already present says
somebody has worked it and formed a view - go read what they found.
`perceived difficulty: 2` beside `actual difficulty: 8` says the filing badly
underestimated it.

**The two difficulty families are set at different times.** `perceived
difficulty: N` is the filing-time estimate and is left as filed. `actual
difficulty: N` is set and adjusted as the work proceeds, with the rationale in
a comment. The gap between them is the signal, which only exists if both ends
are recorded - so **every issue you close carries an `actual difficulty: N`
before it closes.** An issue closed by a merge keyword closes without you
touching it, so apply the label *before* the merge; afterwards nobody reopens a
closed issue to add a number.

**A flaky test outranks everything else.** Intermittency is not one issue's
problem: a leg that fails at random makes CI unable to answer the question
every other pull request is asking it. Flaky issues carry the `flaky test`
label, so the candidate set is a query rather than a reading exercise. The
label means a non-deterministic failure - passed on re-run, one leg only, not
reliably reproducible; a deterministic platform failure or a race with repro
steps is not one, and mislabelling either way makes the query useless. If it
cannot be made deterministic, quarantining it with a written reason and a
tracking issue restores CI's ability to answer, which is the actual goal.

## Filing a second defect

**File it as its own issue rather than folding it in** - or comment on the
existing issue if one already tracks it. Confirm it yourself first: a report is
a lead, not a source, so reproduce it with your own command against the branch
the issue will name and put *that* output in the body. File while the
reproduction is still in front of you; a finding held to the end of the work
lives in a report, and nobody triages a report. Label it as you file it,
`perceived difficulty` included - filing is the only moment that number can
honestly be set.

**Whose defect it is comes down to who introduced it, not where it lives.** A
defect in code the branch adds is the branch's debt and gets fixed there,
however tempting a follow-up is; merging a new API already known to violate its
own contract is how the contract stops meaning anything.

**That test has two limbs, and the symptom alone is the wrong one.** A defect
is pre-existing only when the symptom reproduces on the base **and** the code
responsible for it is unchanged there. Where the observable effect is identical
on the base but its cause is code the branch **adds**, it is the branch's. Seen
here: a change fixing a suppressed write error added a marker whose own write
failure it suppressed the same way, so the observable bypass looked unchanged
on the base while the `except OSError: pass` producing it sat inside the fix
under review. A fix that reintroduces the defect it closes, one level down, is
the most common shape this project sees, and a symptom-only test is blind to
exactly that shape.

**A defect some other gate happens to reject is still a defect.** That another
checker refuses the input is a mitigation, not a licence for the code under
review to return a wrong answer. Classify by what the code itself does, then
record the mitigation as context for severity - never as the reason to drop it.

## Working in the open

**Never write `#` followed by digits for anything that is not an issue or
pull-request number.** GitHub autolinks it. A code-scanning alert is not an
issue: `#82` resolves to an unrelated closed issue about extension privileges.
Write `alert 82`. This binds commit messages as well as bodies, and in a commit
message the fix costs a history rewrite.

**Never put a closing keyword inside a fenced code block.** GitHub excludes
fenced blocks from reference parsing, so a fenced `Fixes #174` closes nothing
and reports no error. Write it as plain body text, then verify it registered:

```
gh api graphql -f query='{repository(owner:"<owner>",name:"<repo>"){
  pullRequest(number:<N>){closingIssuesReferences(first:10){nodes{number}}}}}'
```

Read that as a **count** and re-read before believing a low one - the linkage
is recomputed asynchronously after a body edit, so a query run immediately
after can return one node for a body carrying two keywords and both a moment
later. Decide on `totalCount` against the number of issues you meant to close.
Adjacent keyword lines are not a problem: a body carrying two `Fixes` lines
consecutively with no blank line between them registers both.

**Never interpolate prose into a `gh api -f` value.** Pass bodies as
`-F field=@<file>`, and anything containing quotes, apostrophes or newlines via
`--input <json>` built by a JSON serializer. Shell quoting mangles it silently:
an issue here was filed reading *"a pull request own added lines"* because an
apostrophe could not survive `-f title='...'`.

**A pull request body is not a battle log.** It describes what the change is
and why, as it stands - not how it got there. A body that narrates the rounds,
recounts what each reviewer found, or contrasts the current design against
revisions nobody will ever see is written for the people who lived the branch,
and they are the one audience that does not need it. Where the history is
genuinely load-bearing, it is load-bearing as a *property* of the change, so
state it as one: not "a review found four bypasses and round five closed them",
but the rule the design now enforces and why it has to.

**Do a conciseness pass over the branch's comments before merging.** Cut what
restates the code beside it. `AGENTS.md` sets the target and names the
measurement. It runs last because each fix round explains itself in place and
nobody re-reads the accumulation until the branch is being merged; it touches
comments only, so it is not a reason to re-open review.
