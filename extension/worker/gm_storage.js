/* exported handleGmStorage */
/* global canonicalOrigin */

// ─── Page-facing GM storage, served in the service-worker realm ───
//
// The GM key namespace, the per-origin byte cap, and the per-namespace write
// queue all live here, in the one realm per extension. A content script cannot
// host them: every top-level tab of the same origin is a separate content-
// script instance with a separate module scope, so two tabs each read the
// store before the other's write committed and each computed its sum against
// the same pre-write snapshot. Here one queue per namespace is real.
//
// The origin is sender.origin — Chrome's own report of the calling document.
// Neither the page's message payload nor the content script's location is
// consulted; canonicalOrigin refuses an absent, non-http(s) or opaque ("null")
// origin rather than falling back to anything a page supplied.

const RESERVED_KEY = /^daedalus-/;

// GM_QUOTA_BYTES bounds one origin's partition. Chrome's documented `local`
// cap is 10 MB and the extension asks for no `unlimitedStorage`, so a page
// that filled the shared area would make the extension's own writes fail; one
// origin is capped at 1 MB, at most a tenth of the area, leaving 9 MB of
// headroom for the extension's state and other origins. The sum is recomputed
// from the values actually stored on every write — never an accumulator — so a
// delete frees budget and a value the page did not count still counts.
const GM_QUOTA_BYTES = 1024 * 1024;

function gmNamespace(origin) {
  return 'gm:' + encodeURIComponent(origin) + ':';
}

function _jsonBytes(value) {
  const json = JSON.stringify(value);
  if (typeof json !== 'string') throw new Error('not serialisable');
  return new TextEncoder().encode(json).length;
}

// Chrome's local QUOTA_BYTES is "as measured by the JSON stringification of
// every value plus every key's length", so an entry's charge is the value's
// JSON byte length PLUS the byte length of the storage key it lives under. A
// long key holding a tiny value is a real quota consumer; a measure that
// omitted the key would let a page blow past Chrome's whole area.
function gmEntryBytes(storageKey, value) {
  return _jsonBytes(value) + new TextEncoder().encode(storageKey).length;
}

// Per-namespace write queue. chrome.storage has no compare-and-swap, so the
// cap's get → sum → set is a read-modify-write: concurrent setValue calls for
// one origin would each read the store before any set committed and each pass.
// Runs go one at a time, in submission order, each reading the store only after
// the previous write's set callback has committed it. The run's every exit —
// each storage callback and the synchronous issuance — reaches done exactly
// once, including a throw, so a failure releases the origin and is reported
// to the page instead of wedging the queue.
const _gmWriteQueues = new Map();

function _enqueue(namespace, run) {
  let queue = _gmWriteQueues.get(namespace);
  if (!queue) {
    queue = { active: false, waiting: [] };
    _gmWriteQueues.set(namespace, queue);
  }
  const advance = () => {
    queue.active = true;
    let finished = false;
    run(() => {
      if (finished) return;
      finished = true;
      queue.active = false;
      const next = queue.waiting.shift();
      if (next) next();
    });
  };
  if (queue.active) queue.waiting.push(advance);
  else advance();
}

function _storageError() {
  return (chrome.runtime.lastError && chrome.runtime.lastError.message) || '';
}

function _gmSetValue(origin, key, value, sendResponse) {
  const namespace = gmNamespace(origin);
  const storeKey = namespace + key;
  let incoming;
  try {
    incoming = gmEntryBytes(storeKey, value);
  } catch (e) {
    return sendResponse({ error: 'value could not be measured' });
  }
  _enqueue(namespace, (done) => {
    try {
      chrome.storage.local.get(null, (data) => {
        try {
          const err = _storageError();
          if (err) { sendResponse({ error: err }); return done(); }
          let stored = 0;
          for (const storedKey of Object.keys(data)) {
            if (!storedKey.startsWith(namespace) || storedKey === storeKey) {
              continue;
            }
            stored += gmEntryBytes(storedKey, data[storedKey]);
          }
          if (stored + incoming > GM_QUOTA_BYTES) {
            sendResponse({ error: 'gm storage quota exceeded' });
            return done();
          }
        } catch (e) {
          sendResponse({ error: (e && e.message) || String(e) });
          return done();
        }
        chrome.storage.local.set({ [storeKey]: value }, () => {
          try {
            const err = _storageError();
            sendResponse(err ? { error: err } : {});
          } finally {
            done();
          }
        });
      });
    } catch (e) {
      sendResponse({ error: (e && e.message) || String(e) });
      done();
    }
  });
}

function _gmGetValue(origin, key, defaultValue, sendResponse) {
  const storeKey = gmNamespace(origin) + key;
  chrome.storage.local.get([storeKey], (data) => {
    const err = _storageError();
    if (err) return sendResponse({ error: err });
    const val = data[storeKey] !== undefined ? data[storeKey] : defaultValue;
    sendResponse({ value: val });
  });
}

function _gmDeleteValue(origin, key, sendResponse) {
  chrome.storage.local.remove([gmNamespace(origin) + key], () => {
    const err = _storageError();
    sendResponse(err ? { error: err } : {});
  });
}

function _gmListValues(origin, sendResponse) {
  chrome.storage.local.get(null, (data) => {
    const err = _storageError();
    if (err) return sendResponse({ error: err });
    // Only this origin's partition is listed, and the extension's own keys
    // carry no namespace, so they are invisible rather than merely
    // unreadable.
    const namespace = gmNamespace(origin);
    const keys = Object.keys(data)
      .filter((k) => k.startsWith(namespace))
      .map((k) => k.slice(namespace.length));
    sendResponse({ keys });
  });
}

function handleGmStorage(msg, sender, sendResponse) {
  const origin = canonicalOrigin(sender && sender.origin);
  if (origin === null) return sendResponse({ error: 'opaque origin' });
  const handler = msg && msg.handler;
  if (handler !== 'listValues') {
    if (typeof msg.key !== 'string') {
      return sendResponse({ error: 'invalid key' });
    }
    if (RESERVED_KEY.test(msg.key)) {
      return sendResponse({ error: 'reserved key' });
    }
  }
  switch (handler) {
    case 'getValue':
      return _gmGetValue(origin, msg.key, msg.defaultValue, sendResponse);
    case 'setValue':
      return _gmSetValue(origin, msg.key, msg.value, sendResponse);
    case 'deleteValue':
      return _gmDeleteValue(origin, msg.key, sendResponse);
    case 'listValues':
      return _gmListValues(origin, sendResponse);
    default:
      return sendResponse({ error: 'unknown GM storage handler' });
  }
}
