"""The parent-capability questions this tree's fixtures must ask first.

Three capabilities, independent of each other, and none of them a property of
`os.name`: whether the parent resolves a name differing only in case to the
entry it spells, whether it will hold a symlink, and whether it will give
one file a second name. A vfat parent answers no, no, no; an ext4 parent
yes, yes, yes; a case-insensitive volume that holds symlinks is yes to the
first two and the fixtures below are not the ones that need the third.

So a fixture that asserts one verdict of one of those questions, or that
builds a shape the parent may not hold, asks it here rather than branching on
the platform -- and asks about the operation it actually performs. The gate
in `test_case_fold_parent.py` and the case-sensitive twins beside it share
the first question for exactly that reason.
"""
import os
from pathlib import Path

import _util


def folds(root):
    """Whether `root` folds case, or None when that could not be asked.

    Asked by creating a file whose name carries letters and looking for it
    under the other spelling. The name has to carry letters because a
    difference in a digit answers nothing: `probe1` and `probe2` are two
    names on a parent that folds.

    `None` is its own answer -- a parent that will not take a probe file is
    not a parent any of these fixtures can be run against -- and every caller
    treats it as a reason to skip rather than as an answer.
    """
    stem = f'daedalus-fold-probe-aa{os.getpid()}'
    probe = Path(root) / stem
    try:
        probe.write_text('', encoding='utf-8')
    except OSError:
        return None
    try:
        return os.path.exists(str(Path(root) / stem.upper()))
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def require_case_sensitive(root, what, other_half):
    """Skip unless `root` keeps two spellings of one name apart.

    `what` names the property the caller was about to assert and `other_half`
    says where that property is pinned on a parent that does fold, so a
    reader who lands on the skip learns what still carries the question
    rather than only that something did not run.
    """
    answer = folds(root)
    if answer is None:
        _util.skip(f'{root} will not take a probe file')
    if answer:
        _util.skip(
            f'{root} folds case, so "{what}" cannot be asked of it here; '
            f'that half is pinned in {other_half}')


def require_second_name(root, what, operation, label):
    """Skip unless `root` can give one file a second name.

    `operation` is the call the caller is about to make -- `os.link` or
    `os.symlink` -- because the two are separate capabilities and a parent
    may refuse one while allowing the other. `what` names the property the
    caller was about to pin, so the skip says what is still pinned
    elsewhere rather than only that something did not run.
    """
    first = Path(root) / f'daedalus-link-probe-a{os.getpid()}'
    second = Path(root) / f'daedalus-link-probe-b{os.getpid()}'
    try:
        first.write_text('', encoding='utf-8')
        try:
            operation(first, second)
        except OSError as why:
            # The probe's own paths are noise in a skip line; the operation
            # and the reason are what a reader needs.
            _util.skip(
                f'{root} cannot {label}, so "{what}" cannot be built here '
                f'({why.strerror or type(why).__name__})')
    finally:
        for probe in (first, second):
            try:
                probe.unlink()
            except OSError:
                pass
