"""The fixture writer the patch-coverage suites share.

`test_diff_coverage.py` and `test_diff_coverage_javascript.py` each write
one file under a temporary directory and hand the path back, to lay out
the package, the coverage XML and the patch a patch-coverage run reads.
Both use this one writer, so the layout is spelled once and a fix to it
reaches every suite.
"""
from pathlib import Path


def _written_file(tmp, name, text):
    """Write one fixture file under tmp and return its path."""
    path = Path(tmp) / name
    path.write_text(text, encoding='utf-8')
    return path
