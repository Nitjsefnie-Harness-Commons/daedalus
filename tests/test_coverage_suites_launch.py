#!/usr/bin/env python3
"""coverage_suites.py's own verdicts, driven through `main()` in process.

The controls in `test_coverage_suites.py` copy the runner into a fabricated
tree and execute it there, so what they measure is recorded against a
temporary path that dies with the tree: the runner's own statements are
measured only where something runs it in this checkout. These drive
`main()` here and stand in for the one boundary that cannot be crossed
in-process -- the launch -- through `tests/_coverage_launch_stubs.py`.

What belongs here is therefore the runner's DECISIONS rather than its
behaviour under real concurrency: the record a suite ended at its bound
leaves, the sentence the platform it read picks, a suite the launcher
never started, and the safety net its own output is put under.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _coverage_launch_stubs as _launch  # noqa: E402
from _coverage_suite_fixture import (  # noqa: E402
    OPERATOR_ASKS_FIRST, OPERATOR_FORCES)


_FAILURE_MARKER = '(suite did not pass; its coverage still counts)'


def test_a_suite_ended_at_its_bound_is_recorded_and_refuses_the_run(tmp):
    """A suite stopped at the bound leaves its record and refuses the run."""
    outcome = _launch.run_main(tmp, scripts={
        'test_alpha': {'result': (0, _launch.KILLED)}})
    _launch.assert_timed_out(outcome, _launch.KILLED)


def test_a_forced_kill_is_told_so_and_the_other_platform_is_not(tmp):
    """The platform the runner read picks the sentence an operator reads."""
    outcome = _launch.run_main(tmp, platform='win32', scripts={
        'test_alpha': {'result': (0, _launch.KILLED)}})
    _launch.assert_timed_out(outcome, _launch.KILLED)
    assert outcome.launch.calls[0].platform == 'win32', outcome.launch.calls
    assert OPERATOR_FORCES in outcome.stderr, outcome.stderr
    assert OPERATOR_ASKS_FIRST not in outcome.stderr, outcome.stderr


def test_a_suite_that_could_not_start_is_grouped_not_fatal(tmp):
    """A suite the launcher never started is named, not fatal to the run."""
    outcome = _launch.run_main(
        tmp, suites=('test_alpha.py', 'test_beta.py'),
        scripts={'test_alpha': {'error': FileNotFoundError('no python')}})
    _launch.assert_launch_failed(outcome, 'test_alpha.py', _FAILURE_MARKER)
    assert ('::group::tests/test_beta.py\nmeasured\n::endgroup::\n'
            in outcome.stdout), outcome.stdout


def test_the_output_safety_net_asks_and_survives_a_refusal(tmp):
    """`main()` re-arms its output and keeps going when that fails."""
    refusal = _launch.run_main_on_streams(tmp, tty=True, error=OSError())
    asked = _launch.run_main_on_streams(tmp, tty=True)
    assert asked.launch.calls[0].platform == sys.platform
    assert asked.stdout.reconfigured == [{'errors': 'replace'}], asked
    assert refusal.stdout.reconfigured, refusal.stdout.reconfigured
    assert refusal.status == 0, (refusal.stdout.text, refusal.stderr.text)


raise SystemExit(_util.runner(_util.collect(dict(globals()))))