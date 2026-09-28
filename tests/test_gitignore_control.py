#!/usr/bin/env python3
"""The fixtures and verdicts for the committed `.gitignore` control.

The control itself, and the rule it implements — the scope, why it is
scoped, what it does not detect and what survives the base moving — are
stated in `tests/_gitignore_control.py`. This suite builds the
repositories it runs against and asserts a verdict for each shape.

Every fixture runs the shipped generator in its own throwaway repository
and commits what it wrote, so no base artifact is ever a literal the
fixture wrote itself. Each verdict below is one the control actually
produced; the report names which fixture decided which clause.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gitignore_control as ctl  # noqa: E402
import _util  # noqa: E402
from _gitignore_control import (  # noqa: E402
    ARTIFACT, BLOCK, REMEDY, SHALLOW_REMEDY, control, git_read, paths)
from _repo import ROOT  # noqa: E402


# ── fixture repositories ───────────────────────────────────────────────

def _git_of(repo, *args):
    return git_read(repo, *args)


def _write(repo, name, body='content\n'):
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    return path


def _regenerate(repo, message='regenerate the ignore file'):
    """Run the shipped generator HERE and commit what it wrote.

    A fixture whose subject the fixture itself wrote cannot fail. The
    generator runs twice: its first pass reads the tracked set before it
    writes the file, so the file it wrote is not yet among the paths it
    names, and the second pass names it.
    """
    for _ in range(2):
        assert ctl.generator().main(repo) == 0, (
            'the generator refused its own tree')
        _git_of(repo, 'add', '-f', ARTIFACT)
    _git_of(repo, 'commit', '-q', '-m', message)
    return _head(repo)


def _fixture_repo(directory, files):
    """A throwaway repository whose ignore file the real generator wrote."""
    repo = Path(directory)
    repo.mkdir(parents=True)
    _git_of(repo, 'init', '-q')
    _git_of(repo, 'config', 'user.email', 'control@example.invalid')
    _git_of(repo, 'config', 'user.name', 'Control')
    for name in files:
        _write(repo, name)
    _git_of(repo, 'add', '-f', *files)
    _git_of(repo, 'commit', '-q', '-m', 'the tracked files')
    _regenerate(repo, 'name every tracked file')
    return repo


def _head(repo):
    return _git_of(repo, 'rev-parse', 'HEAD').strip()


def _commit_ignore(repo, text, message):
    """Write the ignore file and commit it, as a merge resolution would."""
    (Path(repo) / ARTIFACT).write_text(text, encoding='utf-8')
    _git_of(repo, 'add', '-f', ARTIFACT)
    _git_of(repo, 'commit', '-q', '-m', message)


def _edit_ignore(repo, replace, message):
    """Edit the committed file in place and commit the edit."""
    text = (Path(repo) / ARTIFACT).read_text(encoding='utf-8')
    assert replace[0] in text, (
        f'the fixture text to replace is absent: {replace[0]!r}')
    _commit_ignore(repo, text.replace(*replace), message)


# ── the repository this suite runs in ───────────────────────────────────

def test_the_committed_ignore_file_is_what_the_generator_derives(tmp):
    del tmp
    verdict = control(ROOT)
    assert verdict.status == 'green', verdict.detail
    assert verdict.base, 'the control reached no base commit at all'
    assert verdict.scope, 'the control scoped against nothing'
    # Whether the base happens to be HEAD is a property of where this
    # branch's commits sit, not of the control: a commit that rewrote the
    # ignore file IS the last writer. The fixture below is where the base
    # is pinned to neither the head nor the fork point.


def test_the_line_endings_are_folded_before_anything_compares_them(tmp):
    """CRLF is folded, and the fixture is synthetic because CI cannot be.

    `git cat-file` hands back the blob's own bytes, which are LF, while a
    Windows checkout hands the same file to `read_text` as CRLF. Fold one
    and not the other and the two disagree about a file that is identical,
    which is a false red on the platform the clause exists for. Linux and
    macOS checkouts never produce the difference, so the only way this can
    fail anywhere is a string the test writes itself.
    """
    del tmp
    assert ctl.normalise('!/a.py\r\n!/b.py\r\n') == '!/a.py\n!/b.py\n'
    assert ctl.normalise('!/a.py\n') == '!/a.py\n'


def test_the_base_itself_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    assert verdict.scope, verdict


# ── the merges the scoping exists for ──────────────────────────────────

def test_a_file_added_after_the_base_and_never_regenerated_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    fork = _head(repo)
    _git_of(repo, 'checkout', '-q', '-b', 'regenerated')
    _write(repo, 'd.py')
    _git_of(repo, 'add', '-f', 'd.py')
    _git_of(repo, 'commit', '-q', '-m', 'add d')
    written = _regenerate(repo, 'name d as well')
    _git_of(repo, 'checkout', '-q', '-b', 'forgotten', fork)
    _write(repo, 'e.py')
    _git_of(repo, 'add', '-f', 'e.py')
    _git_of(repo, 'commit', '-q', '-m', 'add e and do not regenerate')
    _git_of(repo, 'merge', '-q', '--no-edit', 'regenerated')

    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    # The base has to be the commit that wrote the file and neither the
    # fork point nor the head, or swapping the base argument for HEAD would
    # still pass and the fixture would prove nothing about which commit the
    # control reads.
    assert verdict.base == written, verdict.base
    assert verdict.base not in (fork, _head(repo)), verdict.base
    assert 'd.py' in verdict.scope, sorted(verdict.scope)
    assert 'e.py' not in verdict.scope, sorted(verdict.scope)
    # Six paths are tracked and five are in scope, so every figure here is
    # over the compared set: a number taken over the tracked set would say
    # six, and this is the assertion that says so.
    assert len(paths(_git_of(repo, 'ls-files', '-z'))) == 6, 'the fixture'
    assert 'in scope 5' in verdict.detail, verdict.detail
    assert 'in scope 6' not in verdict.detail, verdict.detail
    assert '1 arrived after the base' in verdict.detail, verdict.detail
    assert '0 gone since' in verdict.detail, verdict.detail


def test_two_branches_that_both_regenerated_merge_green(tmp):
    names = tuple(f'm{index:02d}.txt' for index in range(1, 9))
    repo = _fixture_repo(Path(tmp) / 'repo', names)
    fork = _head(repo)
    # `aaa.txt` and `zzz.txt` sort to opposite ends of the root block, so
    # the two regenerations merge cleanly instead of conflicting. Adjacent
    # insertions would land in one hunk and force the resolution case below,
    # which is a different clause entirely.
    for branch, added in (('left', 'aaa.txt'), ('right', 'zzz.txt')):
        _git_of(repo, 'checkout', '-q', '-b', branch, fork)
        _write(repo, added)
        _git_of(repo, 'add', '-f', added)
        _git_of(repo, 'commit', '-q', '-m', f'add {added}')
        _regenerate(repo, f'name {added} as well')
    _git_of(repo, 'merge', '-q', '--no-edit', 'left')
    _git_of(repo, 'merge', '-q', '--no-edit', 'right')

    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    assert {'aaa.txt', 'zzz.txt'} <= verdict.scope, sorted(verdict.scope)


def test_a_file_removed_after_the_base_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    fork = _head(repo)
    _git_of(repo, 'checkout', '-q', '-b', 'dropped')
    _git_of(repo, 'rm', '-q', 'c.py')
    _git_of(repo, 'commit', '-q', '-m', 'drop c and do not regenerate')
    _git_of(repo, 'checkout', '-q', '-b', 'touched', fork)
    _write(repo, 'a.py', 'edited\n')
    _git_of(repo, 'add', '-f', 'a.py')
    _git_of(repo, 'commit', '-q', '-m', 'edit a')
    _git_of(repo, 'merge', '-q', '--no-edit', 'dropped')

    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    assert 'c.py' not in verdict.scope, sorted(verdict.scope)
    assert '1 gone since' in verdict.detail, verdict.detail


def test_a_directory_emptied_after_the_base_drops_its_whole_block(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('keep.py', 'doomed/old.txt'))
    fork = _head(repo)
    _git_of(repo, 'checkout', '-q', '-b', 'emptied')
    _git_of(repo, 'rm', '-q', 'doomed/old.txt')
    _git_of(repo, 'commit', '-q', '-m', 'empty the directory, no regenerate')
    _git_of(repo, 'checkout', '-q', '-b', 'grown', fork)
    _write(repo, 'fresh/new.txt')
    _git_of(repo, 'add', '-f', 'fresh/new.txt')
    _git_of(repo, 'commit', '-q', '-m', 'add a wholly new directory')
    _regenerate(repo, 'name the new directory')
    _git_of(repo, 'merge', '-q', '--no-edit', 'emptied')

    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    assert 'doomed/old.txt' not in verdict.scope, sorted(verdict.scope)
    assert 'fresh/new.txt' in verdict.scope, sorted(verdict.scope)


# ── the defects the control exists to catch ────────────────────────────

def test_an_entry_deleted_for_a_still_tracked_file_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _edit_ignore(repo, ('!/b.py\n', ''), 'hand-delete the b entry')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'b.py' in verdict.detail, verdict.detail
    assert REMEDY in verdict.detail, verdict.detail


def test_a_reordered_mangled_or_corrupted_block_is_red(tmp):
    cases = (('reordered', '!/a.py\n!/b.py\n', '!/b.py\n!/a.py\n'),
             ('mangled header', BLOCK + 'root ───', BLOCK + 'root ---'),
             ('no deny rule', '\n*\n', '\n'))
    for name, before, after in cases:
        repo = _fixture_repo(Path(tmp) / name, ('a.py', 'b.py', 'c.py'))
        _edit_ignore(repo, (before, after), f'{name} by hand')
        verdict = control(repo)
        assert verdict.status == 'red', f'{name}: {verdict.detail}'


def test_an_invented_entry_for_an_untracked_path_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _edit_ignore(repo, ('!/c.py\n', '!/c.py\n!/ghost.py\n'),
                 'invent an entry for a path nothing tracks')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'ghost.py' in verdict.detail, verdict.detail


def test_an_invented_directory_block_is_dropped_as_empty(tmp):
    """The block clause is broader than the entry clause, and says so.

    A `!<path>` line inside a real block naming a path tracked nowhere is a
    difference the control reports. A whole invented DIRECTORY block is
    dropped, because the block clause drops any block left with no in-scope
    entry and an invented block has none. That follows from the rule as
    specified, and this test makes it a property rather than an accident.
    """
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    invented = ('\n# ─── ghostdir ───\n!/ghostdir/\n'
                '!/ghostdir/nothing.py\n')
    _edit_ignore(repo, ('\n# ─── root ───\n',
                        invented + '\n# ─── root ───\n'),
                 'invent a whole directory block')
    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail


# ── the refusals ───────────────────────────────────────────────────────

def test_a_checkout_with_truncated_history_refuses_loudly(tmp):
    """Both shapes a truncated history arrives in, and both must refuse.

    The second is the one that matters: `.git` is a FILE in a linked
    worktree, so a control that opened `<repo>/.git/shallow` finds nothing
    there and concludes the history is complete — a fail-open in the one
    place this control has to fail closed. The marker lives in the COMMON
    directory, so the control asks git where its directories are.
    """
    source = _fixture_repo(Path(tmp) / 'source', ('a.py', 'b.py'))
    clone = Path(tmp) / 'shallow'
    # file:// is what makes --depth bite; a local path is hardlinked and
    # cloned in full whatever the flag says.
    subprocess.run(
        ['git', 'clone', '--quiet', '--depth', '1', '--no-local',
         f'file://{source}', str(clone)],
        check=True, capture_output=True)
    verdict = control(clone)
    assert verdict.status == 'refused', verdict
    assert 'shallow' in verdict.detail, verdict.detail
    assert SHALLOW_REMEDY in verdict.detail, verdict.detail

    linked = Path(tmp) / 'linked'
    _git_of(clone, 'worktree', 'add', '--quiet', '-b', 'side', str(linked))
    assert (linked / '.git').is_file(), 'the fixture is not a linked worktree'
    linked_verdict = control(linked)
    assert linked_verdict.status == 'refused', linked_verdict
    assert 'shallow' in linked_verdict.detail, linked_verdict.detail


def test_a_repository_with_no_committed_ignore_file_refuses(tmp):
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    _git_of(repo, 'init', '-q')
    _write(repo, 'a.py')
    _git_of(repo, 'add', '-f', 'a.py')
    verdict = control(repo)
    assert verdict.status == 'refused', verdict
    assert 'no commit has' in verdict.detail, verdict.detail


def test_an_unreadable_base_tree_refuses_rather_than_comparing_nothing(tmp):
    """A tree that reads as empty is a refusal, not a clean comparison."""
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    real = ctl.git_read

    def unreadable(root, *args):
        if 'ls-tree' in args and '-r' in args:
            return ''
        return real(root, *args)

    ctl.git_read = unreadable
    try:
        verdict = control(repo)
    finally:
        ctl.git_read = real
    assert verdict.status == 'refused', verdict
    assert 'nothing' in verdict.detail, verdict.detail


def test_a_failing_git_read_refuses_naming_the_command(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    real = ctl.git_read

    def failing(root, *args):
        if 'ls-tree' in args and '-r' in args:
            raise subprocess.CalledProcessError(
                128, ['git', '-C', str(root), *args], stderr='fatal: bad')
        return real(root, *args)

    ctl.git_read = failing
    try:
        verdict = control(repo)
    finally:
        ctl.git_read = real
    assert verdict.status == 'refused', verdict
    assert 'fatal: bad' in verdict.detail, verdict.detail


def test_a_working_copy_that_is_not_the_committed_file_refuses(tmp):
    """Two answers to "the committed file" is a refusal, not a preference."""
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    (Path(repo) / ARTIFACT).write_text('*\n!/a.py\n', encoding='utf-8')
    verdict = control(repo)
    assert verdict.status == 'refused', verdict
    assert 'working' in verdict.detail, verdict.detail


# ── the enumerations, and the two sets they produce ───────────────────

def test_the_two_sets_come_from_the_two_different_calls(tmp):
    """`ls-files` reads the index; `ls-tree` reads a commit.

    They agree on a clean checkout at HEAD, which is what CI gives, and
    they part company the moment the index and the commit do — so the
    control takes the tracked set from `git ls-files`, exactly the call
    `scripts/gen_gitignore.py` derives from, and the base set from
    `git ls-tree -r` at the base commit, which is the one place reading a
    commit is right. A staged-but-uncommitted path is where the two part
    company, and the figure that notices is the arrived-after-the-base one.
    """
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _write(repo, 'd.py')
    _git_of(repo, 'add', '-f', 'd.py')
    index = paths(_git_of(repo, 'ls-files', '-z'))
    commit = paths(_git_of(repo, 'ls-tree', '-r', '--name-only', '-z', 'HEAD'))
    assert 'd.py' in index and 'd.py' not in commit, (
        'the fixture did not make the index and the commit disagree')

    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    assert '1 arrived after the base' in verdict.detail, verdict.detail


# ── the merges that resolve into a decision ───────────────────────────

def test_a_conflicted_merge_resolution_is_judged_from_the_merge(tmp):
    """A merge commit that resolved the file is itself the last writer.

    The resolution below is a value NEITHER parent had, which is where the
    design's own reasoning could be wrong: git's path-limited history
    simplification might or might not report that merge as a writer. The
    fixture settles it by execution, and this follows the fixture.
    """
    names = tuple(f'm{index:02d}.txt' for index in range(1, 9))
    repo = _fixture_repo(Path(tmp) / 'repo', names)
    fork = _head(repo)
    # Two new files whose entries land on either side of the same line, so
    # the merge conflicts instead of composing — which is the point: the
    # merge commit is then the last writer of the file.
    for branch, added in (('left', 'm04a.txt'), ('right', 'm04b.txt')):
        _git_of(repo, 'checkout', '-q', '-b', branch, fork)
        _write(repo, added, f'added on {branch}\n')
        _git_of(repo, 'add', '-f', added)
        _git_of(repo, 'commit', '-q', '-m', f'add {added}')
        _regenerate(repo, f'name {added} as well')
    _git_of(repo, 'checkout', '-q', 'left')
    conflicted = subprocess.run(
        ['git', '-C', str(repo), 'merge', '--no-edit', 'right'],
        capture_output=True, text=True, check=False)
    assert conflicted.returncode != 0, 'the fixture did not conflict'
    assert 'CONFLICT' in conflicted.stdout, conflicted.stdout
    # Resolve to a value NEITHER parent had: the conflicted hunk replaced
    # by one entry naming a path nothing tracks. The file is not derived
    # here — `git ls-files` reports a conflicted path once per stage, and
    # deriving from that is how a merge gets a triplicated entry.
    text = (Path(repo) / ARTIFACT).read_text(encoding='utf-8')
    cut = text.index('<<<<<<<')
    resumed = text.index('\n', text.index('>>>>>>>')) + 1
    _commit_ignore(repo,
                   text[:cut] + '!/neither-parent.txt\n' + text[resumed:],
                   'resolve the conflict by hand')

    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert verdict.base == _head(repo), (
        'the merge commit that resolved the file is not the last writer, '
        f'so the control read base {verdict.base} instead')
    assert 'neither-parent.txt' in verdict.detail, verdict.detail
    assert 'm04b.txt' in verdict.detail, verdict.detail


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='gitignorecontrol_')


if __name__ == '__main__':
    raise SystemExit(main())
