#!/usr/bin/env python3
"""Serviced and attempt-count bounds on work a starved process still owes.

A wall-clock bound rejects or kills work that was never scheduled, where a
serviced or attempt-based bound would have waited (issue 925). Each control
below was watched failing against the wall-clock code it replaces, for the
defect's own reason.
"""
import ast
import sys
from pathlib import Path
from typing import TypeGuard
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

# The starvation scenarios run with the freeze/thaw budget of their own; the
# harness children the wall-timeout guard names do not.
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

# The freeze control's child spends its budget in one deliberate busy-wait
# and ends on its own: a wedged child is the only failure here that never
# ends, and the suite's ceiling is what names that, as it names every hung job.


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


# The modules every harness child is launched through: the gate helpers and
# the neutral file launcher behind them. The guard resolves the launcher each
# caller actually calls (by the callee at its own call site) and follows that
# call graph, so the verdict is bound to the operation, not to one function
# name.
_LAUNCHER_MODULES = ('_stream_fake.py', '_noderun.py')
# The harness modules that launch a child themselves, each also checked for
# a `timeout=` at its own call sites.
_HARNESS_MODULES = ('_relayharness.py', '_cdpharness.py')
# The `subprocess` entry points a child is launched through, and what such a
# launch may legitimately carry. A keyword outside the second is refused
# rather than passed: the concept scan reads one spelling, and a bound renamed
# to anything else would walk past it.
_SUBPROCESS_LAUNCHES = frozenset(
    {'run', 'Popen', 'call', 'check_call', 'check_output'})
_BENIGN_LAUNCH_ARGS = frozenset({
    'args', 'bufsize', 'capture_output', 'check', 'close_fds', 'cwd',
    'encoding', 'env', 'errors', 'executable', 'extra_groups', 'group',
    'input', 'pass_fds', 'restore_signals', 'shell', 'start_new_session',
    'stderr', 'stdin', 'stdout', 'text', 'universal_newlines', 'umask',
    'user'})
# Sentinel for a callee that names a launcher-module entity but whose body the
# walk cannot see. It is a refusal, not a skip: an unread body is a hole in
# this audit, so the audit cannot certify it, and a bound hiding there would
# be a real false green.
_UNRESOLVED = 'unresolved'


def _is_def(
        node: ast.AST,
) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
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


def _subprocess_bindings(tree):
    """The names in `tree` that call a `subprocess` launch, per binding form.

    `import subprocess`, `import subprocess as sp` and `from subprocess
    import run as launch` name one call three ways; a census reading only
    one of them would miss a bound behind the other two.
    """
    modules = {'subprocess'}
    members = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'subprocess':
            members.update(alias.asname or alias.name
                           for alias in node.names
                           if alias.name in _SUBPROCESS_LAUNCHES)
        elif isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names
                           if alias.name == 'subprocess')
    return modules, members


def _is_launch_call(call, launches):
    """Whether a `Call` reaches a `subprocess` launch, by either binding."""
    modules, members = launches
    func = call.func
    if isinstance(func, ast.Attribute):
        return (func.attr in _SUBPROCESS_LAUNCHES
                and getattr(func.value, 'id', None) in modules)
    return isinstance(func, ast.Name) and func.id in members


def _launcher_context(trees):
    """Index every function and method the launcher modules define.

    Bodies are keyed so a callee resolves by bare name, module-qualified
    attribute, `self.<m>` or `<Class>.<m>` / `<Class>().<m>`; collecting
    class methods is what stops that route being an unseen hole. The
    `subprocess` bindings are unioned across the modules: a launcher module
    is a launcher module, so a name one of them imports for its launch is a
    launch binding in all of them.
    """
    functions = {}
    methods = {}
    classes = set()
    local_classes = {}
    stems = set()
    modules, members = {'subprocess'}, set()
    for name, tree in trees.items():
        stems.add(name[:-len('.py')])
        bound_modules, bound_members = _subprocess_bindings(tree)
        modules |= bound_modules
        members |= bound_members
        classes.update(node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef))
        for node in ast.walk(tree):
            if _is_def(node):
                functions.setdefault(node.name, node)
            elif isinstance(node, ast.ClassDef):
                methods.update(_class_methods(node))
        local_classes.update(_module_class_aliases(tree, classes))
    return {'functions': functions, 'methods': methods, 'classes': classes,
            'local_classes': local_classes, 'stems': stems,
            'launches': (modules, members)}


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

    The line is *whose code is it*, not *does it look risky*: a launcher-module
    receiver (module stem, class, `self`, local bound to a class) must resolve
    to a body I can read, or it is `_UNRESOLVED` — an unread body is a hole
    this audit cannot certify. Any other receiver is external (I cannot type it
    as my own code, and refusing it would refuse `dumps.glob(...)`), so it is
    skipped; a `timeout=` keyword on it is still caught by the caller's own
    concept scan.
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


def _numeric_default_faults(function):
    """Every parameter of `function` that defaults to a number.

    A bound spelled under a word the concept scan does not read arrives
    through a signature first, and a launcher module has no numeric
    parameter that is not one.
    """
    args = function.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    paired = list(zip(positional[len(positional) - len(defaults):], defaults))
    paired.extend((arg, default)
                  for arg, default in zip(args.kwonlyargs, args.kw_defaults)
                  if default is not None)
    return [(default.lineno, f'{arg.arg}= number default')
            for arg, default in paired
            if isinstance(default, ast.Constant)
            and isinstance(default.value, (int, float))
            and not isinstance(default.value, bool)]


def _timeout_faults(function, launches):
    """Every place a bound on the child appears in `function`.

    The concept, not the spelling of any one launch, is what keeps this from
    moving when a route is missed. It enters a child five ways: a `timeout=`
    keyword on any call, a `timeout` parameter, a parameter defaulting to a
    number, a `'timeout'` key a `**` spread forwards, and an argument handed
    to a `subprocess` launch that is not one of the launch's own arguments —
    how a bound renamed to `deadline` or `wall` is caught. A bare `**opts`
    with no `timeout` anywhere is deliberately NOT a fault: a spread is not
    evidence of a bound.
    """
    faults = []
    if 'timeout' in _parameter_names(function):
        faults.append((function.lineno, 'timeout parameter'))
    faults.extend(_numeric_default_faults(function))
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
        elif isinstance(node, ast.Call) and _is_launch_call(
                node, launches):
            faults.extend(
                (keyword.value.lineno, f'{keyword.arg}= at a child launch')
                for keyword in node.keywords
                if keyword.arg is not None
                and keyword.arg not in _BENIGN_LAUNCH_ARGS)
    return faults


def _census(entry_bodies, trees, ctx):
    """Every timeout fault and every refusal reachable from the entries.

    A callee that resolves to `_UNRESOLVED` is a refusal (this audit cannot
    see it, so it cannot certify it); a body whose timeout concept the scan
    finds is a fault.
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
        for fault in _timeout_faults(body, ctx['launches']):
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
    """The launcher bodies a tree reaches at its own call sites.

    Returns `(bodies, refusals)`, deduplicated. A caller entry reads only
    the bodies: a caller is not audited code, so a launcher-module entity
    it names without calling is no hole; for a harness module it is one.
    """
    bodies = []
    refusals = []
    for node in ast.walk(harness_tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_callee(node, None, ctx, {})
        if resolved is _UNRESOLVED:
            refusals.append((node.lineno, _UNRESOLVED))
            continue
        for key in resolved or ():
            if key not in bodies:
                bodies.append(key)
    return bodies, refusals


def _caller_roots(tests_dir, ctx):
    """The launcher bodies each module under `tests/` calls, per module.

    Parsing the whole directory rather than naming today's callers is what
    makes the entry set complete: a module that starts calling a launcher
    later is on the graph without this guard being edited."""
    roots = {}
    for path in sorted(tests_dir.glob('*.py')):
        bodies, _ = _harness_entries(
            ast.parse(path.read_text(encoding='utf-8')), ctx)
        if bodies:
            roots[path.name] = bodies
    return roots


def test_the_harness_children_run_without_a_wall_timeout(tmp):
    """Every harness child, inline or from a file, runs with no wall bound.

    A reintroduced wall backstop around an attempt-bounded child is the
    starvation rejection this branch removes. The guard resolves the launcher
    each caller's own call sites reach (see `_resolve_callee` and
    `_timeout_faults` for the resolve-or-refuse census and the bound concept
    it refuses).

    The entry set is what makes the coverage whole: the harness modules
    (each also checked for a `timeout=` at its own call sites) and every
    module in `tests/` reaching a launcher body, derived from the directory
    rather than listed. The census is the same for both, so a bound in
    either launch is refused the same way, for the argument it hands the
    child as well as for the word `timeout`.

    Enforced: no bound concept, and no unread launcher-module body, on the
    resolved call graph from every entry. Not enforced, and not claimed to
    be, a deadline reached any other way: (1) the harness's own JavaScript,
    which this guard's input language (Python `ast`) cannot see; (2) a helper
    the launcher modules import from outside themselves; (3) a `timeout`
    parameter defaulted inside a method reached through a receiver the walk
    cannot type to a class; (4) a deadline assembled without any argument to
    a child launch — a clock comparison plus a kill, or `signal.alarm`; (5) a
    launcher-module body the census does not put on the graph by
    construction — a class constructor (a bare `C()` call resolves to no
    body), a method reached through a subscript or other
    non-Name/non-Attribute callee, or a decorator that replaces a body at
    runtime; (6) a method inherited from a base class, which the census
    refuses rather than follows, so a bound-free launcher of that shape is a
    false red.

    (5) and (6) are parked: the property is currently true, and the one-line
    remedy for (5)'s constructor arm (in `_resolve_callee`, return the class's
    `__init__` key for a bare class-name call instead of `[]`) is recorded here
    and deliberately not applied, because it would not fix (6).
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    trees = {name: ast.parse((tests_dir / name).read_text(encoding='utf-8'))
             for name in _LAUNCHER_MODULES}
    ctx = _launcher_context(trees)
    for name in _HARNESS_MODULES:
        tree = ast.parse((tests_dir / name).read_text(encoding='utf-8'))
        sites = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.keyword) and node.arg == 'timeout']
        assert not sites, (name, sites)
        entries, refusals = _harness_entries(tree, ctx)
        assert entries, (name, 'no launcher call resolved from the harness')
        faults = _census(entries, trees, ctx) + refusals
        assert not faults, (name, sorted(entries), faults)
    roots = _caller_roots(tests_dir, ctx)
    reached = {key for bodies in roots.values() for key in bodies}
    gate = {('fn', 'run_gate', None), ('fn', 'run_node_program', None)}
    assert gate <= reached, (sorted(reached), 'the file gate')
    for name, bodies in sorted(roots.items()):
        faults = _census(bodies, trees, ctx)
        assert not faults, (name, sorted(bodies), faults)


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
