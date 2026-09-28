#!/usr/bin/env python3
"""The fixtures and verdicts for the committed `.gitignore` control.

The control, and the rule it implements, are stated in
`tests/_gitignore_control.py`. This suite builds the repositories it runs
against and asserts a verdict for each shape.

Every fixture runs the shipped generator in its own throwaway repository
and commits what it wrote, so no base artifact is ever a literal the
fixture wrote itself.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gitignore_control as ctl  # noqa: E402
import _util  # noqa: E402
from _gitignore_control import (  # noqa: E402
    ARTIFACT, PREFIX, REMEDY, control, git_read)
from _repo import ROOT  # noqa: E402

BLOCK = '# ─── '


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
    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', message],
                   check=False, capture_output=True)


def _fixture_repo(directory, files):
    """A throwaway repository whose ignore file the real generator wrote."""
    repo = Path(directory)
    repo.mkdir(parents=True)
    _git_of(repo, 'init', '-q', '-b', 'main')
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


def _edit_ignore(repo, replace, message):
    """Edit the committed file in place and commit the edit."""
    text = (Path(repo) / ARTIFACT).read_text(encoding='utf-8')
    assert replace[0] in text, (
        f'the fixture text to replace is absent: {replace[0]!r}')
    (Path(repo) / ARTIFACT).write_text(
        text.replace(*replace), encoding='utf-8')
    _git_of(repo, 'add', '-f', ARTIFACT)
    _git_of(repo, 'commit', '-q', '-m', message)


def _merge(repo, branch):
    """A real `git merge`; True when git raised a conflict."""
    done = subprocess.run(
        ['git', '-C', str(repo), 'merge', '--no-ff', '--no-edit', branch],
        capture_output=True, text=True, check=False)
    return done.returncode != 0


def test_the_committed_ignore_file_is_what_the_generator_derives(tmp):
    del tmp
    verdict = control(ROOT)
    assert verdict.status == 'green', verdict.detail
    assert verdict.scope, 'the control compared against nothing'


def test_the_base_itself_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    verdict = control(repo)
    assert verdict.status == 'green', verdict.detail
    assert len(verdict.scope) == 4, sorted(verdict.scope)


def test_the_line_endings_are_folded_before_anything_compares_them(tmp):
    """CRLF is folded, and the fixture is synthetic because CI cannot be.

    A Windows checkout hands `read_text` CRLF where the generator emits
    LF, and the control compares the two. Fold one and not the other and
    a file that is identical reports as a difference. No POSIX checkout
    produces the difference, so the only way this can fail anywhere is a
    string the test writes itself.
    """
    del tmp
    assert ctl.normalise('!/a.py\r\n!/b.py\r\n') == '!/a.py\n!/b.py\n'
    assert ctl.normalise('!/a.py\n') == '!/a.py\n'


def test_a_hand_deleted_entry_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _edit_ignore(repo, (f'{PREFIX}b.py\n', ''), 'hand-delete the b entry')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'b.py' in verdict.detail, verdict.detail
    assert REMEDY in verdict.detail, verdict.detail
    assert 'tracked but not named' in verdict.detail, verdict.detail


def test_an_invented_entry_for_an_untracked_path_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    invented = f'{PREFIX}c.py\n{PREFIX}ghost.py\n'
    _edit_ignore(repo, (f'{PREFIX}c.py\n', invented),
                 'invent an entry for a path nothing tracks')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'named but not tracked' in verdict.detail, verdict.detail


def test_a_reordering_of_two_entries_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _edit_ignore(repo, (f'{PREFIX}a.py\n{PREFIX}b.py\n',
                        f'{PREFIX}b.py\n{PREFIX}a.py\n'), 'swap a and b')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail


def test_a_swapped_whole_block_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'd/x.py', 'e/y.py'))
    first = (f'{BLOCK}d ───\n{PREFIX}d/\n{PREFIX}d/x.py\n'
             f'\n{BLOCK}e ───\n{PREFIX}e/\n{PREFIX}e/y.py\n')
    second = (f'{BLOCK}e ───\n{PREFIX}e/\n{PREFIX}e/y.py\n'
              f'\n{BLOCK}d ───\n{PREFIX}d/\n{PREFIX}d/x.py\n')
    _edit_ignore(repo, (first, second), 'swap the d and e blocks whole')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail


def test_a_missing_deny_rule_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    _edit_ignore(repo, ('\n*\n', '\n'), 'drop the deny rule')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail


def test_a_stray_blank_line_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    _edit_ignore(repo, (f'\n{PREFIX}b.py\n', f'\n\n{PREFIX}b.py\n'),
                 'a stray blank line inside a block')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail


def test_a_trailing_space_on_an_entry_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    _edit_ignore(repo, (f'{PREFIX}b.py\n', f'{PREFIX}b.py \n'),
                 'a trailing space on an entry')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail


def test_a_dropped_directory_reopen_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'd/x.py'))
    _edit_ignore(repo, (f'{PREFIX}d/\n', ''), 'drop the directory re-open')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail


def test_a_missing_ignore_file_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    (Path(repo) / ARTIFACT).unlink()
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'no .gitignore' in verdict.detail, verdict.detail


def test_a_file_added_with_force_and_no_regeneration_is_red(tmp):
    """The pre-merge half: the branch is red on its OWN head.

    This is the property that makes strictness safe here, and it is a
    property of PLACEMENT rather than of the comparison. `git add -f`
    bypasses the deny rule completely, so a branch can carry a tracked
    file its ignore file does not name; the control is red on that
    branch's own head and on its own merge ref, the required check holds
    the merge, and the default branch never sees it.

    It is a fixture about a BRANCH, and that is the point. The merge
    fixture below builds its repositories in process and touches no CI
    configuration, so it would still pass if the control were moved to a
    post-merge step — which is why this one cannot be read as a test that
    defends where the control runs. What it pins is the premise; the
    placement is the control's docstring and the workflow's.
    """
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    _write(repo, 'forced.py')
    _git_of(repo, 'add', '-f', 'forced.py')
    _git_of(repo, 'commit', '-q', '-m', 'add with -f and do not regenerate')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'tracked but not named' in verdict.detail, verdict.detail
    assert 'forced.py' in verdict.detail, verdict.detail


def test_a_nested_directory_reports_no_ghost(tmp):
    """A `!/dir/` re-open is not an entry, and must not read as one.

    The path classifier takes `!/d/e.py` as naming a file and `!/d/` as
    naming a directory, and the second is the arm that is easy to drop:
    without it every re-open line is reported as `named but not tracked`,
    so a perfectly correct file diagnoses as full of ghosts. The only
    thing that can fail this is a fixture with a nested directory, which
    is why the flat fixtures cannot see it.
    """
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'd/e.py', 'f/g.py'))
    assert control(repo).status == 'green'
    # The ghost list is only built on a RED, so asserting it on a green
    # tree would be vacuous. Delete a real entry to force the diagnosis,
    # and the re-opens for `d` and `f` must stay out of it.
    _edit_ignore(repo, (f'{PREFIX}a.py\n', ''), 'hand-delete the a entry')
    verdict = control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'tracked but not named: a.py' in verdict.detail, verdict.detail
    assert 'named but not tracked' not in verdict.detail, verdict.detail
    # And the same holds when a re-open is MISSING rather than present: the
    # ghost list is built from the lines that are there, so deleting
    # `!/f/` must not conjure an entry for the directory.
    _edit_ignore(repo, (f'{PREFIX}f/\n', ''), 'drop the f re-open')
    dropped = control(repo)
    assert dropped.status == 'red', dropped.detail
    assert 'named but not tracked' not in dropped.detail, dropped.detail


def _two_branches(tmp, name, left_adds, right_adds):
    """Two branches off ONE base, each green on its own head."""
    repo = _fixture_repo(Path(tmp) / name, tuple(f'm{i:02d}.txt'
                                                 for i in range(1, 11)))
    base = _head(repo)
    for branch_name, adds in (('left', left_adds), ('right', right_adds)):
        _git_of(repo, 'checkout', '-q', '-b', branch_name, base)
        for added in adds:
            _write(repo, added, f'added on {branch_name}\n')
            _git_of(repo, 'add', '-f', added)
            _git_of(repo, 'commit', '-q', '-m', f'add {added}')
        _regenerate(repo, f'branch {branch_name} names its addition')
    return repo


def test_main_is_green_after_a_merge_of_two_green_branches(tmp):
    """The property the whole rule leans on, in both orders, both shapes.

    Two branches each add a tracked file and each regenerate, so each is
    green on its own head. Main must be green after the merge. The two
    shapes are the two ways their entries can meet: far apart in the
    sorted list, which git merges textually, and adjacent, which git
    REFUSES. The adjacent case is the one that matters — a conflict is a
    human decision, and the resolution this pins is regenerating, not
    "take both sides", which is order-sensitive and green only when the
    side checked out happens to come first in sort order.
    """
    for shape, (left_adds, right_adds), expect_conflict in (
            ('far apart', (['aaa.txt'], ['zzz.txt']), False),
            ('adjacent', (['m04a.txt'], ['m04b.txt']), True)):
        for order, (target, incoming) in (('right into left',
                                           ('left', 'right')),
                                          ('left into right',
                                           ('right', 'left'))):
            name = f'{shape.replace(" ", "-")}-{order}'
            repo = _two_branches(tmp, name, left_adds, right_adds)
            for branch_name in ('left', 'right'):
                _git_of(repo, 'checkout', '-q', branch_name)
                assert control(repo).status == 'green', (
                    f'{name}: {branch_name} was not green on its own head')
            _git_of(repo, 'checkout', '-q', target)
            conflicted = _merge(repo, incoming)
            assert conflicted == expect_conflict, (
                f'{name}: expected a conflict={expect_conflict}, '
                f'git said {conflicted}')
            if conflicted:
                _regenerate(repo, 'resolve the conflict by regenerating')
            verdict = control(repo)
            assert verdict.status == 'green', f'{name}: {verdict.detail}'
            merged = sorted(verdict.scope)
            assert all(add in merged for add in left_adds + right_adds), (
                f'{name}: a merge dropped an addition: {merged}')


def test_a_union_resolution_is_order_sensitive_and_regenerating_is_not(tmp):
    """Why the adjacent fixture resolves by regenerating, in both orders.

    "Take both sides" keeps the side that was checked out first, so it is
    sorted only when that side happens to come first in sort order. One
    of these two merges is therefore green after a union and the other
    is red, and a control that accepted the union would be accepting a
    coin flip. Regenerating is green in both, which is what the adjacent
    fixture does.
    """
    verdicts = {}
    for order, (target, incoming) in (('right into left', ('left', 'right')),
                                      ('left into right', ('right', 'left'))):
        repo = _two_branches(tmp, f'union-{target}',
                             ['m04a.txt'], ['m04b.txt'])
        _git_of(repo, 'checkout', '-q', target)
        assert _merge(repo, incoming), f'{order}: expected a conflict'
        text = (Path(repo) / ARTIFACT).read_text(encoding='utf-8')
        union = [line for line in text.split('\n')
                 if not line.startswith(('<<<<<<<', '|||||||', '=======',
                                         '>>>>>>>'))]
        (Path(repo) / ARTIFACT).write_text('\n'.join(union),
                                           encoding='utf-8')
        verdicts[order] = control(repo).status
        _regenerate(repo, 'resolve by regenerating instead')
        verdict = control(repo)
        assert verdict.status == 'green', f'{order}: {verdict.status}'
    assert sorted(set(verdicts.values())) == ['green', 'red'], verdicts


def test_an_undecodable_byte_in_the_ignore_file_does_not_crash(tmp):
    """A byte no decoder can read is a verdict, not a traceback.

    The artifact is read from the working tree, and a filename or a
    hand-edit can leave a byte no decoder will read. `git_read` one
    function up already decodes with surrogateescape for exactly this
    reason, and scripts/gen_gitignore.py documents it twice; reading the
    file without it raises UnicodeDecodeError, which the public entry
    point does not catch — it catches OSError and SubprocessError — so
    the raw traceback escapes and the suite reports a crash instead of a
    difference. The file is written as raw bytes because a fixture cannot
    rely on the platform's own encoding.
    """
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    path = Path(repo) / ARTIFACT
    path.write_bytes(path.read_bytes() + b'\n!/\xff.py\n')
    verdict = control(repo)
    assert verdict.status in ('green', 'red'), verdict
    # The undecodable entry is a path git never tracked, so it is named
    # but not tracked, and the control says so instead of raising.
    assert verdict.status == 'red', verdict.detail
    assert 'named but not tracked' in verdict.detail, verdict.detail


def test_a_repository_with_no_tracked_paths_refuses(tmp):
    """An empty tracked set is unreadable, not a clean comparison."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    _git_of(repo, 'init', '-q', '-b', 'main')
    _git_of(repo, 'config', 'user.email', 'control@example.invalid')
    _git_of(repo, 'commit', '-q', '--allow-empty', '-m', 'an empty commit')
    (Path(repo) / ARTIFACT).write_text('*\n', encoding='utf-8')
    verdict = control(repo)
    assert verdict.status == 'refused', verdict
    assert 'nothing' in verdict.detail, verdict.detail


def test_a_failing_git_read_refuses_naming_the_command(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    real = ctl.git_read

    def failing(root, *args):
        if 'ls-files' in args:
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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='gitignorecontrol_')


if __name__ == '__main__':
    raise SystemExit(main())
