#!/usr/bin/env python3
"Two-origin GM storage isolation, quota and admission harness."
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gm_harness import _PRELUDE, _run_node  # noqa: E402


_TWO_ORIGIN_HARNESS = _PRELUDE + r"""
// One shared chrome.storage.local — the single extension store — observed
// through content-script frames and ONE service-worker realm. Each frame
// forwards gm-storage to that background with sender.origin = its own origin,
// so two frames of the same origin share the worker's one per-namespace
// queue: the real cross-tab serialization. Storage callbacks are deferred, so
// a burst dispatched in one turn submits every read before any write commits.
// The one queue serves every origin, and the cases below record the order the
// pages were ANSWERED in, so admission order is asserted from the record
// rather than from a wall clock.
const [contentPath, , utilPath, gmPath] = process.argv.slice(1);
const ORIGIN_A = 'https://alpha.example.com';
const ORIGIN_B = 'https://beta.example.com';
const ORIGIN_C = 'https://gamma.example.com';
const QUOTA_BYTES = 1048576;
const enc = new TextEncoder();
const store = Object.create(null);
const storageCalls = [];
const deferred = [];
// Every reply the worker sent to a page, in the order it sent it: the record
// the admission cases assert on.
const answered = [];
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
// A second worker's store over the SAME chrome.storage, differing in exactly
// one place: set hands its callback to the deferred queue twice. It exists for
// the queue's release-once guard, which an honest store never exercises.
function makeDoubleFireStorage() {
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
      deferred.push(() => callback());
    },
    remove(keys, callback) {
      storageCalls.push('remove');
      for (const key of keys) delete store[key];
      deferred.push(() => callback());
    },
  };
}
// The caps as the PRODUCTION module declares them, so every boundary below is
// placed by a measured charge against the real constant. A cap the module
// does not declare reads null and its cases then fail rather than falling back
// to a copy of the number that could drift.
const PER_ORIGIN_CAP = background.constants.GM_QUOTA_BYTES;
const TOTAL_CAP = background.constants.GM_TOTAL_QUOTA_BYTES;

function createFrame(origin, hostname, target) {
  const listeners = {};
  const posted = [];
  const realm = target || background;
  const windowObject = {
    addEventListener(type, listener) {
      (listeners[type] ||= []).push(listener);
    },
    postMessage(message) {
      posted.push(message);
      if (message.direction === 'daedalus-bg-to-page') {
        answered.push(message.reqId);
      }
    },
  };
  const chrome = frameChrome(realm.chrome.storage.local, realm, origin);
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
  answered.length = 0;
}

// The order the worker ANSWERED these submissions in, labelled by the key each
// one wrote. A submission the worker never answered is absent from the record,
// so a stalled queue shows up as a short order rather than as a slow one.
function completionOrder(ids, labels) {
  const wanted = new Set(ids);
  const order = answered.filter((id) => wanted.has(id))
    .map((id) => labels.get(id));
  return { order, answered: order.length, submitted: ids.length };
}

// The label map a run of submissions shares: one label per key, in order.
function labelsFor(keys, ids) {
  const labels = new Map();
  keys.forEach((key, i) => labels.set(ids[i], key));
  return labels;
}

function bigString(n) { return 'x'.repeat(n); }

// The charge the CAP is expected to apply, computed from the test's OWN
// construction of the storage key and value (never the production formula):
// Chrome measures QUOTA_BYTES as the JSON stringification of every value plus
// every key's length, so an entry costs value-JSON bytes + storage-key bytes.
function entryBytes(key, value) {
  return enc.encode(JSON.stringify(value)).length + enc.encode(key).length;
}

function partitionBytes(origin) {
  const ns = 'gm:' + encodeURIComponent(origin) + ':';
  let total = 0;
  for (const key of Object.keys(store)) {
    if (!key.startsWith(ns)) continue;
    total += entryBytes(key, store[key]);
  }
  return total;
}

// The same measure over EVERY gm: key, which is the page-owned set by
// construction: a GM key is 'gm:' + encodeURIComponent(origin) + ':' + key and
// no extension key carries that prefix.
function gmBytes() {
  let total = 0;
  for (const key of Object.keys(store)) {
    if (!key.startsWith('gm:')) continue;
    total += entryBytes(key, store[key]);
  }
  return total;
}

// The key-length term of gmBytes() on its own, so a test can show that
// charging it (or the value term beside it) is what makes a write go over.
function keyLengthBytes() {
  let total = 0;
  for (const key of Object.keys(store)) {
    if (!key.startsWith('gm:')) continue;
    total += enc.encode(key).length;
  }
  return total;
}

// A value whose stored charge is exactly `charge` bytes, from the test's own
// construction: the value's JSON is two quote characters around the 'x' run.
function valueOfCharge(origin, key, charge) {
  const overhead = 2 + enc.encode(nsKey(origin, key)).length;
  return 'x'.repeat(charge - overhead);
}

// Six distinct origins, so a case that needs several partitions writes to
// genuinely different senders rather than to one origin under several keys.
const FILLERS = [
  'https://cap-0.example.com', 'https://cap-1.example.com',
  'https://cap-2.example.com', 'https://cap-3.example.com',
  'https://cap-4.example.com', 'https://cap-5.example.com',
];

function fillerHost(origin) {
  return origin.slice('https://'.length);
}

// A fixture written straight into the store, so a boundary is placed by a
// measured charge rather than by a restated byte count.
function seed(specs) {
  for (const spec of specs) {
    store[nsKey(spec.origin, spec.key)] =
      valueOfCharge(spec.origin, spec.key, spec.charge);
  }
}

function seedSpecs(origins, charge) {
  return origins.map((origin) => ({ origin, key: 's', charge }));
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

  out.aggregate = aggregateCases();
  out.admission = admissionCases();
  return out;
}

// ── Admission order ──
// The aggregate cap makes the queue ONE serial section over the whole GM area,
// so a run holds it for exactly its own single write. The order that section
// takes its next write in is the other half: an origin with a write waiting is
// served once per turn, so a page's burst decides only how long its OWN writes
// wait. Every case records the order the pages were ANSWERED in.
function admissionCases() {
  const a = createFrame(ORIGIN_A, 'alpha.example.com');
  const b = createFrame(ORIGIN_B, 'beta.example.com');
  const c = createFrame(ORIGIN_C, 'gamma.example.com');
  const out = {};

  // Five writes from one origin issued WITHOUT awaiting between them, then one
  // from an unrelated origin: a flooding page's loop with a bystander page's
  // single ordinary write landing inside it. Nothing is refused and no cap is
  // broken — the bystander's write is merely behind every write the flood
  // issued, which is the wait this case measures.
  resetStore();
  const floodKeys = ['f0', 'f1', 'f2', 'f3', 'f4'];
  const flood = floodKeys.map((k) => a.dispatch('setValue', k, 'v'));
  const bystander = b.dispatch('setValue', 'b', 'v');
  flushDeferred();
  const rotation = [...flood, bystander];
  out.rotation = completionOrder(rotation,
    labelsFor([...floodKeys, 'b'], rotation));

  // One origin alone, so no other origin is ever in the rotation: the writes
  // still commit in submission order and every one of them is answered.
  resetStore();
  const soloKeys = ['s0', 's1', 's2', 's3'];
  const solo = soloKeys.map((k) => a.dispatch('setValue', k, 'v'));
  flushDeferred();
  out.singleOrigin = completionOrder(solo, labelsFor(soloKeys, solo));

  // An origin whose queue drained is no longer in the rotation, and a write it
  // issues later re-enters at the BACK — so a page that refills its queue does
  // not resume the front it held before. The two rounds are flushed apart, so
  // the second one starts from an empty rotation: A's two later writes are
  // behind the origins that were already waiting when A came back.
  resetStore();
  const firstKeys = ['a0', 'a1', 'a2', 'b0'];
  const first = [a.dispatch('setValue', 'a0', 'v'),
    a.dispatch('setValue', 'a1', 'v'),
    a.dispatch('setValue', 'a2', 'v'),
    b.dispatch('setValue', 'b0', 'v')];
  flushDeferred();
  const secondKeys = ['a3', 'b1', 'a4', 'c0'];
  const second = [a.dispatch('setValue', 'a3', 'v'),
    b.dispatch('setValue', 'b1', 'v'),
    a.dispatch('setValue', 'a4', 'v'),
    c.dispatch('setValue', 'c0', 'v')];
  flushDeferred();
  out.rejoin = completionOrder([...first, ...second],
    labelsFor([...firstKeys, ...secondKeys], [...first, ...second]));

  // Nothing active and nothing waiting: the first submission runs at once, so
  // the second is served behind it rather than ahead of it.
  resetStore();
  const idleKeys = ['i0', 'i1'];
  const idle = [a.dispatch('setValue', 'i0', 'v'),
    b.dispatch('setValue', 'i1', 'v')];
  flushDeferred();
  out.idle = completionOrder(idle, labelsFor(idleKeys, idle));

  // The queue's release is once per run. A storage callback entered twice must
  // not release the queue twice and admit a write beside the one still in
  // flight — the read-modify-write hole the one serial section exists to
  // close. The double-firing write is issued by an origin with NOTHING else
  // waiting, so a second release draws the first origin's first queued write
  // and then, with no guard, its second one beside it. Three writes of a
  // measured charge are seeded one byte under the point where all three fit,
  // so the first two are admitted and the third is not: the concurrent pair
  // reads the store before either of them committed, admits both, and lands
  // the sum one byte over the cap.
  resetStore();
  const charge = 1000;
  seed(seedSpecs([FILLERS[0]], TOTAL_CAP - 3 * charge + 1));
  const twice = buildBackground(utilPath, gmPath, makeDoubleFireStorage);
  const origins = [ORIGIN_C, ORIGIN_A, ORIGIN_A];
  const frames = origins.map((origin) => createFrame(
    origin, origin.slice('https://'.length), twice));
  const ids = frames.map((frame, i) => frame.dispatch(
    'setValue', 'd' + i, valueOfCharge(origins[i], 'd' + i, charge)));
  flushDeferred();
  const errors = ids.map((id, i) =>
    (frames[i].replyFor(id) || {}).error || null);
  out.doubleCallback = {
    errors,
    order: completionOrder(ids, labelsFor(['d0', 'd1', 'd2'], ids)).order,
    total: gmBytes(), charge, seeded: TOTAL_CAP - 3 * charge + 1,
    cap: TOTAL_CAP,
  };
  return out;
}

// ── The aggregate cap ──
// The per-origin cap bounds ONE partition; the sum over every origin is what
// overruns Chrome's `local` area, because a dozen origins each at their own
// cap are all admitted and the writes that then fail are the extension's own.
// Every boundary below is placed against the PRODUCTION GM_TOTAL_QUOTA_BYTES,
// measured the way Chrome measures the area: the JSON stringification of every
// value plus every key's length, over every gm: key. A module that declares no
// aggregate cap reports that fact instead of falling back to a local copy.
function aggregateCases() {
  const out = { declared: typeof TOTAL_CAP === 'number',
                cap: TOTAL_CAP, perOriginCap: PER_ORIGIN_CAP };

  // Six DISTINCT origins, one write each, all dispatched in ONE turn: the
  // deferred store hands every read the pre-write snapshot, so only a single
  // serial queue over the whole GM area keeps the committed total under the
  // cap. Each write is just inside its own origin's cap, so the aggregate is
  // the only thing that can refuse any of them — which is why this case needs
  // no aggregate constant to be meaningful.
  resetStore();
  const perWrite = PER_ORIGIN_CAP - 1000;
  const crowd = [];
  for (let i = 0; i < 6; i++) {
    const origin = FILLERS[i];
    crowd.push(createFrame(origin, fillerHost(origin)));
  }
  const reqIds = crowd.map((f, i) =>
    f.dispatch('setValue', 'k' + i, bigString(perWrite)));
  flushDeferred();
  const replies = crowd.map((f, i) => f.replyFor(reqIds[i]));
  out.concurrency = {
    total: gmBytes(),
    stored: replies.filter((m) => m && !m.error).length,
    refusals: replies.filter((m) => m && m.error).length,
    errors: replies.map((m) => (m && m.error) || null),
    perWrite,
  };
  if (!out.declared) return out;

  const seeds = FILLERS.slice(0, 5);
  const sixth = FILLERS[5];
  const last = createFrame(sixth, fillerHost(sixth));
  const sendCharge = (frame, origin, key, charge) =>
    frame.send('setValue', key, valueOfCharge(origin, key, charge));
  const perSeed = Math.floor((TOTAL_CAP - 2000) / seeds.length);
  const tail = TOTAL_CAP - perSeed * seeds.length;
  const leg = Math.floor(TOTAL_CAP / 11);
  if (perSeed >= PER_ORIGIN_CAP || tail < 2000 ||
      5 * perWrite + 400 > TOTAL_CAP) {
    throw new Error('GM cap changed: aggregate fixtures no longer fit it');
  }

  // Five origins filled to the cap less a small tail, then one write that
  // lands the sum under, exactly on, and one byte over the cap. Under and on
  // are admitted; over is refused with set never called, and the refusal names
  // the aggregate limit rather than the per-origin one.
  const boundary = {};
  for (const [label, charge] of
    [['under', tail - 1], ['on', tail], ['over', tail + 1]]) {
    resetStore();
    seed(seedSpecs(seeds, perSeed));
    const r = sendCharge(last, sixth, 't', charge);
    boundary[label] = { error: r.error, calls: r.calls, total: gmBytes() };
  }
  out.boundary = boundary;

  // The extension's own keys are the cap's REASON, not its charge: a token
  // nearly as large as the cap beside an empty GM area does not refuse a page
  // write, because the reserve is what pays for the token and only gm: keys
  // are summed. Their sum together is past the cap, so charging them would
  // refuse this write.
  resetStore();
  const token = Math.floor(TOTAL_CAP * 0.85);
  store['daedalus-token'] = bigString(token);
  const beside = sendCharge(last, sixth, 'b',
    Math.floor(TOTAL_CAP * 0.16));
  out.extensionKeys = { error: beside.error, calls: beside.calls,
                        total: gmBytes(), token };

  // Replacing a key is old-out/new-in: the aggregate charges the incoming
  // entry and excludes the key being written, so a rewrite that lands the sum
  // back under the cap is admitted rather than counted twice.
  resetStore();
  const replaceCharge = 2000;
  seed(seedSpecs(seeds, Math.floor((TOTAL_CAP - 3000) / seeds.length)));
  seed([{ origin: sixth, key: 'r', charge: replaceCharge }]);
  const beforeReplace = gmBytes();
  const replaced = sendCharge(last, sixth, 'r', replaceCharge);
  out.replacement = { error: replaced.error, calls: replaced.calls,
                      before: beforeReplace, after: gmBytes(),
                      charge: replaceCharge };

  // The aggregate charges the two terms Chrome's QUOTA_BYTES names, the
  // value's JSON bytes AND the key's length: five origins holding a long key
  // beside a long value fill the cap, so one further entry is refused.
  // Charging either term alone roughly halves the sum and admits it.
  resetStore();
  const longKey = 'k'.repeat(leg);
  for (let i = 0; i < 5; i++) {
    const origin = seeds[i];
    store[nsKey(origin, longKey)] = bigString(leg);
  }
  const legs = gmBytes();
  const legKeyTerm = keyLengthBytes();
  const incoming = Math.floor(TOTAL_CAP / 10);
  const term = sendCharge(last, sixth, 'L', incoming);
  out.terms = { error: term.error, calls: term.calls, before: legs,
                after: gmBytes(), incoming, keyTerm: legKeyTerm };
  return out;
}

function nsKey(origin, key) {
  return 'gm:' + encodeURIComponent(origin) + ':' + key;
}

process.stdout.write(JSON.stringify(main()));
"""


def run_two_origin(content_path=None):
    return _run_node(_TWO_ORIGIN_HARNESS, content_path=content_path)
