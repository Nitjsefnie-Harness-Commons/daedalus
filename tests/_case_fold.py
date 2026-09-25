"""Ask a parent whether it folds case, for the fixtures that must know.

The gate in `test_case_fold_parent.py` and the case-sensitive twins beside it
ask one question of one kind of directory — does this parent resolve a name
that differs only in case to the entry it already spells? — and a fixture
that asserts a verdict has to know which host it is on before it asserts it.
So the question lives here once.

It is asked of the filesystem and never of `os.name`: a macOS or Windows
volume folds, an ext4 or vfat volume does not, and `posixpath.normcase` is
the identity on POSIX while `ntpath.normcase` lower-cases, so neither says
anything about the *volume*.
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
