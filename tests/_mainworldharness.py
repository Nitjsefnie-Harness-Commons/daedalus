"""The Node VM harness the MAIN-world settlement-bound controls run in.

`_relayharness.py` drives the shipped worker scripts through a fake browser
so a real command can be watched end to end, but its own size ceiling makes
it the wrong home for a second, larger block of modes. This harness is that
block, focused: it loads only `background.js`, runs the injected MAIN-world
function for real in a page context, and models a worker-side clock the mode
steps so a bound fires deterministically on any host rather than on a wall
clock. The fake `executeScript` genuinely awaits the promise the injected
function returns, so a promise that never settles stays un-settled — the
exact property the settlement bound exists to bound.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_MAINWORLD_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, mode] = process.argv.slice(1);
const postedResults = [];
const evalResolvers = {};
const bgConsole = [];
let hotfixStore = null;
let probeCount = 0;
let injectionSeq = 0;

// Worker-side virtual clock. setTimeout records a deadline the mode steps;
// nothing waits on the wall, so every control below is host-speed
// independent. This is the controlled clock, not a forcing device: a timer
// fires exactly when the stepped clock reaches its deadline, so it models
// real timer semantics rather than manufacturing an interleaving.
const clock = { now: 0, seq: 0, armed: new Map() };
function fakeSetTimeout(callback, delay) {
  const id = ++clock.seq;
  clock.armed.set(id, {
    callback, at: clock.now + (Number(delay) || 0),
  });
  return id;
}
function fakeClearTimeout(id) { clock.armed.delete(id); }
function advanceClock(targetMs) {
  for (;;) {
    let due = null;
    for (const entry of clock.armed) {
      if (entry[1].at <= targetMs
          && (due === null || entry[1].at < due[1].at)) {
        due = entry;
      }
    }
    if (!due) break;
    clock.armed.delete(due[0]);
    clock.now = Math.max(clock.now, due[1].at);
    due[1].callback();
  }
  clock.now = Math.max(clock.now, targetMs);
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

const chrome = {
  storage: {
    local: {
      get: async () => Object.assign({
        'daedalus-token': 'bound-token',
        'daedalus-server': 'test-bridge',
      }, hotfixStore || {}),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query: async () => [{ id: 7, url: '', title: 'Page' }],
    get: async (tabId) => ({ id: tabId, url: '', title: 'Page' }),
    sendMessage: async () => {},
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => { throw new Error('cdp unused in bound harness'); },
    detach: async () => {},
    sendCommand: async () => ({}),
  },
  scripting: {
    async executeScript(injection) {
      if (injection.func.name === '_canUseMainWorldEval') {
        probeCount++;
        if (mode === 'replay-probe-hang' && probeCount === 1) {
          // The first fix's source-free probe never answers, so its
          // injection is never reached. Only a bound covering the whole
          // per-fix operation, not just the injection after it, contains it.
          return new Promise(() => {});
        }
      }
      pageContext.__args = injection.args || [];
      const source = '(' + injection.func.toString()
        + ')(...__args)';
      // vm-load-exempt: runs the function the extension injected
      const result = await vm.runInContext(source, pageContext);
      delete pageContext.__args;
      return [{ result }];
    },
  },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.26.1' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.includes('/stream?')) {
      // Park the SSE stream at its fetch so its reconnect backoff arms no
      // worker timer the controls could mistake for a settlement bound.
      return new Promise(() => {});
    }
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'bound-' + (++injectionSeq) },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  setInterval: () => 1,
  clearInterval() {},
  console: {
    log: (...a) => bgConsole.push({ level: 'log', text: a.join(' ') }),
    warn: (...a) => bgConsole.push({ level: 'warn', text: a.join(' ') }),
    error: (...a) => bgConsole.push({ level: 'error', text: a.join(' ') }),
  },
});
""" + import_scripts_stub('context') + r"""

const pageContext = vm.createContext({
  performance,
  evalResolvers,
  console: { log() {}, warn() {}, error() {} },
});

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

// Attempt-bounded and returns a verdict instead of throwing: a control that
// asserts a bound is absent must report the absence, not hang the child.
async function waitForResult(predicate) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return true;
    await delay();
  }
  return predicate();
}

// The only surface an operator sees for a replay: handleHotfixReplay
// reports failures to the worker's console and nowhere else.
function replayConsole() {
  return bgConsole.filter((e) => e.text.includes('hotfix')
    || e.text.includes('replayed'));
}
function hotfixStoreFor(fixes) {
  hotfixStore = {
    'daedalus-hotfixes': {
      fixes: fixes.map((f) => Object.assign({ permanent: true }, f)),
    },
  };
}

function postedSummary(items) {
  return items.map((item) => ({
    result: item.result === undefined ? null : item.result,
    error: item.error,
    world: item.world,
    exec_ms: item.exec_ms === undefined ? null : item.exec_ms,
  }));
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);

  if (mode === 'eval-timeout') {
    context.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise(() => { evalResolvers.started = true; })',
      chromeTab: 7,
      _did: 'did-eval-timeout',
    };
    vm.runInContext('dispatchCommand(command)', context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    const postedBeforeClock = postedResults.length;
    if (armed) advanceClock(10000);
    const got = await waitForResult(
      () => postedResults.length >= 1);
    return {
      armed,
      postedBeforeClock,
      got,
      posted: postedSummary(postedResults),
    };
  }

  if (mode === 'eval-inside') {
    context.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise((resolve) => {'
        + ' evalResolvers.finish = () => resolve("V"); })',
      chromeTab: 7,
      _did: 'did-eval-inside',
    };
    const execution = vm.runInContext(
      'dispatchCommand(command)', context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    // Settle strictly inside the window: a bound that fires early is
    // crossed here and loses the real value below.
    if (armed) advanceClock(9000);
    if (typeof evalResolvers.finish === 'function') evalResolvers.finish();
    const got = await waitForResult(
      () => postedResults.length >= 1);
    await execution;
    return {
      armed,
      got,
      posted: postedSummary(postedResults),
    };
  }

  if (mode === 'replay-timeout') {
    hotfixStoreFor([
      {
        id: 'fix1',
        code: 'await new Promise(() => {'
          + ' evalResolvers.fix1Started = true; })',
      },
      { id: 'fix2', code: 'evalResolvers.fix2Ran = true' },
    ]);
    vm.runInContext('handleHotfixReplay(7)', context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    if (armed) advanceClock(10000);
    const reported = await waitForResult(
      () => replayConsole().length > 0);
    return {
      armed,
      reported,
      fix1Started: Boolean(evalResolvers.fix1Started),
      ranSecond: Boolean(evalResolvers.fix2Ran),
      replay: replayConsole(),
    };
  }

  if (mode === 'replay-probe-hang') {
    hotfixStoreFor([
      { id: 'fix1', code: 'evalResolvers.fix1Ran = true' },
      { id: 'fix2', code: 'evalResolvers.fix2Ran = true' },
    ]);
    vm.runInContext('handleHotfixReplay(7)', context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    if (armed) advanceClock(10000);
    const reported = await waitForResult(
      () => replayConsole().length > 0);
    return {
      armed,
      reported,
      fix1Ran: Boolean(evalResolvers.fix1Ran),
      ranSecond: Boolean(evalResolvers.fix2Ran),
      replay: replayConsole(),
    };
  }

  if (mode === 'replay-inside') {
    hotfixStoreFor([
      {
        id: 'fix1',
        code: 'await new Promise((resolve) => {'
          + ' evalResolvers.finish1 = () => resolve("ok"); })',
      },
      { id: 'fix2', code: 'evalResolvers.fix2Ran = true' },
    ]);
    vm.runInContext('handleHotfixReplay(7)', context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    if (armed) advanceClock(9000);
    if (typeof evalResolvers.finish1 === 'function') evalResolvers.finish1();
    const cleared = await waitForResult(
      () => bgConsole.some((e) => e.text.includes('replayed')));
    return {
      armed,
      cleared,
      ranSecond: Boolean(evalResolvers.fix2Ran),
      errors: bgConsole.filter(
        (e) => e.level === 'error' && e.text.includes('hotfix')),
      clear: bgConsole.filter((e) => e.text.includes('replayed')),
    };
  }

  throw new Error('unknown harness mode ' + mode);
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""")


def _run_bound_child(mode):
    """Run one MAIN-world bound control and parse its one JSON answer.

    No wall bound: the mode bounds itself by attempt count, so a genuine
    deadlock surfaces as a hung job under the runner's own suite ceiling.
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the MAIN-world bound harness'
    proc = subprocess.Popen(
        [node, '-e', _MAINWORLD_HARNESS,
         str(EXTENSION_ROOT / 'background.js'), mode],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate()
    assert proc.returncode == 0, (proc.returncode, out, err)
    return json.loads(out)


def run_main_world_eval_timeout():
    return _run_bound_child('eval-timeout')


def run_main_world_eval_inside():
    return _run_bound_child('eval-inside')


def run_hotfix_replay_timeout():
    return _run_bound_child('replay-timeout')


def run_hotfix_replay_probe_hang():
    return _run_bound_child('replay-probe-hang')


def run_hotfix_replay_inside():
    return _run_bound_child('replay-inside')
