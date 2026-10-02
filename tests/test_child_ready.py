#!/usr/bin/env python3
"""The harness's readiness waits, driven as the thing they wait ON.

A wait whose iteration count is a function of wall time is not a measurement:
the instructions it contributes grow with however long the machine took to
get there, and the budget above them subtracts a background measured on the
same machine — so the term lands in the residual with nothing to cancel it.
`JOURNEY DETERMINISM` settled this once already, for a polled `/health`,
and the same argument applies to a polled announcement.

Every control here therefore pins a property of the WAIT rather than of the
bridge it waits for: the number of times it runs is the number of lines the
child printed, and nothing else. Nothing is spawned but a `python3 -c` that
prints what it is told to, so no suite is asserting a margin against a
machine.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _child_ready  # noqa: E402
import _util  # noqa: E402


def _child(script):
    """A child that prints `script`'s lines and then stays up."""
    proc = subprocess.Popen(
        [sys.executable, '-c',
         f'import sys, time\n{script}\ntime.sleep(600)'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc


def test_the_blocking_step_has_no_interval_of_its_own(tmp):
    """The wait is pinned where it lives, because its outcome is not.

    A 50 ms tick and a condition wait return the same port from the same
    child, and the control above cannot tell them apart: the tick version
    also returns when the stream closes, and also returns when the line
    arrives. What differs is the number of times the loop runs, and nothing
    observable from outside the module says what that is except a profiler —
    which is what the budget is made of and what a suite cannot afford.

    So the shape is asserted instead. The blocking step waits on the
    CONDITION, with the caller's deadline as its only bound: `Condition.wait`
    with an interval of its own, or a `sleep`, is a poll wearing a
    condition's name, and the whole argument for this module is that neither
    is here.
    """
    del tmp
    source = Path(_child_ready.__file__).read_text(encoding='utf-8')
    assert 'time.sleep' not in source, (
        'the readiness module sleeps, so something in it is waiting on a '
        'tick rather than on the child')
    assert '.wait(' not in source, (
        'the readiness module calls Condition.wait with an interval, which '
        'is the same poll the condition replaced')
    assert '.wait_for(' in source, (
        'the readiness module no longer waits on the condition at all')
    # And the bound it does hand over is the caller's, not a constant: a
    # deadline is a bound and a step is a driver.
    assert 'deadline - time.monotonic()' in source, (
        'the condition wait is no longer bounded by the caller\'s deadline')


def test_the_announcement_wait_blocks_on_a_line_and_not_on_a_tick(tmp):
    """One line ends the wait however long the child took to print it.

    The child is told to sleep first, so a poll loop would spend a
    proportional number of iterations getting here and a blocking wait would
    spend none. What is asserted is that the wait RETURNS the announcement
    and that it did so after the line — not how long that took, which is the
    one thing a control here must not pin.
    """
    del tmp
    proc = _child("import time; time.sleep(0.2)\n"
                  "print('[Daedalus] Listening on 127.0.0.1:43210',"
                  ' flush=True)')
    drained = _child_ready.drain(proc)
    try:
        port = _util.await_listening_line(proc, drained, timeout=60)
        assert port == 43210, port
        # And the arrival it blocked on is the pump's, not a timer: the
        # child is still running, so nothing but a line could have ended it.
        assert proc.poll() is None, proc.poll()
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_front_end_wait_blocks_on_a_line_and_not_on_a_tick(tmp):
    """The front end's wait is the same shape, and the same property.

    `await_mcp_ready` used to poll the captured output every 50 ms, so its
    iteration count was the front end's import time in seconds — which under
    an instruction counter is minutes, and which the harness's own docstring
    already named as the wall-clock term this wait exists to remove.
    """
    del tmp
    proc = _child("import time; time.sleep(0.2)\n"
                  "print('[MCP] streamable-http on 127.0.0.1:43211',"
                  ' flush=True)')
    drained = _child_ready.drain(proc)
    try:
        assert _child_ready.await_state(proc, drained, timeout=60) == 'up'
        assert proc.poll() is None, proc.poll()
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_child_that_never_prints_wakes_the_wait_when_it_exits(tmp):
    """A wait that only woke on its deadline would hang until the bound on
    a child that is already gone — the case a poll checked on every tick.

    The wait returns the moment the stream closes, so the caller learns the
    child died rather than that it was slow. What is asserted is the
    CAUSE, not the duration: this control must not fail on a loaded runner
    by holding the child up for longer than it can afford.
    """
    del tmp
    proc = subprocess.Popen(
        [sys.executable, '-c', 'print("not an announcement", flush=True)'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    drained = _child_ready.drain(proc)
    proc.wait(timeout=10)
    started = time.monotonic()
    found = _child_ready.await_line(proc, drained,
                                    _util.LISTENING.search, 600)
    elapsed = time.monotonic() - started
    assert found is None, found
    assert elapsed < 60, (
        'a child that had already exited kept the wait blocked for '
        f'{elapsed:.1f}s, so the deadline is what ended it rather than the '
        'stream closing')


def test_a_wait_on_a_child_nobody_is_relaying_is_a_refusal_naming_it(tmp):
    """The fallback is a sentence, not a sleep.

    `arrival_of` used to have no answer for a child whose drain nobody
    started. Returning a tick interval there would reintroduce exactly the
    poll this module removed, silently, on the one path where there is
    nothing to wait on — so it names the child instead.
    """
    del tmp
    proc = _child('pass')
    try:
        assert getattr(proc, _child_ready.ARRIVAL, None) is None
        _child_ready.arrival_of(proc)
    except RuntimeError as refusal:
        assert f'pid {proc.pid}' in str(refusal), refusal
        assert 'drain thread' in str(refusal), refusal
    else:
        raise AssertionError(
            'a wait on a child with no drain thread returned something '
            'instead of refusing')
    finally:
        proc.kill()
        proc.wait(timeout=10)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='childready_')


if __name__ == '__main__':
    raise SystemExit(main())