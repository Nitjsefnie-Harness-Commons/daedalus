"""The run builder and the frozen clock the ci_wait suites drive.

Not a suite itself — `run_tests.py` only loads `test_*.py`.

These three live here rather than in any one suite because three suites now
need them: `test_ci_wait.py` and `test_ci_wait_gate.py` share the verdict
contract, and `test_ci_gate.py` asks the gate predicate what it answers. A
helper declared in one and imported from the other is a suite importing a
sibling suite, which `tests/test_suite_import_boundaries.py` refuses. A
`tests/_*.py` module is where a shared helper belongs; this is that module
for these three.

All three carry a `ci_wait` prefix and none keeps the bare name it had in
its declaring suite. That is the same reason twice: both `_run` and
`_Clock` are names suites in this tree already declare, and neither is
declared generically - `tests/_boundary.py`'s `_run` is a node-scenario
runner's, and the `_Clock` declarations belong to suites that are
genuinely different classes (one records each attempt and may refuse, one
is a settable wall clock). A `tests/_*.py` module taking a bare `_run` or
`_Clock` would own a name suites already use, trading the sibling-import
red for a re-implementation red - and the allowance rows that would clear
it belong to suites this branch has no business editing.
"""
import contextlib


def _ci_wait_run(rid, conclusion, started, workflow: int | None = 11,
                 path=None, **fields):
    """One workflow run as the actions API reports it against a SHA.

    Passing `path` removes workflow_id, standing the path in alone;
    `workflow=None` leaves a run naming no workflow at all.
    """
    run = {
        'id': rid,
        'name': f'run {rid}',
        'status': 'completed',
        'conclusion': conclusion,
        'run_started_at': started,
        'workflow_id': workflow,
        'html_url': f'https://github.com/o/r/actions/runs/{rid}',
    }
    if path is not None:
        del run['workflow_id']
        run['path'] = path
    run.update(fields)
    return run


class _ci_wait_clock:
    """The clock both modules read, moved only by the sleeps themselves."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@contextlib.contextmanager
def _frozen_ci_wait_clock(mod, clock):
    """One clock for ci_wait and the client it polls through.

    The bound is read from the client's own `time`, so a wait faked on
    ci_wait's clock alone would compare a real monotonic clock against the
    fake deadline and expire immediately, before any request. Nothing real
    is waited on, so a timeout here is a value, not a margin.
    """
    real = (mod.time, mod.gh_client.time)
    mod.time = clock
    mod.gh_client.time = clock
    try:
        yield clock
    finally:
        mod.time, mod.gh_client.time = real
