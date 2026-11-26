#!/usr/bin/env python3
"""One per-suite wall-clock bound, read by `tests/conftest.py`.

`tests/conftest.py` times each suite against this bound, so a hung suite
ends at one number, and the record it writes for one it stopped names the
suite. The bound lives here rather than in a test module because a number
two files each hold is two premises: widening one leaves the other bounding
the value before, and neither file says so.
"""
import math
import os

DEFAULT_SUITE_TIMEOUT_S = 900
TIMEOUT_ENV = 'DAEDALUS_SUITE_TIMEOUT'
# One number for every bounded step of a teardown, because the steps share
# the reason it is bounded: a cleanup that waits for ever hands the hang
# back through the error path that was supposed to report it.
CLEANUP_TIMEOUT_S = 10


def suite_timeout():
    """The per-suite wall-clock bound, overridable for slow machines.

    A value that is not a finite positive number is refused rather than
    rounded or clamped: `float('inf')` passes a `<= 0` test, and a bound
    of infinity never expires, which is the unbounded wait this bound
    exists to remove.
    """
    raw = os.environ.get(TIMEOUT_ENV)
    if raw is None:
        # Cast, so the record reads the same number whether the bound came
        # from the environment or from the constant above.
        return float(DEFAULT_SUITE_TIMEOUT_S)
    try:
        timeout = float(raw)
    except ValueError:
        raise SystemExit(
            f'{TIMEOUT_ENV}: not a number: {raw!r}') from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise SystemExit(
            f'{TIMEOUT_ENV}: must be a finite positive number; '
            f'got {raw!r}')
    return timeout


def timeout_record(name, bound, returncode, cleanup=''):
    """The line a launcher appends for the suite it stopped at `bound`.

    It names the suite: a group of output is the only place a reader
    learns which concurrent child wedged.
    """
    record = (f'SUITE TIMED OUT after {bound} s (returncode {returncode!r})'
              f' in {name}')
    if cleanup:
        record += f'; {cleanup}'
    return record + '; its last lines are above\n'
