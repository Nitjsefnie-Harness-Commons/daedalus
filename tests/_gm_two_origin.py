#!/usr/bin/env python3
"Two-origin GM storage isolation + quota harness (service-worker realm)."
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gm_harness import _PRELUDE, _run_node  # noqa: E402


_TWO_ORIGIN_HARNESS = (_PRELUDE + r"""
// One shared chrome.storage.local — the single extension store — observed
// through content-script frames and ONE service-worker realm. Each frame
// forwards gm-storage to that background with sender.origin = its own origin,
// so two frames of the same origin share the worker's one per-namespace
// queue: the real cross-tab serialization. Storage callbacks are deferred, so
// a burst dispatched in one turn submits every read before any write commits.
const [contentPath, , utilPath, gmPath] = process.argv.slice(1);
const ORIGIN_A = 'https://alpha.example.com';
const ORIGIN_B = 'https://beta.example.com';
const QUOTA_BYTES = 1048576;
const store = Object.create(null);
const storageCalls = [];
const deferred = [];
let reqCounter = 0;

function storedValues(keys) {
  const values = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(store, key)) values[key] =
      store[key];
  }
  return values;
}

function flushDeferred() {
  while (deferred.length) deferred.shift()();
}

function makeStorage() {
  return {
    get(keys, callback) {
      storageCalls.push('get');
      const data = keys === null ? { ...store } : storedValues(keys);
      deferred.push(() => callback(data));
    },
    set(values, callback) {
      storageCalls.push('set');
      Object.assign(store, values);
      deferred.push(() => callback());
    },
    remove(keys, callback) {
      storageCalls.push('remove');
      for (const key of keys) delete store[key];
      deferred.push(() => callback());
    },
  };
}

const background = buildBackground(utilPath, gmPath, makeStorage);

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
  const chrome = frameChrome(background.chrome.storage.local, background,
    origin);
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

  function dispatch(handler, key, value, extra) {
    const reqId = ++reqCounter;
    const data = Object.assign({
      direction: 'daedalus-page-to-bg', reqId, handler, key, value,
      defaultValue: 'DEFAULT',
    }, extra || {});
    for (const listener of (listeners.message || [])) {
      listener({ source: windowObject, data });
    }
    return reqId;
  }

  function replyFor(reqId) {
    return posted.find((m) =>
      m.direction === 'daedalus-bg-to-page' && m.reqId === reqId) || null;
  }

  function send(handler, key, value, extra) {
    posted.length = 0;
    storageCalls.length = 0;
    const reqId = dispatch(handler, key, value, extra);
    flushDeferred();
    const reply = replyFor(reqId);
    return {
      error: (reply && reply.error) || null,
      value: reply ? reply.value : undefined,
      keys: (reply && reply.keys) || null,
      calls: [...storageCalls],
      storeKeys: Object.keys(store).sort(),
    };
  }

  function burstWrites(count, prefix, bytes) {
    posted.length = 0;
    storageCalls.length = 0;
    for (let i = 0; i < count; i++) {
      dispatch('setValue', prefix + i, 'x'.repeat(bytes));
    }
    flushDeferred();
    return [...posted];
  }

  return { origin, dispatch, replyFor, send, burstWrites };
}

function resetStore() {
  for (const key of Object.keys(store)) delete store[key];
  storageCalls.length = 0;
  deferred.length = 0;
}

function bigString(n) { return 'x'.repeat(n); }

// The charge the CAP is expected to apply, computed from the test's OWN
// construction of the storage key and value (never the production formula):
// Chrome measures QUOTA_BYTES as the JSON stringification of every value plus
// every key's length, so an entry costs value-JSON bytes + storage-key bytes.
function partitionBytes(origin) {
  const ns = 'gm:' + encodeURIComponent(origin) + ':';
  const enc = new TextEncoder();
  let total = 0;
  for (const key of Object.keys(store)) {
    if (!key.startsWith(ns)) continue;
    total += enc.encode(JSON.stringify(store[key])).length;
    total += enc.encode(key).length;
  }
  return total;
}

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

  // One frame, a burst of setValue in one turn.
  resetStore();
  const burstReplies = a.burstWrites(12, 'k', 90000);
  out.concurrent = {
    total: partitionBytes(ORIGIN_A),
    cap: QUOTA_BYTES,
    stored: burstReplies.filter((m) => !m.error).length,
    refusals: burstReplies.filter((m) =>
      m.error === 'gm storage quota exceeded').length,
  };

  // T tabs of the SAME origin, one setValue each, all dispatched in one turn
  // against the one shared store and the one shared background. This is the
  // cross-tab control: a per-frame queue cannot serialize these, the worker's
  // per-namespace queue can.
  resetStore();
  const frames = [];
  for (let i = 0; i < 8; i++) {
    frames.push(createFrame(ORIGIN_A, 'alpha.example.com'));
  }
  const reqIds = frames.map((f, i) =>
    f.dispatch('setValue', 'k' + i, bigString(900000)));
  flushDeferred();
  let crossRefusals = 0;
  frames.forEach((f, i) => {
    const r = f.replyFor(reqIds[i]);
    if (r && r.error === 'gm storage quota exceeded') crossRefusals++;
  });
  out.crossTab = {
    total: partitionBytes(ORIGIN_A),
    cap: QUOTA_BYTES,
    refusals: crossRefusals,
  };

  // Key length is charged: a long GM key holding a tiny value is a real quota
  // consumer (Chrome charges value-JSON + every key's length), so a run of
  // long-key items is refused once the cap is reached. The test's own
  // partitionBytes charges the key, so this agrees with the cap.
  resetStore();
  const longKey = 'k'.repeat(8100);
  let keyRefusals = 0;
  let keyStored = 0;
  for (let i = 0; i < 300; i++) {
    const r = a.send('setValue', longKey + i, 0);
    if (r.error === 'gm storage quota exceeded') keyRefusals++;
    else keyStored++;
  }
  out.keyLength = {
    total: partitionBytes(ORIGIN_A),
    cap: QUOTA_BYTES,
    stored: keyStored,
    refusals: keyRefusals,
  };

  // Near-miss on the value side with a small key, each measured on an empty
  // store: a value whose charge (value-JSON + storage-key) is just under the
  // cap is admitted, just over is refused.
  resetStore();
  const under = a.send('setValue', 'u', bigString(QUOTA_BYTES - 200));
  resetStore();
  const over = a.send('setValue', 'o', bigString(QUOTA_BYTES));
  out.nearMiss = {
    underError: under.error, underCalls: under.calls,
    overError: over.error, overCalls: over.calls,
  };

  // An opaque origin reports "null" and has no owner to name, so every GM
  // storage handler refuses rather than share a gm:null: partition.
  const o = createFrame('null', 'opaque');
  out.opaque = {
    get: o.send('getValue', 'k').error,
    set: o.send('setValue', 'k', 'v').error,
    list: o.send('listValues').error,
    del: o.send('deleteValue', 'k').error,
  };

  // Map/Set are charged by their JSON form, matching Chrome's QUOTA_BYTES. A
  // 100k-entry Map is stored and charged as {} (2 bytes) plus its key, and a
  // following large value still fits beside it — which it would not if either
  // were charged by its entries.
  resetStore();
  const hugeMap = new Map();
  const hugeSet = new Set();
  for (let i = 0; i < 100000; i++) {
    hugeMap.set('k' + i, 'v' + i);
    hugeSet.add('v' + i);
  }
  const mapSet = a.send('setValue', 'm', hugeMap);
  const setSet = a.send('setValue', 's', hugeSet);
  const fillSet = a.send('setValue', 'z', bigString(QUOTA_BYTES - 200));
  out.mapSet = {
    mapError: mapSet.error, mapCalls: mapSet.calls,
    setError: setSet.error, setCalls: setSet.calls,
    fillError: fillSet.error, fillCalls: fillSet.calls,
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

  // A replace is old-out/new-in.
  resetStore();
  a.send('setValue', 'k', bigString(QUOTA_BYTES - 1000));
  const replaced = a.send('setValue', 'k', bigString(QUOTA_BYTES - 100));
  out.replace = { error: replaced.error, calls: replaced.calls };

  // A delete frees budget.
  resetStore();
  a.send('setValue', 'k', bigString(QUOTA_BYTES - 200));
  const addRefused = a.send('setValue', 'j', bigString(500));
  a.send('deleteValue', 'k');
  const addAccepted = a.send('setValue', 'j', bigString(500));
  out.deleteFrees = {
    refusedError: addRefused.error, refusedCalls: addRefused.calls,
    acceptedError: addAccepted.error, acceptedCalls: addAccepted.calls,
  };

  // A partition already over the cap refuses every page write.
  resetStore();
  store[nsKey(ORIGIN_A, 'over')] = bigString(1500000);
  const alreadyOver = a.send('setValue', 'anything', 'small');
  out.alreadyOver = { error: alreadyOver.error, calls: alreadyOver.calls };

  // A value that cannot be JSON.stringify is refused before any get.
  resetStore();
  const circular = {};
  circular.self = circular;
  const unserialisable = a.send('setValue', 'k', circular);
  out.unserialisable = { error: unserialisable.error,
                         calls: unserialisable.calls,
                         storeKeys: unserialisable.storeKeys };

  return out;
}

function nsKey(origin, key) {
  return 'gm:' + encodeURIComponent(origin) + ':' + key;
}

process.stdout.write(JSON.stringify(main()));
""")


def run_two_origin(content_path=None):
    return _run_node(_TWO_ORIGIN_HARNESS, content_path=content_path)
