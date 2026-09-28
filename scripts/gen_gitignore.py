#!/usr/bin/env python3
"""Rewrite .gitignore as deny-by-default, naming every tracked FILE.

Per-extension globs are a compromise worth avoiding: a rule like
`!/examples/*.js` re-admits any script dropped in that directory, and the same
shape re-admits a note wherever Markdown legitimately lives. Naming files
individually has no such gap, and the cost is the intended one: adding a file
to the release is a deliberate line.

Adding a new file is therefore two steps, because the ignore file denies it
until it is named:

    git add -f path/to/new_file.py
    python3 scripts/gen_gitignore.py .

The refusal on the first `git add` is the feature, not an obstacle -- it is the
same refusal that stops a stray note or a build product from being taken.
"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

GIT_TIMEOUT = 30


def _log_safe(value):
    """Render a caller- or filesystem-derived value safe for an output line.

    The repository argument arrives through argv's surrogateescape, so a path
    containing an undecodable byte holds U+DC80..U+DCFF; printing it raises
    UnicodeEncodeError wherever the stream's errors are strict — the success
    line died that way AFTER writing a correct .gitignore. Exception text
    carries the same bytes via command lines and paths. backslashreplace
    escapes them, so no diagnostic can kill the run.

    Every step of the rendering is guarded for the same reason: str() raises
    on a conversion-limited huge int or an exception object whose __str__
    fails, and a str subclass can reach the encode step carrying an encode()
    that raises or a decode() that returns a non-string. The result leaves
    only when its type is exactly str — never a subclass — because the
    caller's interpolation must not see a caller-controlled __format__. The
    fallback is a fixed ASCII string that never interpolates the object that
    just failed — interpolating it would reopen the hole. Kept
    behavior-identical to daedalus_bridge.log_safe.log_safe; this standalone
    script must run
    without the repository root on its import path. except Exception is
    deliberate: KeyboardInterrupt and SystemExit still propagate.
    """
    try:
        rendered = (str(value).encode('utf-8', 'backslashreplace')
                    .decode('utf-8'))
    except Exception:
        return '<unprintable value>'
    # Exact type, not isinstance: a str subclass is itself the hostile shape.
    if type(rendered) is not str:  # pylint: disable=unidiomatic-typecheck
        return '<unprintable value>'
    return rendered


HEAD = ("""# Deny by default; nothing is tracked unless a rule below names it.
#
# The usual shape of this file is the opposite -- list the junk, let everything
# else through -- and that shape has two failures this one does not. A new kind
# of file matches no rule and is one reflexive `git add` from the history. And
# the deny list is itself published: every path it names announces that such a
# file exists in the working tree, so naming a private file in order to exclude
# it still tells the reader that file exists.
#
# Inverted, a file nobody named does not get committed, and this file describes
# only what ships.
#
# The keep list names FILES, not extensions. This repository keeps its two
# Markdown documents at the root; a `!/**/*.md` rule would also re-admit any
# note dropped into a different directory. Naming files individually """
        """has no gap.
#
# Git will not look inside a directory it has ignored, so each directory below
# is re-opened before its files are named back.
#
# Regenerate after adding files:
#   python3 <this script's path> <repo>
#
# Forgetting to regenerate means the new file is simply not tracked, which
# `git status` shows as nothing to commit -- check that what you added appears
# in the commit.

*
""")


def _git_failure(repo, command, failure):
    print(f'{_log_safe(repo)}: FAIL — git {command} failed:\n'
          f'{_log_safe(failure)}', file=sys.stderr)
    return 1


def _check_ignore(root, tracked):
    """Run `git check-ignore` over the tracked paths, or return the failure."""
    try:
        return subprocess.run(
            ['git', '-C', str(root), 'check-ignore', '-z', '--no-index',
             '--stdin'],
            input='\0'.join(tracked), capture_output=True, text=True,
            timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError,
            UnicodeDecodeError) as failure:
        return failure


def derive(tracked):
    """The ignore-file text for exactly the tracked paths handed in.

    Pure, and that is the whole contract: it launches no process, reads no
    file and consults no clock, so the only state it consumes is the
    iterable it is given, and it orders that itself rather than trusting the
    caller's order.

    Two callers need this text and there is one of it. `main` writes what
    this returns, and the committed-file control in
    tests/_gitignore_control.py derives the side it compares the
    committed file against by calling this — its suite is
    tests/test_gitignore_control.py — so a change to the rendering
    moves both at once instead of leaving a second copy to rot.

    The input is the whole set of tracked paths and not a verdict about
    which of them ship, and no path is named that was not handed in: the
    control hands this every path `git ls-files` reports, so nothing can
    be absent from the derivation by construction. The deny rule is not
    the caller's to supply either — the `*` below is part of this
    function's own output, which is what makes the rule checkable
    independently of anything derived from this same text.
    """
    by_dir = defaultdict(list)
    for path in sorted(tracked):
        by_dir[path.rsplit('/', 1)[0] if '/' in path else ''].append(path)

    out = [HEAD]
    for directory in sorted(by_dir):
        out.append('')
        out.append(f'# ─── {directory or "root"} ───')
        if directory:
            parts = directory.split('/')
            for depth in range(1, len(parts) + 1):
                out.append('!/' + '/'.join(parts[:depth]) + '/')
        out += [f'!/{path}' for path in by_dir[directory]]
    return '\n'.join(out) + '\n'


def main(repo):
    root = Path(repo)
    shown = _log_safe(repo)
    try:
        listed = subprocess.run(
            ['git', '-C', str(root), 'ls-files', '-z'], capture_output=True,
            text=True, check=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError,
            UnicodeDecodeError) as failure:
        return _git_failure(repo, 'ls-files', failure)
    # -z + NUL split: without it a tracked path containing a space arrives as
    # two tokens, and the generator would name and postcondition-check the
    # FRAGMENTS while the real file stayed ignored — a fail-open success.
    # derive() orders these itself, so this list is not sorted here.
    tracked = [path for path in listed.stdout.split('\0') if path]

    (root / '.gitignore').write_text(derive(tracked), encoding='utf-8')

    ignored = _check_ignore(root, tracked)
    if isinstance(ignored, BaseException):
        return _git_failure(repo, 'check-ignore', ignored)
    if ignored.returncode not in (0, 1):
        detail = ignored.stderr.strip()
        suffix = f':\n{_log_safe(detail)}' if detail else ''
        print(f'{shown}: FAIL — git check-ignore failed{suffix}',
              file=sys.stderr)
        return 1
    matched = '\n'.join(path for path in ignored.stdout.split('\0') if path)
    if ignored.returncode == 0:
        if matched:
            print(f'{shown}: FAIL — tracked files ignored:\n'
                  f'{_log_safe(matched)}')
        else:
            print(f'{shown}: FAIL — git check-ignore reported matches without '
                  'naming paths', file=sys.stderr)
        return 1
    if matched:
        print(f'{shown}: FAIL — git check-ignore reported no matches '
              'but named '
              f'paths:\n{_log_safe(matched)}', file=sys.stderr)
        return 1
    print(f'{shown}: ok — {len(tracked)} tracked files named, none ignored')
    return 0


# One spelling, two callers, so the help line and the no-argument line
# cannot drift — that is true by construction, not by a control. What
# tests/test_gitignore_generator.py pins is the bare invocation's exit
# status, its stream, and the word `usage`; a rewritten string here still
# passes every assertion in the tree.
USAGE = 'usage: {script} <repo> [<repo> ...]'
HELP_FLAGS = ('--help', '-h')


def _run(argv):
    """The script's entry point, so a flag is never a repository path.

    The repository is this script's first POSITIONAL argument, so before
    it meant that `--help` was passed to `git -C` and the failure reported
    was git's, naming a subcommand nobody asked to run (#1301). A flag
    that looks like a flag is a request for the usage line, not a path.
    Two flags, so no parser: an argument is a help flag or it is a repo.
    """
    if any(arg in HELP_FLAGS for arg in argv):
        print(USAGE.format(script=sys.argv[0]))
        return 0
    if not argv:
        print(USAGE.format(script=sys.argv[0]), file=sys.stderr)
        return 2
    return max(main(repo) for repo in argv)


if __name__ == '__main__':
    sys.exit(_run(sys.argv[1:]))
