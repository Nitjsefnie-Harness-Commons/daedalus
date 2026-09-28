"""The one-line source builder the boundary controls write their trees with.

Each of the four controls here writes a fake `tests/` tree of module source
and then reads it back through a boundary detector, so the source has to be
spelled the same way in every case. It lived as a private `def` in each of
them, which meant a fix to the spelling reached one control and not the
other three, and it is here so there is a single copy of it to fix.

The name says what it builds, module TEXT, rather than what it is,
because `main` already binds a different `_mod` in the gate-freshness
suites, and a shared helper that adopted that name would make both of them
offenders of it (see `test_helper_reimplementation.py`).
"""


def _mod_text(*lines):
    return ''.join(line + '\n' for line in lines)
