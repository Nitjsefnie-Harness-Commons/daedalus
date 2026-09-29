#!/usr/bin/env python3
"""The shared source readers' own semantics, proved on files.

`_normalized_source` and `_expected_binding_diagnostic` travelled out of
the coverage suites that each kept a copy, and a read that moves into
shared code needs a control of its own: the suite it left proved a rule
about the guard, not about the reader that now carries the bytes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_source_fixtures import (  # noqa: E402
    _normalized_source)


def test_a_file_that_really_carries_crlf_reads_back_normalised(tmp):
    """The reader's CRLF arm, over bytes on disk rather than in memory."""
    target = Path(tmp) / 'crlf.py'
    target.write_bytes(b'import os\r\nimport sys\r\n')
    assert b'\r\n' in target.read_bytes()
    assert _normalized_source(target) == 'import os\nimport sys\n'


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
