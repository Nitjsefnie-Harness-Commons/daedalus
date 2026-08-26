#!/usr/bin/env python3
"""Diagnostics from the dashboard suites' Node harness process boundary.

Each stall is driven through a real Node subprocess so the suite checks the
exact evidence returned to Python rather than the helpers' source text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402


def test_phase_records_a_harness_checkpoint(tmp):
    """A phase reaches captured stderr in its stable diagnostic format."""
    del tmp
    result = _dashnode.run_dashboard_node(
        "phase('dashboard harness started');")
    assert result.stderr == '[phase] dashboard harness started\n', result


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashharness_')


if __name__ == '__main__':
    raise SystemExit(main())
