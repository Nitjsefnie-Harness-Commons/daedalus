#!/usr/bin/env python3
"""The committed `.gitignore` is what the generator derives, for the files
it was written to cover.

`.gitignore` denies by default and names every tracked file back, and
`scripts/gen_gitignore.py` writes it. Nothing read the committed file and
compared it against what the generator derives, so it was correct by the
diligence of whoever last ran the script: issue 458 recorded five tracked
`tests/_jsroute*` helpers landing across four commits, none of which
regenerated it. Every fixture below runs the shipped generator in its own
throwaway repository, so no base artifact is a literal the fixture wrote.

THE SCOPE, AND WHY THERE IS ONE. An exhaustive "committed == derived"
control cannot be kept exact by two independent branches, so it fails on
the MERGE, on the default branch, where no branch is left to fix it — the
sibling control on `.github/reserved-test-names.json` turned the default
branch red three times on 2026-09-27 exactly that way. So the comparison
is scoped, and the scope comes from git at control time rather than from
the committed file, because a `!` line records no producer and no base:

    base    the commit that last wrote .gitignore
            (git log -1 --format=%H -- .gitignore)
    T       the tracked paths now       (git ls-files)
    T_base  the tracked paths at base   (git ls-tree -r --name-only base)
    S       T and T_base                in scope
    fresh   T only                      arrived after the file was written
    stale   T_base only                 gone since it was written

    reduce  the committed file, minus every `!<path>` line whose path is
            stale, minus any directory block left with no in-scope entry
    verdict reduce == derive(sorted(S))

The derived side is NOT a rendering over T that is then filtered; it is
`derive(sorted(S))`, because the derivation is per-file and dropping files
from it IS the reduction. Both sides name S here and in the code, so a
reader cannot reconstruct the other reading. A `fresh` file with no entry is
allowed — that clause is what makes the control compose. A `stale` entry
is allowed — the half that is easy to miss, since scoping only the absent
direction leaves a branch that drops a file without regenerating turning
the default branch red over a line it is entitled to keep. A directory
block with no in-scope entry is dropped whole, so a branch adding a wholly
new directory does not leave a header the other side has not got yet.

WHAT IT DOES NOT DETECT, stated plainly: a tracked file that arrives with
no entry at all. Scoping the absent direction is what buys composition and
composition was the binding requirement. The routes into the gap are
`git add -f` of an unlisted path — a plain `git add` IS refused by the deny
rule and `-f` bypasses the refusal completely, which is the route issue
458 took and on which nothing forces regeneration — and `git mv` or a
rename, where the old path goes stale and is reduced away while the new
path's absence is allowed. The file rots until some unrelated regeneration
absorbs it. The deny-by-default rule does NOT cover this: the file IS
tracked, `git status` shows nothing to commit, and the only thing that
would have noticed is a control scoped away from it. So the one-line honest
description is: the committed file is exactly what the generator derives,
for every file it was written to cover. That catches a hand-deleted entry,
a reordered block, a mangled header, an invented `!<path>` line and a
corrupted deny rule.

WHAT SURVIVES THE BASE MOVING. `base` changes identity every time
`.gitignore` is rewritten, and with it the identity of S: a claim of the
form "this control checks these 696 paths" is false the moment anyone
regenerates, so no such claim is made anywhere in this file. What survives
is the rule — the committed file equals derive(S), with S recomputed from
the base on the day — and the composition property: whatever S is, a
branch that adds a file without regenerating still merges green against one
that did regenerate.

THE SUBJECT is the base's own committed file, read from the blob id in its
`git ls-tree` record with `git cat-file blob`. Not `git show <c>:<path>`,
which puts a path the repository's own branch text can rename back into
revision grammar, and not the working copy, which after a regeneration
nobody has committed is a second answer to "the committed file"; that
disagreement is refused rather than resolved by preference. A checkout
whose history is truncated refuses too, loudly, because there the control
would silently become the strict form. A skip would be that failure
wearing a green.
"""
import difflib
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

_ARTIFACT = '.gitignore'
_REMEDY = 'python3 scripts/gen_gitignore.py .'
_SHALLOW_REMEDY = 'git fetch --unshallow'
_BLOCK = '# ─── '
_PREFIX = '!/'
_DIFF_LINES = 24
_NAMED_LINES = 5
_Verdict = namedtuple('_Verdict', 'status detail base scope')


class _Refusal(Exception):
    """No verdict was reached; the text names the cause and the remedy."""

    def __init__(self, detail, base=''):
        super().__init__(detail)
        self.base = base


# ── the control ────────────────────────────────────────────────────────

def _git_read(root, *args):
    """One git read against `root`, NUL-safe about the paths it names."""
    return subprocess.run(
        ['git', '-C', str(root), *args], capture_output=True, check=True,
        text=True, errors='surrogateescape').stdout


def _paths(text):
    """The NUL-separated paths git printed."""
    return {path for path in text.split('\0') if path}


def _normalise(text):
    """Fold CRLF, so a checkout that rewrote the endings is not a diff.

    The blob holds LF whatever platform wrote it and reading the same file
    as text on Windows hands back CRLF. The question is which paths are
    named, not which characters end the line.
    """
    return text.replace('\r\n', '\n')


def _generator():
    return _util.load(ROOT / 'scripts' / 'gen_gitignore.py',
                      'gen_gitignore_control')


def _named_path(line):
    """The path a `!<path>` FILE line names, or None for anything else.

    A directory re-open line ends in `/` and is not a path git ever
    tracked, so it is not one of the entries the reduction can remove.
    """
    if line.startswith(_PREFIX) and not line.endswith('/'):
        return line[len(_PREFIX):]
    return None


def _reduce(text, scope, stale):
    """The committed file with its excuses for the scope's absences removed.

    Two clauses, each half of what makes the control compose under merge
    order: a `!<path>` line whose path is `stale` — tracked at the base and
    not tracked now — goes, because a branch that drops a file without
    regenerating leaves that line behind and the default branch should not
    be made to answer for it; and a directory block left with no entry
    naming an in-scope path goes whole, header, re-opens and all, so a
    directory that has emptied out does not leave a header the derivation
    has not got. What does NOT go is the point of the control: a missing
    entry for a path in `scope`, a reordering, a mangled header, and a `!`
    line naming a path tracked neither at the base nor now all survive.
    """
    lines = text.split('\n')
    starts = [index for index, line in enumerate(lines)
              if line.startswith(_BLOCK)]
    if not starts:
        return text
    # The generator emits each block's blank separator WITH that block, so a
    # block's extent begins one line early. A header the file did not put a
    # blank before is left where it is: swallowing the line above it would
    # repair the malformation here and hide it from the comparison.
    begins = [start - 1 if start and lines[start - 1] == '' else start
              for start in starts]
    kept = list(lines[:begins[0]])
    for position, begin in enumerate(begins):
        end = (begins[position + 1] if position + 1 < len(begins)
               else len(lines))
        body = lines[begin:end]
        # The file's own final newline belongs to no block and is put back
        # whichever way the last block goes.
        tail = None
        if end == len(lines) and body and body[-1] == '':
            tail, body = body[-1], body[:-1]
        body = [line for line in body if _named_path(line) not in stale]
        if any(_named_path(line) in scope for line in body):
            kept.extend(body)
        if tail is not None:
            kept.append(tail)
    return '\n'.join(kept)


def _difference(reduced, expected, scope):
    """What the committed file and the derivation disagree about."""
    named = {_named_path(line) for line in reduced.split('\n')}
    missing = sorted(path for path in scope if path not in named)
    lines = list(difflib.unified_diff(
        reduced.split('\n'), expected.split('\n'),
        fromfile='committed', tofile='derive(in scope)', lineterm='',
        n=1))[2:]
    body = '\n'.join(lines[:_DIFF_LINES])
    if len(lines) > _DIFF_LINES:
        body += f'\n... and {len(lines) - _DIFF_LINES} more diff lines'
    if missing:
        shown = ', '.join(missing[:_NAMED_LINES])
        if len(missing) > _NAMED_LINES:
            shown += f' and {len(missing) - _NAMED_LINES} more'
        body += f'\nnot named, though in scope at the base: {shown}'
    return f'{body}\nrepair: {_REMEDY}'


def _committed_ignore(root, base):
    """The base's own committed file, read from the blob id it records."""
    record = _git_read(root, 'ls-tree', base, '--', _ARTIFACT).strip()
    if not record:
        raise _Refusal(
            f'no commit has ever written {_ARTIFACT}, so there is no '
            'artifact to compare and no base to scope against', base)
    fields = record.split()
    if len(fields) < 3 or fields[1] != 'blob':
        raise _Refusal(
            f'commit {base} records {_ARTIFACT} as {record!r}, which names '
            'no blob to read', base)
    return _normalise(_git_read(root, 'cat-file', 'blob', fields[2]))


def _base_commit(root):
    """The commit that last wrote the ignore file, or '' when there is none.

    A repository with no commits at all makes `git log` exit 128 and print
    nothing, and that is the same answer as "no commit has ever written
    .gitignore" rather than a git failure that wants a different message.
    """
    done = subprocess.run(
        ['git', '-C', str(root), 'log', '-1', '--format=%H', '--',
         _ARTIFACT], capture_output=True, check=False,
        text=True, errors='surrogateescape')
    return done.stdout.strip() if done.returncode == 0 else ''


def _git_directories(root):
    """The git directories git itself names, resolved against `root`.

    `<repo>/.git` is a FILE in a linked worktree and a DIRECTORY in a
    clone, so a control that opened the path it assumed would find no
    marker there and conclude the history is complete when it is not. The
    COMMON directory is the one that carries `shallow`, and asking git
    where its directories are is the only spelling right in both shapes.
    """
    for args in (('--absolute-git-dir',),
                 ('--path-format=absolute', '--git-common-dir')):
        named = _git_read(root, 'rev-parse', *args).strip()
        yield Path(named).resolve()


def _refuse_shallow(root):
    """Refuse a checkout whose history is truncated, or return None."""
    for directory in _git_directories(root):
        if (directory / 'shallow').is_file():
            return _Refusal(
                f'this checkout has truncated history: '
                f'{directory}/shallow exists, so `git log -1 -- .gitignore` '
                'answers HEAD, the scope collapses onto the paths tracked '
                'now, and this control would silently become the strict '
                f'comparison — a false red on exactly the merge the scoping '
                f'exists to survive. Run `{_SHALLOW_REMEDY}` and run again; '
                'the control refuses rather than falling through.')
    return None


def _refuse_dirty_copy(root, committed, base):
    """Refuse a working copy that is not the file the base commit holds."""
    path = Path(root) / _ARTIFACT
    if not path.is_file():
        raise _Refusal(
            f'the working tree holds no {_ARTIFACT}, so the repository this '
            'control was pointed at is not checked out', base)
    if _normalise(path.read_text(encoding='utf-8')) != committed:
        raise _Refusal(
            f'the working copy of {_ARTIFACT} is not the file commit {base} '
            'holds, so "the committed file" has two answers and this '
            'control will not pick one. Commit the regeneration, or '
            f'`git checkout -- {_ARTIFACT}` to put the committed file back.',
            base)


def _decide(root):
    """The verdict for `root`, or a refusal naming why there is none."""
    shallow = _refuse_shallow(root)
    if shallow is not None:
        raise shallow
    base = _base_commit(root)
    if not base:
        raise _Refusal(
            f'no commit has written {_ARTIFACT}, so there is no artifact to '
            'compare and no base to scope against')
    committed = _committed_ignore(root, base)
    _refuse_dirty_copy(root, committed, base)
    now = _paths(_git_read(root, 'ls-files', '-z'))
    if not now:
        raise _Refusal(
            'git ls-files returned no tracked path: the tracked set is '
            'unreadable rather than empty, and an empty set would leave '
            'this comparing the committed file against the derivation of '
            'nothing', base)
    at_base = _paths(_git_read(root, 'ls-tree', '-r', '--name-only', '-z', base))
    if not at_base:
        raise _Refusal(
            f'the commit {base} names no tracked path: its tree is '
            'unreadable rather than empty, and an empty set would leave '
            'this comparing the committed file against the derivation of '
            'nothing', base)
    scope = now & at_base
    reduced = _reduce(committed, scope, at_base - now)
    expected = _generator().derive(sorted(scope))
    if reduced == expected:
        return _Verdict('green', '', base, scope)
    return _Verdict('red', _difference(reduced, expected, scope), base,
                    scope)


def _control(root):
    """Run the control against the repository at `root`.

    `root` is resolved first: a temp root is a symlink on macOS and a
    short name on Windows, and an unresolved join never matches.
    """
    root = Path(root).resolve()
    try:
        return _decide(root)
    except _Refusal as refusal:
        return _Verdict('refused', str(refusal), refusal.base, frozenset())
    except (OSError, subprocess.SubprocessError) as failure:
        return _Verdict('refused', _read_failure(failure), '',
                        frozenset())


def _read_failure(failure):
    """Name the git read that failed, so a refusal is attributable."""
    detail = getattr(failure, 'stderr', '') or ''
    return (f'a git read failed, so no comparison was made: {failure}'
            + (f'\n{detail.strip()}' if detail.strip() else ''))


# ── fixture repositories ───────────────────────────────────────────────

def _git_at(repo, *args):
    return _git_read(repo, *args)


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
        assert _generator().main(repo) == 0, 'the generator refused its tree'
        _git_at(repo, 'add', '-f', _ARTIFACT)
    _git_at(repo, 'commit', '-q', '-m', message)


def _fixture_repo(directory, files):
    """A throwaway repository whose ignore file the real generator wrote."""
    repo = Path(directory)
    repo.mkdir(parents=True)
    _git_at(repo, 'init', '-q')
    _git_at(repo, 'config', 'user.email', 'control@example.invalid')
    _git_at(repo, 'config', 'user.name', 'Control')
    for name in files:
        _write(repo, name)
    _git_at(repo, 'add', '-f', *files)
    _git_at(repo, 'commit', '-q', '-m', 'the tracked files')
    _regenerate(repo, 'name every tracked file')
    return repo


def _head(repo):
    return _git_at(repo, 'rev-parse', 'HEAD').strip()


def _commit_ignore(repo, text, message):
    """Write the ignore file and commit it, as a merge resolution would."""
    (Path(repo) / _ARTIFACT).write_text(text, encoding='utf-8')
    _git_at(repo, 'add', '-f', _ARTIFACT)
    _git_at(repo, 'commit', '-q', '-m', message)


def _edit_ignore(repo, replace, message):
    """Edit the committed file in place and commit the edit."""
    text = (Path(repo) / _ARTIFACT).read_text(encoding='utf-8')
    assert replace[0] in text, (
        f'the fixture text to replace is absent: {replace[0]!r}')
    _commit_ignore(repo, text.replace(*replace), message)


# ── the repository this suite runs in ───────────────────────────────────

def test_the_committed_ignore_file_is_what_the_generator_derives(tmp):
    del tmp
    verdict = _control(ROOT)
    assert verdict.status == 'green', verdict.detail
    assert verdict.base, 'the control reached no base commit at all'
    assert verdict.base != _head(ROOT), (
        'the control scoped against HEAD, which is the strict form it '
        'exists to scope away')


def test_the_base_itself_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    verdict = _control(repo)
    assert verdict.status == 'green', verdict.detail
    assert verdict.scope, verdict


# ── the merges the scoping exists for ──────────────────────────────────

def test_a_file_added_after_the_base_and_never_regenerated_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    fork = _head(repo)
    _git_at(repo, 'checkout', '-q', '-b', 'regenerated')
    _write(repo, 'd.py')
    _git_at(repo, 'add', '-f', 'd.py')
    _git_at(repo, 'commit', '-q', '-m', 'add d')
    _regenerate(repo, 'name d as well')
    _git_at(repo, 'checkout', '-q', '-b', 'forgotten', fork)
    _write(repo, 'e.py')
    _git_at(repo, 'add', '-f', 'e.py')
    _git_at(repo, 'commit', '-q', '-m', 'add e and do not regenerate')
    _git_at(repo, 'merge', '-q', '--no-edit', 'regenerated')

    verdict = _control(repo)
    assert verdict.status == 'green', verdict.detail
    # The base has to be neither the fixture's fork point nor its head, or
    # swapping the base argument for HEAD would still pass and the fixture
    # would prove nothing about which commit the control reads.
    assert verdict.base not in (fork, _head(repo)), verdict.base
    assert 'd.py' in verdict.scope, sorted(verdict.scope)
    assert 'e.py' not in verdict.scope, sorted(verdict.scope)


def test_two_branches_that_both_regenerated_merge_green(tmp):
    names = tuple(f'm{index:02d}.txt' for index in range(1, 9))
    repo = _fixture_repo(Path(tmp) / 'repo', names)
    fork = _head(repo)
    for branch, added in (('left', 'aaa.txt'), ('right', 'zzz.txt')):
        _git_at(repo, 'checkout', '-q', '-b', branch, fork)
        _write(repo, added)
        _git_at(repo, 'add', '-f', added)
        _git_at(repo, 'commit', '-q', '-m', f'add {added}')
        _regenerate(repo, f'name {added} as well')
    _git_at(repo, 'merge', '-q', '--no-edit', 'left')
    _git_at(repo, 'merge', '-q', '--no-edit', 'right')

    verdict = _control(repo)
    assert verdict.status == 'green', verdict.detail
    assert {'aaa.txt', 'zzz.txt'} <= verdict.scope, sorted(verdict.scope)


def test_a_file_removed_after_the_base_is_green(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    fork = _head(repo)
    _git_at(repo, 'checkout', '-q', '-b', 'dropped')
    _git_at(repo, 'rm', '-q', 'c.py')
    _git_at(repo, 'commit', '-q', '-m', 'drop c and do not regenerate')
    _git_at(repo, 'checkout', '-q', '-b', 'touched', fork)
    _write(repo, 'a.py', 'edited\n')
    _git_at(repo, 'add', '-f', 'a.py')
    _git_at(repo, 'commit', '-q', '-m', 'edit a')
    _git_at(repo, 'merge', '-q', '--no-edit', 'dropped')

    verdict = _control(repo)
    assert verdict.status == 'green', verdict.detail
    assert 'c.py' not in verdict.scope, sorted(verdict.scope)


def test_a_directory_emptied_after_the_base_drops_its_whole_block(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('keep.py', 'doomed/old.txt'))
    fork = _head(repo)
    _git_at(repo, 'checkout', '-q', '-b', 'emptied')
    _git_at(repo, 'rm', '-q', 'doomed/old.txt')
    _git_at(repo, 'commit', '-q', '-m', 'empty the directory, no regenerate')
    _git_at(repo, 'checkout', '-q', '-b', 'grown', fork)
    _write(repo, 'fresh/new.txt')
    _git_at(repo, 'add', '-f', 'fresh/new.txt')
    _git_at(repo, 'commit', '-q', '-m', 'add a wholly new directory')
    _regenerate(repo, 'name the new directory')
    _git_at(repo, 'merge', '-q', '--no-edit', 'emptied')

    verdict = _control(repo)
    assert verdict.status == 'green', verdict.detail
    assert 'doomed/old.txt' not in verdict.scope, sorted(verdict.scope)
    assert 'fresh/new.txt' in verdict.scope, sorted(verdict.scope)


# ── the defects the control exists to catch ────────────────────────────

def test_an_entry_deleted_for_a_still_tracked_file_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _edit_ignore(repo, ('!/b.py\n', ''), 'hand-delete the b entry')
    verdict = _control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'b.py' in verdict.detail, verdict.detail
    assert _REMEDY in verdict.detail, verdict.detail


def test_a_reordered_mangled_or_corrupted_block_is_red(tmp):
    cases = (('reordered', '!/a.py\n!/b.py\n', '!/b.py\n!/a.py\n'),
             ('mangled header', _BLOCK + 'root ───', _BLOCK + 'root ---'),
             ('no deny rule', '\n*\n', '\n'))
    for name, before, after in cases:
        repo = _fixture_repo(Path(tmp) / name, ('a.py', 'b.py', 'c.py'))
        _edit_ignore(repo, (before, after), f'{name} by hand')
        verdict = _control(repo)
        assert verdict.status == 'red', f'{name}: {verdict.detail}'


def test_an_invented_entry_for_an_untracked_path_is_red(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    _edit_ignore(repo, ('!/c.py\n', '!/c.py\n!/ghost.py\n'),
                 'invent an entry for a path nothing tracks')
    verdict = _control(repo)
    assert verdict.status == 'red', verdict.detail
    assert 'ghost.py' in verdict.detail, verdict.detail


def test_an_invented_directory_block_is_dropped_as_empty(tmp):
    """The block clause is broader than the entry clause, and says so.

    A `!<path>` line inside a real block naming a path tracked nowhere is a
    difference the control reports. A whole invented DIRECTORY block is
    dropped, because the block clause drops any block left with no in-scope
    entry and an invented block has none. That is a consequence of the rule
    as specified rather than a decision taken here, and this test makes it
    a property of the control instead of an accident of it.
    """
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    invented = ('\n# ─── ghostdir ───\n!/ghostdir/\n'
                '!/ghostdir/nothing.py\n')
    _edit_ignore(repo, ('\n# ─── root ───\n',
                        invented + '\n# ─── root ───\n'),
                 'invent a whole directory block')
    verdict = _control(repo)
    assert verdict.status == 'green', verdict.detail


# ── the refusals ───────────────────────────────────────────────────────

def test_a_checkout_with_truncated_history_refuses_loudly(tmp):
    """Both shapes a truncated history arrives in, and both must refuse.

    The second is the one that matters: `.git` is a FILE in a linked
    worktree, so a control that opened `<repo>/.git/shallow` finds nothing
    there and concludes the history is complete — a fail-open in the one
    place this control has to fail closed. The marker lives in the COMMON
    directory, which is why the control asks git where its directories
    are rather than assuming a path.
    """
    source = _fixture_repo(Path(tmp) / 'source', ('a.py', 'b.py'))
    clone = Path(tmp) / 'shallow'
    # file:// is what makes --depth bite; a local path is hardlinked and
    # cloned in full whatever the flag says.
    subprocess.run(
        ['git', 'clone', '--quiet', '--depth', '1', '--no-local',
         f'file://{source}', str(clone)],
        check=True, capture_output=True)
    verdict = _control(clone)
    assert verdict.status == 'refused', verdict
    assert 'shallow' in verdict.detail, verdict.detail
    assert _SHALLOW_REMEDY in verdict.detail, verdict.detail

    linked = Path(tmp) / 'linked'
    _git_at(clone, 'worktree', 'add', '--quiet', '-b', 'side', str(linked))
    assert (linked / '.git').is_file(), 'the fixture is not a linked worktree'
    linked_verdict = _control(linked)
    assert linked_verdict.status == 'refused', linked_verdict
    assert 'shallow' in linked_verdict.detail, linked_verdict.detail


def test_a_repository_with_no_committed_ignore_file_refuses(tmp):
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    _git_at(repo, 'init', '-q')
    _write(repo, 'a.py')
    _git_at(repo, 'add', '-f', 'a.py')
    verdict = _control(repo)
    assert verdict.status == 'refused', verdict
    assert 'no commit has' in verdict.detail, verdict.detail


def test_an_unreadable_base_tree_refuses_rather_than_comparing_nothing(tmp):
    """A tree that reads as empty is a refusal, not a clean comparison."""
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    real = globals()['_git_read']

    def unreadable(root, *args):
        if 'ls-tree' in args and '-r' in args:
            return ''
        return real(root, *args)

    globals()['_git_read'] = unreadable
    try:
        verdict = _control(repo)
    finally:
        globals()['_git_read'] = real
    assert verdict.status == 'refused', verdict
    assert 'nothing' in verdict.detail, verdict.detail


def test_a_failing_git_read_refuses_naming_the_command(tmp):
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py'))
    real = globals()['_git_read']

    def failing(root, *args):
        if 'ls-tree' in args and '-r' in args:
            raise subprocess.CalledProcessError(
                128, ['git', '-C', str(root), *args], stderr='fatal: bad')
        return real(root, *args)

    globals()['_git_read'] = failing
    try:
        verdict = _control(repo)
    finally:
        globals()['_git_read'] = real
    assert verdict.status == 'refused', verdict
    assert 'fatal: bad' in verdict.detail, verdict.detail


def test_a_working_copy_that_is_not_the_committed_file_refuses(tmp):
    """Two answers to "the committed file" is a refusal, not a preference."""
    repo = _fixture_repo(Path(tmp) / 'repo', ('a.py', 'b.py', 'c.py'))
    (Path(repo) / _ARTIFACT).write_text(
        '*\n!/a.py\n', encoding='utf-8')
    verdict = _control(repo)
    assert verdict.status == 'refused', verdict
    assert 'working' in verdict.detail, verdict.detail


# ── the merges that resolve into a decision ───────────────────────────

def test_a_conflicted_merge_resolution_is_judged_from_the_merge(tmp):
    """A merge commit that resolved the file is itself the last writer.

    The resolution below is a value NEITHER parent had, which is the case
    the design's own reasoning could be wrong about: git's path-limited
    history simplification might or might not report the merge as a writer
    of the file. The fixture establishes it by execution, and the docstring
    follows the fixture rather than the reasoning.
    """
    names = tuple(f'm{index:02d}.txt' for index in range(1, 9))
    repo = _fixture_repo(Path(tmp) / 'repo', names)
    fork = _head(repo)
    # Two new files whose entries land on either side of the same line, so
    # the merge conflicts instead of composing — which is the point: the
    # merge commit is then the last writer of the file.
    for branch, added in (('left', 'm04a.txt'), ('right', 'm04b.txt')):
        _git_at(repo, 'checkout', '-q', '-b', branch, fork)
        _write(repo, added, f'added on {branch}\n')
        _git_at(repo, 'add', '-f', added)
        _git_at(repo, 'commit', '-q', '-m', f'add {added}')
        _regenerate(repo, f'name {added} as well')
    _git_at(repo, 'checkout', '-q', 'left')
    conflicted = subprocess.run(
        ['git', '-C', str(repo), 'merge', '--no-edit', 'right'],
        capture_output=True, text=True, check=False)
    assert conflicted.returncode != 0, 'the fixture did not conflict'
    assert 'CONFLICT' in conflicted.stdout, conflicted.stdout
    # Resolve to a value NEITHER parent had: the conflicted hunk replaced
    # by one entry naming a path nothing tracks. The file is not derived
    # here — `git ls-files` reports a conflicted path once per stage, and
    # deriving from that is how a merge gets a triplicated entry.
    text = (Path(repo) / _ARTIFACT).read_text(encoding='utf-8')
    cut = text.index('<<<<<<<')
    resumed = text.index('\n', text.index('>>>>>>>')) + 1
    _commit_ignore(repo,
                   text[:cut] + '!/neither-parent.txt\n' + text[resumed:],
                   'resolve the conflict by hand')

    verdict = _control(repo)
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
