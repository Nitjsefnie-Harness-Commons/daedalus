#!/usr/bin/env python3
"""Browser-free controls for the control extension and the probe it owes.

The diagnosis is only ours if the control extension is not, so the control
has to load, has to carry a worker script no shipped one collides with, and
has to satisfy its own probe — which is a real `node` child, bounded by a
figure this suite composes. They are here rather than in
`tests/test_real_browser_classification.py` because that file sits at its
own size ceiling, and because a suite may not import a sibling suite: the
probe's healthy half and the probe's stalled half have to stand together.
"""
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402
from _controlled_call import _call_failure  # noqa: E402
from _node_launch_routing import (  # noqa: E402
    SITE_HANG_MULTIPLE, NodeBoundExceeded, node_bound_expiry)
from _outer_bound import (  # noqa: E402
    OuterBoundExpired, announcing_pid, outer_bound)
from _repo import EXTENSION_ROOT  # noqa: E402

# Hang detectors, not health margins; the shape and the shared argument are
# in `tests/_node_launch_routing.py`, and these samples are measured with the
# machine BUSY. `--check` parses the worker's source and `-e` runs it, so
# one deadline covers both.
CONTROL_CHILD_CHECK_SAMPLES_S = (0.179, 0.252, 0.150, 0.085,
                                 0.319, 0.862, 0.786, 1.613)
CONTROL_CHILD_PROBE_SAMPLES_S = (0.353, 0.293, 0.214, 0.306,
                                 1.447, 1.103, 2.470, 0.186)
CONTROL_CHILD_SLOWEST_S = max(
    *CONTROL_CHILD_CHECK_SAMPLES_S, *CONTROL_CHILD_PROBE_SAMPLES_S)
CONTROL_CHILD_DEADLINE_S = round(CONTROL_CHILD_SLOWEST_S * SITE_HANG_MULTIPLE)

# The outer bound the stalled control holds while it waits, for the reason
# `tests/_outer_bound.py` gives: the bound it would be waiting out is the
# one a reversion removes, and the suite ceiling's SIGTERM reports a job
# timeout rather than a control. A bound carried in from a sibling is a seam
# this repository refuses, so the chain is repeated here rather than
# imported.
#
#   OUTER_BOUND_SAMPLES     the same measurement as the other three files
#                           that hold one — these samples are of the bound's
#                           own expiry, measured in test_noderun_deadline.py
#   OUTER_BOUND_SLOWEST_S   max of those samples
#   OUTER_BOUND_S           the bound, with no multiple
#
# The margin is over this file's own healthy path and nothing else: the
# `CONTROL_CHILD_DEADLINE_S` above, 12s, and node's startup. The samples are
# borrowed rather than re-derived, so the two facts are stated apart on
# purpose.
OUTER_BOUND_SAMPLES = (52.0, 55.0, 57.0)
OUTER_BOUND_SLOWEST_S = max(OUTER_BOUND_SAMPLES)
OUTER_BOUND_S = round(OUTER_BOUND_SLOWEST_S)


def test_the_control_extension_satisfies_its_own_probe(tmp):
    """The verdict rests on the control's script reaching its flag."""
    node = shutil.which('node')
    if not node:
        _realbrowser_controls.control_requirement_missing(
            'Node is absent, so the control worker probe cannot be checked')
    control = _realbrowser._control_extension(tmp)
    source = (control / _realbrowser.CONTROL_WORKER_SCRIPT).read_text(
        encoding='utf-8')
    try:
        checked = subprocess.run(
            [node, '--check'], input=source, capture_output=True, text=True,
            timeout=CONTROL_CHILD_DEADLINE_S)
        answer = subprocess.run(
            [node, '-e',
             source + '\nprocess.stdout.write(String('
                      + _realbrowser.CONTROL_WORKER_PROBE + '))'],
            capture_output=True, text=True, timeout=CONTROL_CHILD_DEADLINE_S)
    except subprocess.TimeoutExpired as why:
        raise node_bound_expiry(why, CONTROL_CHILD_DEADLINE_S) from why
    assert checked.returncode == 0, (checked.returncode, checked.stderr)
    assert answer.returncode == 0, (answer.returncode, answer.stderr)
    assert answer.stdout == 'true', (answer.stdout, answer.stderr)


def test_the_control_probe_site_reports_its_own_stalled_child(tmp):
    """A wedged control script is the site's own named failure, not a hang.

    The child is real and the deadline is this file's own composed figure
    rather than a shortened one, because a control that proved a different
    number would be proving nothing about this site.

    The bound is held from outside for the reason beside its chain, and the
    child announces a pid because a bound with nothing to kill only reports.
    Node itself is not re-checked here: the control above names its own
    absence by name.
    """
    pid_file = Path(tmp) / 'control.pid'
    stalling = (announcing_pid(pid_file) + '\n'
                "process.stdout.write('ctrl spoke\\n');"
                'setInterval(()=>{},900)')
    ext = _realbrowser._control_extension(tmp)
    script = ext / _realbrowser.CONTROL_WORKER_SCRIPT
    script.write_text(stalling, encoding='utf-8')
    with mock.patch.object(_realbrowser, '_control_extension',
                           lambda _root: ext):
        try:
            with outer_bound(OUTER_BOUND_S, pid_file, 'the control probe'):
                try:
                    test_the_control_extension_satisfies_its_own_probe(tmp)
                except NodeBoundExceeded as failure:
                    assert failure.deadline_s == CONTROL_CHILD_DEADLINE_S
                    assert 'ctrl spoke' in failure.stdout, failure.stdout
                    assert isinstance(failure.stdout, str), (
                        type(failure.stdout))
                    return
        except OuterBoundExpired as wedged:
            raise AssertionError(
                "the outer bound fired, so the control child's own bound never ended "
                "it, which is what this control exists to prevent. What the "
                f"bound reports: {wedged}"
            ) from wedged
    raise AssertionError('the control script that wedges finished')


def test_the_control_probe_requirement_is_a_skip_not_a_failure(tmp):
    """A Node-less leg skips the probe control instead of failing it."""
    with mock.patch.object(shutil, 'which', return_value=None):
        failure = _call_failure(
            lambda: test_the_control_extension_satisfies_its_own_probe(tmp))
    assert failure.__class__ is (
        _realbrowser_controls.ControlRequirementSkipped), failure


def test_the_control_extension_is_loadable_and_cannot_collide_with_ours(tmp):
    control = _realbrowser._control_extension(tmp)
    ours = _realbrowser.declared_worker(EXTENSION_ROOT)
    assert _realbrowser.declared_worker(control) == (
        _realbrowser.CONTROL_WORKER_SCRIPT)
    assert _realbrowser.CONTROL_WORKER_SCRIPT != ours, ours
    listed = [
        {'type': 'service_worker',
         'url': f'chrome-extension://ours/{ours}',
         'webSocketDebuggerUrl': 'ws://ours'},
        {'type': 'service_worker', 'url': 'chrome-extension://theirs/x',
         'webSocketDebuggerUrl': 'ws://theirs'},
    ]
    assert _realbrowser._worker_targets(
        listed, _realbrowser.CONTROL_WORKER_SCRIPT) == [], listed


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowsercontrolextension_')


if __name__ == '__main__':
    raise SystemExit(main())
