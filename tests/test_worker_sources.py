#!/usr/bin/env python3
"""The shared worker-source stubs' own contract.

The `eventTarget` stand-in is opt-in twice over, and both halves are load
bearing: a target built without the flag cannot be fired at all, and one
built with it delivers to every listener. A stub whose only control
exercised a single listener would pin none of that.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _noderun import run_node_program  # noqa: E402
from _repo import ROOT  # noqa: E402
from _stream_fake import require_node  # noqa: E402
from _worker_sources import event_target_stub  # noqa: E402


def _run(program):
    result = run_node_program(require_node(), program, [], cwd=ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


# The delivery, driven twice over one target. Round one is the whole
# contract; round two separates "a listener added during a dispatch waits"
# from "the stub threw it away".
_DELIVERY = event_target_stub() + (
    r"""
const target = eventTarget([], true);
const ran = [];
const late = [];
const escaped = [];
const lateAfterRound = [];
target.addListener(() => {
  ran.push('first');
  throw new Error('listener blew up');
});
target.addListener(() => { ran.push('second'); });
target.addListener(() => {
  ran.push('third');
  target.addListener(() => { late.push('late'); });
});
for (let round = 0; round < 2; round += 1) {
  try {
    target.dispatch(7, { windowId: 42, isWindowClosing: false });
    escaped.push(null);
  } catch (error) {
    escaped.push(error.message);
  }
  lateAfterRound.push(late.length);
}
process.stdout.write(JSON.stringify({ ran, late, escaped, lateAfterRound }));
""")


def test_every_retained_listener_is_called_in_registration_order(tmp):
    del tmp
    record = _run(_DELIVERY)
    assert record['ran'] == [
        'first', 'second', 'third',
        'first', 'second', 'third',
    ], record


def test_a_throwing_listener_neither_stops_the_next_nor_fails_the_caller(tmp):
    del tmp
    record = _run(_DELIVERY)
    assert record['escaped'] == [None, None], record
    assert record['ran'].count('second') == 2, record


def test_a_listener_added_during_a_dispatch_waits_for_the_next(tmp):
    del tmp
    record = _run(_DELIVERY)
    assert record['lateAfterRound'] == [0, 1], record
    assert record['late'] == ['late'], record


# A target that did not ask to fire has no way to fire: the property is
# ABSENCE, so what this reads is the stub's own surface, not a listener's
# side effect. A stub that handed every target a dispatch would let a
# pre-existing caller's registration fire behind its back.
_INERT = event_target_stub() + (
    r"""
const messageListeners = [];
const plain = eventTarget();
const shared = eventTarget(messageListeners);
const recording = [];
plain.addListener(() => { recording.push('plain'); });
shared.addListener(() => { recording.push('shared'); });
const out = {
  plainDispatch: typeof plain.dispatch,
  sharedDispatch: typeof shared.dispatch,
  plainKeys: Object.keys(plain).sort(),
  sharedKeys: Object.keys(shared).sort(),
  listeners: shared.listeners.length,
  recording,
};
try {
  plain.dispatch(1);
  out.attempted = 'no error';
} catch (error) {
  out.attempted = error.constructor.name;
}
process.stdout.write(JSON.stringify(out));
""")


def test_a_target_that_did_not_opt_in_carries_no_way_to_dispatch(tmp):
    del tmp
    record = _run(_INERT)
    assert record['plainKeys'] == ['addListener', 'listeners'], record
    assert record['sharedKeys'] == ['addListener', 'listeners'], record
    assert record['plainDispatch'] == 'undefined', record
    assert record['sharedDispatch'] == 'undefined', record
    assert record['attempted'] == 'TypeError', record
    assert record['recording'] == [], record


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='wskeletons_')


if __name__ == '__main__':
    raise SystemExit(main())
