"""The Node VM harness the MAIN-world settlement-bound controls run in.

`_relayharness.py` drives the shipped worker scripts through a fake browser
so a real command can be watched end to end, but its own size ceiling makes
it the wrong home for a second, larger block of modes. This harness is that
block, focused: it loads only `background.js`, runs the injected MAIN-world
function for real in a page context, and models a worker-side clock the mode
steps so a bound fires deterministically on any host rather than on a wall
clock. The fake `executeScript` genuinely awaits the promise the injected
function returns, so a promise that never settles stays un-settled — the
exact property the settlement bound exists to bound. A mode whose subject
never arrives — `waitForResult` exhausting with the eval never dispatching,
so the source never runs and `handleEval` never posts — exits with empty
stdout, and its control reports `JSONDecodeError` on that empty answer. A
decode error here means the thing being waited for never happened; it is not
a verdict, and not a broken harness.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import (  # noqa: E402
    event_target_stub, import_scripts_stub, resolve_target_stub)


_MAINWORLD_HARNESS = (
    event_target_stub() + resolve_target_stub()
    + r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, mode] = process.argv.slice(1);
const postedResults = [];
const evalResolvers = {};
const bgConsole = [];
const messageListeners = [];
const deadlineHistory = [];
const cdpDispatches = [];
let hotfixStore = null;
let probeCount = 0;
let injectionSeq = 0;

// Worker-side virtual clock. setTimeout records a deadline the mode steps;
// nothing waits on the wall, so every control below is host-speed
// independent. This is the controlled clock, not a forcing device: a timer
// fires exactly when the stepped clock reaches its deadline, so it models
// real timer semantics rather than manufacturing an interleaving. Every
// arming is recorded by its DELAY, so a control sees which ceiling the
// worker armed: a second one, or a stray timer, is a second entry, where a
// bare count could not tell them apart.
const clock = { now: 0, seq: 0, armed: new Map() };
function fakeSetTimeout(callback, delay) {
  const id = ++clock.seq;
  const ms = Number(delay) || 0;
  deadlineHistory.push(ms);
  clock.armed.set(id, { callback, delay: ms, at: clock.now + ms });
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
function nextDeadline() {
  let at = null;
  for (const entry of clock.armed.values()) {
    if (at === null || entry.at < at) at = entry.at;
  }
  return at;
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
      // Only the requested keys come back, as the real API answers. A get
      // that returned the whole store would satisfy a hotfix selector
      // reading the wrong key, and every replay control would stay green.
      get: async (keys) => {
        const store = Object.assign({
          'daedalus-token': 'bound-token',
          'daedalus-server': 'test-bridge',
        }, hotfixStore || {});
        const wanted = keys === null || keys === undefined
          ? Object.keys(store) : [].concat(keys);
        const out = {};
        for (const key of wanted) {
          if (key in store) out[key] = store[key];
        }
        return out;
      },
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
""" + INERT_WORKER_APIS + r"""
};

const PAGE_URL = 'https://page.example.com/';
const documents = { 'doc-7': { id: 'doc-7', url: PAGE_URL, live: true } };
let liveDocument = 'doc-7';

// The tab's document. Chrome's resolution of an injection target is the
// shared stub — a bare tab id lands on whatever the tab holds at injection
// time, a documentIds target lands on the document it names, and any other
// shape is refused by name — so the two accessors it takes, inlined at the
// one call site rather than named, are all this double contributes.


// The shared double's executeScript is inert and its debugger refuses every
// call; both are replaced here. The live executeScript runs the injected
// function in a page context, so a promise that never settles stays
// un-settled. It models the MAIN world only — an ISOLATED injection would
// compile under a different CSP — so a world it does not model is refused
// rather than silently run in the wrong one.
chrome.scripting.executeScript = async (injection) => {
  if (injection.world !== 'MAIN') {
    throw new Error('unmodelled injection world ' + injection.world);
  }
  const doc = resolveTarget(
    injection.target, (id) => documents[id],
    () => documents[liveDocument]);
  if (injection.func.name === '_canUseMainWorldEval') {
    probeCount++;
    if ((mode === 'replay-probe-hang' || mode === 'eval-probe-hang')
        && probeCount === 1) {
      // The first probe never answers, so the injection after it is never
      // reached: only a bound over the whole operation contains it, and the
      // eval path's own probe is bounded the same way.
      return new Promise(() => {});
    }
    // A page whose CSP refuses dynamic compilation. The probe runs for real
    // everywhere else; this mode needs the answer that routes the fix to
    // CDP, which then has a dispatch to wedge on.
    if (mode === 'replay-cdp-timeout') {
      return [{ documentId: doc.id, result: false }];
    }
  }
  pageContext.__args = injection.args || [];
  const source = '(' + injection.func.toString() + ')(...__args)';
  // vm-load-exempt: runs the function the extension injected
  const result = await vm.runInContext(source, pageContext);
  delete pageContext.__args;
  return [{ documentId: doc.id, result }];
};

chrome.debugger.attach = async () => {
  if (mode === 'replay-cdp-timeout' || mode === 'eval-probe-hang') return;
  throw new Error('cdp unused in bound harness');
};
chrome.debugger.sendCommand = async (target, method) => {
  if (method !== 'Runtime.evaluate') return {};
  cdpDispatches.push(method);
  // A wedged dispatch is the CDP-routed counterpart of a never-settling
  // page promise: the per-fix bound is what contains it.
  if (mode === 'replay-cdp-timeout') return new Promise(() => {});
  // A plain dispatched value, for the eval whose own probe never answered.
  if (mode === 'eval-probe-hang') {
    return { result: { type: 'number', value: 2 } };
  }
  return {};
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

// Steps the clock to the next armed deadline, yielding between steps so a
// settled await can arm the one after it. Replay reports only once every
// fix has settled, and each fix arms only after the one before it.
async function stepUntilSettled(predicate) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return true;
    const at = nextDeadline();
    if (at !== null) advanceClock(at);
    await delay();
  }
  return predicate();
}

// What every mode reports: the armings seen, by delay, and whatever is
// still armed once the run has settled. A bound that leaves its own timer
// behind delays an MV3 worker suspend, and arms one more per replayed fix.
function observed() {
  const remaining = [...clock.armed.values()].map((entry) => entry.delay);
  remaining.sort((a, b) => a - b);
  return { deadlines: deadlineHistory.slice(), remaining };
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
    // The probe is bounded too, so the injection's own bound is identified
    // by the submitted source having run, not by being the first arming.
    await waitForResult(() => Boolean(evalResolvers.started));
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    const postedBeforeClock = postedResults.length;
    const at = nextDeadline();
    if (at !== null) advanceClock(at);
    const got = await waitForResult(
      () => postedResults.length >= 1);
    return {
      armed,
      postedBeforeClock,
      got,
      ...observed(),
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
    // Wait for the submitted promise to exist, so the clock step lands on
    // the injection's bound and the probe's bound is already cleared.
    await waitForResult(
      () => typeof evalResolvers.finish === 'function');
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    // Settle strictly inside the window: a ceiling below this 9000 is
    // crossed here, and the real-value assertion below fails.
    if (armed) advanceClock(9000);
    if (typeof evalResolvers.finish === 'function') evalResolvers.finish();
    const got = await waitForResult(
      () => postedResults.length >= 1);
    await execution;
    return {
      armed,
      got,
      ...observed(),
      posted: postedSummary(postedResults),
    };
  }

  if (mode === 'eval-probe-hang') {
    context.command = {
      id: '_eval',
      type: 'eval',
      code: '1+1',
      chromeTab: 7,
      _did: 'did-eval-probe-hang',
    };
    vm.runInContext('dispatchCommand(command)', context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    const postedBeforeClock = postedResults.length;
    const at = nextDeadline();
    if (at !== null) advanceClock(at);
    const got = await waitForResult(
      () => postedResults.length >= 1);
    return {
      armed,
      postedBeforeClock,
      got,
      dispatches: cdpDispatches.length,
      ...observed(),
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
    vm.runInContext(
      "handleHotfixReplay(7, 'doc-7', 'https://page.example.com/')",
      context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    const at = nextDeadline();
    if (at !== null) advanceClock(at);
    const reported = await waitForResult(
      () => replayConsole().length > 0);
    return {
      armed,
      reported,
      fix1Started: Boolean(evalResolvers.fix1Started),
      ranSecond: Boolean(evalResolvers.fix2Ran),
      ...observed(),
      replay: replayConsole(),
    };
  }

  if (mode === 'replay-probe-hang') {
    hotfixStoreFor([
      { id: 'fix1', code: 'evalResolvers.fix1Ran = true' },
      { id: 'fix2', code: 'evalResolvers.fix2Ran = true' },
    ]);
    vm.runInContext(
      "handleHotfixReplay(7, 'doc-7', 'https://page.example.com/')",
      context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    const at = nextDeadline();
    if (at !== null) advanceClock(at);
    const reported = await waitForResult(
      () => replayConsole().length > 0);
    return {
      armed,
      reported,
      fix1Ran: Boolean(evalResolvers.fix1Ran),
      ranSecond: Boolean(evalResolvers.fix2Ran),
      ...observed(),
      replay: replayConsole(),
    };
  }

  if (mode === 'replay-cdp-timeout') {
    hotfixStoreFor([
      { id: 'fix1', code: 'await new Promise(() => {})' },
      { id: 'fix2', code: 'await new Promise(() => {})' },
    ]);
    vm.runInContext(
      "handleHotfixReplay(7, 'doc-7', 'https://page.example.com/')",
      context);
    const armed = await waitForResult(
      () => clock.armed.size > 0);
    // Both fixes dispatch through CDP and wedge there, so the second fix's
    // bound is armed only once the first has been refused.
    const reported = await stepUntilSettled(
      () => replayConsole().length > 0);
    return {
      armed,
      reported,
      dispatches: cdpDispatches.length,
      ...observed(),
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
    vm.runInContext(
      "handleHotfixReplay(7, 'doc-7', 'https://page.example.com/')",
      context);
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
      ...observed(),
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


def run_main_world_eval_probe_hang():
    return _run_bound_child('eval-probe-hang')


def run_hotfix_replay_timeout():
    return _run_bound_child('replay-timeout')


def run_hotfix_replay_probe_hang():
    return _run_bound_child('replay-probe-hang')


def run_hotfix_replay_cdp_timeout():
    return _run_bound_child('replay-cdp-timeout')


def run_hotfix_replay_inside():
    return _run_bound_child('replay-inside')
