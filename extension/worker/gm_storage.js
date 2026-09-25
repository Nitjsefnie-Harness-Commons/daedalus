/* exported handleGmStorage */
/* global canonicalOrigin, storageEntryBytes */

// ─── Page-facing GM storage, served in the service-worker realm ───
//
// The GM key namespace, both byte caps — one over a single origin's partition
// and one over their sum — and the write queue all live here, in the one realm
// per extension. A content script cannot host them: every top-level tab of the
// same origin is a separate content-script instance with a separate module
// scope, so two tabs each read the store before the other's write committed
// and each computed its sum against the same pre-write snapshot. The cap over
// the sum is a read-modify-write over the WHOLE store, so the queue is one
// queue for every origin: keyed by namespace, two origins would each read the
// same pre-write store and each pass the aggregate check.
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

// GM_TOTAL_QUOTA_BYTES bounds the SUM over every origin, which the per-origin
// cap cannot: a dozen origins at their own 1 MB are all admitted, they overrun
// the 10 MB area, and the writes that then fail are the extension's own. Half
// the area is the sum, and half is reserve: 5 MiB of GM + 2 MiB of hotfixes
// (HOTFIX_QUOTA_BYTES) + 3 MiB of reserve = the 10 MB Chrome caps `local` at.
// Chrome's own enforcement stays the backstop — the extension asks for no
// `unlimitedStorage`, so a browser cap is still there under this one. The 3 MB
// reserve spends on the extension's own four keys — daedalus-token,
// daedalus-server, daedalus-segment-origins, daedalus-seen-dids. It is sized
// against the largest of them that a machine keeps count-bounded: the ledger,
// capped at 1000 entries, and a delivery id is `<ms>_<counter>`, so
// 1000 of them is on the order of 22 KB and the reserve is roughly
// 140x that term.
// `daedalus-segment-origins` has no count cap, but only an operator adds to it
// and a page cannot write a `daedalus-` key. Only `gm:` keys are summed: the
// extension's own keys are what the reserve pays for, not page budget.
const GM_TOTAL_QUOTA_BYTES = 5 * 1024 * 1024;

const GM_KEY_PREFIX = 'gm:';

function gmNamespace(origin) {
  return GM_KEY_PREFIX + encodeURIComponent(origin) + ':';
}

// The one GM write queue. chrome.storage has no compare-and-swap, so every
// run's get → sum → set is a read-modify-write, and the queue runs them one at
// a time — each reading the store only after the previous write's set callback
// has committed it. Within that one serial section the origins take turns: a
// page's burst sets the order of its OWN writes and nothing else. The run's
// every exit — each storage callback and the synchronous issuance — reaches
// done exactly once, including a throw, so a failure releases the queue and is
// reported to the page instead of wedging it.
const _gmWriteQueue = { active: false, rotation: [], waiting: new Map() };

// A run for `origin` has finished, so its turn is over; a page that goes
// quiet and comes back re-enters at the back rather than resuming the place
// it held before.
function _retire(origin, queue) {
  const at = queue.rotation.indexOf(origin);
  if (at === -1) return;
  queue.rotation.splice(at, 1);
  if (queue.waiting.get(origin).length) queue.rotation.push(origin);
  else queue.waiting.delete(origin);
}

// The origin whose turn has been longest over.
function _nextQueued(queue) {
  if (!queue.rotation.length) return null;
  return queue.waiting.get(queue.rotation[0]).shift();
}

function _enqueue(origin, run) {
  const queue = _gmWriteQueue;
  const advance = () => {
    queue.active = true;
    let finished = false;
    run(() => {
      if (finished) return;
      finished = true;
      queue.active = false;
      _retire(origin, queue);
      const next = _nextQueued(queue);
      if (next) next();
    });
  };
  if (!queue.active) {
    advance();
    return;
  }
  const runs = queue.waiting.get(origin);
  if (runs) {
    runs.push(advance);
    return;
  }
  queue.waiting.set(origin, [advance]);
  queue.rotation.push(origin);
}

function _storageError() {
  return (chrome.runtime.lastError && chrome.runtime.lastError.message) || '';
}

function _gmSetValue(origin, key, value, sendResponse) {
  const namespace = gmNamespace(origin);
  const storeKey = namespace + key;
  let incoming;
  try {
    incoming = storageEntryBytes(storeKey, value);
  } catch (e) {
    return sendResponse({ error: 'value could not be measured' });
  }
  _enqueue(origin, (done) => {
    try {
      chrome.storage.local.get(null, (data) => {
        try {
          const err = _storageError();
          if (err) { sendResponse({ error: err }); return done(); }
          // Both caps exclude the key being written, so a replace is charged
          // its delta: the entry it replaces stops counting and the incoming
          // one is added below. The aggregate is the same measure summed over
          // every gm: key — the page-owned set by construction, since a GM key
          // is the prefix plus the origin plus the page's key, and no
          // extension key carries it.
          let stored = 0;
          let total = 0;
          for (const storedKey of Object.keys(data)) {
            if (storedKey === storeKey) continue;
            // Only a gm: key is charged, so an extension key is skipped
            // before it is stringified rather than measured and dropped.
            if (!storedKey.startsWith(GM_KEY_PREFIX)) continue;
            const bytes = storageEntryBytes(storedKey, data[storedKey]);
            if (storedKey.startsWith(namespace)) stored += bytes;
            total += bytes;
          }
          if (stored + incoming > GM_QUOTA_BYTES) {
            sendResponse({ error: 'gm storage quota exceeded' });
            return done();
          }
          if (total + incoming > GM_TOTAL_QUOTA_BYTES) {
            sendResponse({ error: 'gm storage total quota exceeded' });
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
