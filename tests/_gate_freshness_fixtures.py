"""The value encoder both gate-freshness suites each kept a copy of.

`test_gate_freshness.py` drives the decision core and
`test_gate_freshness_run.py` its orchestration, and both hand a value to
the module under test that is already text when it is a string and has to
be JSON when it is not. Each said so with the same two lines, so a change
to the spelling a case is handed reached one suite and not the other.

The name is unchanged. No other module in `tests/` declares `_encode` at
module scope, so publishing it claims no second owner.
"""
import json


def _encode(value):
    return value if isinstance(value, str) else json.dumps(value)
