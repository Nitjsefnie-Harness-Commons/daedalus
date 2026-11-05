"""The fixture writer both patch-coverage suites each kept a copy of.

`test_diff_coverage.py` and `test_diff_coverage_javascript.py` each wrote
one file under a temporary directory and handed the path back, to lay out
the package, the coverage XML and the patch a patch-coverage run reads.
The two bodies were byte-identical, so the layout a run is handed was
spelled twice and a fix to one spelling reached one suite.

The name says what it returns: the file it wrote.
"""
from pathlib import Path


def _written_file(tmp, name, text):
    """Write one fixture file under tmp and return its path."""
    path = Path(tmp) / name
    path.write_text(text, encoding='utf-8')
    return path
