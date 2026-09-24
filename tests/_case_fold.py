"""Emulate a case-folding filesystem for the tree one test names.

Not a suite itself -- run_tests.py only loads `test_*.py`. A
case-insensitive parent is a different filesystem, not a broken host, and a
test cannot be handed one on a case-sensitive box without root and a mount.
So the two answers a platform gives and the code under test reads are
emulated instead, and only under the root the test names: the on-disk
spelling a name resolves to (`realpath`), and whether two spellings reach
one entry (`samefile`). Nothing else about a case-folding parent is
modelled, and every path outside that root keeps the host's own answers, so
a test that does not ask for this emulates nothing.

A test that pins behaviour under it must pin the same fixture's behaviour
without it as well: the two verdicts together are what make the difference
a property of the filesystem rather than of the assertion.
"""
import contextlib
import os


def _folded_spelling(parent, name):
    """The spelling a case-folding parent reports for `name`, or None.

    A parent that folds case resolves a name it does not spell itself to the
    entry whose spelling only case distinguishes. A case-sensitive parent has
    no such entry, which is what makes this a property to ask about rather
    than a normalization to apply.
    """
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    if name in names:
        return name
    return next((entry for entry in names
                 if entry.lower() == name.lower()), None)


def _folding_realpath(original, root, path):
    """`realpath` as a case-folding parent under `root` answers it."""
    resolved = original(path)
    head, tail = os.path.split(resolved)
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


def _folding_samefile(original, root, left, right):
    """`samefile` as a case-folding parent under `root` answers it."""
    try:
        return original(left, right)
    except OSError:
        left_text, right_text = os.fspath(left), os.fspath(right)
        if not all(text == root or text.startswith(root + os.sep)
                   for text in (left_text, right_text)):
            raise
        # The host has no entry for one of the two spellings. A parent that
        # folds case answers with the entry the other spelling does reach;
        # two spellings differing in more than case are two entries there.
        if os.path.basename(left_text).lower() != os.path.basename(
                right_text).lower():
            return False
        return _folded_spelling(
            os.path.dirname(left_text),
            os.path.basename(left_text)) is not None


@contextlib.contextmanager
def case_folding(root):
    """Answer two path queries as a case-folding filesystem would.

    A case-folding filesystem is a different filesystem, not a broken host:
    the tree under `root` is emulated, and every path outside it keeps the
    host's own answers, so a test that does not ask for this emulates
    nothing. What is emulated is the pair of answers the platform gives and
    the code under test reads -- the on-disk spelling a name resolves to
    (`realpath`), and whether two spellings reach one entry (`samefile`) --
    because those two are all a Windows, macOS or vfat parent changes, and
    nothing else.

    A test that pins behaviour under it must also pin the same fixture's
    behaviour without it: the two verdicts are what make the difference a
    property of the filesystem rather than of the assertion.
    """
    root = os.path.realpath(str(root))
    real_realpath = os.path.realpath
    real_samefile = os.path.samefile
    os.path.realpath = lambda path: _folding_realpath(
        real_realpath, root, path)
    os.path.samefile = lambda left, right: _folding_samefile(
        real_samefile, root, left, right)
    try:
        yield root
    finally:
        os.path.realpath = real_realpath
        os.path.samefile = real_samefile
