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
# Sentinel for a callee that names a launcher-module entity but whose body the
# walk cannot see. It is a refusal, not a skip: an unread body is a hole in
# this audit, so the audit cannot certify it, and a bound hiding there would
# be a real false green. See `_resolve_callee` for the line drawn between this
# and a genuinely-external call.
_UNRESOLVED = 'unresolved'


def _is_def(node):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _class_of_call(node):
    """The launcher class a call constructs, or None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return node.func.id
    return None


def _class_methods(node):
    """`(classname, methodname) -> node` for one ClassDef."""
    return {(node.name, child.name): child for child in node.body
            if _is_def(child)}


def _module_class_aliases(tree, classes):
    """Module-level `NAME = _Class()` bindings, as `{name: class}`."""
    aliases = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        cls = _class_of_call(node.value)
        if cls in classes:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = cls
    return aliases


def _launcher_context(trees):
    """Index every function and method the launcher modules define.

    Keys bodies three ways so a callee can be resolved by a bare name, by a
    module-qualified attribute (`_noderun.run_node_program`), by `self.<m>`, or
    by `<Class>.<m>` / `<Class>().<m>`. Collecting class methods here is what
    stops the class-method route from being an unseen hole.
    """
    functions = {}
    methods = {}
    classes = set()
    local_classes = {}
    stems = set()
    for name, tree in trees.items():
        stems.add(name[:-len('.py')])
        classes.update(node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef))
        for node in ast.walk(tree):
            if _is_def(node):
                functions.setdefault(node.name, node)
            elif isinstance(node, ast.ClassDef):
                methods.update(_class_methods(node))
        local_classes.update(_module_class_aliases(tree, classes))
    return {'functions': functions, 'methods': methods, 'classes': classes,
            'local_classes': local_classes, 'stems': stems}


def _local_class_bindings(function, classes):
    """Local names bound to a launcher-module class inside `function`."""
    bound = {}
    for node in ast.walk(function):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in classes):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value.func.id
    return bound


def _resolve_callee(call, current_class, ctx, locals_):
    """The bodies a call reaches, or `_UNRESOLVED`, or None for external.

    This is a resolve-or-refuse census over the callee grammar. The line I draw
    is *whose code is it*, not *does it look risky*:

    - a receiver that is a launcher-module entity (a module stem, a class, a
      `self`, or a local bound to a class) must resolve to a body I can read;
      if it does not, the callee is `_UNRESOLVED` — an unread launcher-module
      body is a hole in this audit, so it is refused;
    - any other receiver (a stdlib module, a parameter, a local, a literal) is
      external: I cannot type it as my own code, and refusing it would refuse
      legitimate code like `dumps.glob(...)` and an imported helper module. It
      is skipped, and a `timeout=` keyword on the call is still caught by the
      concept scan of the caller's own body.
    """
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in ctx['functions']:
            return [('fn', func.id, None)]
        if func.id in ctx['classes']:
            return []
        return None
    if not isinstance(func, ast.Attribute):
        return None
    attr = func.attr
    recv = func.value
    if isinstance(recv, ast.Name):
        if recv.id in ctx['stems']:
            if attr in ctx['functions']:
                return [('fn', attr, None)]
            return _UNRESOLVED
        if recv.id in ctx['classes']:
            if (recv.id, attr) in ctx['methods']:
                return [('method', recv.id, attr)]
            return _UNRESOLVED
        if recv.id == 'self' and current_class is not None:
            if (current_class, attr) in ctx['methods']:
                return [('method', current_class, attr)]
            return _UNRESOLVED
        local_class = locals_.get(recv.id) or ctx['local_classes'].get(recv.id)
        if local_class is not None:
            if (local_class, attr) in ctx['methods']:
                return [('method', local_class, attr)]
            return _UNRESOLVED
        return None
    if (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name)
            and recv.func.id in ctx['classes']):
        if (recv.func.id, attr) in ctx['methods']:
            return [('method', recv.func.id, attr)]
        return _UNRESOLVED
    return None


def _bodies(trees, ctx):
    """Index a resolved body key to its AST node."""
    table = {}
    for key in ctx['functions']:
        table[('fn', key, None)] = ctx['functions'][key]
    for (cls, meth) in ctx['methods']:
        table[('method', cls, meth)] = ctx['methods'][(cls, meth)]
    return table


def _parameter_names(function):
    """The names a function's signature exposes, star-args excluded."""
    args = function.args
    names = set()
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        for arg in group:
            names.add(arg.arg)
    return names


def _const_str(node):
    """The string a constant node holds, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _timeout_faults(function):
    """Every place the deadline concept `timeout` appears in `function`.

    The recogniser decides the *concept* a deadline travels in, not the
    spelling of any one launch, because that is what keeps it from moving when
    a route is missed. On a resolved body the concept can enter a child three
    ways, and all three are found here: a `timeout=` keyword on any call (so
    `subprocess.run(timeout=)`, `communicate(timeout=)` and a locally bound
    `_wait(timeout=)` are all the same fact), a `timeout` parameter in the
    signature (a deadline passed through the function), and a `'timeout'` key
    written into a container a `**` spread forwards (an aliased launcher
    bounded via `k['timeout'] = n`). A bare `**opts` with no `timeout`
    anywhere is
    deliberately NOT a fault: a spread is not evidence of a bound, and refusing
    one refuses legitimate code.
    """
    faults = []
    if 'timeout' in _parameter_names(function):
        faults.append((function.lineno, 'timeout parameter'))
    for node in ast.walk(function):
        if isinstance(node, ast.keyword) and node.arg == 'timeout':
            faults.append((node.lineno, 'timeout= keyword'))
        elif (isinstance(node, ast.Subscript)
              and not isinstance(node.ctx, ast.Load)
              and _const_str(node.slice) == 'timeout'):
            faults.append((node.lineno, "'timeout' key write"))
        elif (isinstance(node, ast.Dict)
              and any(_const_str(k) == 'timeout' for k in node.keys)):
            faults.append((node.lineno, "'timeout' key in a dict"))
    return faults


def _census(entry_bodies, trees, ctx):
    """Every timeout fault and every refusal reachable from the entries.

    Walks the resolved call graph. A body visited more than once is visited
    once; a callee that resolves to `_UNRESOLVED` is a refusal (this audit
    cannot see it, so it cannot certify it); a body whose timeout concept the
    scan finds is a fault.
    """
    table = _bodies(trees, ctx)
    faults = []
    seen = set()
    work = list(entry_bodies)
    while work:
        key = work.pop()
        if key in seen:
            continue
        seen.add(key)
        body = table.get(key)
        if body is None:
            faults.append((key, _UNRESOLVED))
            continue
        current_class = key[1] if key[0] == 'method' else None
        locals_ = _local_class_bindings(body, ctx['classes'])
        for fault in _timeout_faults(body):
            faults.append((key, fault))
        for node in ast.walk(body):
            if not isinstance(node, ast.Call):
                continue
            resolved = _resolve_callee(node, current_class, ctx, locals_)
            if resolved is _UNRESOLVED:
                faults.append((key, _UNRESOLVED))
            elif resolved:
                work.extend(resolved)
    return faults


def _harness_entries(harness_tree, ctx):
    """The launcher bodies a harness reaches, resolved off its call sites.

    Resolves each call in the harness with the census grammar, so an
    attribute-called launcher and a class-instance method are entries just as
    a bare name is. Returns `(entries, refusals)`.
    """
    entries = []
    refusals = []
    for node in ast.walk(harness_tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_callee(node, None, ctx, {})
        if resolved is _UNRESOLVED:
            refusals.append((node.lineno, _UNRESOLVED))
        elif resolved:
            entries.extend(resolved)
    return entries, refusals


def test_the_harness_children_run_without_a_wall_timeout(tmp):
    """The Surface D runners launch their children with no wall bound.

    A reintroduced wall backstop around an attempt-bounded child is the
    starvation rejection this branch removes. The guard asks two questions.
    *Reachability*: from each harness's own call sites it resolves the launcher
    the child is launched through and performs a resolve-or-refuse census over
    the callee grammar — a bare name, a module-qualified attribute,
    `self.<method>`, and a class-instance method (`C().m()`) resolve to a body;
    a callee that names a launcher-module entity but whose body the walk cannot
    read is refused, not skipped, because an unread body is a hole this audit
    cannot certify. *Recognition*: on every body the walk reaches it refuses
    the deadline concept `timeout` in the three positions it enters a child — a
    `timeout=` keyword on any call, a `timeout` parameter, and a `'timeout'`
    key forwarded through a `**` spread. A bare `**opts` with no `timeout`
    anywhere is deliberately not a fault.

    Enforced: no `timeout` concept, and no unread launcher-module body, on the
    resolved call graph from each harness's launcher. Not enforced, and not
    claimed to be, a deadline reached any other way: (1) the harness's own
    JavaScript, which this guard's input language (Python `ast`) cannot see;
    (2) a helper the launcher modules import from outside themselves;
    (3) a `timeout` parameter defaulted inside a method reached through a
    receiver the walk cannot type to a class (an untypeable receiver is
    external, not refused, to avoid refusing legitimate code); (4) a deadline
    assembled without the word `timeout` — a clock comparison plus a kill, or
    `signal.alarm`; (5) a launcher-module body the census does not put on the
    graph by construction — a class constructor (a bare `C()` call resolves to
    no body), a method reached through a subscript or other
    non-Name/non-Attribute callee, or a decorator that replaces a body at
    runtime; (6) a method inherited from a base class, which the census refuses
    rather than follows, so a deadline-free launcher of that shape is a false
    red.

    (5) and (6) are parked: the property is currently true — none of those
    forms is on the shipped launcher path — and the one-line remedy for (5)'s
    constructor arm (in `_resolve_callee`, return the class's `__init__` key
    for a bare class-name call instead of `[]`) is recorded here and
    deliberately not applied, because it would not fix (6). Each mechanism is
    named so the next maintainer can act on it, the way `_worker_sources.py`
    names the duplicate check's blindness to string-literal JavaScript.
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    trees = {name: ast.parse((tests_dir / name).read_text(encoding='utf-8'))
             for name in _LAUNCHER_MODULES}
    ctx = _launcher_context(trees)
    for name in ('_relayharness.py', '_cdpharness.py'):
        tree = ast.parse((tests_dir / name).read_text(encoding='utf-8'))
        # A Python-level `timeout=` anywhere in the harness is refused.
        sites = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.keyword) and node.arg == 'timeout']
        assert not sites, (name, sites)
        entries, refusals = _harness_entries(tree, ctx)
        assert entries, (name, 'no launcher call resolved from the harness')
        faults = _census(entries, trees, ctx) + refusals
        assert not faults, (name, sorted(entries), faults)


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
