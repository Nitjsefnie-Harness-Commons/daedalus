#!/usr/bin/env python3
"""The control `tests/test_gitignore_control.py` drives.

THE RULE, in full, with nothing scoped out of it:

    the committed `.gitignore` == derive(sorted(git ls-files))

No base commit, no `git log -1`, no `ls-tree`, no blob id, no scope, no
`fresh`/`stale` reduction, no directory-block clause.

WHY IT COMPOSES, AND THE ONE THING IT DEPENDS ON. Main stays green after
a merge whenever every branch in that merge was green. That is not a
property of the comparison — it is a property of WHERE THIS RUNS, and it
is the whole reason the rule can be this strict:

  * this is a suite, so it runs on every pull request, not after the
    merge. A branch that adds a tracked file and does not regenerate is
    RED on its own head and RED on its own merge ref, before it can
    merge at all;
  * the `suites` job runs this file, `Aggregate workflow checks` is a
    required context under the `main requires green checks` ruleset, and
    base-branch policy holds a merge while it is red — so that red lands
    on the pull request, where a human is already looking. The job is
    `suites`, not `tests`: a reader grepping the workflow for a `tests:`
    job finds `suites:`, and the aggregate above it;
  * therefore a branch cannot contribute a red main, because it cannot
    merge while carrying one.

**A future reader who moves this check off the pull-request path must
know why it was allowed to be this strict.** As a post-merge step on
main, or as a hook that runs only on the default branch, the exact same
comparison turns the "branch added a file and forgot" case into "main is
red and nobody is looking" — the failure this rule's predecessor was
built to survive. Nothing about the rule changes; only the place it runs
does, and that is the whole of the margin.

The `*` deny rule is the one that matters most, and NEITHER this control
nor the generator's `check-ignore` postcondition covers it. With the
rule deleted every path falls back to ignored, and the postcondition
rejects only paths that ARE ignored — with nothing denied nothing is
ignored, so it answers `ok`. This control compares the committed file
against the derivation, and when both come from the same `HEAD` constant
they agree on a file that denies nothing. The rule is pinned instead as a
literal by `tests/test_gitignore_generator.py`'s
`test_the_derivation_always_carries_the_deny_rule`, which shares no state
with the text it checks. Removing `*` from `HEAD` and regenerating turns
that one red AND this suite's `test_a_missing_deny_rule_is_red`, whose
anchor assertion fires because the text it edits is gone — the substantive
red is the generator suite's.

Both sides are enumerated by the SAME call. The tracked set is
`git ls-files`, which reads the INDEX, and that is exactly the call
`scripts/gen_gitignore.py` derives from; take it from a commit instead
and the expectation and the scope are computed by different means than
the design assumes, which agree on a clean checkout and part company the
moment the index and the commit do. The other side is the file in the
working tree, which in CI is the checked-out artifact; a working copy
that was regenerated or hand-edited without being committed is RED here,
not excused, because under this rule the working copy IS the subject.

"""
import difflib
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

ARTIFACT = '.gitignore'
REMEDY = 'python3 scripts/gen_gitignore.py .'
PREFIX = '!/'
DIFF_LINES = 24
NAMED_LINES = 5
Verdict = namedtuple('Verdict', 'status detail scope')


class Refusal(Exception):
    """No comparison was possible; the text names the cause."""


def git_read(root, *args):
    """One git read against `root`, NUL-safe about the paths it names."""
    return subprocess.run(
        ['git', '-C', str(root), *args], capture_output=True, check=True,
        text=True, errors='surrogateescape').stdout


def paths(text):
    return {path for path in text.split('\0') if path}


def normalise(text):
    """Fold CRLF, so a checkout that rewrote the endings is not a diff.

    The blob a checkout produces holds LF whatever platform wrote it and
    Windows hands the same file back as CRLF. The question is which paths
    are named, not which characters end the line. A test feeds this a
    string, because no POSIX checkout can produce the difference.
    """
    return text.replace('\r\n', '\n')


def generator():
    """The shipped generator, loaded so the rendering has one home.

    `derive` is this repository's single definition of the text. The
    control calls it rather than reimplementing it, and the suite that
    pins the rendering literally is `tests/test_gitignore_generator.py`.
    """
    return _util.load(ROOT / 'scripts' / 'gen_gitignore.py',
                      'gen_gitignore_control')


def named_path(line):
    """The path a `!<path>` FILE line names, or None for anything else."""
    if line.startswith(PREFIX) and not line.endswith('/'):
        return line[len(PREFIX):]
    return None


def counts(tracked):
    """The one set this verdict is about, and the number that describes it.

    Nothing checks this sentence: it reaches message text only, and no
    test asserts it, so a constant here would pass the tree. Stated
    rather than claimed as a control, because a claim nothing can fail is
    the defect this change exists to remove.
    """
    return (f'{len(tracked)} tracked path(s) checked, and the derivation '
            'ran over exactly those')


def difference(actual, expected, tracked):
    """What the committed file and the derivation disagree about."""
    lines = actual.split('\n')
    unlisted = [p for p in sorted(tracked) if f'{PREFIX}{p}' not in lines]
    named = {named_path(line) for line in lines}
    known = set(tracked)
    ghosts = sorted(p for p in named if p and p not in known)
    body = list(difflib.unified_diff(
        lines, expected.split('\n'), fromfile='committed',
        tofile='derive(git ls-files)', lineterm='', n=1))[2:]
    report = '\n'.join(body[:DIFF_LINES])
    if len(body) > DIFF_LINES:
        report += f'\n... and {len(body) - DIFF_LINES} more diff lines'
    if unlisted:
        report += ('\ntracked but not named: '
                   + ', '.join(unlisted[:NAMED_LINES]))
    if ghosts:
        report += ('\nnamed but not tracked: '
                   + ', '.join(ghosts[:NAMED_LINES]))
    return f'{report}\nrepair: {REMEDY}'


def read_failure(failure):
    """Name the git read that failed, so a refusal is attributable."""
    detail = getattr(failure, 'stderr', '') or ''
    return (f'a git read failed, so no comparison was made: {failure}'
            + (f'\n{detail.strip()}' if detail.strip() else ''))


def control(root):
    """Run the control against the repository at `root`.

    `root` is resolved first: a temp root is a symlink on macOS and a
    short name on Windows, and an unresolved join never matches.
    """
    root = Path(root).resolve()
    try:
        return _decide(root)
    except Refusal as refusal:
        return Verdict('refused', str(refusal), frozenset())
    except (OSError, subprocess.SubprocessError) as failure:
        return Verdict('refused', read_failure(failure), frozenset())


def _decide(root):
    """The verdict for `root`, or a refusal naming why there is none."""
    tracked = sorted(paths(git_read(root, 'ls-files', '-z')))
    if not tracked:
        raise Refusal(
            'git ls-files returned no tracked path: the tracked set is '
            'unreadable rather than empty, and an empty set would leave '
            'this comparing the committed file against the derivation of '
            'nothing')
    path = Path(root) / ARTIFACT
    summary = counts(tracked)
    if not path.is_file():
        detail = (f'{summary}\nno {ARTIFACT} in the working tree, so '
                  'nothing re-admits the tracked paths\n'
                  f'repair: {REMEDY}')
        return Verdict('red', detail, frozenset(tracked))
    # surrogateescape for the reason scripts/gen_gitignore.py documents
    # twice: a file holding a byte no decoder can read must not kill the
    # run. A raw UnicodeDecodeError here would escape the entry point,
    # which catches only OSError and SubprocessError.
    actual = normalise(path.read_text(encoding='utf-8',
                                      errors='surrogateescape'))
    expected = generator().derive(tracked)
    if actual == expected:
        return Verdict('green', summary, frozenset(tracked))
    return Verdict('red',
                   f'{summary}\n{difference(actual, expected, tracked)}',
                   frozenset(tracked))
