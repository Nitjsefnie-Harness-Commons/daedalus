#!/usr/bin/env python3
"""Serviced and attempt-count bounds on work a starved process still owes.

A wall-clock bound rejects or kills work that was never scheduled, where a
serviced or attempt-based bound would have waited (issue 925). Each control
below was watched failing against the wall-clock code it replaces, for the
defect's own reason.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402
import _util  # noqa: E402
import test_cli  # noqa: E402
from _repo import ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_sources import (  # noqa: E402
    chrome_stub, import_scripts_stub)

# The starvation scenarios run with the freeze/thaw budget of their own.
_ENV = _util.child_coverage('scrub')


_SAMPLE_MS = 100
_CREDIT_CAP_MS = 2 * _SAMPLE_MS
_SETTLE_BUDGET_MS = 10000

BRIDGE = 'https://starve.example.com'
SYNC = 'POST /sync-tabs'
# Every mode's recording, from a run of the shipped worker: boot opens the
# stream and syncs the tab list, and nothing after that touches the bridge.
BOOT_PLAN = [SYNC]
BOOT_STREAM = [503]

# The guard's serviced budget and the sampler interval live in cdp.js; the
# numbers here only say what a control waits through.
_FREEZE_MS = _SETTLE_BUDGET_MS + 500

_CDP_STARVE_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

// run_node_program pushes the payload as an object literal, last, not text.
const [backgroundPath, mode, plan] = process.argv.slice(1);
const fakeTimers = mode !== 'freeze';
const released = [];
const timers = [];
let pendingResolve = null;
let clockMs = 0;
let clockStep = 100;
let fires = 0;

// The inspector command this scenario drives; the shared release bookkeeping
// lives in the chrome stub.
async function sendCommand(_target, method, params) {
  if (method === 'Runtime.evaluate') {
    return { result: { value: 1 } };
  }
  if (method === 'Runtime.awaitPromise'
      && params.promiseObjectId === 'pending-original') {
    return new Promise((resolve) => { pendingResolve = resolve; });
  }
  return {};
}

function eventTarget() {
  return { addListener() {} };
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

const BRIDGE_URL = '__BRIDGE__';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
// The stream stays unanswered, as a live SSE connection does: the fetch
// never settles, so a boot that reaches startStream cannot spin on the
// retry loop.
function streamResponse() {
  return new Promise(() => {});
}
""" + STRICT_FETCH + r"""

""" + chrome_stub("'starve-token'", 'BRIDGE_URL', 'sendCommand') + r"""
const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: () => 'starve-id' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: fakeTimers
    ? (callback, ms) => {
      const timer = { callback, ms, active: true };
      timers.push(timer);
      return timers.length;
    }
    : (callback, ms) => setTimeout(callback, ms),
  clearTimeout: fakeTimers
    ? (id) => {
      if (timers[id - 1]) timers[id - 1].active = false;
    }
    : (id) => clearTimeout(id),
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('context') + r"""

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

// Read at write time, not at load time: the arrays are filled by the fetches
// the drive below makes.
function gateReport() {
  return {
    records: nonStreamFetches,
    refused: refusedFetches,
    badOrigins,
    streamAnswered: streamFetches.map((f) => f.answered),
    contractFaults: gateContractFaults,
  };
}

async function drive() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await delay();
  // A kept claim is what an attachment another feature already holds
  // looks like now; it is held for the rest of the run.
  await vm.runInContext(
    'cdpClaimAttachment(7, { keep: true }).ready', context);
  if (fakeTimers) {
    context._cdpNow = () => { clockMs += clockStep; return clockMs; };
  }
  const pending = vm.runInContext(
    "_cdpSettle(7, { objectId: 'pending-original',"
    + " subtype: 'promise' })", context);
  let outcome = 'resolved';
  let value = null;
  pending.then((settled) => { value = settled.value; },
    (error) => { outcome = error.message; });
  await delay();
  if (mode === 'freeze') {
    await new Promise((resolve) => setTimeout(() => {
      const until = Date.now() + """ + str(_FREEZE_MS) + r""";
      while (Date.now() < until) {}
      setTimeout(resolve, 0);
    }, 0));
    setTimeout(() => {
      if (pendingResolve) {
        pendingResolve({ result: { value: 'THAWED' } });
      }
    }, 0);
    await pending.catch(() => {});
    process.stdout.write(JSON.stringify(Object.assign(
      { mode, outcome, value }, gateReport())));
    return;
  }
  let survivedTheFreeze = null;
  if (mode === 'cap') {
    clockStep = 600000;
  }
  for (let attempt = 0; attempt < 300; attempt++) {
    const sampler = timers.find((item) => item.active && item.ms === 100);
    if (!sampler) break;
    sampler.active = false;
    sampler.callback();
    fires += 1;
    await delay();
    if (mode === 'cap' && fires === 1) {
      survivedTheFreeze = outcome === 'resolved';
      clockStep = 100;
    }
    if (outcome !== 'resolved') break;
  }
  await delay();
  await delay();
  process.stdout.write(JSON.stringify(Object.assign(
    { mode, outcome, value, released, fires, survivedTheFreeze },
    gateReport())));
}

drive().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""").replace('__BRIDGE__', BRIDGE)

# The freeze control's child spends `_FREEZE_MS` in one deliberate busy-wait
# and then ends on its own, so it needs no bound here of its own. It is a
# CONTROL, though, and a control's double has to stay finite under mutation
# as well as on correct code: a mutation that wedged the child must not
# become a job timeout. The launcher's hang detector in `tests/_noderun.py`
# is what ends it, and it names the scenario and the child's output rather
# than a suite, which is why the number that used to sit here was not simply
# put back.


def _starve_run(mode):
    """Drive the settlement guard in a node child under one starvation mode."""
    outcome = run_gate(
        require_node(), _CDP_STARVE_HARNESS,
        [str(_repo.ROOT / 'extension' / 'background.js'), mode],
        cwd=ROOT, plan={'planned': list(BOOT_PLAN)})
    assert_gate_clean(
        contract_faults=outcome['contractFaults'],
        records=outcome['records'], refused=outcome['refused'],
        bad_origins=outcome['badOrigins'],
        stream_answered=outcome['streamAnswered'],
        planned=list(BOOT_PLAN), planned_stream=list(BOOT_STREAM))
    return outcome


def test_a_cdp_settlement_needing_one_turn_after_a_freeze_still_settles(
        tmp):
    """Starved work that settles after the thaw is not rejected.

    A wall guard whose timer expired inside the freeze is swept in that same
    thaw phase, ahead of the work, and rejects a settlement the worker had
    already earned; the serviced guard charges the freeze at the cap and
    the race resolves.
    """
    del tmp
    outcome = _starve_run('freeze')
    assert outcome['outcome'] == 'resolved', outcome
    assert outcome['value'] == 'THAWED', outcome


def test_a_cdp_guard_rejects_only_once_serviced_for_its_budget(tmp):
    """A never-settling step still rejects, on serviced evidence only.

    The rejection names the same label the wall guard named, and the late
    response a rejected race abandons is released exactly as before: the
    `timedOut` flip on serviced expiry is what releases it.
    """
    del tmp
    outcome = _starve_run('serviced')
    assert outcome['outcome'] == (
        f'promise settlement timed out after {_SETTLE_BUDGET_MS} ms'), (
        outcome)
    assert outcome['fires'] >= (
        _SETTLE_BUDGET_MS // _CREDIT_CAP_MS), outcome
    assert sorted(outcome['released']) == [
        'pending-late', 'pending-original'], outcome


def test_a_cdp_guard_credits_a_frozen_stretch_one_doubled_interval(tmp):
    """One frozen stretch is charged at most twice the sampler interval.

    The first sample reads a clock pushed far past the whole budget; the
    guard must survive that sample — an uncapped credit would spend the
    budget in one go — and then reach expiry on ordinary credits alone.
    """
    del tmp
    outcome = _starve_run('cap')
    assert outcome['survivedTheFreeze'] is True, outcome
    expected_fires = 1 + -(
        -(_SETTLE_BUDGET_MS - _CREDIT_CAP_MS) // _SAMPLE_MS)
    assert outcome['fires'] == expected_fires, outcome
    assert outcome['outcome'] == (
        f'promise settlement timed out after {_SETTLE_BUDGET_MS} ms'), (
        outcome)


def test_a_cli_wait_for_survives_a_clock_jump_mid_wait(tmp):
    """A starved runner's clock jump does not end a condition wait early.

    The wall deadline version raised on that jump; the attempt-count version
    has no clock to jump and finds the condition.
    """
    del tmp
    readings = iter([0.0, 0.0] + [30.0] * 8)
    polls = []
    with mock.patch('time.time', lambda: next(readings)):
        test_cli._wait_for(
            lambda: polls.append(1) or len(polls) >= 2,
            what='the frozen condition')
    assert len(polls) == 2, polls


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='starve_'))
