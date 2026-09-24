#!/usr/bin/env python3
"""Node-VM harnesses for the GM storage boundary in content.js.

Three harnesses, all over the shipped extension/content.js and a fake
chrome.storage.local:

- a single-origin relay harness (content.js + page.js) driving GM through the
  page's promise API, for the reserved/invalid-key refusals and the ordinary
  round trip;
- a failure harness where every storage call reports Chrome's lastError, for
  the write that must reject rather than resolve;
- a two-origin isolation harness over one shared store, for the per-origin
  partition and the per-origin byte quota.

They live here so test_gm_storage.py holds the pins rather than the fixtures.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import ROOT  # noqa: E402

SINGLE_ORIGIN = 'https://storage-test.example.com'
FAILURE_ORIGIN = 'https://storage-failure.example.com'
ORIGIN_A = 'https://alpha.example.com'
ORIGIN_B = 'https://beta.example.com'

_STORAGE_RELAY_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath] = process.argv.slice(1);
const ORIGIN = 'https://storage-test.example.com';
const NS = 'gm:' + encodeURIComponent(ORIGIN) + ':';
const listeners = {};
const messages = [];
const posted = [];
const storageCalls = [];
const store = Object.create(null);

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

function storedValues(keys) {
  const values = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(store, key)) values[key] ="""
                          r""" store[key];
  }
  return values;
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
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
    },
  },
};

const context = {
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-test.example.com', origin: ORIGIN },
  TextEncoder,
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
};
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
  for (const key of ['daedalus-server', 'daedalus-hotfixes',"""
                          r""" 'daedalus-token']) {
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
    getValue: dispatch('getValue', 'ordinary', { [NS + 'ordinary']:"""
                          r""" 'kept' }),
    setValue: dispatch('setValue', 'ordinary'),
    deleteValue: dispatch('deleteValue', 'ordinary', { [NS + 'ordinary']:"""
                          r""" 'remove-me' }),
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
""")


_STORAGE_FAILURE_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

// Every chrome.storage call fails the way Chrome fails one: the callback is
// invoked exactly as on success, the store is left alone, and the only trace
// is chrome.runtime.lastError — which Chrome clears once the callback
// returns, so it is set around the call and cleared after it.
const [contentPath, pagePath] = process.argv.slice(1);
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

function failing(callback, value) {
  chrome.runtime.lastError = { message: FAILURE };
  try {
    callback(value);
  } finally {
    chrome.runtime.lastError = null;
  }
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
      get(keys, callback) { failing(callback, {}); },
      set(values, callback) { failing(callback); },
      remove(keys, callback) { failing(callback); },
    },
  },
};

const context = {
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
};
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
    (value) => { outcomes[name] = { settled: 'resolved', value: value ??"""
                            r""" null }; },
    (error) => { outcomes[name] = { settled: 'rejected', error:"""
                            r""" String(error && error.message) }; },
  ));
}
flushMessages();

Promise.all(settled).then(() => {
  process.stdout.write(JSON.stringify(outcomes), () => process.exit(0));
});
""")


_TWO_ORIGIN_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

// One shared chrome.storage.local — the single extension store — observed
// through two content-script frames, one per location.origin. Everything the
// page sends is attacker-controlled, so the origin each frame is served from
// can only come from that frame's own location.
const [contentPath] = process.argv.slice(1);
const ORIGIN_A = 'https://alpha.example.com';
const ORIGIN_B = 'https://beta.example.com';
const QUOTA_BYTES = 1048576;
const store = Object.create(null);
const storageCalls = [];
let reqCounter = 0;

function storedValues(keys) {
  const values = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(store, key)) values[key] ="""
                          r""" store[key];
  }
  return values;
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
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
    },
  },
};

function createFrame(origin, hostname) {
  const listeners = {};
  const posted = [];
  const windowObject = {
    addEventListener(type, listener) {
      (listeners[type] ||= []).push(listener);
    },
    postMessage(message) {
      posted.push(message);
    },
  };
  const context = {
    window: windowObject,
    chrome,
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    location: { origin, hostname },
    TextEncoder,
    setInterval: () => 1,
    clearInterval() {},
    setTimeout: () => 1,
    clearTimeout() {},
    crypto: { randomUUID: () => 'frame-uuid' },
    console: { log() {}, error() {} },
  };
  vm.runInNewContext(
    fs.readFileSync(contentPath, 'utf8'), context,
    { filename: contentPath });

  function send(handler, key, value, extra) {
    posted.length = 0;
    storageCalls.length = 0;
    const reqId = ++reqCounter;
    const data = Object.assign({
      direction: 'daedalus-page-to-bg', reqId, handler, key, value,
      defaultValue: 'DEFAULT',
    }, extra || {});
    for (const listener of (listeners.message || [])) {
      listener({ source: windowObject, data });
    }
    const reply = posted.find((m) =>
      m.direction === 'daedalus-bg-to-page' && m.reqId === reqId) || null;
    return {
      error: (reply && reply.error) || null,
      value: reply ? reply.value : undefined,
      keys: (reply && reply.keys) || null,
      calls: [...storageCalls],
      storeKeys: Object.keys(store).sort(),
    };
  }

  return { origin, send };
}
function resetStore() {
  for (const key of Object.keys(store)) delete store[key];
  storageCalls.length = 0;
}

function nsKey(origin, key) {
  return 'gm:' + encodeURIComponent(origin) + ':' + key;
}

function bigString(n) { return 'x'.repeat(n); }

function main() {
  const a = createFrame(ORIGIN_A, 'alpha.example.com');
  const b = createFrame(ORIGIN_B, 'beta.example.com');
  const out = {};

  // B must not read A's GM value.
  resetStore();
  a.send('setValue', 'secret', 'A-value');
  const bReadsA = b.send('getValue', 'secret');
  const aReadsOwn = a.send('getValue', 'secret');
  out.readIsolation = { bValue: bReadsA.value, aValue: aReadsOwn.value };

  // B must not overwrite A's GM value.
  resetStore();
  a.send('setValue', 'shared', 'A-value');
  b.send('setValue', 'shared', 'B-value');
  const aAfterOverwrite = a.send('getValue', 'shared');
  const bAfterOverwrite = b.send('getValue', 'shared');
  out.overwriteIsolation = {
    aValue: aAfterOverwrite.value,
    bValue: bAfterOverwrite.value,
    storeKeys: aAfterOverwrite.storeKeys,
  };

  // B's listValues must not name A's GM key.
  resetStore();
  a.send('setValue', 'a-only', 'x');
  const bList = b.send('listValues');
  const aList = a.send('listValues');
  out.listIsolation = { bKeys: bList.keys, aKeys: aList.keys };

  // B's deleteValue must not remove A's GM key.
  resetStore();
  a.send('setValue', 'a-key', 'A-value');
  b.send('deleteValue', 'a-key');
  const aAfterDelete = a.send('getValue', 'a-key');
  out.deleteIsolation = {
    aValue: aAfterDelete.value, storeKeys: aAfterDelete.storeKeys };

  // The quota is per origin and ignores the extension's own keys: a large
  // daedalus-token sits in the store, A's own-partition write still fits, and
  // A's oversized write is refused without reaching set.
  resetStore();
  store['daedalus-token'] = bigString(1500000);
  const quotaFits = a.send('setValue', 'small', 'tiny');
  const quotaCross = a.send('setValue', 'huge', bigString(1500000));
  out.quota = {
    fitsError: quotaFits.error, fitsCalls: quotaFits.calls,
    crossError: quotaCross.error, crossCalls: quotaCross.calls,
    crossStoreKeys: quotaCross.storeKeys,
  };

  // A page that puts another origin in the payload is served its own.
  resetStore();
  a.send('setValue', 'secret', 'A-value');
  const spoofedRead = b.send('getValue', 'secret', undefined, {
    origin: ORIGIN_A, hostname: ORIGIN_A, location: { origin: ORIGIN_A } });
  b.send('setValue', 'spoofed', 'B-value', {
    origin: ORIGIN_A, hostname: ORIGIN_A });
  out.spoof = {
    value: spoofedRead.value,
    aKeys: a.send('listValues').keys,
    bKeys: b.send('listValues').keys,
  };

  // A write that fits reaches set and replies with no error.
  resetStore();
  const fits = a.send('setValue', 'k', 'small');
  out.fits = { error: fits.error, calls: fits.calls };

  // A write past the cap is refused and set is never called.
  resetStore();
  const cross = a.send('setValue', 'k', bigString(1500000));
  out.cross = { error: cross.error, calls: cross.calls,
                storeKeys: cross.storeKeys };

  // A replace is old-out/new-in: a value that only fits because the value it
  // replaces stops counting.
  resetStore();
  a.send('setValue', 'k', bigString(QUOTA_BYTES - 1000));
  const replaced = a.send('setValue', 'k', bigString(QUOTA_BYTES - 100));
  out.replace = { error: replaced.error, calls: replaced.calls };

  // A delete frees budget, because the sum is recomputed every write.
  resetStore();
  a.send('setValue', 'k', bigString(QUOTA_BYTES - 200));
  const addRefused = a.send('setValue', 'j', bigString(500));
  a.send('deleteValue', 'k');
  const addAccepted = a.send('setValue', 'j', bigString(500));
  out.deleteFrees = {
    refusedError: addRefused.error, refusedCalls: addRefused.calls,
    acceptedError: addAccepted.error, acceptedCalls: addAccepted.calls,
  };

  // A partition already over the cap refuses every page write, set uncalled.
  resetStore();
  store[nsKey(ORIGIN_A, 'over')] = bigString(1500000);
  const alreadyOver = a.send('setValue', 'anything', 'small');
  out.alreadyOver = { error: alreadyOver.error, calls: alreadyOver.calls };

  // A value that cannot be JSON.stringify is refused, never reaching set.
  resetStore();
  const circular = {};
  circular.self = circular;
  const unserialisable = a.send('setValue', 'k', circular);
  out.unserialisable = { error: unserialisable.error,
                         calls: unserialisable.calls,
                         storeKeys: unserialisable.storeKeys };

  return out;
}

process.stdout.write(JSON.stringify(main()));
""")


def _run_node(harness, with_page):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension storage boundary'
    argv = [node, '-e', harness, str(ROOT / 'extension' / 'content.js')]
    if with_page:
        argv.append(str(ROOT / 'extension' / 'page.js'))
    result = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_relay():
    return _run_node(_STORAGE_RELAY_HARNESS, with_page=True)


def run_failure():
    return _run_node(_STORAGE_FAILURE_HARNESS, with_page=True)


def run_two_origin():
    return _run_node(_TWO_ORIGIN_HARNESS, with_page=False)
