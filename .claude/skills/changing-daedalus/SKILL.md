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
| Auditing outgoing co-author trailers | `git-coauthorship` and `CONTRIBUTING.md` |

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
`max-line-length = 10000`, so pycodestyle will not catch a long line;
`python3 tests/test_line_lengths.py` does, against the recorded baseline.

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

**Restore the way `plant.py` does, never with the VCS.** Run
`python3 .claude/skills/changing-daedalus/plant.py save FILE` before you
revert the fix and `plant.py restore FILE` after. The restore republishes
the stored bytes in one atomic step and exits nonzero on a store it cannot
read, a target it cannot write, a recorded mode it could not apply, or an entry
it could not remove - so a nonzero exit says the restore is not fully complete,
not that the file was left unrestored: the bytes are already published before
the last two of those can fail. It also names the state the stored
copy was captured in - `clean`, `dirty` or `unknown` - and whether the file
already held exactly those bytes, changed them, or could not be compared because
it could not be read, so the check that the restore did something is the tool's
own output rather than a byte count a reader has to interpret.
A `save` that finds an entry from
an earlier plant refuses rather than overwriting it, and when the file has
moved on since that copy it names `plant.py clear FILE` rather than a restore,
because restoring the older bytes over the newer change is the loss this
replaces. A
path-scoped VCS restore - `git checkout -- FILE`, `git restore FILE` - is a
statement about the whole path, not about the plant: it cannot tell the
planted bytes from uncommitted work on the same path, so it hands back HEAD
and that work is gone with nothing in the output to say so. Hence never
`git checkout --` a path you planted into unless that path's work is already
committed, and make **commit before you plant** the standing order so every
restore lands on a committed tree. Prove the restore with a diff of the
**whole tree**, not of the planted path - the whole tree is where the damage
shows - and read a suite that is green the instant after a restore as the
**signal that the restore did nothing**, never as evidence that it worked.

**When the change *is* a guard, green CI proves nothing.** A guard passes on
the tree it was written against by construction. For any change that adds or
alters a guard - an audit, a linter, a CI check, a ratchet - plant the defect
it exists to catch **in a real target**, prove the guard fails, restore, prove
it passes. A synthetic fixture shows what the guard thinks; only a real target
shows whether the guard and the runtime agree. Save and restore that target
with the `plant.py` pair above; a VCS restore there is the same silent loss.

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

**`.github/ci-thresholds.json`'s `module_size_baseline` is never hand-edited
to make room, and a recorded number is never raised.** Growth is not the way
out; shrinking is recorded with
`python3 scripts/ci/size_baseline.py --tighten`. If a change would push a
listed file past its number, relocate the code into a new module. A stale
entry is the one hand edit that is correct:
`python3 scripts/ci/size_baseline.py --tighten` drops an entry whose file is
back under its ceiling, but an entry naming a file that is gone is deleted by
hand. `tests/test_file_sizes.py` gates the policy, pins the script's docstring
to the remedy a refusal prints, pins the whole policy source to the rule
above, and reads this paragraph so the named state owner and tightening
command cannot drift from the implementation.

**The same file's `long_line_baseline` holds the 79-column policy on the same
terms.** It records how many over-limit lines each tracked Python file still
carries; a number is never raised by hand and no entry is ever added by hand.
The remedy for a refusal is to wrap the line, and the shrink is recorded with
`python3 scripts/ci/line_lengths.py --tighten`, which also drops an entry
whose file has no over-limit line left. `tests/test_line_lengths.py` gates
the policy and reads this paragraph the same way.

**`.github/ci-thresholds.json`'s `type_error_baseline` holds the test tree's
type-error count on the same terms.** It records how many type errors each
tracked test module still carries, as measured by `pyright` over
`pyrightconfig.tests.json`;
a number is never raised by hand and no entry is ever added by hand. The
remedy for a refusal is to fix the type error in the named test module, and
the fall is recorded with
`python3 scripts/ci/type_error_baseline.py --tighten`, which also drops an
entry whose file has no type error left. An entry naming a file that is gone
is removed by hand. `tests/test_type_errors.py` gates the policy and reads
this paragraph the same way.

**The same file's `js_coverage_baseline` holds the per-module JavaScript
coverage policy on the same terms.** It records how many uncovered
executable lines each tracked JavaScript file still carries, counted from
the same V8 dumps and the same physical code-line detection
`python3 scripts/ci/js_coverage.py` reports the tree-wide total from; a
recorded number is never raised by hand. The remedy for a refusal is to
cover the uncovered lines in the named file - a total that clears its
floor says nothing about the one module that rotted while its neighbours
improved - and the fall is recorded with
`python3 scripts/ci/js_module_coverage.py --tighten "$NODE_V8_COVERAGE"`,
which also drops an entry whose file is fully covered. An entry naming a
file that is gone is removed by hand.

**`js_coverage_baseline` admits exactly one hand edit: a new module.** The
`--tighten` command iterates what the record already names, so it can
lower a number and drop an entry but never add one; a module the record
does not name is admitted the way the seed itself was - at its measured
uncovered count, in the same reviewed diff that introduces the module,
after which the ordinary ratchet applies to it like any other. The
alternative is to cover its lines, and then it needs no entry at all. Do
not leave a new module unrecorded expecting the next tightening run to
pick it up; it will not. `tests/test_js_module_coverage.py` gates the
policy and reads both paragraphs the same way.

## Git and CI

**Audit co-author trailer values before every push.** Run
`python3 ~/.agent-bundle/scripts/author_stats.py --list origin/main..HEAD` so
the complete outgoing range is visible. The tool checks trailer presence, not
whether the value is standardized, so compare every listed value literally
with `CONTRIBUTING.md`; for GPT-5.6 Sol only
`GPT-5.6 Sol <noreply@openai.com>` is valid, not `gpt-5.6-sol` or
`GPT-5.6-Sol`.

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

**Base movement is checked once, at the final gate; it is not a mid-work
event.** During implementation, draft pushes, CI and review rounds, do not
rebase merely because `main` advanced and do not make `behind == 0` a push
precondition. Otherwise concurrent branches repeatedly rebase for one
another's merges, invalidating evidence without changing the outcome.

At the final gate before taking the pull request out of draft, fetch `main` and
inspect the commits added since the branch diverged. Rebase only when those
commits are relevant: they change behavior the branch also changes or depends
on, a shared helper or import surface it uses, or a build, test, scanner or CI
path that verifies it. A new commit or an overlapping filename is a reason to
inspect, not by itself a reason to rebase. Record the comparison. If no added
commit can affect the branch or its verification, finish the gate without a
rebase.

If a relevant change exists, create the recovery anchor, rebase, and re-verify
against the rebased SHA; all pre-rebase test, review and CI evidence is stale.
Establish which rebase outcome occurred: if `git rev-list --count
HEAD..<saved-old-head>` is 0, history was preserved and that containment is the
proof; otherwise compare the old and new series with `git range-diff` and
account for every changed or unmatched commit. If `main` advances again while
the final gate runs, inspect only the newly added commits by the same relevance
test; do not restart the gate for an irrelevant change.

**A check that gates an action goes in its own invocation, and you read it
before acting.** Printing a precondition beside the command it guards gates
nothing. Watch the shape of compound checks generally - `git status --short &&
echo "(clean)"` prints `(clean)` next to a list of modified files, because `&&`
fires on exit status and `git status` succeeds either way.

**Re-run a failing CI leg on the unchanged commit before calling it a flake.**
"The branch touches none of those files", "the suite calls the changed helper
zero times" and "it passes locally" are all arguments, none of them evidence.
Re-running the same SHA and watching it pass is what makes intermittency a
fact - and it earns its own issue. "Probably the known flake" is how a real
regression gets merged.

**A verdict names the SHA it was measured on and binds only that SHA.** A gate
run before your last edit has not run on your last edit, whatever a report says
about it: state the SHA each verdict in a report was taken against, and re-run
the gates a task names after that task's last edit. A check that is in the
batch only sometimes is a sample, not a gate - its agreeing with the last run
is not evidence, and a suite that happened to be in that batch is verified no
more than one that was not.

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

**The slowest check on a head gates like any other check.** Its conclusion
is waited on and read, so any "all checks concluded" condition must include
it: one that excludes the longest-running check fires while that check is
still running, and reads as a settled head.

## Before and during a branch

**Check the already-open pull requests before picking work**, so you do not
collide with a branch that already has those files open.

**Open the pull request as a draft as soon as you have a commit, and push as
you go** rather than batching. CI here covers twelve legs across three
platforms, and the draft period exists so it catches problems early; a red
draft head is that working, not damage.

## Watching a pull request

A watcher used to be shipped here: `watch_all.py`, with `ci_watch.py` and
`pr_comment_watch.py` as its children. None of the three is repository content
any more - a checkout carries no copy - and the prose describing how the
aggregator batched, held and condensed its children's output is cut with it.
What follows is the tooling that is still shipped.

Waiting on one commit's CI is `ci_wait.py`, beside this file:

```
python3 -u .claude/skills/changing-daedalus/ci_wait.py <sha> \
    [--repo OWNER/REPO] [--required NAME]
```

Its exit code is the verdict, so a caller never has to read the loop: 0 every
run on the SHA concluded `success`, `neutral` or `skipped` **and the
`tests` matrix has a run on that SHA** **and the `gate freshness` check
run has concluded acceptably**; 1 every run concluded and one concluded
otherwise, or a required published check run did, offenders - runs and
checks alike - named with URLs; 2 the `--timeout` bound expired first - with
named runs still open, with no run ever appearing, or with every run
concluded and a required workflow or check still absent; 3 the
invocation was rejected or a query failed - loud and at
once, never retried behind a message that reads like waiting; 4 every run
concluded acceptably and either none of them is a `tests` run or no
`gate freshness` check run is on the SHA, so this head is not
certified - no merge is claimed, because this is reached with a pull
request, without one, and on a branch of its own. A rate-limit refusal is
the one exception to exit 3: it is a known wait, so it pauses until the
reset and polls again, bounded by the same `--timeout` - and a bound
reached inside such a pause is still exit 2, never 3. The refusal is
recognised from the EVIDENCE the answer carries and from whether it
delivered, never from the status or the exit code on their own, because
GitHub really does answer a throttled query with a 200 and an exit 1
(issue 1338).

**Exit 4 exists because "every run that happened to exist passed" is not
"every run that should exist did"** (issue #1217, PR #1122 head
`cb67badf`). That head conflicts with its base, so the merge ref cannot be
built, no `pull_request` workflow is dispatched, and the twelve-cell `tests`
matrix has no run at all. `gate freshness` and CodeQL do run and both
conclude `success`; the tool reported `all 2 run(s) acceptable` and exited
0, and every seat on this fleet reads that exit code as its green read. The
same false green appears on a mergeable head in the first minutes of a
push, while the short workflows have concluded and the matrix is still being
created. So an absent required workflow is `REQUIRED_WORKFLOWS`
(`ci_wait.py`'s constant, which is also its default: a caller who names
nothing cannot switch the expectation off, and the reader this tool exists
to protect is one who never says otherwise), and it is
answered rather than waited on forever: a conflicting pull request for the
head refuses at once, and anything else is given `--grace` seconds
(default 300) from the first observation before it refuses, naming the
missing workflow AND the missing check, the grace and the runs that do
exist. The pull-request
lookup is a disambiguation, not this tool's subject, so its failure is said
once on stderr and the wait continues to the grace - a slower correct
answer, never a green. A present-but-red `tests` run is exit 1, not exit 4:
the conclusion is judged before the set is, and so is a present-but-red
`gate freshness` check - which is why a red check outranks exit 4 even on a
head whose `tests` run is also absent.

**A PUBLISHED CHECK-RUN is a gate that is not a workflow run, and this
repository has one** (issue #1360). The `gate freshness` workflow's own run
concludes `success` on every head, red verdict or not, because publishing
that verdict is the run's job: `scripts/ci/gate_freshness.py` writes a check
run of its own through the Checks API onto each open pull-request head, and
the rulesets read THAT. So a waiter reading only runs reads the publisher
and misses the gate - head `8ddfec21f32d484657e7ebc56c46a1b395670ca9`
carried seven green runs and a `gate freshness` check that concluded
FAILURE, and the tool exited 0. The run is the publisher, the check is the
gate, and only the second is read by the rulesets.

**The predicate for both kinds of gate is `ci_gate.py`'s, and so is the
direction rule that says which of them an invocation is held to** - the
required-workflow set, the published-check set, and the note a refusal
carries all live beside each other so they cannot drift apart, and
`ci_wait.py` reaches each through that module rather than keeping a copy.
The predicate is shared anyway so a second reader has one place to reach
for.

`--required NAME` states a workflow gate, and it does not reach the
published one: `gate freshness` is a name THIS repository's publisher
chose, so it is required here where no argument can switch it off, and
required nowhere else, where that name on a stranger's repository is a
guess - the same asymmetry the workflows follow, and for the same reason.

**`--required NAME` states a gate, and what that may DO depends on which
repository this is** (issue #1318). It is repeatable and all-of:
`--required ci --required lint` needs both, the same reading `verdict` has
always given. Absent, the expectation is `REQUIRED_WORKFLOWS` exactly as
before - nothing about the exit-4 refusal above moves, and a caller who
names no gate still cannot switch it off.

What it exists for is `--repo`: `tests` is THIS repository's gate and is not
any other one's, so on a repository whose gating workflow is named
something else an all-green head used to exit 4 with a line reading as "the
gate never started" when in fact the gate was never what this tool looked
for. A caller who names another repository and no gate is told that on the
refusal itself - which name is the only one checked, and that `--required`
is how to state a different one - rather than left to read a missing
workflow as a head nobody verified.

The direction is not symmetric, and the asymmetry IS the rule. This tool
knows daedalus's gate and is only guessing about another repository's, so
here the names are ADDED to the required set: naming a workflow can only
make the tool stricter, and `tests` cannot be dropped by argument - which
is the #1217 false green, made unreachable through the flag that fixes its
other face. On another repository they REPLACE the set, because there the
default was only ever a guess. So `--required ci` on this repository means
"ci AS WELL AS tests", not "ci instead of tests". The note is empty on this
repository under every spelling of its name, and empty whenever `--required`
was given at all; naming the gate this tool already knows leaves the output
byte for byte what it was.

**A workflow's verdict is the run GitHub's required-check status reports for
it, which is that workflow's NEWEST run on the SHA.** Every older run of the
same workflow is out of the judged set whatever it concluded (issue #1249).
A cancelled remnant of a re-run has always gated nothing; a FAILED one is
the intermittent failure the re-run then cleared, and the same status
supersedes it. A close/reopen is the shape that shows it - the head gets a
second run against a new merge ref while the first run's failure lingers on
the same SHA - and on such a head the older failure used to outvote the
newer green. A run with NO newer sibling is judged as it stands, so a
deliberate cancel and an unretried failure both still fail, and nothing is
judged by a run name: the grouping is by workflow id, the path standing in
when the id is absent, with "newer" by `run_started_at`, `created_at`
standing in, ties by numeric id.

**Discarding a failure is what that rule costs, so the discard is never
silent.** The exit-0 line counts the runs the filter dropped AND prints
each one, with its workflow, its run id, its conclusion and its URL - the
same fields the exit-1 offender line prints. A green handed over without
the failure it discarded is a green nobody can audit, and an intermittent
failure on the very merge ref that cleared it is exactly the thing a caller
reading only the exit code cannot see.

It PINS the SHA it is given instead of re-resolving the branch head each
poll: a push landing mid-wait must not turn the answer into one about a commit
the caller never asked about. Zero runs on the SHA is waiting, not success. Run
`--once` before a long wait - it prints `state: incomplete` for a head whose
gate is not dispatched yet, and still exits 0, because a trial call is not a
verdict.

**A pull request has three comment surfaces, and a review is not a comment:**
`pulls/<N>/reviews`, `pulls/<N>/comments` (inline) and `issues/<N>/comments`
(the conversation). Read or unread is delivery bookkeeping, not evidence that a
thread has been dealt with.

This is not redundant with the local suites, which cover one platform: a
`.gitattributes` regression here passed every local suite and all eight Linux
and macOS cells, then failed all four Windows cells because `bash` there
resolves to the WSL launcher.

## Issues, claims and labels

**Claim every issue you work**, including one you filed yourself and are
closing from your own branch. `.github/workflows/claim.yml` assigns you when
you comment, and the comment body must be **exactly** the claim command after
trimming - `/claim`, `/unclaim` or `/release`, each optionally followed by the
issue number, `#`-prefix optional, which must match the issue the comment is
posted on; surrounding prose makes it a sentence, and a sentence is not a
command. Open, unassigned issues only, never a pull request.
`/unclaim` and `/release` are the same command under two names, under the same
exact-match rule.

**Do not read the claim back in the next breath.** The workflow runs on
`issue_comment`, so an immediate read returns empty `assignees` for a claim
that is about to succeed. That empty read is a race, not evidence - never
re-post on the strength of it. Check on your next natural touch of the issue.
A declined claim - a closed issue, a pull request, an inexact body carrying a
command word, or a mismatched number - answers on the issue and fails the run.
Reaching the cap for your role - which limits how many open issues you are
assigned at once - is answered too, with the role, the cap and your count,
though that run still succeeds. A body with no command word is skipped by the
trigger filter before the action runs, and a bot's comment is refused without
answering - the two silences that remain.

**A claim lapses on its assignment age, not on your silence.** The holder's
clock runs from when they were assigned, so a claim can expire while its holder
is still working the issue every day. It expires only once that assignment is
more than seven days old, judged inside the run that answers a comment rather
than on a schedule, and only an assignment the action made itself can expire -
one a maintainer added by hand never does. Your own `/claim` on an issue you
still hold tells you so and changes nothing, whatever its age; it is another
commenter's `/claim` that takes an expired one over, and someone with write
access may release it instead.

**Declare an absorbed issue in the pull request body the moment it is
absorbed.** A branch that takes on work filed under a second issue - a finding
parked by its own review, or an issue another session would otherwise start -
adds that issue to its body, closing keyword included, in the same pass that
decides the absorption. The claim workflow records who owns the issue a branch
was opened for; it says nothing about what the branch grew to cover, so an
absorption visible nowhere but the owning session's context leaves the issue
reading as free work, and the collision surfaces only as two branches carrying
the same fix.

**Release an issue your merge did not finish**, and note what remains in the
same comment. The ordering is load-bearing: the claim action refuses every
command on a closed issue, so an issue a merge keyword closed can never be
unassigned again. A pull request that does not finish its issue must therefore
not carry a closing keyword for it.

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

**Every defect you discover while working a pull request reaches the issue
tracker** - a flaky test, a bug in code the branch does not touch, a guard that
passes what it should refuse. This is not optional and not a judgement call
about importance: a finding held in a report, a review comment or a commit
message is a finding nobody triages. Search open and closed issues first.

- **No issue tracks it: file it as its own issue rather than folding it in.**
  Confirm it yourself first: a report is a lead, not a source, so reproduce it
  with your own command against the branch the issue will name and put *that*
  output in the body. File while the reproduction is still in front of you.
  Label it as you file it, `perceived difficulty` included - filing is the
  only moment that number can honestly be set.
- **An issue already tracks it: comment there, and only after reading the
  whole thread** - the body and every comment, to the end. A thread records
  what has already been tried, reverted, ruled out or re-estimated, and a
  sighting posted without that reading repeats a comment that is already
  there or re-proposes an attempt the thread already records as failed. The
  comment carries what your sighting adds: the branch and SHA, the exact
  output, and what it changes about the thread's current understanding.
  Never open a second issue for the same defect.
- **Disclose it in the pull request body**, under Bugs Discovered, whichever
  route it took: the issue you filed, or the existing issue you commented on.
  A reviewer meets the branch's side effects in that section or not at all.

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
