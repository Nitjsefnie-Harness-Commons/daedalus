#!/usr/bin/env python3
"""Which checkout shapes a base can be read out of, and which cannot.

`scripts/ci/artifact_base.py` answers one question -- which commit last
wrote a tracked artifact, and what was in its tree -- and it is only
able to answer it when the checkout carries history. The failure that
matters is the quiet one: a checkout that looks complete and is not, so
the caller receives a plausible answer instead of a refusal, the
control degrades into its strict form, and the merge the scoping exists
to survive goes red on a runner nobody can diagnose.

The shape that produces it is a LINKED WORKTREE of a shallow clone.
A clone fetched with a depth keeps its marker in the clone's own git
directory; a linked worktree's own directory never has one, so a helper
that opens `<root>/.git/shallow` reads "complete" in exactly the case
that is not. No CI run on this repository will ever execute that shape,
which is why it is built here rather than trusted.

The three shapes, and what each must do:

  (a) a shallow clone                  refuse
  (b) a linked worktree of that clone  refuse -- the case that earns this
  (c) a linked worktree of a full clone answer normally

(c) matters as much as (b): a check that refuses anything with a
worktree git directory would pass this suite and be useless.
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import _util  # noqa: E402
import artifact_base  # noqa: E402

ARTIFACT = '.github/reserved-test-names.json'
PAYLOAD = '{\n  "schema_version": 1,\n  "names": {}\n}\n'


def _repo_git(cwd, *argv):
    return subprocess.run(
        ['git', '-C', str(cwd), *argv], check=True, capture_output=True,
        text=True, env=_util.child_coverage('scrub'))


def _origin(root, name='origin'):
    """A real repository with two commits, the second writing the artifact."""
    tree = Path(root) / name
    (tree / '.github').mkdir(parents=True)
    (tree / 'tests').mkdir(parents=True)
    (tree / 'tests' / 'one.py').write_text('def one():\n    pass\n',
                                           encoding='utf-8')
    _repo_git(tree, 'init', '-q', '-b', 'main')
    _repo_git(tree, 'config', 'user.email', 'tests@example.invalid')
    _repo_git(tree, 'config', 'user.name', 'Tests')
    _repo_git(tree, 'add', '-A')
    _repo_git(tree, 'commit', '-q', '-m', 'first')
    (tree / ARTIFACT).write_text(PAYLOAD, encoding='utf-8')
    _repo_git(tree, 'add', '-A')
    _repo_git(tree, 'commit', '-q', '-m', 'the artifact')
    return tree


def _worktree_path(root, name):
    return Path(root) / name


def _clone(root, source, name, depth=None):
    argv = ['git', 'clone', '-q']
    if depth is not None:
        # `file://` is load-bearing: a clone from a plain local path
        # hardlinks the objects and silently IGNORES --depth, so the
        # fixture would not be the shape under test.
        argv += ['--depth', str(depth), f'file://{source}']
    else:
        argv += [str(source)]
    argv += [str(Path(root) / name)]
    subprocess.run(argv, check=True, capture_output=True, text=True,
                   cwd=root, env=_util.child_coverage('scrub'))
    return Path(root) / name


def _refusal(clone):
    """The refusal a shallow checkout gets, as text."""
    try:
        artifact_base.require_history(clone, ARTIFACT)
    except artifact_base.ShallowHistory as error:
        return str(error)
    raise AssertionError('a shallow checkout was accepted')


# The two branches the refusal takes on `GITHUB_JOB`, pinned in FULL. The
# VARIABLE IS SET BY THIS TEST, never inherited: GitHub Actions sets it for
# every step, so an assertion that reads the ambient value is green on a
# developer's box and red on every runner, which is how this suite came to
# be red in fourteen CI legs while passing everywhere it was run by hand.
# The whole sentence is pinned, not a fragment, so a reworded or truncated
# remedy on EITHER branch is caught rather than one of them drifting
# unremarked -- and the two are built to differ, so neither pin can be a
# tautology over the other.
JOB_PRESENT = "job 'reserved-names'"
JOB_ABSENT = (
    'the job that ran this (GITHUB_JOB is unset, so this refusal cannot name '
    'it -- read it from the run that produced it)')


def _expected(root, named):
    return (f'cannot resolve the base of {ARTIFACT}: the checkout at {root} '
            f'is shallow, so no commit in it is known to be the one that last '
            f'wrote that artifact. {artifact_base.SHALLOW_REMEDY}. '
            f'Affected: {named}')


def test_both_branches_of_the_refusal_name_the_job_and_carry_the_remedy(tmp):
    """Both wordings, driven in-process, each pinned in full.

    The remedy token is a PROMISE TO ANOTHER SEAT: a control stacked on
    this module asserts `SHALLOW_REMEDY` is in the refusal, so a branch
    that worded itself without the token would turn THEIR suite red on
    CI for exactly the reason this one was. Both branches are therefore
    asserted to carry it, not only the one this box happens to take.
    """
    clone = _clone(tmp, _origin(tmp), 'shallow', depth=1)
    assert artifact_base.is_shallow(clone), 'the fixture is not shallow'
    seen = {}
    for job in ('reserved-names', None):
        environ = dict(os.environ)
        if job is None:
            environ.pop('GITHUB_JOB', None)
        else:
            environ['GITHUB_JOB'] = job
        with mock.patch.dict(os.environ, environ, clear=True):
            seen[job] = _refusal(clone)
    assert seen['reserved-names'] == _expected(clone, JOB_PRESENT), \
        seen['reserved-names']
    assert seen[None] == _expected(clone, JOB_ABSENT), seen[None]
    for job, text in seen.items():
        assert artifact_base.SHALLOW_REMEDY in text, (job, text)
        assert 'fetch-depth: 0' in text, (job, text)
    # And the two really are different, so neither pin is a tautology.
    assert seen['reserved-names'] != seen[None]


def test_a_shallow_clone_is_refused(tmp):
    clone = _clone(tmp, _origin(tmp), 'shallow', depth=1)
    assert artifact_base.is_shallow(clone), 'the fixture is not shallow'
    assert artifact_base.SHALLOW_REMEDY in _refusal(clone)


def test_a_linked_worktree_of_a_shallow_clone_is_refused(tmp):
    """The shape no CI run on this repository will ever execute.

    The marker is in the clone's directory, and the worktree's own git
    directory is a different one that has no marker, so a check reading
    the worktree's path concludes the checkout is complete. The
    refusal has to come from the COMMON directory, or this is a silent
    pass on the one shape that is both reachable here and invisible
    there.
    """
    clone = _clone(tmp, _origin(tmp), 'shallow', depth=1)
    linked = _worktree_path(tmp, 'linked')
    _repo_git(clone, 'worktree', 'add', '-q', '-b', 'wt', str(linked), 'HEAD')
    shown = _repo_git(linked, 'rev-parse', '--absolute-git-dir')
    own = Path(shown.stdout.strip())
    assert not (own / 'shallow').exists(), (
        f'the fixture is not the shape under test: {own} has a marker')
    assert artifact_base.is_shallow(linked), (
        'a linked worktree of a shallow clone read as complete')
    try:
        artifact_base.require_history(linked, ARTIFACT)
    except artifact_base.ShallowHistory as error:
        assert 'fetch-depth: 0' in str(error), str(error)
    else:
        raise AssertionError('a linked worktree of a shallow clone was '
                             'accepted -- the marker was read from the '
                             'worktree git dir instead of the clone\'s')
    # And the answer the caller would otherwise have got is a refusal
    # too, not an empty scope.
    try:
        artifact_base.base_files(linked, ARTIFACT)
    except artifact_base.ShallowHistory:
        pass
    else:
        raise AssertionError('base_files answered from a shallow checkout')


def test_a_linked_worktree_of_a_full_clone_is_answered(tmp):
    """The other half: the check distinguishes the shapes."""
    clone = _clone(tmp, _origin(tmp, 'fullsrc'), 'full')
    linked = _worktree_path(tmp, 'fulllinked')
    _repo_git(clone, 'worktree', 'add', '-q', '-b', 'wt', str(linked), 'HEAD')
    shown = _repo_git(linked, 'rev-parse', '--absolute-git-dir')
    own = Path(shown.stdout.strip())
    assert own.parent.name == 'worktrees', own
    assert not artifact_base.is_shallow(linked), (
        'a linked worktree of a full clone read as shallow')
    artifact_base.require_history(linked, ARTIFACT)
    commit, covered = artifact_base.base_files(linked, ARTIFACT)
    assert len(commit) == 40, commit
    assert ARTIFACT in covered, sorted(covered)
    assert 'tests/one.py' in covered, sorted(covered)


def test_a_full_clone_answers_and_a_path_nobody_wrote_does_not(tmp):
    clone = _clone(tmp, _origin(tmp, 'src2'), 'whole')
    artifact_base.require_history(clone, ARTIFACT)
    commit, covered = artifact_base.base_files(clone, ARTIFACT)
    assert len(commit) == 40 and ARTIFACT in covered
    try:
        artifact_base.base_files(clone, 'never/written.py')
    except ValueError as error:
        assert 'no commit has written' in str(error), str(error)
    else:
        raise AssertionError('a path no commit wrote answered with a base')
    try:
        artifact_base.relative_to_tree(clone, Path('/nowhere/else.json'))
    except ValueError as error:
        assert 'not under' in str(error), str(error)
    else:
        raise AssertionError('an artifact outside the tree was accepted')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='artifactbase_')


if __name__ == '__main__':
    raise SystemExit(main())
