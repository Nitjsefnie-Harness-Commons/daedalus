#!/usr/bin/env python3
"""The refusal assertion shared by the workflow-reader suites.

Not a suite itself — only `test_*.py` files load as suites. Living here
rather than in one suite keeps the split's imports one-directional: a suite
that needed another suite's helper would drag that suite into every run.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wfcheckout  # noqa: E402


def _assert_yaml_refusal(workflow, wording, line=None):
    """Assert the reader refuses `workflow` with `wording` at `line`.

    The reader reports the line every refusal happened on, so a fixture that
    knows which line is offending pins that number: an unpinned message can
    drift to a neighbouring line and still say where something happened.
    """
    try:
        _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        message = str(error)
        assert wording in message, message
        assert 'line ' in message, message
        if line is not None:
            reported = re.search(r'at line (\d+)', message)
            assert reported, message
            assert int(reported.group(1)) == line, message
    else:
        raise AssertionError(f'expected refusal mentioning {wording!r}')
