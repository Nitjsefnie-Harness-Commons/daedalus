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


def test_a_windows_read_gets_the_same_sentence_and_refuses_the_dead_one(tmp):
    """The sentence no longer depends on the platform the runner read.

    Both routes ask before they escalate, so the sentence an operator
    reads is the same under a Windows read as under a POSIX one, and the
    forced-only wording this runner retired is refused: a conditional
    that resurrects it for one platform is red here rather than silent.
    """
    outcome = _launch.run_main(tmp, platform='win32', scripts={
        'test_alpha': {'result': (0, _launch.KILLED)}})
    _launch.assert_timed_out(outcome, _launch.KILLED)
    assert outcome.launch.calls[0].platform == 'win32', outcome.launch.calls
    assert OPERATOR_ASKS_FIRST in outcome.stderr, outcome.stderr
    assert OPERATOR_FORCES not in outcome.stderr, outcome.stderr


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


def test_the_runner_removes_its_outputs_through_the_bounded_removal(tmp):
    """This launcher's own `finally` is the bounded removal, not a bare one.

    Both new controls in `test_run_tests.py` drive the runner, so nothing
    else watches what this launcher does with its output directory -- and a
    bare `shutil.rmtree` there raises out of `main()` and takes the
    `TIMED OUT:` report below it with it, which is issue #1485 on the
    second of the two launchers.

    The two halves are asserted apart, because either alone is satisfied
    by the other: the stand-in's own log says the bounded function was
    entered AT THIS CALL SITE and retried, and the launcher's own verdict
    says the run still reported after it.
    """
    outcome = _launch.run_main(tmp, scripts={
        'test_alpha': {'result': (0, _launch.KILLED)}},
        refuse_removals=1)
    removals = outcome.removals
    assert removals.calls, (
        'the runner never removed its output directory through the '
        'bounded removal, so it removed it some other way')
    assert len(removals.calls) == 2, (
        f'one refusal and one answer were expected: {removals.calls}')
    assert set(removals.calls) == {removals.calls[0]}, removals.calls
    _launch.assert_timed_out(outcome, _launch.KILLED)


def test_an_argument_the_runner_does_not_take_prints_the_usage_it_takes(
        tmp):
    """An unknown argument is refused by name, before anything is launched.

    The exit status is the contract and the message is the half of it an
    operator reads: a bare 2 says something was wrong with nothing about
    what to write instead. That no suite was launched is asserted too,
    because an argument typo that went on to measure the whole suite would
    cost the run it was meant to save.
    """
    outcome = _launch.run_main(tmp, argv=('--bogus',))
    assert outcome.status == 2, (outcome.stdout, outcome.stderr)
    assert outcome.stderr == (
        'usage: coverage_suites.py [--require-all]\n'), outcome.stderr
    assert outcome.stdout == '', outcome.stdout
    assert not outcome.launch.calls, outcome.launch.calls


def test_a_tree_holding_no_suite_is_refused_rather_than_measured(tmp):
    """An empty measurement is a refusal, not a coverage number.

    The glob the runner discovers suites with can come back empty -- a
    wrong working directory, a filter that matched nothing -- and 0% is a
    number every gate reads as a run that found nothing to measure. The
    message names both the emptiness and the refusal, and no launch
    happened because there was nothing to launch.
    """
    outcome = _launch.run_main(tmp, suites=())
    assert outcome.status == 1, (outcome.stdout, outcome.stderr)
    assert ('no suites found — refusing to report 0% as a pass'
            in outcome.stderr), outcome.stderr
    assert outcome.stdout == '', outcome.stdout
    assert not outcome.launch.calls, outcome.launch.calls


def test_suite_output_without_a_trailing_newline_is_closed_off(tmp):
    """The group terminator cannot be swallowed by a child that wrote none.

    The block is compared byte for byte, so both halves are pinned at once:
    the newline the runner adds is there, and it is one -- an unterminated
    suite followed by `::endgroup::` on its own line is what this exists to
    keep out of a workflow log, and a second newline is the same log
    damage in the other direction.
    """
    outcome = _launch.run_main(tmp, output='no newline')
    assert outcome.status == 0, (outcome.stdout, outcome.stderr)
    assert outcome.stdout == ('::group::tests/test_alpha.py\nno newline\n'
                              '::endgroup::\n'), outcome.stdout


def test_require_all_refuses_a_partial_run_the_runner_would_otherwise_keep(
        tmp):
    """`--require-all` is the gate that turns one failed suite into a fail.

    Both halves of the arm are asserted, and so is the run it contrasts
    with: the same stand-ins without the flag measure and exit 0, so the
    refusal belongs to the gate and not to the failure it reports. The
    count and the total are both named, because "some suites failed" leaves
    a reader unable to tell a flake from a broken tree.
    """
    scripts = {'test_beta': {'result': (1, '')}}
    suites = ('test_alpha.py', 'test_beta.py')
    gated = _launch.run_main(
        tmp, suites=suites, scripts=scripts, argv=('--require-all',))
    assert gated.status == 1, (gated.stdout, gated.stderr)
    assert ('1 of the 2 suites failed' in gated.stderr), gated.stderr
    assert 'refusing partial coverage' in gated.stderr, gated.stderr
    assert 'every one of the 2 suites failed' not in gated.stderr, (
        gated.stderr)
    ungated = _launch.run_main(tmp, suites=suites, scripts=scripts)
    assert ungated.status == 0, (ungated.stdout, ungated.stderr)
    assert ungated.stderr == '', ungated.stderr


def test_every_suite_failing_is_refused_even_without_the_gate(tmp):
    """A number for a program that never ran is refused on its own account.

    Without `--require-all` a failed suite is reported and the run still
    publishes coverage, which is the whole point of partial measurement. A
    run where NOTHING measured is a different thing, so the runner refuses
    it whatever the flag says -- and the refusal is two lines, both of which
    are asserted, because the second is the sentence that says why. The
    groups are asserted too: the report of what failed is still owed before
    the run ends.
    """
    suites = ('test_alpha.py', 'test_beta.py')
    outcome = _launch.run_main(tmp, suites=suites, scripts={
        'test_alpha': {'result': (1, '')},
        'test_beta': {'result': (2, '')}})
    assert outcome.status == 1, (outcome.stdout, outcome.stderr)
    first = 'every one of the 2 suites failed — refusing to'
    second = 'report a coverage number for a program that did not run.'
    assert first in outcome.stderr, outcome.stderr
    assert second in outcome.stderr, outcome.stderr
    assert outcome.stderr.index(first) < outcome.stderr.index(second), (
        outcome.stderr)
    for name in suites:
        block = _launch.group(outcome, name)
        assert _FAILURE_MARKER in block, block


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
