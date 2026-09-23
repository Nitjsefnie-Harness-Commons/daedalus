#!/usr/bin/env python3
"""Serviced and attempt-count bounds on work a starved process still owes.

A wall-clock bound rejects or kills work that was never scheduled, where a
serviced or attempt-based bound would have waited (issue 925). The CDP
settlement guard is bounded by serviced event-loop time — crediting each
sampler gap at one doubled interval, so a frozen stretch is charged at most
that cap — and the test-side queue and CLI waits are bounded by poll
attempts. Each control below was watched failing against the wall-clock
code it replaces, for the defect's own reason.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402
import _util  # noqa: E402
import test_cli  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_SAMPLE_MS = 100
_CREDIT_CAP_MS = 2 * _SAMPLE_MS
_SETTLE_BUDGET_MS = 10000

# The guard's serviced budget and the sampler interval live in cdp.js; the
# numbers here only say what a control waits through.
_FREEZE_MS = _SETTLE_BUDGET_MS + 500

_CDP_STARVE_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, mode] = process.argv.slice(1);
const fakeTimers = mode !== 'freeze';
const released = [];
const timers = [];
let pendingResolve = null;
let clockMs = 0;
let clockStep = 100;
let fires = 0;

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

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'starve-token',
        'daedalus-server': 'test-bridge',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {},
    detach: async () => {},
    sendCommand: async (_target, method, params) => {
      if (method === 'Runtime.releaseObject') {
        released.push(params.objectId);
        if (params.objectId === 'pending-original' && pendingResolve) {
          const resolve = pendingResolve;
          pendingResolve = null;
          setImmediate(() => resolve({
            result: { objectId: 'pending-late' },
          }));
        }
        return {};
      }
      if (method === 'Runtime.evaluate') {
        return { result: { value: 1 } };
      }
      if (method === 'Runtime.awaitPromise'
          && params.promiseObjectId === 'pending-original') {
        return new Promise((resolve) => { pendingResolve = resolve; });
      }
      return {};
    },
  },
  scripting: { executeScript: async () => [{ result: false }] },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    if (String(target).endsWith('/result') && init.method === 'POST') {
      return response(200, { ok: true });
    }
    if (String(target).includes('/stream?')) {
      return new Promise(() => {});
    }
    return response(200, { ok: true });
  },
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

async function drive() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await delay();
  vm.runInContext('_cdpSessions[7] = true', context);
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
    // The #928 idiom: one real busy-wait freeze outrunning the guard's
    // whole budget, the work settling one timers phase after the thaw. A
    // wall guard swept in that thaw phase rejects finished work; the
    // serviced guard charges the freeze at its cap and waits.
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
    process.stdout.write(JSON.stringify({ mode, outcome, value }));
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
  process.stdout.write(JSON.stringify(
    { mode, outcome, value, released, fires, survivedTheFreeze }));
}

drive().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""")

# An outer backstop, not a bound on awaited work: the freeze control's child
# spends its budget in one deliberate busy-wait, so a wedged child is the
# only failure this ceiling can name.
_FREEZE_RUN_TIMEOUT_S = 60


def _starve_run(mode):
    """Drive the settlement guard in a node child under one starvation mode."""
    node = shutil.which('node')
    assert node, 'node is required to execute the CDP settlement guard'
    result = subprocess.run(
        [node, '-e', _CDP_STARVE_HARNESS,
         str(_repo.ROOT / 'extension' / 'background.js'), mode],
        cwd=_repo.ROOT, capture_output=True, text=True,
        timeout=_FREEZE_RUN_TIMEOUT_S)
    assert result.returncode == 0, (result.returncode, result.stderr)
    return json.loads(result.stdout)


def test_a_cdp_settlement_needing_one_turn_after_a_freeze_still_settles(
        tmp):
    """Starved work that settles after the thaw is not rejected.

    The child freezes its loop for longer than the guard's whole budget and
    the pending settlement resolves one timers phase after the thaw. A wall
    guard whose timer expired inside the freeze is swept in that same thaw
    phase, ahead of the work, and rejects a settlement the worker had
    already earned; the serviced guard charges the freeze at the cap and
    the race resolves.
    """
    del tmp
    outcome = _starve_run('freeze')
    assert outcome['outcome'] == 'resolved', outcome
    assert outcome['value'] == 'THAWED', outcome


def test_a_cdp_guard_rejects_only_once_serviced_for_its_budget(tmp):
    """A never-settling step still rejects, on serviced evidence only.

    The child fires the guard's self-rescheduling sampler with a clock that
    only moves when the loop is serviced, so reaching the budget takes at
    least budget-over-cap serviced samples — never one wall timer. The
    rejection names the same label the wall guard named, and the late
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
    budget in one go — and then reach expiry on ordinary credits alone. The
    fires-to-reject count pins the arithmetic: cap, then one interval per
    serviced sample.
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

    The condition becomes true on the second poll, but the wall clock jumps
    far past a 15 s budget between the polls — the shape of a runner that
    was not scheduled. The wall deadline version raised on that jump; the
    attempt-count version has no clock to jump and finds the condition.
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
