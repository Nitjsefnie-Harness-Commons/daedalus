#!/usr/bin/env python3
"""The gate controls: admission, OS-death release, and the normal-exit release.

These gate controls live apart from the boundary/starvation diagnostics in
test_dashboard_harness.py so that a comment here can say what the gate is for
and where it is deliberately bypassed, without a size ceiling pressing the
reasoning out of the file.

The gate itself is _dashnode._dashboard_child_gate: an OS-released exclusive
lock that _run_dashboard_node_once takes around each dashboard Node child, so
that at most one dashboard child runs at a time per checkout. Exactly three
places run a real or queued child WITHOUT the gate, each on purpose (checked
against the tree, not remembered):

  1. The UTF-8 decode probe in test_dashboard_harness.py — its caller bound
     is a decode bound, not an admission one, so it would otherwise be charged
     for however long other dashboard children hold the gate.
  2. The amplifier test in test_dashboard_harness.py — a drain-timing control
     that fakes Popen with a 1.2 s delay, which would hold the real lock.
  3. _controlled_run in test_dashboard_behaviour.py — a fully faked retry run
     that should not take a real cross-process lock at all.

A gate whose comment names a closed set nobody checked is worse than one that
names nothing, so if a new bypass is added, add it here.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
import test_dashboard_behaviour as behaviour  # noqa: E402


# The OS-release holder writes a pre-gate marker, then takes the gate, then
# writes a held marker, so the control below can report which phase the holder
# actually reached rather than guessing a cause it cannot observe.
_GATE_HOLDER = (
    'import sys, time\nsys.path.insert(0, "tests")\nimport _dashnode\n'
    'open(sys.argv[1] + ".waiting", "w").close()\n'
    'with _dashnode._dashboard_child_gate():\n'
    '    open(sys.argv[1], "w").close()\n'
    '    while True: time.sleep(0.05)\n')
_TRIVIAL_CHILD = 'process.stdout.write("child");'
_GATE_CHILD = (
    'import sys\nsys.path.insert(0, "tests")\nimport _dashnode\n'
    f'h = _dashnode.DashboardNodeHarness({_TRIVIAL_CHILD!r}, 0)\n'
    'print(_dashnode.run_dashboard_node(h).stdout)\n')
# How long the holder may wait to be admitted before the wait is reported.
# Derived, not chosen: queue depth (at most os.cpu_count()-1 suites ahead of
# the holder) x worst single hold (a deeply-starved child holds the gate about
# 6.1 s; a healthy one about 1.1 s) x a contention factor, which lands near
# 90 s — roughly 5x the worst single hold. A legitimate queue must not
# false-red (that is the failure the gate introduces); too large a bound only
# delays a real fault, so the error is in the safe direction.
_HOLDER_ADMISSION_S = 90


def _popen(script, *args):
    return subprocess.Popen(
        [sys.executable, '-c', script, *args], cwd=behaviour.ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_gate_is_released_by_the_os_when_the_holder_is_killed(tmp):
    """A killed gate-holder's flock is dropped, so the next child proceeds.

    The holder takes the gate directly (no node grandchild to orphan, no
    self-expiry to degrade the control). The escape reports what was observed
    — the holder did not reach admission — and lists what that could mean,
    because the control cannot distinguish queued-behind, admitted-then-
    descheduled (the #1030 mechanism itself), or a failed marker write.
    """
    held = Path(tmp) / 'held'
    waiting = Path(str(held) + '.waiting')
    holder = _popen(_GATE_HOLDER, str(held))
    try:
        escape = time.monotonic() + _HOLDER_ADMISSION_S
        while not held.exists() and holder.poll() is None:
            if time.monotonic() > escape:
                phase = ('pre-gate marker written, not admitted'
                         if waiting.exists() else 'no pre-gate marker')
                raise AssertionError(
                    f'the holder did not reach admission within '
                    f'{_HOLDER_ADMISSION_S}s ({phase}). It may be queued '
                    'behind another child, or admitted then OS-descheduled '
                    'before writing its held marker — the #1030 mechanism '
                    'this issue addresses — or the write failed. The '
                    'control '
                    'cannot distinguish these.')
            time.sleep(0.02)
        # The holder reached the gate and holds the flock, so this kill is the
        # only thing that can let the waiter in: the anti-vacuity assert keeps
        # a holder that died before the gate from passing this vacuously.
        assert held.exists(), (
            f'the holder exited (rc {holder.poll()}) before the gate')
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=90)
    waiter = _popen(_GATE_CHILD)
    out = waiter.communicate(timeout=90)
    assert waiter.returncode == 0, out[1]


def test_gate_releases_between_two_children_in_one_process(tmp):
    del tmp
    # Two sequential children in one process: the first must release before the
    # second acquires; with the release removed the second blocks on a lock
    # this process still holds — the mutant's signature is this hang, not a
    # named assert. Pinning the release limb accepts the hang signature.
    for index in range(2):
        result = _dashnode.run_dashboard_node(
            _dashnode.DashboardNodeHarness(_TRIVIAL_CHILD, 0))
        assert result.returncode == 0, (index, result)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashgate_')


if __name__ == '__main__':
    raise SystemExit(main())
