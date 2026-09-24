#!/usr/bin/env python3
"""Serviced and attempt-count bounds on work a starved process still owes.

A wall-clock bound rejects or kills work that was never scheduled, where a
serviced or attempt-based bound would have waited (issue 925). The CDP
settlement guard is bounded by serviced event-loop time — crediting each
sampler gap at one doubled interval, so a frozen stretch is charged at most
that cap — and the test-side queue and CLI waits are bounded by poll
attempts. Each control below was watched failing against the wall-clock
code it replaces, for the defect's own reason.

The worker-bound scenarios run the shipped worker in a Node VM with the
shared bridge gate in place: every mode declares the exact requests its
boot makes, and a request outside the plan is refused by status and
recorded, so an invented fetch fails the scenario.
"""
import ast
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

# The starvation scenarios run with the freeze/thaw budget of their own (see
# _FREEZE_RUN_TIMEOUT_S); the harness children the wall-timeout guard names do
# not, and run_gate/run_inline_gate leave the child's environment to the one
# scrubbed constant those launchers name.
_ENV = _util.child_coverage('scrub')


_SAMPLE_MS = 100
_CREDIT_CAP_MS = 2 * _SAMPLE_MS
_SETTLE_BUDGET_MS = 10000

BRIDGE = 'https://starve.example.com'
SYNC = 'POST /sync-tabs'
# Every mode's recording, from a run of the shipped worker: boot opens the
# stream and syncs the tab list, and nothing after that touches the bridge.
# The boot stream fetch is answered 503 and is declared and asserted like
# the accounted routes.
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
// lives in the chrome stub. This handles the rest.
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

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request it sees.
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

# An outer backstop, not a bound on awaited work: the freeze control's child
# spends its budget in one deliberate busy-wait, so a wedged child is the
# only failure this ceiling can name.
_FREEZE_RUN_TIMEOUT_S = 60


def _starve_run(mode):
    """Drive the settlement guard in a node child under one starvation mode.

    The mode's declared requests are checked against what the gate recorded,
    so a request outside the plan is refused by status and fails here.
    """
    outcome = run_gate(
        require_node(), _CDP_STARVE_HARNESS,
        [str(_repo.ROOT / 'extension' / 'background.js'), mode],
        cwd=ROOT, plan={'planned': list(BOOT_PLAN)},
        timeout=_FREEZE_RUN_TIMEOUT_S)
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


# The modules the two harness children are launched through: `_stream_fake`
# holds the gate's file and `node -e` launchers, `_noderun` the file launcher
# they forward to. The guard resolves the launcher each harness actually calls
# (by the callee at its own call site) and follows that call graph, so the
# verdict is bound to the operation the harness performs, not to one function
# name.
_LAUNCHER_MODULES = ('_stream_fake.py', '_noderun.py')
_LAUNCH_ATTRS = frozenset({
    'run', 'Popen', 'call', 'check_call', 'check_output'})


def _launcher_functions(trees):
    """Every function the launcher modules define, by name."""
    functions = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, node)
    return functions


def _harness_launchers(harness_tree, functions):
    """Launcher names a harness actually calls, read off its own call sites."""
    reached = set()
    for node in ast.walk(harness_tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in functions):
            reached.add(node.func.id)
    return reached


def _bounded_launches(launcher_names, functions):
    """Launch sites reachable from `launcher_names` that carry a `timeout=`.

    Follows the call graph across the launcher modules, so a bound on any
    function the child's launcher actually reaches is found — not just one
    spelled in the entry function. An unresolvable name is reported, not
    skipped.
    """
    found = []
    seen = set()
    work = list(launcher_names)
    while work:
        name = work.pop()
        if name in seen:
            continue
        seen.add(name)
        function = functions.get(name)
        if function is None:
            found.append((name, 'unresolved route'))
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if (isinstance(node.func, ast.Attribute)
                    and node.func.attr in _LAUNCH_ATTRS
                    and any(k.arg == 'timeout' for k in node.keywords)):
                found.append((name, node.lineno))
            if isinstance(node.func, ast.Name) and node.func.id in functions:
                work.append(node.func.id)
    return found


def test_the_harness_children_run_without_a_wall_timeout(tmp):
    """The Surface D runners launch their children with no timeout=.

    A reintroduced wall backstop around an attempt-bounded child is the
    starvation rejection this branch removes. The question is bound to the
    operation, not to a function name: from each harness's own call sites the
    guard resolves which launcher the child is actually launched through, then
    follows that launcher's call graph across `_stream_fake.py` and
    `_noderun.py` and refuses any launch on the path that carries a `timeout=`.
    A harness that swapped `run_inline_gate` for `run_gate`, or that reached a
    bounded helper, is caught by this path, not by an enumeration of names. An
    unresolvable launcher is a violation, not a skip.

    Named blind spot: a wall bound written in the harness's own JavaScript —
    inside a spliced program string — is invisible here, because this guard
    reads Python `ast` and the bound it cannot see lives in JavaScript. The
    guard's input language is not the language the property lives in; that gap
    is named, not papered over, the same way the cross-file duplicate check's
    blindness to string-literal JavaScript is named in `_worker_sources.py`.
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    trees = {name: ast.parse((tests_dir / name).read_text(encoding='utf-8'))
             for name in _LAUNCHER_MODULES}
    functions = _launcher_functions(trees)
    for name in ('_relayharness.py', '_cdpharness.py'):
        tree = ast.parse((tests_dir / name).read_text(encoding='utf-8'))
        # A Python-level `timeout=` anywhere in the harness is refused.
        sites = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.keyword) and node.arg == 'timeout']
        assert not sites, (name, sites)
        launchers = _harness_launchers(tree, functions)
        assert launchers, (name, 'no launcher call resolved from the harness')
        bounded = _bounded_launches(launchers, functions)
        assert not bounded, (name, sorted(launchers), bounded)


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
