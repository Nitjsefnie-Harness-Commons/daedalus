#!/usr/bin/env python3
"""The commit a tracked artifact was last written by, and what it held.

WHAT THIS RESOLVES. A control that keeps a GENERATED artifact in the
tree and checks it against what the tree derives needs to know which
tree the artifact was generated over. That fact is not recorded in the
artifact, and must not be: an artifact that carries its own scope is a
scope the artifact can widen, and a control whose scope the thing it
scopes controls is switched off by a five-line edit. So it is read from
git instead.

    base     the commit that last wrote <artifact>
    covered  the paths present in that commit's tree

<artifact> is a repository-relative path and <root> the checkout to ask,
so the caller controls both. Nothing here knows what an artifact is and
nothing here knows any particular policy: a caller passes a path and
reads the answer back. That is deliberate, because the shape is wanted
by more than one artifact and a second copy would drift from the first.

THE REBASE MERGE IS WHY THE COMMIT MATTERS. A repository that lands by
rebase rewrites the last writer on main: the commit that wrote the
artifact there is not the commit a branch pushed, and a base resolved
from the branch would be a commit that is not in the history the check
runs against. `last_writer` therefore reads the history as the checkout
has it, which is the only history the check can be honest about.

WHAT IT REFUSES. A shallow checkout cannot name the commit that last
wrote anything. Every answer here would then be a guess wearing the
shape of a fact, and the guess is always the permissive one, so the
refusal is the whole point: a shallow repository raises, naming the job
that has to deepen its checkout and the line that fixes it. It is a
``ValueError`` so a caller already refusing malformed input reports it
as one line, and it RAISES rather than returning an empty answer --
an empty answer reads as "nothing is covered", which is a pass, and a
pass that is really an unanswerable question is worse than a red.

THE MARKER IS NOT WHERE THE NAIVE PATH LOOKS. A clone that was fetched
with a depth keeps its marker in the CLONE's own git directory. In a
linked worktree `git rev-parse --absolute-git-dir` answers the
worktree's own directory instead -- something like
``.git/worktrees/<name>/`` -- and that directory carries no marker at
all, so a helper that opens ``<root>/.git/shallow`` concludes the
checkout is complete when it is not. That failure is invisible in CI,
where a checkout is a clone, and it is the whole of what a linked
worktree of a shallow clone is: the control degrades into its strict
form and reports a false red on the merge the scoping exists to survive.
So the marker is read from the COMMON directory, which every worktree of
a clone shares, and a linked worktree of a FULL clone has no marker
there either -- so the check distinguishes the two shapes rather than
refusing everything with a worktree git dir.

CALLERS ASK FIRST. ``require_history`` is a separate, explicitly named
call, made before anything else is asked of this module, because a
helper that can also be called in a way that yields a plausible empty
set is not fail-closed.
"""
import os
import subprocess
from pathlib import Path

SHALLOW_REMEDY = (
    'a control that compares a generated artifact against a tree has to '
    'know which tree the artifact was generated over, and a shallow '
    'checkout cannot say: set `fetch-depth: 0` on the actions/checkout '
    'step of the job named below, then re-run it')

SHALLOW_MARKER = 'shallow'


class ShallowHistory(ValueError):
    """The checkout has no history to resolve a last writer from."""


def _git(root, *argv, check=True):
    result = subprocess.run(
        ['git', '-C', str(root), *argv], check=False, capture_output=True,
        text=True)
    if check and result.returncode != 0:
        raise ValueError(f'git {argv[0]} failed under {root}: '
                         f'{result.stderr.strip()}') from None
    return result


def common_dir(root):
    """The clone's own git directory, which every worktree of it shares.

    Asked for by `--git-common-dir` rather than derived from the path,
    because in a linked worktree the two are different directories and
    only this one is the clone's.
    """
    result = _git(root, 'rev-parse', '--path-format=absolute',
                  '--git-common-dir', check=False)
    if result.returncode != 0:
        raise ValueError(f'{root} is not a git checkout: '
                         f'{result.stderr.strip()}') from None
    return Path(result.stdout.strip())


def is_shallow(root):
    """True when the clone behind `root` has a truncated history.

    A linked worktree of a shallow clone is shallow, because the marker
    is in the clone's directory and a worktree never has one of its own.
    """
    return (common_dir(root) / SHALLOW_MARKER).exists()


def _job():
    return os.environ.get('GITHUB_JOB') or ''


def require_history(root, artifact):
    """Refuse a shallow checkout, naming the job that has to deepen it.

    The call a caller makes FIRST. It raises rather than returning a
    verdict, and it raises rather than answering, because every answer
    this module could give about a shallow checkout is a guess and the
    guess is always the permissive one.
    """
    if is_shallow(root):
        job = _job()
        named = (f'job {job!r}' if job else
                 'the job that ran this (GITHUB_JOB is unset, so this '
                 'refusal cannot name it -- read it from the run that '
                 'produced it)')
        raise ShallowHistory(
            f'cannot resolve the base of {artifact}: the checkout at {root} '
            f'is shallow, so no commit in it is known to be the one that '
            f'last wrote that artifact. {SHALLOW_REMEDY}. Affected: {named}')


def last_writer(root, artifact):
    """The commit that last wrote `artifact`, as a 40-character SHA.

    `artifact` is relative to `root`. A path no commit has ever written
    is a refusal, not an empty answer: there is no base to scope
    against, and reporting one would exempt every name. The shallow
    refusal is repeated here so the function is not a way to skip it.
    """
    require_history(root, artifact)
    result = _git(root, 'log', '-1', '--format=%H', '--', artifact,
                  check=False)
    commit = result.stdout.strip()
    if result.returncode != 0:
        raise ValueError(f'cannot read the history of {artifact} under '
                         f'{root}: {result.stderr.strip()}') from None
    if not commit:
        raise ValueError(f'no commit has written {artifact} under {root}, '
                         'so there is no base to check it against')
    return commit


def tree_files(root, commit):
    """Every path present in `commit`'s tree.

    Recursive, and blobs only: a subtree is not a file anything can
    name, and a submodule is a commit id rather than a path, so neither
    belongs in the set a policy compares module paths against.
    """
    result = _git(root, 'ls-tree', '-r', '-z', '--name-only', commit)
    return frozenset(one for one in result.stdout.split('\0') if one)


def base_files(root, artifact):
    """`(commit, paths)` for the tree that last wrote `artifact`."""
    commit = last_writer(root, artifact)
    return commit, tree_files(root, commit)


def relative_to_tree(root, artifact):
    """`artifact` as a path relative to `root`, or a refusal.

    The two are the same checkout by construction in every shipped
    caller, and a caller that paired an artifact from one checkout with
    a tree from another would be reading a base out of a repository
    that does not contain it.
    """
    try:
        return Path(artifact).resolve().relative_to(
            Path(root).resolve()).as_posix()
    except ValueError:
        raise ValueError(
            f'the artifact is not inside the tree it is checked against: '
            f'{artifact} is not under {root}') from None
