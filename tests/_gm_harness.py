#!/usr/bin/env python3
"""Node-VM harnesses for the GM storage boundary.

The GM storage namespace, the per-origin and aggregate byte caps, and the
single GM write queue live in the service worker
(extension/worker/gm_storage.js), so every content frame forwards its GM
messages there via chrome.runtime.sendMessage and the worker keys the
partition on sender.origin. Frames also keep a direct handle on the shared
store so the pre-fix content script can be driven against it for the cross-tab
before-evidence.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _node_launch_routing import (  # noqa: E402
    SITE_HANG_MULTIPLE, node_bound_expiry)
from _repo import ROOT  # noqa: E402
from _worker_sources import CONTENT_SCRIPT_PAGE  # noqa: E402

SINGLE_ORIGIN = 'https://storage-test.example.com'
FAILURE_ORIGIN = 'https://storage-failure.example.com'
ORIGIN_A = 'https://alpha.example.com'
ORIGIN_B = 'https://beta.example.com'


# Shared prelude: build the service-worker realm once per harness. makeStorage
# receives the worker's chrome.runtime so a failing store can set lastError.
_PRELUDE = CONTENT_SCRIPT_PAGE + r"""
const fs = require('fs');
const vm = require('vm');

function buildBackground(utilPath, gmPath, makeStorage) {
  const chrome = { runtime: { lastError: null }, storage: { local: null } };
  chrome.storage.local = makeStorage(chrome.runtime);
  const context = {
    chrome, TextEncoder, URL,
    console: { log() {}, error() {}, warn() {} },
  };
  vm.runInNewContext(
    fs.readFileSync(utilPath, 'utf8'), context, { filename: utilPath });
  vm.runInNewContext(
    fs.readFileSync(gmPath, 'utf8'), context, { filename: gmPath });
  // The production caps, read back out of the module's own scope: a test
  // boundary derived from them moves with the constant rather than restating
  // it, and a cap the module does not declare reads null — so its absence is
  // itself observable instead of being papered over with a local copy.
  const constants = vm.runInNewContext(
    '({ GM_QUOTA_BYTES: typeof GM_QUOTA_BYTES === "number"' +
    ' ? GM_QUOTA_BYTES : null, GM_TOTAL_QUOTA_BYTES:' +
    ' typeof GM_TOTAL_QUOTA_BYTES === "number"' +
    ' ? GM_TOTAL_QUOTA_BYTES : null })', context);
  return { handle: context.handleGmStorage, chrome, constants };
}

// A content frame's chrome: a direct handle on the shared store (the pre-fix
// content script's path) plus a sendMessage that routes gm-storage to the
// shared background with sender.origin = this frame's own origin.
// `deadWorker` refuses to answer at all, the two ways a worker that never
// ran can present itself: Chrome's lastError, and a bare absent response.
function frameChrome(storage, background, origin, deadWorker = 'none') {
  const runtime = {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage(msg, callback) {
      if (msg && msg.type === 'gm-storage') {
        if (deadWorker === 'lastError') {
          runtime.lastError = { message: 'Could not establish connection.' };
          try { if (callback) callback(undefined); }
          finally { runtime.lastError = null; }
          return;
        }
        if (deadWorker === 'silent') {
          if (callback) callback(undefined);
          return;
        }
        background.handle(msg, { origin }, (response) => {
          if (callback) callback(response);
        });
        return;
      }
      if (callback) callback(undefined);
    },
    getManifest() { return { version: '0.26.1' }; },
    connect() {
      return { disconnect() {}, postMessage() {},
               onDisconnect: { addListener() {} } };
    },
  };
  return { runtime, storage: { local: storage } };
}

"""

_STORAGE_RELAY_HARNESS = _PRELUDE + r"""
// Single-origin relay: content.js + page.js in one frame over a synchronous
// fake store and a synchronous background. Drives GM through the page's
// promise API for the reserved/invalid-key refusals and the ordinary round
// trip, and through raw dispatch for the keyed-handler pins.
const [contentPath, pagePath, utilPath, gmPath] = process.argv.slice(1);

const NS = 'gm:' + encodeURIComponent(
  'https://storage-test.example.com') + ':';
const listeners = {};
const messages = [];
const posted = [];
const storageCalls = [];
const store = Object.create(null);

function storedValues(keys) {
  const values = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(store, key)) values[key] =
      store[key];
  }
  return values;
}

function makeStorage(runtime) {
  return {
    get(keys, callback) {
      storageCalls.push('get');
      callback(keys === null ? { ...store } : storedValues(keys));
    },
    set(values, callback) {
      storageCalls.push('set');
      Object.assign(store, values);
      callback();
    },
    remove(keys, callback) {
      storageCalls.push('remove');
      for (const key of keys) delete store[key];
      callback();
    },
  };
}

const background = buildBackground(utilPath, gmPath, makeStorage);
const chrome = frameChrome(background.chrome.storage.local, background,
  'https://storage-test.example.com');

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

const context = Object.assign({
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-test.example.com', origin: SINGLE() },
  TextEncoder,
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
}, contentScriptPage());
function SINGLE() { return 'https://storage-test.example.com'; }
vm.runInNewContext(
  fs.readFileSync(contentPath, 'utf8'), context,
  { filename: contentPath });
vm.runInNewContext(
  fs.readFileSync(pagePath, 'utf8'), context,
  { filename: pagePath });

function reset(initial = {}) {
  for (const key of Reflect.ownKeys(store)) delete store[key];
  Object.assign(store, initial);
  messages.length = 0;
  posted.length = 0;
  storageCalls.length = 0;
}

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

function responseFor(reqId) {
  return posted.find((message) =>
    message.direction === 'daedalus-bg-to-page' && message.reqId === reqId);
}

let directReqId = 1000;
function dispatch(handler, key, initial = {}) {
  reset(initial);
  const reqId = ++directReqId;
  const data = {
    direction: 'daedalus-page-to-bg',
    reqId,
    handler,
    key,
    value: 'attacker',
    defaultValue: 'default',
  };
  for (const listener of listeners.message) {
    listener({ source: windowObject, data });
  }
  flushMessages();
  const response = responseFor(reqId);
  return {
    error: response && response.error || null,
    value: response && response.value,
    calls: [...storageCalls],
    storedKeys: Object.keys(store).sort(),
    protectedValue: store['daedalus-server'],
  };
}

async function gmSet(label, key) {
  reset();
  const pending = windowObject.GM.setValue(key, 'attacker');
  flushMessages();
  let status = 'resolved';
  let error = null;
  try {
    await pending;
  } catch (caught) {
    status = 'rejected';
    error = caught && caught.message || String(caught);
  }
  return {
    label,
    status,
    error,
    calls: [...storageCalls],
    storedKeys: Object.keys(store).sort(),
  };
}

async function main() {
  const gmSetCases = [];
  for (const key of ['daedalus-server', 'daedalus-hotfixes',
                     'daedalus-token']) {
    gmSetCases.push(await gmSet(`array:${key}`, [key]));
  }
  gmSetCases.push(await gmSet('string:daedalus-server', 'daedalus-server'));
  gmSetCases.push(await gmSet('string:ordinary', 'ordinary'));

  const invalidHandlers = {};
  for (const handler of ['getValue', 'setValue', 'deleteValue']) {
    invalidHandlers[handler] = dispatch(
      handler, ['daedalus-server'], { 'daedalus-server': 'protected' });
  }

  const coercibleKeys = {
    number: 7,
    object: { toString() { return 'daedalus-server'; } },
    nestedArray: [['daedalus-server']],
    symbol: Symbol('daedalus-server'),
  };
  const coercible = {};
  for (const [label, key] of Object.entries(coercibleKeys)) {
    coercible[label] = dispatch(
      'setValue', key, { 'daedalus-server': 'protected' });
  }

  const ordinaryHandlers = {
    getValue: dispatch('getValue', 'ordinary', { [NS + 'ordinary']: 'kept' }),
    setValue: dispatch('setValue', 'ordinary'),
    deleteValue: dispatch('deleteValue', 'ordinary',
      { [NS + 'ordinary']: 'remove-me' }),
  };

  reset({
    [NS + 'ordinary']: 'visible',
    'daedalus-server': 'hidden',
    'daedalus-hotfixes': 'hidden',
    'daedalus-token': 'hidden',
  });
  const listPending = windowObject.GM.listValues();
  flushMessages();
  const listed = await listPending;

  process.stdout.write(JSON.stringify({
    gmSetCases,
    invalidHandlers,
    coercible,
    ordinaryHandlers,
    listValues: { keys: listed, calls: [...storageCalls] },
  }));
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
"""


_STORAGE_FAILURE_HARNESS = _PRELUDE + r"""
// Every chrome.storage call in the background fails the way Chrome fails one:
// the callback is invoked exactly as on success, the store is left alone, and
// the only trace is chrome.runtime.lastError.
const [contentPath, pagePath, utilPath, gmPath] = process.argv.slice(1);
const FAILURE = 'QUOTA_BYTES quota exceeded';
const listeners = {};
const messages = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    messages.push(message);
  },
};

function makeStorage(runtime) {
  const failing = (callback, value) => {
    runtime.lastError = { message: FAILURE };
    try {
      callback(value);
    } finally {
      runtime.lastError = null;
    }
  };
  return {
    get(keys, callback) { failing(callback, {}); },
    set(values, callback) { failing(callback); },
    remove(keys, callback) { failing(callback); },
  };
}

const background = buildBackground(utilPath, gmPath, makeStorage);
const chrome = frameChrome(background.chrome.storage.local, background,
  'https://storage-failure.example.com');

const context = Object.assign({
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-failure.example.com',
              origin: 'https://storage-failure.example.com' },
  TextEncoder,
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
}, contentScriptPage());
vm.runInNewContext(
  fs.readFileSync(contentPath, 'utf8'), context,
  { filename: contentPath });
vm.runInNewContext(
  fs.readFileSync(pagePath, 'utf8'), context,
  { filename: pagePath });

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

const outcomes = {};
const settled = [];
for (const [name, call] of [
  ['getValue', () => windowObject.GM.getValue('ordinary', 'fallback')],
  ['setValue', () => windowObject.GM.setValue('ordinary', 'value')],
  ['deleteValue', () => windowObject.GM.deleteValue('ordinary')],
  ['listValues', () => windowObject.GM.listValues()],
]) {
  settled.push(call().then(
    (value) => { outcomes[name] = { settled: 'resolved', value: value ??
      null }; },
    (error) => { outcomes[name] = { settled: 'rejected', error:
      String(error && error.message) }; },
  ));
}
flushMessages();

Promise.all(settled).then(() => {
  process.stdout.write(JSON.stringify(outcomes), () => process.exit(0));
});
"""


_DEAD_WORKER_HARNESS = _PRELUDE + r"""
// A frame whose service worker never answers a gm-storage message. The store
// itself works, so what the page sees is the relay's own dead-worker arm and
// nothing else. Chrome reports an undelivered message through lastError; the
// bare absent response is the same failure as the belt under it, and each
// names itself in the message the page receives.
const [contentPath, pagePath, utilPath, gmPath, deadWorker] =
  process.argv.slice(1);

const listeners = {};
const messages = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    messages.push(message);
  },
};

// The store itself works, so a third `makeStorage` would be a copy of a
// double this harness is not the subject of: the dead worker is.
const background = buildBackground(utilPath, gmPath, () => ({
  get(keys, callback) { callback({}); },
  set(values, callback) { callback(); },
  remove(keys, callback) { callback(); },
}));
const chrome = frameChrome(background.chrome.storage.local, background,
  'https://storage-test.example.com', deadWorker);

const context = Object.assign({
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-test.example.com',
              origin: 'https://storage-test.example.com' },
  TextEncoder,
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
}, contentScriptPage());
vm.runInNewContext(
  fs.readFileSync(contentPath, 'utf8'), context, { filename: contentPath });
vm.runInNewContext(
  fs.readFileSync(pagePath, 'utf8'), context, { filename: pagePath });

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

const outcomes = {};
const settled = [];
for (const [name, call] of [
  ['getValue', () => windowObject.GM.getValue('ordinary', 'fallback')],
  ['setValue', () => windowObject.GM.setValue('ordinary', 'value')],
  ['deleteValue', () => windowObject.GM.deleteValue('ordinary')],
  ['listValues', () => windowObject.GM.listValues()],
]) {
  settled.push(call().then(
    (value) => { outcomes[name] = { settled: 'resolved',
      value: value ?? null }; },
    (error) => { outcomes[name] = { settled: 'rejected',
      error: String(error && error.message) }; },
  ));
}
flushMessages();

Promise.all(settled).then(() => {
  process.stdout.write(JSON.stringify(outcomes), () => process.exit(0));
});
"""


# The bound on the child `_run_node` launches. It is a hang detector and not
# a health margin, and the reasoning for that verdict is here because a
# shipped module is not a probe and a reader is entitled to the argument:
#
#   The child loads the SHIPPED `extension/content.js` and `page.js` into a
#   fake window, so the question is whether a change to either could keep
#   the event loop open. It cannot. Every handle one of them could hold it
#   with is a no-op this harness installs — `setInterval` and `setTimeout`
#   return 1 without arming anything, above, and the two modules' only
#   timers are `content.js`'s keep-alive and `page.js`'s CSP poll — and the
#   harness force-exits on its first write, so even a keeper a future
#   module added would not hold the child.
#
#   So the real cost is a fixed unit of work, and what a bound on it must
#   cover is a WEDGED child, never a slow one. The samples are measured
#   with the machine busy, because a wall-clock bound is two margins and a
#   bare 90 measured only the second. This one call site serves three
#   harnesses, and the table is the SLOWEST child's: the other two cost a
#   fraction of it, so one figure covers all three.
#
#   GM_CHILD_SAMPLES_S   the eight slowest-child runs, idle then busy
#   GM_CHILD_SLOWEST_S   18.015  max of those
#   SITE_HANG_MULTIPLE   5       a wedged child, not a slow one
#   GM_CHILD_DEADLINE_S  90      round(18.015 * 5)
#
# The composed figure lands on the 90 this wrote by hand, which is a
# coincidence worth stating rather than hiding. What changed is that the
# number is now re-derivable from the four lines above, and that the expiry
# is a named failure carrying the child's own output instead of a
# `TimeoutExpired` that reaches the suite as an error naming the whole
# command.
#
# One DEVIATION is on the record here, because the argument above concludes
# the child is a fixed unit of work and the branch's own rule says such a
# child carries no call-site `timeout=`. It keeps one anyway, and the reason
# is architectural rather than behavioural: routing this site through
# `tests/_noderun.py` pulls every module this one calls inside that
# launcher's audited path. The figure is composed from the recorded
# table below and the shared multiple in
# `tests/_node_launch_routing.py`, rather than typed at the call site,
# and the expiry is that module's named failure.
GM_CHILD_SAMPLES_S = (3.713, 3.810, 2.981, 4.005,
                      16.873, 7.637, 18.015, 7.419)
GM_CHILD_SLOWEST_S = max(GM_CHILD_SAMPLES_S)
GM_CHILD_DEADLINE_S = round(GM_CHILD_SLOWEST_S * SITE_HANG_MULTIPLE)


def _run_node(harness, content_path=None, with_page=False, extra=()):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension storage boundary'
    ext = ROOT / 'extension'
    argv = [node, '-e', harness,
            str(content_path or (ext / 'content.js')),
            str(ext / 'page.js') if with_page else '',
            str(ext / 'worker' / 'util.js'),
            str(ext / 'worker' / 'gm_storage.js'), *extra]
    try:
        result = subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True,
            timeout=GM_CHILD_DEADLINE_S)
    except subprocess.TimeoutExpired as why:
        raise node_bound_expiry(why, GM_CHILD_DEADLINE_S) from why
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_relay():
    return _run_node(_STORAGE_RELAY_HARNESS, with_page=True)


def run_failure():
    return _run_node(_STORAGE_FAILURE_HARNESS, with_page=True)


def run_dead_worker(dead_worker):
    """GM outcomes over a service worker that never answers."""
    return _run_node(_DEAD_WORKER_HARNESS, with_page=True,
                     extra=(dead_worker,))
