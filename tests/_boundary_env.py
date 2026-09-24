"""The fake browser the extension-boundary scenarios run inside.

Not a suite itself — run_tests.py only loads `test_*.py`.

Chrome's own APIs, modelled closely enough that the shipped worker cannot
tell: storage that hands back a structured clone, a debugger that counts its
attachments, a fetch whose body arrives one chunk at a time, and a second
context standing in for the worker Chrome restarts after idle suspension.
The scenarios that drive it are in _boundary.

Bridge traffic goes through the shared gate (`_stream_fake.STRICT_FETCH`):
each scenario declares the requests it makes, the gate answers only those and
refuses (and records) everything else, and the scenario reads that record.
`SCENARIO_PLANS` is that declaration, one entry per scenario, injected below
and read back by the runners in _boundary to `assert_gate_clean`.
"""

import json

# run_node_program is re-exported: harnesses that predate the neutral _noderun
# module import it from here. The gate itself imports it from _noderun, so
# this module and _stream_fake do not import each other.
from _noderun import run_node_program  # noqa: F401,E501 pylint: disable=W0611
from _stream_fake import STRICT_FETCH
from _worker_sources import import_scripts_stub

BRIDGE = 'https://initial.example.com'
REPLACEMENT = 'https://replacement.example.com'
RELAY = 'https://big.example.com'
RESULT = 'POST /result'
UPLOAD = 'POST /upload'
SYNC = 'POST /sync-tabs'

_UPLOAD_OK = {'status': 200, 'body': {'path': 'capture.png', 'size': 4}}
_UPLOAD_REJECT = {'status': 400, 'body': {'error': 'invalid path component'}}
# route: the config module retries a 5xx, so the first /result is refused and
# the retry and the sibling command's result answer 200. A per-attempt
# sequence, not one answer for the route.
_ROUTE_RESULTS = [
    {'status': 503, 'body': {'error': 'retry'}},
    {'status': 200},
    {'status': 200},
]


def _blob(chunks):
    return 'GET ' + RELAY + '/blob?chunks=' + str(chunks)


# Every scenario's declaration, recorded from a run of the shipped worker on
# the pre-change tree (a temporary recorder in the old bridgeFetch, five runs
# each, all stable). Boot opens the stream and, now that the tabs.query double
# honours its callback, syncs the tab list; a restarted context repeats both,
# so a restart scenario declares two of each. A request the worker invents is
# outside the plan and is refused and recorded by the gate.
SCENARIO_PLANS = {
    # worker-sources returns the loader trace before its own loadConfig, but
    # background.js's boot still opens the stream and syncs the tab list, so
    # the drained gate records one of each.
    'worker-sources': {'planned': [SYNC], 'planned_stream': [503]},
    # The runtime observer's default: a stub background (the binding controls)
    # makes no bridge request. A caller observing the SHIPPED background must
    # pass its own plan (the boot stream and sync); omitting it is a loud
    # mismatch, not a silent pass.
    'worker-bindings': {'planned': [], 'planned_stream': []},
    'capability-routes': {'planned': [SYNC], 'planned_stream': [503]},
    'unknown-command': {'planned': [SYNC, RESULT], 'planned_stream': [503]},
    'capacity': {'planned': [SYNC, RESULT], 'planned_stream': [503]},
    'expiry': {'planned': [SYNC, RESULT], 'planned_stream': [503]},
    # On the gate: the boot stream fetch and boot's tab sync, then the
    # reconnect phase answered 503 and the watchdog phase answered by a 'hang'
    # entry (a connected 200 whose body never yields). statuses is the
    # gate's stream-answer queue, consumed one per stream fetch.
    'stream-timers': {
        'planned': [SYNC, SYNC],
        'planned_stream': [503, 503, 503, 'hang'],
        'statuses': [503, 503, 503, 'hang'],
    },
    'clear-partitioned': {'planned': [SYNC, RESULT], 'planned_stream': [503]},
    'unblock-zero': {'planned': [SYNC, RESULT], 'planned_stream': [503]},
    'hotfix-race': {'planned': [SYNC, RESULT, RESULT],
                    'planned_stream': [503]},
    'net-capture': {'planned': [SYNC] + [RESULT] * 4, 'planned_stream': [503]},
    'dedup-restart': {'planned': [SYNC, RESULT, SYNC],
                      'planned_stream': [503, 503]},
    'block-rule-restart': {'planned': [SYNC, RESULT, SYNC]
                           + [RESULT] * 3,
                           'planned_stream': [503, 503]},
    'screenshot-target': {'planned': [SYNC, UPLOAD, RESULT],
                          'planned_stream': [503],
                          'answers': {UPLOAD: _UPLOAD_OK}},
    'screenshot-reject': {'planned': [SYNC, UPLOAD, RESULT],
                          'planned_stream': [503],
                          'answers': {UPLOAD: _UPLOAD_REJECT}},
    'route': {
        'planned': [SYNC, UPLOAD, RESULT, RESULT, RESULT],
        'planned_stream': [503, 503],
        # the config rotates to a second bridge, which is a bridge host (the
        # boot stream is fetched from it), not a relay origin.
        'hosts': [BRIDGE, REPLACEMENT],
        'answers': {UPLOAD: _UPLOAD_OK, RESULT: _ROUTE_RESULTS},
    },
    'fetch-bound': {
        'planned': [SYNC] + [_blob(n) for n in (8, 9, 12, 1)],
        'planned_stream': [503],
        # big.example.com is the GM relay target, not a bridge: a permitted
        # non-bridge origin, keyed on the full URL so it cannot collide with a
        # bridge request to the same path.
        'relayHosts': [RELAY],
        'answers': {_blob(n): {'stream': n} for n in (8, 9, 12, 1)},
    },
}


ENVIRONMENT = (
    'const ALL_PLANS = ' + json.dumps(SCENARIO_PLANS) + ';\n'
    + r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, scenario, commandText]
  = process.argv.slice(1);
const changeListeners = [];
const detachListeners = [];
const sentMessages = [];
const timers = [];
const rules = [];
const createdTabs = [];
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
    query: (query, callback) => {
      const modeled = scenario === 'screenshot-target'
        ? windowTabs
          .filter((tab) =>
            (query.active === undefined || tab.active === query.active)
            && (query.windowId === undefined || tab.windowId ==="""
    r""" query.windowId))
          .map((tab) => ({ ...tab }))
        : [{ id: 7, url: 'https://page.example.com' }];
      // registerAllTabs reads the result from a callback; the rest of the
      // worker awaits the promise. Chrome honours both, and delivers the
      // callback on a microtask, so the double does too — otherwise a double
      // that models the call but not its timing lets an ordering defect
      // through.
      if (typeof callback === 'function') {
        queueMicrotask(() => callback(modeled));
      }
      if (scenario === 'route' && Object.keys(query).length === 0) {
        return new Promise((resolve) => {
          tabQueryResolver = resolve;
        });
      }
      return Promise.resolve(modeled);
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
        return 'data:image/png;base64,' + btoa('captured:' +"""
    r""" (active && active.id));
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
      if (attachCalls === 1) throw new Error('Another debugger"""
    r""" is already attached');
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

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared, refuses everything else by status, and records every
// request it sees; the scenarios read that record.
const BRIDGE_URL = 'https://initial.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
// A `let` so the runtime observer (OBSERVER = ENVIRONMENT + observer code) can
// substitute its own plan for a call whose background is a stub; the gate
// reads plan lazily, and its splice-time contract check only needs a
// `planned` array, which every plan here carries.
let plan = ALL_PLANS[scenario] || { planned: [] };
function streamResponse(answer) {
  if (answer === 'hang') {
    // A connected 200 whose body never yields a chunk: the live-but-idle
    // stream the watchdog treats as open. The fetch RESOLVES (so the worker's
    // stream loop arms its watchdog) but read() never settles.
    return {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: () => new Promise(() => {}),
          cancel: () => Promise.resolve(),
        }),
      },
    };
  }
  return response(answer, { error: 'disabled' });
}
// The gate builds a declared {stream: N} answer through the harness's chunk
// factory; this is the harness that models the relay's chunked body.
function chunkedResponse(count) {
  return streamingResponse(count);
}
""" + STRICT_FETCH + r"""
// The scenarios read the gate's records, not a second fake: a projection of
// nonStreamFetches into the {kind, url, token, id, error} rows the worker-
// behaviour assertions consume, and the relay upload bodies they inspect.
const resultPayloads = resultPosts;
function bridgeRequests() {
  return nonStreamFetches.map((record) => {
    const space = record.request.indexOf(' ');
    const target = record.request.slice(space + 1);
    // A relay key is the full URL; a bridge key is the bare path the gate
    // recorded, so it gets the bridge origin back. No scenario posts to a
    // second bridge, so this reconstructs the initial origin today; the
    // record's own `url` field (pinned by the oracle) is the faithful
    // source if one ever does.
    const url = /^https?:\/\//.test(target) ? target : BRIDGE_URL + target;
    const body = record.body || {};
    return {
      kind: url.endsWith('/result') ? 'result'
        : (url.endsWith('/upload') ? 'upload' : 'request'),
      url, token: body.token, id: body.id, error: body.error,
    };
  });
}
function uploadBodies() {
  return nonStreamFetches
    .filter((record) => record.request.endsWith(' /upload'))
    .map((record) => record.body);
}

let relaySequence = 0;

// One contextified worker. A second one models the service worker Chrome
// restarts after idle suspension: fresh script state, same"""
    r""" browser-side stores.
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
""") + import_scripts_stub('workerContext', 'workerSourcePaths') + r"""
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
