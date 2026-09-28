#!/usr/bin/env python3
"""The control `tests/test_gitignore_control.py` drives.

This module is the control, not a suite: the fixtures and the verdicts
they assert live in the suite beside it, and the rule is stated here.

The committed `.gitignore` must be what the generator derives, for the
files it was written to cover.

`.gitignore` denies by default and names every tracked file back, and
`scripts/gen_gitignore.py` writes it. Nothing read the committed file and
compared it against what the generator derives, so it was correct by the
diligence of whoever last ran the script: issue 458 recorded five tracked
`tests/_jsroute*` helpers landing across four commits, none of which
regenerated it.

THE SCOPE, AND WHY THERE IS ONE. An exhaustive "committed == derived"
control cannot be kept exact by two independent branches, so it fails on
the MERGE, on the default branch, where no branch is left to fix it — the
sibling control on `.github/reserved-test-names.json` turned the default
branch red three times on 2026-09-27 exactly that way. So the comparison
is scoped, and the scope comes from git at control time rather than from
the committed file, because a `!` line records no producer and no base:

    base    the commit that last wrote .gitignore
            (git log -1 --format=%H -- .gitignore)
    T       the tracked paths now       (git ls-files -z)
    T_base  the tracked paths at base   (git ls-tree -r --name-only -z base)
    S       T and T_base                in scope
    fresh   T only                      arrived after the file was written
    stale   T_base only                 gone since it was written

    reduce  the committed file, minus every `!<path>` line whose path is
            stale, minus any directory block left with no in-scope entry
    verdict reduce == derive(sorted(S))

The two sets are enumerated by DIFFERENT mechanisms on purpose, and each
by the one the design assumes. `T` comes from `git ls-files`, which reads
the INDEX, because that is exactly the call `scripts/gen_gitignore.py`
derives from: take `T` from `ls-tree HEAD` instead and the scope and the
derivation are enumerated by different means, which agree on a clean
checkout at HEAD and part company the moment the index and the commit do.
`T_base` comes from `git ls-tree -r` at the base commit, which is the one
place reading a commit is correct.

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

The one asymmetry that falls out of those clauses, stated because it is a
property of the rule rather than a decision taken here: a `!<path>` line
inside a real block naming a path tracked at neither base nor now is
reported, while a whole invented DIRECTORY block is dropped as empty. Both
are pinned by the suite.

WHAT IT DOES NOT DETECT, stated plainly: a tracked file that arrives with
no entry at all. Scoping the absent direction is what buys composition and
composition was the binding requirement. The routes into the gap are
`git add -f` of an unlisted path — a plain `git add` IS refused by the deny
rule and `-f` bypasses the refusal completely, which is the route issue
458 took and on which nothing forces regeneration — and `git mv` or a
rename, where the old path goes stale and is reduced away while the new
path's absence is allowed. This was demonstrated rather than only
described: a throwaway clone of this repository, one `git add -f` of an
unlisted helper, one commit with no regeneration, and this control
answered `green` while naming the file zero times. The file rots until
some unrelated regeneration absorbs it. The deny-by-default rule does NOT
cover this: the file IS tracked, `git status` shows nothing to commit,
and the only thing that would have noticed is a control scoped away from
it. So the one-line honest description is: the committed file is exactly
what the generator derives, for every file it was written to cover. That
catches a hand-deleted entry, a reordered block, a mangled header, an
invented `!<path>` line and a corrupted deny rule.

WHAT SURVIVES THE BASE MOVING. `base` changes identity every time
`.gitignore` is rewritten, and with it the identity of S: a claim of the
form "this control checks these 696 paths" is false the moment anyone
regenerates, so no such claim is made anywhere in this tree. What
survives is the rule — the committed file equals derive(S), with S
recomputed from the base on the day — and the composition property:
whatever S is, a branch that adds a file without regenerating still merges
green against one that did regenerate. Every figure the verdict prints is
computed over S and over the two sets derived from it, never over T.

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

ARTIFACT = '.gitignore'
REMEDY = 'python3 scripts/gen_gitignore.py .'
SHALLOW_REMEDY = 'git fetch --unshallow'
BLOCK = '# ─── '
PREFIX = '!/'
DIFF_LINES = 24
NAMED_LINES = 5
Verdict = namedtuple('Verdict', 'status detail base scope')


class Refusal(Exception):
    """No verdict was reached; the text names the cause and the remedy."""

    def __init__(self, detail, base=''):
        super().__init__(detail)
        self.base = base


def git_read(root, *args):
    """One git read against `root`, NUL-safe about the paths it names."""
    return subprocess.run(
        ['git', '-C', str(root), *args], capture_output=True, check=True,
        text=True, errors='surrogateescape').stdout


def paths(text):
    """The NUL-separated paths git printed."""
    return {path for path in text.split('\0') if path}


def normalise(text):
    """Fold CRLF, so a checkout that rewrote the endings is not a diff.

    The blob holds LF whatever platform wrote it and Windows hands the same
    file back as CRLF. The question is which paths are named.
    """
    return text.replace('\r\n', '\n')


def generator():
    return _util.load(ROOT / 'scripts' / 'gen_gitignore.py',
                      'gen_gitignore_control')


def named_path(line):
    """The path a `!<path>` FILE line names, or None for anything else.

    A directory re-open line ends in `/` and is not a path git ever
    tracked, so it is not one of the entries the reduction can remove.
    """
    if line.startswith(PREFIX) and not line.endswith('/'):
        return line[len(PREFIX):]
    return None


def reduce_committed(text, scope, stale):
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
              if line.startswith(BLOCK)]
    if not starts:
        return text
    # The generator emits each block's blank separator WITH that block, so
    # a block's extent begins one line early. A header the file put no
    # blank before keeps that line: swallowing it would repair the
    # malformation here and hide it from the comparison.
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
        body = [line for line in body if named_path(line) not in stale]
        if any(named_path(line) in scope for line in body):
            kept.extend(body)
        if tail is not None:
            kept.append(tail)
    return '\n'.join(kept)


def difference(reduced, expected, scope):
    """What the committed file and the derivation disagree about."""
    named = {named_path(line) for line in reduced.split('\n')}
    missing = sorted(path for path in scope if path not in named)
    lines = list(difflib.unified_diff(
        reduced.split('\n'), expected.split('\n'),
        fromfile='committed', tofile='derive(in scope)', lineterm='',
        n=1))[2:]
    body = '\n'.join(lines[:DIFF_LINES])
    if len(lines) > DIFF_LINES:
        body += f'\n... and {len(lines) - DIFF_LINES} more diff lines'
    if missing:
        body += ('\nnot named, though in scope at the base: '
                 + ', '.join(missing[:NAMED_LINES]))
    return f'{body}\nrepair: {REMEDY}'


def counts(scope, fresh, stale):
    """The sets this verdict is about, each number named for its set.

    Every figure is computed over the set the comparison used, and `scope`
    IS what the derivation ran on, so the number the derivation saw and the
    number the comparison saw cannot drift apart. A count taken over the
    tracked set instead would describe a larger set than the one any
    difference was found in, which is a green that counted the wrong thing;
    the suite pins these three figures against a fixture where the sets
    differ.
    """
    return (f'in scope {len(scope)} — the paths tracked at both the base '
            f'and the head, and exactly what the derivation ran on; '
            f'{len(fresh)} arrived after the base and may be absent; '
            f'{len(stale)} gone since and may still be named')


def base_commit(root):
    """The commit that last wrote the ignore file, or '' when there is none.

    A repository with no commits makes `git log` exit 128 and print
    nothing, and that is the same answer as "no commit has ever written
    .gitignore" rather than a failure wanting a different message.
    """
    done = subprocess.run(
        ['git', '-C', str(root), 'log', '-1', '--format=%H', '--',
         ARTIFACT], capture_output=True, check=False,
        text=True, errors='surrogateescape')
    return done.stdout.strip() if done.returncode == 0 else ''


def git_directories(root):
    """The git directories git itself names, resolved against `root`.

    `<repo>/.git` is a FILE in a linked worktree and a DIRECTORY in a
    clone, so a control that opened the path it assumed would find no
    marker there and conclude the history is complete when it is not. The
    COMMON directory is the one that carries `shallow`, and asking git
    where its directories are is the only spelling right in both shapes.
    """
    for args in (('--absolute-git-dir',),
                 ('--path-format=absolute', '--git-common-dir')):
        named = git_read(root, 'rev-parse', *args).strip()
        yield Path(named).resolve()


def committed_ignore(root, base):
    """The base's own committed file, read from the blob id it records.

    `git show <commit>:.gitignore` would parse a path out of revision
    grammar the repository's own branch text can rename, and a path is the
    cheapest thing to preserve across a refactor and the least likely to be
    edited. The blob id comes out of the `ls-tree` record already resolved.
    """
    record = git_read(root, 'ls-tree', base, '--', ARTIFACT).strip()
    if not record:
        raise Refusal(
            f'commit {base} holds no {ARTIFACT} blob, so the artifact this '
            'control exists to check is not in it', base)
    fields = record.split()
    if len(fields) < 3 or fields[1] != 'blob':
        raise Refusal(
            f'commit {base} records {ARTIFACT} as {record!r}, which names '
            'no blob to read', base)
    return normalise(git_read(root, 'cat-file', 'blob', fields[2]))


def artifact_base(root):
    """The one seam onto the base-resolution layer another seat owns.

    Issue 1266 owns "the commit that last wrote the artifact, and its
    tree", together with the truncated-history refusal, and a second copy
    of any of it is a filed defect class in this repository (issues 1187
    and 1260). Every base-side git read this control makes goes through
    this one function, and `decide` calls it once. Until the shipped
    interface lands, this stands in for it; replacing this body with the
    shipped import is a one-line change here and no fixture moves.

    Returns (base, tracked-at-base, the committed file at base).
    """
    for directory in git_directories(root):
        if (directory / 'shallow').is_file():
            raise Refusal(
                f'this checkout has truncated history: {directory}/shallow '
                'exists, so `git log -1 -- .gitignore` answers HEAD, the '
                'scope collapses onto the paths tracked now, and this '
                'control would silently become the strict comparison — a '
                'false red on exactly the merge the scoping exists to '
                f'survive. Run `{SHALLOW_REMEDY}` and run again; the '
                'control refuses rather than falling through.')
    base = base_commit(root)
    if not base:
        raise Refusal(
            f'no commit has written {ARTIFACT}, so there is no artifact to '
            'compare and no base to scope against')
    at_base = paths(
        git_read(root, 'ls-tree', '-r', '--name-only', '-z', base))
    if not at_base:
        raise Refusal(
            f'the commit {base} names no tracked path: its tree is '
            'unreadable rather than empty, and an empty set would leave '
            'this comparing the committed file against the derivation of '
            'nothing', base)
    return base, at_base, committed_ignore(root, base)


def refuse_dirty_copy(root, committed, base):
    """Refuse a working copy that is not the file the base commit holds."""
    path = Path(root) / ARTIFACT
    if not path.is_file():
        raise Refusal(
            f'the working tree holds no {ARTIFACT}, so the repository this '
            'control was pointed at is not checked out', base)
    if normalise(path.read_text(encoding='utf-8')) != committed:
        raise Refusal(
            f'the working copy of {ARTIFACT} is not the file commit {base} '
            'holds, so "the committed file" has two answers and this '
            'control will not pick one. Commit the regeneration, or '
            f'`git checkout -- {ARTIFACT}` to put the committed file back.',
            base)


def read_failure(failure):
    """Name the git read that failed, so a refusal is attributable."""
    detail = getattr(failure, 'stderr', '') or ''
    return (f'a git read failed, so no comparison was made: {failure}'
            + (f'\n{detail.strip()}' if detail.strip() else ''))


def decide(root):
    """The verdict for `root`, or a refusal naming why there is none."""
    base, at_base, committed = artifact_base(root)
    refuse_dirty_copy(root, committed, base)
    now = paths(git_read(root, 'ls-files', '-z'))
    if not now:
        raise Refusal(
            'git ls-files returned no tracked path: the tracked set is '
            'unreadable rather than empty, and an empty set would leave '
            'this comparing the committed file against the derivation of '
            'nothing', base)
    scope = now & at_base
    fresh, stale = now - at_base, at_base - now
    summary = counts(scope, fresh, stale)
    reduced = reduce_committed(committed, scope, stale)
    # `derive` orders its input itself; the sort here is belt and braces,
    # and it MASKS that property here. Dropping the sort inside `derive`
    # leaves every fixture in this suite green — the pin for it is
    # test_gitignore_generator.py's literal block layout, which is why that
    # suite is not optional reading for anyone changing the rendering.
    expected = generator().derive(sorted(scope))
    if reduced == expected:
        return Verdict('green', summary, base, scope)
    return Verdict('red',
                   f'{summary}\n{difference(reduced, expected, scope)}',
                   base, scope)


def control(root):
    """Run the control against the repository at `root`.

    `root` is resolved first: a temp root is a symlink on macOS and a
    short name on Windows, and an unresolved join never matches.
    """
    root = Path(root).resolve()
    try:
        return decide(root)
    except Refusal as refusal:
        return Verdict('refused', str(refusal), refusal.base, frozenset())
    except (OSError, subprocess.SubprocessError) as failure:
        return Verdict('refused', read_failure(failure), '', frozenset())
