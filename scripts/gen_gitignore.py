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
        rendered = str(value).encode('utf-8', 'backslashreplace').decode('utf-8')
    except Exception:
        return '<unprintable value>'
    # Exact type, not isinstance: a str subclass is itself the hostile shape.
    if type(rendered) is not str:  # pylint: disable=unidiomatic-typecheck
        return '<unprintable value>'
    return rendered


HEAD = """# Deny by default; nothing is tracked unless a rule below names it.
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
# note dropped into a different directory. Naming files individually has no gap.
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
"""


def _git_failure(repo, command, failure):
    print(f'{_log_safe(repo)}: FAIL — git {command} failed:\n'
          f'{_log_safe(failure)}', file=sys.stderr)
    return 1


def main(repo):
    root = Path(repo)
    shown = _log_safe(repo)
    try:
        listed = subprocess.run(
            ['git', '-C', str(root), 'ls-files', '-z'], capture_output=True,
            text=True, check=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as failure:
        return _git_failure(repo, 'ls-files', failure)
    # -z + NUL split: without it a tracked path containing a space arrives as
    # two tokens, and the generator would name and postcondition-check the
    # FRAGMENTS while the real file stayed ignored — a fail-open success.
    tracked = sorted(path for path in listed.stdout.split('\0') if path)
    by_dir = defaultdict(list)
    for path in tracked:
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
    (root / '.gitignore').write_text('\n'.join(out) + '\n', encoding='utf-8')

    try:
        ignored = subprocess.run(
            ['git', '-C', str(root), 'check-ignore', '-z', '--no-index',
             '--stdin'],
            input='\0'.join(tracked), capture_output=True, text=True,
            timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as failure:
        return _git_failure(repo, 'check-ignore', failure)
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
        print(f'{shown}: FAIL — git check-ignore reported no matches but named '
              f'paths:\n{_log_safe(matched)}', file=sys.stderr)
        return 1
    print(f'{shown}: ok — {len(tracked)} tracked files named, none ignored')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f'usage: {sys.argv[0]} <repo> [<repo> ...]', file=sys.stderr)
        sys.exit(2)
    sys.exit(max(main(r) for r in sys.argv[1:]))
