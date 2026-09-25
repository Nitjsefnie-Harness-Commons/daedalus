#!/usr/bin/env python3
"""The shared gate's two launchers, and its non-stream hang answer.

Not the accounting itself — `tests/test_bridge_fake_oracle.py` owns that.
These pin the two capabilities the eval-relay and CDP harnesses need on top
of it: a declared `{hang: true}` relay answer that only cancellation ends,
and the `node -e` launcher, which hands the program text to node as an
argument instead of writing it to a file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, require_node, run_gate, run_inline_gate)

SYNC = 'POST /sync-tabs'


_HANG_HARNESS = r"""
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
// The plan rides last in both launches: the file launcher pushes it as an
// object literal, the `node -e` launcher passes it as JSON text. Read it
// once, here, and parse only when it arrived as text.
const gatePlanArg = process.argv[process.argv.length - 1];
const plan = typeof gatePlanArg === 'string'
  ? JSON.parse(gatePlanArg) : gatePlanArg;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

const RELAY = 'https://relay.example.com';

// A promise that never settles loses the race against one event-loop turn; a
// synchronously-refused one wins it. So 'pending' is evidence the gate is
// still holding the request open, not that the probe raced too fast.
function settleOrPending(promise) {
  const PENDING = 'pending';
  return Promise.race([
    promise.then(() => 'settled', () => 'settled'),
    new Promise((resolve) => setImmediate(() => resolve(PENDING))),
  ]);
}

(async () => {
  const controller = new AbortController();
  // With a signal: pending until the signal aborts, then an AbortError.
  const held = bridgeFetch(RELAY + '/slow', {
    method: 'GET', signal: controller.signal,
  });
  const beforeAbort = await settleOrPending(held);
  controller.abort();
  let rejected = null;
  try { await held; } catch (error) { rejected = error.name; }
  // Without a signal: a hang that can never settle is refused, not leaked.
  // Raced against one event-loop turn so a broken refusal reports 'pending'
  // instead of dying with an empty stdout the runner cannot read.
  const unsignalableAnswer = bridgeFetch(RELAY + '/other', { method: 'GET' });
  const unsignalable = await Promise.race([
    unsignalableAnswer,
    new Promise((resolve) => setImmediate(() => resolve('pending'))),
  ]);
  process.stdout.write(JSON.stringify({
    beforeAbort,
    rejected,
    unsignalableStatus: unsignalable === 'pending'
      ? 'pending' : unsignalable.status,
    records: nonStreamFetches,
    refused: refusedFetches,
  }));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""

_RELAY = 'https://relay.example.com'
_HANG_PLAN = {
    'planned': ['GET ' + _RELAY + '/slow', 'GET ' + _RELAY + '/other'],
    'relayHosts': [_RELAY],
    'answers': {
        'GET ' + _RELAY + '/slow': {'hang': True},
        'GET ' + _RELAY + '/other': {'hang': True},
    },
}


def _hang_probe():
    return run_gate(require_node(), _HANG_HARNESS, [], cwd=ROOT,
                    plan=_HANG_PLAN)


def test_a_declared_relay_hang_never_settles_until_its_signal_aborts(tmp):
    """A non-stream `{hang: true}` holds the request until it is aborted."""
    del tmp
    outcome = _hang_probe()
    assert outcome['beforeAbort'] == 'pending', outcome
    assert outcome['rejected'] == 'AbortError', outcome
    slow = [r for r in outcome['records'] if r['request'].endswith('/slow')]
    assert len(slow) == 1, outcome
    assert slow[0]['refused'] is False, outcome
    assert slow[0]['status'] == 200, outcome
    assert slow[0]['url'] == _RELAY + '/slow', outcome


def test_a_hang_answer_without_a_signal_has_no_way_to_settle(tmp):
    """A declared hang with no signal is refused, never leaked.

    A `{hang: true}` answers a never-settling promise, so without an
    AbortSignal there is nothing that can ever end it. The gate records a
    599 refusal instead of handing the worker a promise it must abandon.
    """
    del tmp
    outcome = _hang_probe()
    assert outcome['unsignalableStatus'] == 599, outcome
    other = [r for r in outcome['records'] if r['request'].endswith('/other')]
    assert len(other) == 1, outcome
    assert other[0]['refused'] is True, outcome
    assert 'GET ' + _RELAY + '/other' in outcome['refused'], outcome


_INLINE_HARNESS = r"""
// Under `node -e` the program text is not in process.argv: argv[0] is the
// node binary and argv[1..] are the arguments, so this harness's own
// arguments are read from slice(1) exactly as a file-launched harness does.
const label = process.argv[1];
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
// The plan rides last under both launches: the file launcher splices it in
// as an object literal, the inline launcher appends it as JSON text. Read it
// once, here, and parse only when it arrived as text — so one harness text
// serves `node -e` and the file launcher alike.
const gatePlanArg = process.argv[process.argv.length - 1];
const plan = typeof gatePlanArg === 'string'
  ? JSON.parse(gatePlanArg) : gatePlanArg;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

(async () => {
  await bridgeFetch(BRIDGE_URL + '/sync-tabs', {
    method: 'POST', body: JSON.stringify({ tabs: [] }),
  });
  process.stdout.write(JSON.stringify({
    label,
    records: nonStreamFetches.map((i) => i.request),
    streamAnswered: streamFetches.map((f) => f.answered),
    contractFaults: gateContractFaults,
  }));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""

# Wrong on purpose: it takes the last argv entry as the plan WITHOUT parsing
# it.
_UNPARSED_INLINE_HARNESS = r"""
const plan = process.argv[process.argv.length - 1];
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];

function response(status, data) {
  return {
    ok: true, status, body: null, json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

process.stdout.write(JSON.stringify({ contractFaults: gateContractFaults }));
"""


def test_the_inline_driver_runs_a_node_e_harness_against_a_declared_plan(tmp):
    """`run_inline_gate` drives a `node -e` harness against a plan.

    The plan rides last in both launches and the harness reads its own
    arguments from slice(1) in both, so the spliced gate is the same JS
    either way; this pins the inline driver end to end so a mis-wired `-e`
    launch is caught here and not as a dead child in one harness.
    """
    del tmp
    outcome = run_inline_gate(require_node(), _INLINE_HARNESS, ['labelled'],
                              cwd=ROOT, plan={'planned': [SYNC]})
    assert outcome['label'] == 'labelled', outcome
    assert outcome['records'] == [SYNC], outcome
    assert outcome['streamAnswered'] == [], outcome
    assert outcome['contractFaults'] == [], outcome


def test_an_inline_plan_left_unparsed_is_a_contract_fault(tmp):
    """A harness that skips the parse turns `plan` into a JSON string.

    The inline launcher appends the plan as text, so a harness that forgets
    the parse hands the gate a string; `plan.planned` is then undefined and
    the splice-time check names `plan.planned` rather than letting a
    half-parsed plan decide what the gate answers.
    """
    del tmp
    outcome = run_inline_gate(require_node(), _UNPARSED_INLINE_HARNESS, [],
                              cwd=ROOT, plan={'planned': []})
    assert outcome['contractFaults'] == ['plan.planned'], outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='gateext_')


if __name__ == '__main__':
    raise SystemExit(main())
