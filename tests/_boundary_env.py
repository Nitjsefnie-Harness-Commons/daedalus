"""The fake browser the extension-boundary scenarios run inside.

Not a suite itself — run_tests.py only loads `test_*.py`.

Chrome's own APIs, modelled closely enough that the shipped worker cannot
tell: storage that hands back a structured clone, a debugger that counts its
attachments, a fetch whose body arrives one chunk at a time, and a second
context standing in for the worker Chrome restarts after idle suspension.
The scenarios that drive it are in _boundary.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from _worker_sources import import_scripts_stub


def run_node_program(node, program, arguments, *, cwd, payload=None,
                     timeout=30):
    """Run a Node program from a closed, automatically cleaned file."""
    with tempfile.TemporaryDirectory(prefix='daedalus-node-') as directory:
        program_path = Path(directory) / 'program.js'
        prologue = 'process.argv.splice(1, 1);'
        if payload is not None:
            prologue += f' process.argv.push({json.dumps(payload)});'
        prologue += '\n'
        program_path.write_text(
            prologue + program, encoding='utf-8')
        return subprocess.run(
            [node, str(program_path), *arguments], cwd=cwd,
            capture_output=True, text=True, timeout=timeout)


ENVIRONMENT = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, scenario, commandText]
  = process.argv.slice(1);
const changeListeners = [];
const detachListeners = [];
const sentMessages = [];
const timers = [];
const requests = [];
const resultPayloads = [];
const rules = [];
const createdTabs = [];
const uploadedData = [];
const windowTabs = [
  { id: 7, windowId: 3, active: true, url: 'about:blank#active' },
  { id: 8, windowId: 3, active: false, url: 'about:blank#target' },
];
const activations = [];
const messageListeners = [];
const cookieJar = [];
const removeCalls = [];
const storageStore = {
  'daedalus-token': 'initial-token',
  'daedalus-server': 'https://initial.example.com',
};
let captureResolver;
let tabQueryResolver;
let nextTimerId = 0;
let resultAttempts = 0;
let attachCalls = 0;
let detachCalls = 0;
const workerSourcePaths = new WeakMap();

// chrome.storage.local hands back a structured clone, so a reader that has not
// written yet cannot see another writer's in-flight mutation.
function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
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

function eventTarget(listeners = null) {
  return {
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

function schedule(callback, delay) {
  const timer = {
    id: ++nextTimerId,
    callback,
    delay,
    cleared: false,
  };
  timers.push(timer);
  if (scenario === 'route' && (delay === 300 || delay === 600)) {
    setImmediate(() => {
      if (!timer.cleared) callback();
    });
  }
  return timer.id;
}

function clearScheduled(id) {
  const timer = timers.find((candidate) => candidate.id === id);
  if (timer) timer.cleared = true;
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const key of keys) {
          if (key in storageStore) out[key] = copy(storageStore[key]);
        }
        return out;
      },
      set: async (entries) => {
        for (const key of Object.keys(entries)) {
          storageStore[key] = copy(entries[key]);
        }
      },
      remove: async (keys) => {
        for (const key of keys) delete storageStore[key];
      },
    },
    onChanged: eventTarget(changeListeners),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    create: async (details) => {
      createdTabs.push(details);
      return { id: 100 + createdTabs.length, windowId: 1, url: details.url };
    },
    query: async (query) => {
      if (scenario === 'screenshot-target') {
        return windowTabs
          .filter((tab) =>
            (query.active === undefined || tab.active === query.active)
            && (query.windowId === undefined || tab.windowId === query.windowId))
          .map((tab) => ({ ...tab }));
      }
      if (scenario === 'route' && Object.keys(query).length === 0) {
        return new Promise((resolve) => {
          tabQueryResolver = resolve;
        });
      }
      return [{ id: 7, url: 'https://page.example.com' }];
    },
    get: async (tabId) => {
      const known = windowTabs.find((tab) => tab.id === tabId);
      if (known) return { ...known, title: 'Page' };
      return {
        id: tabId,
        windowId: 3,
        url: 'https://page.example.com',
        title: 'Page',
      };
    },
    update: async (tabId, changes) => {
      if (changes && changes.active) {
        activations.push(tabId);
        for (const tab of windowTabs) tab.active = tab.id === tabId;
      }
      const updated = windowTabs.find((tab) => tab.id === tabId);
      return updated ? { ...updated } : { id: tabId, windowId: 3 };
    },
    sendMessage: async (_tabId, message) => {
      sentMessages.push(message);
    },
    captureVisibleTab: async () => {
      if (scenario === 'screenshot-target') {
        // A capture returns whatever is ACTIVE in the window, which is the
        // whole point: naming a tab does not select it.
        const active = windowTabs.find((tab) => tab.active);
        return 'data:image/png;base64,' + btoa('captured:' + (active && active.id));
      }
      if (scenario !== 'route') return 'data:image/png;base64,AA==';
      return new Promise((resolve) => {
        captureResolver = resolve;
      });
    },
  },
  scripting: {
    executeScript: async () => {
      throw new Error('scripting unavailable in residual relay test');
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(detachListeners),
    attach: async () => {
      attachCalls++;
      if (scenario !== 'net-capture') {
        throw new Error('debugger unavailable in residual relay test');
      }
      // Attempt 1 models a tab another client already owns; attempt 2 attaches
      // but fails to enable the domain.
      if (attachCalls === 1) throw new Error('Another debugger is already attached');
    },
    detach: async () => {
      detachCalls++;
    },
    sendCommand: async (_target, method) => {
      if (method === 'Network.enable' && attachCalls === 2) {
        throw new Error('Network.enable failed');
      }
      return {};
    },
  },
  cookies: {
    getAll: async () => cookieJar.map((cookie) => ({ ...cookie })),
    remove: async (details) => {
      removeCalls.push(details);
      // Chrome matches a partitioned cookie only when the partition is named,
      // and answers null when nothing matched -- which is the whole bug: the
      // caller counted a removal that never happened.
      const partition = JSON.stringify(details.partitionKey || null);
      const at = cookieJar.findIndex((cookie) =>
        cookie.name === details.name
        && JSON.stringify(cookie.partitionKey || null) === partition);
      if (at === -1) return null;
      const [gone] = cookieJar.splice(at, 1);
      return { name: gone.name };
    },
  },
  declarativeNetRequest: {
    getSessionRules: async () => rules.map((rule) => ({ ...rule })),
    updateSessionRules: async (change) => {
      for (const rule of change.addRules) {
        if (rules.some((existing) => existing.id === rule.id)) {
          throw new Error('Duplicate rule ID ' + rule.id);
        }
      }
      // Removal is honoured, not ignored: what the unblock scenario asserts
      // is which rules are STILL installed afterwards.
      for (const id of change.removeRuleIds || []) {
        const at = rules.findIndex((existing) => existing.id === id);
        if (at !== -1) rules.splice(at, 1);
      }
      rules.push(...change.addRules);
    },
  },
  runtime: {
    onMessage: eventTarget(messageListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

// A body handed out one chunk at a time, so the harness can see how much of
// it the relay actually pulled before deciding. A response that reports its
// size only at the end cannot tell a bounded read apart from a full read
// followed by a size check.
const CHUNK_BYTES = 1024 * 1024;
let streamPlan = null;

function streamingResponse(chunkCount) {
  let handed = 0;
  streamPlan = { chunkCount, handed: 0, cancelled: false };
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    url: 'https://big.example.com/blob',
    headers: { forEach() {} },
    body: {
      getReader() {
        return {
          async read() {
            if (handed >= chunkCount) return { done: true, value: undefined };
            handed += 1;
            streamPlan.handed = handed;
            return { done: false, value: new Uint8Array(CHUNK_BYTES) };
          },
          async cancel() { streamPlan.cancelled = true; },
        };
      },
    },
  };
}

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.startsWith('https://big.example.com/')) {
    return streamingResponse(Number(new URL(url).searchParams.get('chunks')));
  }
  if (url.endsWith('/upload') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    requests.push({
      kind: 'upload', url, token: payload.token, id: payload.id,
    });
    uploadedData.push(payload.data);
    if (scenario === 'screenshot-reject') {
      return response(400, { error: 'invalid path component' });
    }
    return response(200, { path: 'capture.png', size: 4 });
  }
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    resultPayloads.push(payload);
    requests.push({
      kind: 'result', url, token: payload.token, id: payload.id,
      error: payload.error,
    });
    resultAttempts++;
    if (scenario === 'route' && resultAttempts === 1) {
      return response(503, { error: 'retry' });
    }
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  return response(200, { ok: true });
}

let relaySequence = 0;

// One contextified worker. A second one models the service worker Chrome
// restarts after idle suspension: fresh script state, same browser-side stores.
function makeContext() {
  const workerContext = vm.createContext({
    chrome,
    fetch: bridgeFetch,
    crypto: { randomUUID: () => 'relay-' + (++relaySequence) },
    AbortController,
    TextDecoder,
    URL,
    performance,
    btoa,
    setTimeout: schedule,
    clearTimeout: clearScheduled,
    setInterval: schedule,
    clearInterval: clearScheduled,
    console: { log() {}, warn() {}, error() {} },
  });
""" + import_scripts_stub('workerContext', 'workerSourcePaths') + r"""
  return workerContext;
}

const context = makeContext();

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}
"""
