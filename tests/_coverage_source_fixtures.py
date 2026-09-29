"""The source readers the coverage suites each kept a copy of.

Two helpers travelled here rather than staying local: two controls read a
real test module out of a copied tree through the same line-ending
normaliser, and two synthetic-source suites spelled the binding diagnostic
they compare against the same way. Each pair was a byte-identical copy, so
a fix to one shape reached one suite and not the other.

The two readers here are new, and each had to be. `_module_text` belongs to
`tests/_binding_assertions.py`, which keeps it, so the reader is named for
what it returns; and `_at` is bound by `tests/test_wfjobs.py`, where it
walks a decoded workflow document rather than a source. The owner set is
read as a set, so publishing either name from a shared helper would make
each of those suites a re-implementation of this module.

`_real_module_copy` is the third, and it is here because
`tests/_control_calls.py` resolves a call a checked control makes through a
def in that same file or a pair tabled in `_PURE_IMPORTS` — and a tabled
pair has to mean the callee writes nothing, which this one does not: it
copies the test tree and hands back the path every later write is proved
against, so tabling it would be the checker losing sight of where a
control writes. It is neither tabled nor local, so
`tests/_control_writes.py` reads this file and judges that body itself,
with the kinds each call site hands it.
`tests/test_shared_helper_calls.py` is the control that fails if that
stops being what the guard does.
"""
from pathlib import Path

from _coverage_guard import _BINDING_MESSAGE
from _owned_writes import copy_test_tree


def _normalized_source(target):
    """A test module's source with checkout line endings normalised."""
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _expected_binding_diagnostic(source, marker, message=_BINDING_MESSAGE):
    """The diagnostic owed a source, at the line carrying `marker`."""
    line = source[:source.index(marker)].count('\n') + 1
    return f'tests/synthetic.py:{line}: {message}'


def _real_module_copy(tmp, relative):
    """Copy the real test tree under a root this control owns."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative
