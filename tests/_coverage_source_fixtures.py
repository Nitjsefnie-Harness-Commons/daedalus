"""The source reader the coverage suites each kept a copy of.

`_expected_binding_diagnostic` travelled here rather than staying local:
the four receiver-descent rows spell the binding diagnostic they compare
against through it.
"""
from _coverage_guard import _BINDING_MESSAGE


def _expected_binding_diagnostic(source, marker, message=_BINDING_MESSAGE):
    """The diagnostic owed a source, at the line carrying `marker`."""
    line = source[:source.index(marker)].count('\n') + 1
    return f'tests/synthetic.py:{line}: {message}'
