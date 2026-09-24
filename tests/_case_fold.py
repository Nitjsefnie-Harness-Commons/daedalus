"""Emulate a case-folding filesystem for the tree one test names.

Not a suite itself -- run_tests.py only loads `test_*.py`.

A case-insensitive parent is a different filesystem, not a broken host, and
a case-sensitive box cannot be handed one without root and a mount. So the
one property such a parent has -- it resolves a name that differs only in
case to the entry it spells -- is emulated for the root the test names, by
answering `os.stat` and `os.lstat` with the entry the parent's own listing
resolves the name to. Every other answer is the host's: `os.listdir` still
lists the spelling on disk, and `os.path.realpath` still returns the
caller's spelling, because a POSIX `realpath` does not canonicalise case
(only `ntpath.realpath`, through `_getfinalpathname`, ever did). An earlier
version of this file faked that Windows answer as though it were POSIX's,
and the whole suite's stripe guarantee rested on the fiction.

Measured on a real vfat image, which is the shape being emulated and needs
no faking at all: with `Alpha` on disk, `os.listdir` answers `['Alpha']`,
`os.stat`/`os.path.samefile` answer for `alpha` too and report one
`(st_dev, st_ino)`, and `os.path.realpath('/…/alpha')` answers with
`alpha`. That is every claim this file makes, and the same assertions run
against that mount verify it.

A test that pins behaviour under it must pin the same fixture's behaviour
without it as well: the two verdicts together are what make the difference
a property of the parent rather than of the assertion.
"""
import contextlib
import os


def _folded_spelling(parent, name):
    """The spelling a folding parent lists for `name`, or None.

    An exact match wins, so a parent that holds both `Foo` and `foo` -- a
    case-sensitive one, where they are two entries -- keeps the entry the
    caller named. Only a name the parent does not spell itself falls to the
    case-insensitive match, and a parent with no such entry has none.
    """
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    if name in names:
        return name
    return next((entry for entry in names
                 if entry.lower() == name.lower()), None)


def _folded(root, path):
    """The path a folding parent under `root` resolves `path` to.

    A component with no entry keeps the spelling it was given, so the
    syscall that follows still raises the refusal a real parent raises for a
    name it holds nothing for.
    """
    head, tail = os.path.split(os.fspath(path))
    parts = []
    while tail:
        parts.append(tail)
        head, tail = os.path.split(head)
    parts.append(head)
    out = parts[-1]
    for part in reversed(parts[:-1]):
        if out == root or out.startswith(root + os.sep):
            folded = _folded_spelling(out, part)
            if folded is not None:
                part = folded
        out = os.path.join(out, part)
    return out


@contextlib.contextmanager
def case_folding(root):
    """Answer name resolution under `root` as a case-folding parent would.

    The patch covers `os.stat` and `os.lstat`, so `os.path.samefile`,
    `os.path.islink` and `os.path.exists` inherit it, and every path outside
    `root` keeps the host's own answers. `os.path.realpath` is deliberately
    left alone: a POSIX `realpath` answers with the caller's spelling on a
    folding parent too, and a double that disagrees with the program it
    stands in for proves nothing.
    """
    root = os.path.realpath(str(root))
    real_stat, real_lstat = os.stat, os.lstat

    def folding_stat(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)):
            path = _folded(root, path)
        return real_stat(path, *args, **kwargs)

    def folding_lstat(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)):
            path = _folded(root, path)
        return real_lstat(path, *args, **kwargs)

    os.stat = folding_stat
    os.lstat = folding_lstat
    try:
        yield root
    finally:
        os.stat = real_stat
        os.lstat = real_lstat
