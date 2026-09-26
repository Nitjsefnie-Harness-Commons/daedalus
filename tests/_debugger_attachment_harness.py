#!/usr/bin/env python3
"""The chrome the debugger-attachment controls run against.

Chrome allows one debugger per tab, so a second `attach` while one is live is
refused with `Another debugger is already attached` and that refusal reaches
the caller as a failed command. This double refuses a second attach per tab
exactly as Chrome does, and it holds a tab as attached until its detach
PROMISE settles rather than until the call is made. That second half is what
makes the detach/attach window observable at all.

A rejecting `chrome.debugger.detach` is modelled too, because a detach whose
rejection escapes ends the extension's service worker (issue #1161).
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _noderun import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


# Chrome's own words, so a control that reads the failure reads what the
# browser says rather than a spelling only this suite would produce.
REFUSAL = 'Another debugger is already attached'

_ATTACHMENT_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const backgroundPath = process.argv[1];
const spec = JSON.parse(process.argv[process.argv.length - 1]);

// A rejecting `chrome.debugger.detach` is how the extension's service worker
// dies: the rejection escapes as an unhandled one, and Node ends the worker
// with it. Node would take this child down too, so the handler is installed
// to REPORT the rejection rather than to survive it — the assertion below is
// that nothing was reported, and a detach site that stopped handling its
// own rejection shows up here instead of ending the process.
const unhandled = [];
process.on('unhandledRejection', (reason) => {
  unhandled.push(String((reason && reason.message) || reason));
});
const attachCalls = [];
const detachCalls = [];
const bgConsole = [];
const order = [];
const live = new Set();
const settling = new Set();
const posted = [];
let attachFailures = spec.attachFailures || 0;
let enableFailures = spec.enableFailures || 0;
let dispatchInDetach = spec.dispatchInDetach || null;
let releaseOnDetach = null;
const handles = [];
let inFlight = [];
let commandSeq = 0;

function turn() {
  return new Promise((resolve) => setImmediate(resolve));
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

function eventTarget(listeners) {
  return { addListener(listener) { listeners.push(listener); } };
}

const tabRemoved = [];
const detachEvents = [];

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'attach-token',
        'daedalus-server': 'https://bridge.example.com',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget([]),
  },
  tabs: {
    onUpdated: eventTarget([]),
    onCreated: eventTarget([]),
    onRemoved: eventTarget(tabRemoved),
    query: async () => [{ id: 7, url: 'https://page.example.com/',
                          title: 'Page' }],
    get: async (id) => ({ id, url: 'https://page.example.com/', title: 'P' }),
  },
  debugger: {
    onEvent: eventTarget([]),
    onDetach: eventTarget(detachEvents),
    attach(target) {
      const tabId = target.tabId;
      attachCalls.push(tabId);
      order.push('attach:' + tabId);
      if (attachFailures > 0) {
        attachFailures -= 1;
        return Promise.reject(new Error('debugger refused the attach'));
      }
      // A tab stays held until the DETACH settles, not until the call is
      // made: that is the window a claim has to chain through.
      if (live.has(tabId) || settling.has(tabId)) {
        return Promise.reject(new Error(
          'Another debugger is already attached'));
      }
      live.add(tabId);
      return turn();
    },
    detach(target) {
      const tabId = target.tabId;
      detachCalls.push(tabId);
      order.push('detach:' + tabId);
      if (spec.detachFails) {
        return Promise.reject(new Error('detach refused'));
      }
      live.delete(tabId);
      settling.add(tabId);
      // The new command is dispatched from inside the detach call, so it
      // arrives in the same turn as the release that issued it.
      if (dispatchInDetach) {
        const command = dispatchInDetach;
        dispatchInDetach = null;
        dispatch(command);
      }
      return turn().then(() => { settling.delete(tabId); });
    },
    sendCommand(_target, method) {
      if (method === 'Network.enable' && enableFailures > 0) {
        enableFailures -= 1;
        return Promise.reject(new Error('Network.enable failed'));
      }
      return Promise.resolve({ result: { value: method } });
    },
  },
  scripting: {
    executeScript: async () => {
      throw new Error('unmodelled executeScript');
    },
  },
  cookies: { getAll: async () => [], remove: async () => null },
  declarativeNetRequest: {
    getSessionRules: async () => [],
    updateSessionRules: async () => {},
  },
  runtime: {
    lastError: null,
    onMessage: eventTarget([]),
    onConnect: eventTarget([]),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget([]), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init && init.method === 'POST') {
      posted.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    // Park the boot stream: a worker-side reconnect would arm a timer a
    // control could mistake for the claim it is here to observe.
    if (url.includes('/stream')) return new Promise(() => {});
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'attach-' + (++commandSeq) },
  AbortController,
  TextDecoder,
  TextEncoder,
  URL,
  performance,
  btoa,
  atob,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn: (t) => bgConsole.push(t), error() {} },
});
""" + import_scripts_stub('context') + r"""

function dispatch(command) {
  const full = Object.assign(
    { _did: 'did-x' }, command);
  context.stepCommand = full;
  inFlight.push(
    vm.runInContext('dispatchCommand(stepCommand)', context)
      .then(() => undefined));
  return full;
}

// A handle onto the shipped claim module, so a control can drive a release
// the four command call sites would not otherwise produce at this point in
// the sequence.
function claimHandle(command) {
  context.claimTabId = command.tabId;
  context.claimKeep = Boolean(command.keep_session);
  return vm.runInContext(
    'cdpClaimAttachment(claimTabId, { keep: claimKeep })', context);
}

function claims() {
  // `null` rather than a throw when the map is absent: a control must fail
  // on the behaviour it is about, and an exception raised here would report
  // the missing readout as the defect instead.
  const observed = vm.runInContext(
    'typeof _cdpClaims === "undefined" ? null'
    + ' : JSON.stringify(Array.from(_cdpClaims.entries())'
    + '.map(([tabId, entry]) => [tabId, entry.refs, entry.keep]))',
    context);
  return observed === null ? null : JSON.parse(observed);
}

async function settle(times) {
  for (let turn_ = 0; turn_ < (times || 6); turn_++) await new Promise(
    (resolve) => setImmediate(resolve));
}

(async () => {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);

  for (const action of spec.actions || []) {
    // Armed first, because a stale release has to be pending BEFORE the
    // event that drops its entry, or it lands after the newer claim and
    // tests something else.
    if (action.releaseOnEvent !== undefined) {
      releaseOnDetach = () => handles[action.releaseOnEvent].release();
    }
    if (action.dispatch) dispatch(action.dispatch);
    if (action.claim) {
      handles.push(claimHandle(action.claim));
    }
    if (action.release !== undefined) {
      const handle = handles[action.release].release;
      if (spec.doubleRelease) { handle(); handle(); } else { handle(); }
    }
    if (action.detach) {
      // Chrome fires onDetach BECAUSE the tab was detached, so the double
      // has to end the attachment it is announcing. Leaving `live` set would
      // assert a browser state that cannot exist, and the first control
      // written against it would be pinning the fixture.
      live.delete(action.detach);
      settling.delete(action.detach);
      for (const listener of detachEvents) listener({ tabId: action.detach });
      // The stale claimant's release, delivered from inside the event so it
      // lands after the forget and before the newer claim's own work.
      if (releaseOnDetach) {
        const release = releaseOnDetach;
        releaseOnDetach = null;
        release();
      }
    }
    if (action.tabRemoved) {
      for (const listener of tabRemoved) listener(action.tabRemoved);
    }
    if (action.settle) await settle(action.settle);
    if (action.drain) {
      const waiting = inFlight;
      inFlight = [];
      await Promise.all(waiting);
      await settle(8);
    }
  }
  await settle(10);

  process.stdout.write(JSON.stringify({
    attachCalls,
    detachCalls,
    unhandled,
    refused: bgConsole,
    order,
    live: Array.from(live).sort(),
    claims: claims(),
    posted: posted.map((item) => ({
      id: item.id,
      result: item.result === undefined ? null : item.result,
      error: item.error,
    })),
  }), () => process.exit(0));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n',
                       () => process.exit(1));
});
""")


def run_attachment_case(case):
    """Run one attachment case and read what the browser was asked to do.

    Nothing about the property is decided here: this builds a chrome that
    refuses a second attach per tab the way Chrome does, dispatches the
    commands the case names, and reports the attach/detach calls in the
    order they happened beside what each command was answered with.
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the debugger attachment harness'
    result = run_node_program(
        node, _ATTACHMENT_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=json.dumps(case))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _cdp(command_id, tab_id=7, **extra):
    return dict({'id': command_id, 'type': 'cdp',
                 'method': 'Runtime.enable'}, **extra)


def _by_id(outcome):
    return {row['id']: row for row in outcome['posted']}


def _ran(outcome, command_id):
    """The CDP method a command's own answer carries.

    The fake answers `sendCommand` with the method it was given, so a
    command's result says which command ran rather than merely that one did.
    """
    return _by_id(outcome)[command_id]['result']['result']['value']
