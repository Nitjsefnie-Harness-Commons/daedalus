"""The source readers the coverage suites each kept a copy of.

`_expected_binding_diagnostic` travelled here rather than staying local:
two synthetic-source suites spell the binding diagnostic they compare
against the same way. `_real_module_copy` hands a control a copied test
tree and the path in it that every later write is proved against.
"""
from pathlib import Path

from _coverage_guard import _BINDING_MESSAGE
from _owned_writes import copy_test_tree


def _expected_binding_diagnostic(source, marker, message=_BINDING_MESSAGE):
    """The diagnostic owed a source, at the line carrying `marker`."""
    line = source[:source.index(marker)].count('\n') + 1
    return f'tests/synthetic.py:{line}: {message}'


def _real_module_copy(tmp, relative):
    """Copy the real test tree under a root this control owns."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative
