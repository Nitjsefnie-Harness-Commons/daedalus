"""Shared Node-VM harness for the extension/worker/tabs.js suites.

Not a suite itself — run_tests.py only loads `test_*.py`. It owns the whole
worker control plane the tab suites share and hands back the posted
postResult payloads alongside every recorded chrome call. The suites supply
the plan and assert the answer and the calls together; this module never
decides what a handler should do.

The chrome surface is the shared stub (tests/_worker_sources.chrome_stub)
plus the scenario deltas the tab handlers need; the retained eventTarget is
the shared helper (tests/_worker_sources.event_target_stub).

The plan may also ask for the deferred surface. `runTimers` makes the
setTimeout stand-in run and record its callback; `fetchTimings` seeds the
timing ring before any command; `hasNativeToBase64` pins whether the realm
carries the native toBase64. The run reports the boot's installed `version`,
the observed `hasNativeToBase64`, and the timers it ran. Each is opt-in and
inert by default. The seeds and the pin shape the world the handler reads;
none of them hands the handler a value to echo back.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import (  # noqa: E402
    chrome_stub, event_target_stub, import_scripts_stub)

TOKEN = 'tabs-token'
SERVER = 'https://bridge.example.com'

_TABS_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);
const resultPayloads = [];
const calls = [];
let createdCount = 0;

function copy(value) {
  return value === undefined
    ? undefined
    : JSON.parse(JSON.stringify(value));
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

function record(api, args) {
  calls.push({ api, args: copy(args) });
}

// A plan can name a chrome surface that must reject, so a handler's catch
// arm is reachable and the ordering of its postResult against the chrome
// call is observable. The call is recorded before it rejects, exactly as the
// create double records before throwing.
function maybeReject(api) {
  const reject = (plan.chromeReject || {})[api];
  if (reject === undefined) return;
  if (typeof reject === 'string') throw new Error(reject);
  throw reject;
}

const DEFAULT_ACTIVE_TABS = [
  { id: 7, windowId: 3, url: 'https://page.example.com', title: 'Page' },
];
"""
    + event_target_stub()
    + chrome_stub(f"'{TOKEN}'", f"'{SERVER}'", '(() => ({}))')
    + r"""
// The tab handlers drive chrome surfaces the shared CDP stub does not
// model, so these are the scenario deltas layered on top of it.
chrome.tabs.query = function(query, callback) {
  // Boot's registerAllTabs calls query with a callback and an empty query.
  // Answer it off the record so only the handlers' own promise-form
  // queries appear in the call log.
  if (typeof callback === 'function') {
    callback([]);
    return undefined;
  }
  record('tabs.query', [query]);
  const tabs = plan.activeTabs || DEFAULT_ACTIVE_TABS;
  return Promise.resolve(tabs.map((tab) => ({ ...tab })));
};
chrome.tabs.create = (details) => {
  record('tabs.create', [details]);
  // createSyncReject throws synchronously, so a map() over the urls aborts
  // and the caller's outer catch is reached; createReject rejects a promise,
  // so Promise.allSettled collects it as a per-url error.
  const sync = (plan.createSyncReject || {})[details.url];
  if (sync !== undefined) {
    if (typeof sync === 'string') throw new Error(sync);
    throw sync;
  }
  const reject = (plan.createReject || {})[details.url];
  if (reject !== undefined) {
    if (typeof reject === 'string') {
      return Promise.reject(new Error(reject));
    }
    return Promise.reject(reject);
  }
  createdCount += 1;
  // A resolved Tab carries Chrome's own url, not the requested one; handing
  // back the request would make the two indistinguishable. A host-free
  // fragment marks the value as this double's own without naming a
  // deployment host.
  const resolved = details.url + '#resolved';
  return Promise.resolve({
    id: 99 + createdCount, windowId: 1, url: resolved,
  });
};
chrome.tabs.update = async (tabId, changes) => {
  record('tabs.update', [tabId, changes]);
  maybeReject('tabs.update');
  return { id: tabId, windowId: 4 };
};
chrome.tabs.reload = async (tabId, options) => {
  record('tabs.reload', [tabId, options]);
  maybeReject('tabs.reload');
};
chrome.tabs.get = async () => {
  throw new Error('unmodelled chrome.tabs.get');
};
chrome.tabs.sendMessage = async () => {
  throw new Error('unmodelled chrome.tabs.sendMessage');
};
chrome.windows = {
  update: async (windowId, details) => {
    record('windows.update', [windowId, details]);
    maybeReject('windows.update');
    return {};
  },
};
chrome.scripting.insertCSS = async (details) => {
  record('scripting.insertCSS', [details]);
  maybeReject('scripting.insertCSS');
};
chrome.scripting.removeCSS = async (details) => {
  record('scripting.removeCSS', [details]);
  maybeReject('scripting.removeCSS');
};
// The reload handler reaches chrome.runtime.reload through a timer; the
// shared runtime carries no reload, so record it here. Like every other
// surface it is a recorder, observable but never substituted.
chrome.runtime.reload = () => {
  record('runtime.reload', []);
};

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/result') && init.method === 'POST') {
    const item = JSON.parse(init.body);
    resultPayloads.push({
      id: item.id,
      tabId: item.tabId,
      result: item.result,
      error: item.error,
    });
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  if (/\/(register|sync-tabs|unregister)$/.test(url)) {
    return response(200, { ok: true });
  }
  throw new Error('unexpected fetch: ' + url);
}

// The deferred stand-in. With runTimers unset it returns an inert handle and
// runs nothing. With runTimers set it records the timer — id, delay, and
// whether the callback reached chrome.runtime.reload — and runs the callback
// in the same tick, so a deferred effect is observable with no wall-clock
// margin and no sleep. Running is armed only while a command is dispatched,
// so the boot's own timers stay inert and cannot hold the process open.
const timers = [];
let nextTimerId = 0;
let timersArmed = false;
function reloadCalls() {
  return calls.filter((call) => call.api === 'runtime.reload').length;
}
function setTimeoutStandIn(callback, delay) {
  if (!plan.runTimers || !timersArmed) return 1;
  const reloadsBefore = reloadCalls();
  const timer = { id: ++nextTimerId, delay, ran: false, error: null,
                  ranReload: false };
  timers.push(timer);
  timer.ran = true;
  try {
    callback();
  } catch (error) {
    timer.error = String((error && error.message) || error);
  }
  if (reloadCalls() > reloadsBefore) timer.ranReload = true;
  return timer.id;
}

const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: () => 'relay-1' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  atob,
  btoa,
  setTimeout: setTimeoutStandIn,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
"""
    + import_scripts_stub('context')
    + r"""

async function dispatch(command) {
  context.nextCommand = command;
  await vm.runInContext('dispatchCommand(nextCommand)', context);
}

async function run() {
  // _hasNativeToBase64 is a const evaluated the moment util.js loads, so the
  // pin has to land before the boot runs. Adding or removing the builtin is a
  // property of this realm, not a value handed to the handler: the handler
  // still reads its own const, so a substituted answer stays observable.
  if (Object.prototype.hasOwnProperty.call(plan, 'hasNativeToBase64')) {
    const pin = plan.hasNativeToBase64
      ? 'Uint8Array.prototype.toBase64 = function () { return ""; };'
      : 'delete Uint8Array.prototype.toBase64;';
    // vm-load-exempt: runs a computed realm pin, not a shipped file
    vm.runInContext(pin, context);
  }
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  if (Array.isArray(plan.fetchTimings)) {
    // The ring is world state: these entries are values the handler reads
    // back out of the world, never values it was given and must echo.
    context.__seededTimings = plan.fetchTimings;
    vm.runInContext('_fetchTimings.push(...__seededTimings)', context);
    delete context.__seededTimings;
  }
  const outcomes = [];
  timersArmed = plan.runTimers === true;
  for (const command of plan.commands || []) {
    try {
      await dispatch(command);
      outcomes.push({ settled: 'resolved' });
    } catch (error) {
      outcomes.push({ settled: 'rejected', message: error.message });
    }
  }
  timersArmed = false;
  return {
    calls,
    posted: resultPayloads,
    outcomes,
    timers,
    version: vm.runInContext('VERSION', context),
    hasNativeToBase64: vm.runInContext('_hasNativeToBase64', context),
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""").replace('__TOKEN__', TOKEN).replace('__SERVER__', SERVER)


def run_tabs(commands, **plan):
    """Run the worker VM over `commands`; return calls/posted/outcomes.

    `plan` carries the fake chrome's behaviour: `activeTabs` (what
    tabs.query resolves), `createReject` (url -> rejection message).
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    payload = dict(plan)
    payload['commands'] = commands
    result = run_node_program(
        node, _TABS_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT, payload=payload)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def command(**fields):
    cmd = {'id': 'cmd-1', '_did': 'did-1'}
    cmd.update(fields)
    return cmd


def apis(outcome, *names):
    """Ordered [api, args] pairs for the named chrome surfaces."""
    return [[c['api'], c['args']] for c in outcome['calls']
            if c['api'] in names]
