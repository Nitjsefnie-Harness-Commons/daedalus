"""The run builder, the frozen clock and the verdict the ci_wait suites drive.

Not a suite itself — `run_tests.py` only loads `test_*.py`.

These four live here rather than in any one suite because three suites now
need them: `test_ci_wait.py` and `test_ci_wait_gate.py` both classify runs
through the verdict contract, and `test_ci_gate.py` asks the gate predicate
what it answers. `run_tests.py` gives each suite its own process, so one
cannot import another; a `tests/_*.py` module is where a shared helper
belongs, and this is that module for these three.

All four carry a `ci_wait` prefix and none keeps the bare name it had in
its declaring suite. That is the same reason twice for `_run` and
`_Clock`: both are names suites in this tree already declare, and neither is
declared generically - `tests/_boundary.py`'s `_run` is a node-scenario
runner's, and the `_Clock` declarations belong to suites that are
genuinely different classes (one records each attempt and may refuse, one
is a settable wall clock). A `tests/_*.py` module taking a bare `_run` or
`_Clock` would own a name suites already use, trading the sibling-import
red for a re-implementation red - and the allowance rows that would clear
it belong to suites this branch has no business editing. `_verdict` is the
same case a third time, ten suites over, and its loader is spelled here
rather than named `_ci_wait` for the same reason: four modules declare
that name at module scope and none of them can import this one.
"""
import contextlib

import _util
from _repo import ROOT


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


def _ci_wait_contract():
    """ci_wait.py loaded under this module's own contract name.

    The two suites that classify runs each keep a `_ci_wait` of their own,
    because each wants its own copy of the module; `_util.load` executes the
    file per call, so the copy a verdict reads is the copy the call made.
    """
    return _util.load(
        ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'ci_wait.py',
        'ci_wait_verdict_contract')


def _ci_wait_verdict(runs, checks=(), required_checks=None):
    """`verdict` on a loaded copy, with the published check nameable.

    `required_checks=None` means the tool's own default rather than no
    required check at all: a suite that wants the check switched off
    says `frozenset()`, which is a different thing to say.
    """
    mod = _ci_wait_contract()
    if required_checks is None:
        required_checks = mod.PUBLISHED_CHECKS
    return mod.verdict(runs, checks, required_checks=required_checks)


def _ci_wait_state(runs, checks=()):
    """(runs, checks) as a poll of the real `ci_wait.py` answers.

    `ci_state` is the one read and it answers both questions at once, so a
    suite that stubs the poll has to say what each poll carried: the run
    list alone is a head whose publisher has written nothing.
    """
    return (list(runs), list(checks))
