// Daedalus Extension — content script (message relay)
// Bridges page context (page.js) ↔ background service worker

// ─── Relay: page context → background ───

// Page-facing GM storage is served in the background service worker, not here.
// The namespace, the per-origin byte cap, and the per-namespace write queue
// must be one implementation in one realm: every top-level tab of an origin is
// a separate content-script instance with a separate module scope, so a queue
// kept here would not serialize a sibling tab — two tabs would each read the
// store before the other's write committed. The four GM storage handlers are
// forwarded to worker/gm_storage.js, which serves them from sender.origin and
// answers here; this script only maps that answer back to the page.
const GM_STORAGE_HANDLERS = new Set([
  'getValue', 'setValue', 'deleteValue', 'listValues',
]);

// One entry per in-flight GM.xmlhttpRequest: the page's request id to
// the id the service worker files its AbortController under. Deleted when
// the request settles and when it is cancelled, so an abort arriving after
// either finds nothing and does nothing.
const _fetchIds = {};
// A per-frame prefix and a counter, from JS builtins rather than a host API:
// the id only has to be unique among the fetches this frame has in flight.
// It is not a capability — an abort can only name an id this frame's own map
// still holds, so another frame's request is unreachable however guessable
// the string is.
const _fetchIdPrefix = Math.random().toString(36).slice(2);
let _fetchSeq = 0;

window.addEventListener('message', (e) => {
  if (e.source !== window || !e.data || e.data.direction !== 'daedalus-page-to-bg') return;
  const msg = e.data;
  const reqId = msg.reqId;

  if (GM_STORAGE_HANDLERS.has(msg.handler)) {
    // Served in the service worker, keyed on sender.origin, so nothing the
    // page put in this message can redirect it to another origin's keys. This
    // script forwards and maps the worker's answer back to the page.
    chrome.runtime.sendMessage({
      type: 'gm-storage',
      handler: msg.handler,
      key: msg.key,
      value: msg.value,
      defaultValue: msg.defaultValue,
    }, (response) => {
      const err = chrome.runtime.lastError;
      if (err || !response) {
        return window.postMessage({ direction: 'daedalus-bg-to-page', reqId,
          handler: msg.handler,
          error: err ? err.message
            : 'no response from background (service worker dead?)' }, '*');
      }
      const reply = { direction: 'daedalus-bg-to-page', reqId,
                      handler: msg.handler };
      if (response.error) reply.error = response.error;
      else if (msg.handler === 'getValue') reply.value = response.value;
      else if (msg.handler === 'listValues') reply.keys = response.keys;
      window.postMessage(reply, '*');
    });
    return;
  }

  if (msg.handler === 'abortRequest') {
    // The page names its own request id; the background knows the fetch by
    // the id minted here, so the mapping is what makes cancellation reach the
    // AbortController. A request that already settled has no mapping left,
    // which is what makes a late or repeated abort a no-op rather than a
    // message about a fetch nobody is running.
    const fetchId = _fetchIds[msg.target];
    if (fetchId) {
      delete _fetchIds[msg.target];
      chrome.runtime.sendMessage({ type: 'abortFetch', fetchId });
    }
    return;
  }

  if (msg.handler === 'xmlhttpRequest') {
    const fetchId = `${_fetchIdPrefix}-${++_fetchSeq}`;
    _fetchIds[reqId] = fetchId;
    chrome.runtime.sendMessage({
      type: 'fetch',
      fetchId,
      url: msg.url,
      method: msg.method,
      headers: msg.headers,
      body: msg.data,
      bodyIsBase64: msg.bodyIsBase64 || false,
      responseType: msg.responseType === 'arraybuffer' ? 'arraybuffer' : 'text',
      timeout: msg.timeout,
      maxResponseBytes: msg.maxResponseBytes,
    }, (resp) => {
      delete _fetchIds[reqId];
      const err = chrome.runtime.lastError && chrome.runtime.lastError.message;
      if (err || !resp) {
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'xmlhttpRequest', event: 'error',
          error: err || 'no response from background (service worker dead?)' }, '*');
      } else if (resp.error) {
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'xmlhttpRequest',
          event: resp.timedOut ? 'timeout' : 'error', error: resp.error }, '*');
      } else {
        // resp.finalUrl is where the body came from after any redirects;
        // msg.url is only where the caller asked. Falling back to the request
        // URL keeps a background that reports neither working, but it is a
        // fallback rather than the answer it used to be.
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'xmlhttpRequest', event: 'load',
          status: resp.status, statusText: resp.statusText || '',
          data: resp.data, headers: resp.headers,
          finalUrl: resp.finalUrl || msg.url }, '*');
      }
    });
  } else if (msg.handler === 'openInTab') {
    chrome.runtime.sendMessage({ type: 'openTab', url: msg.url, active: msg.active }, () => {
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'openInTab' }, '*');
    });
  } else if (msg.handler === 'setClipboard') {
    // Acknowledged only once the write has actually settled. The empty catch
    // here used to swallow the rejection while the acknowledgement went out
    // immediately, so a page without user activation -- where Chromium
    // refuses the write -- was told the clipboard had been set.
    navigator.clipboard.writeText(msg.text).then(() => {
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'setClipboard' }, '*');
    }, (e) => {
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'setClipboard',
        error: (e && e.message) || 'clipboard write refused' }, '*');
    });
  } else if (msg.handler === 'notification') {
    chrome.runtime.sendMessage({ type: 'notification', title: msg.title, text: msg.text });
    window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'notification' }, '*');
  } else if (msg.handler === 'download') {
    chrome.runtime.sendMessage({ type: 'download', url: msg.url, filename: msg.name }, (resp) => {
      // Three ways this fails; only one of them used to be noticed. A
      // sendMessage that never reaches the worker reports through
      // chrome.runtime.lastError and passes NO response, so `resp` was
      // undefined, `resp && resp.error` was false, and the page got a load
      // event for a download that was never started. A response carrying no
      // downloadId is the same story arriving from the other side.
      const err = chrome.runtime.lastError && chrome.runtime.lastError.message;
      const failure = err || (resp && resp.error)
        || (!resp && 'no response from background (service worker dead?)')
        || (resp.downloadId === undefined && 'background started no download');
      if (failure) {
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'download', event: 'error', error: failure }, '*');
      } else {
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'download', event: 'load' }, '*');
      }
    });
  } else if (msg.handler === 'segmentJob') {
    chrome.runtime.sendMessage({ type: 'segmentJob', job: msg.job }, (resp) => {
      const err = chrome.runtime.lastError && chrome.runtime.lastError.message;
      const failure = err || (resp && resp.error)
        || (!resp && 'no response from background (service worker dead?)')
        || ((typeof resp.sig !== 'string' || !resp.sig) && 'background returned no sig');
      if (failure) {
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'segmentJob', error: failure }, '*');
      } else {
        window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'segmentJob', sig: resp.sig }, '*');
      }
    });
  }
});

// ─── Relay: page eval results → background ───

window.addEventListener('message', (e) => {
  if (e.source !== window || !e.data || e.data.direction !== 'daedalus-eval-result') return;
  const msg = e.data;
  let result = msg.r;
  let serialized;
  try { serialized = JSON.stringify(result); } catch { serialized = String(result); }
  try { result = JSON.parse(serialized); } catch { result = serialized; }
  chrome.runtime.sendMessage({
    type: 'result', relayId: msg.relayId, result,
    error: msg.e || null, hostname: location.hostname,
    ms: msg.ms,
  });
});

// ─── Receive eval commands from background → forward to page context ───

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'eval') {
    window.postMessage({
      direction: 'daedalus-eval', id: msg.id,
      relayId: msg.relayId, code: msg.code,
    }, '*');
  }
});

// ─── Keep-alive: persistent port + periodic messages ───
// Chrome force-disconnects ports after 5 minutes regardless of activity.
// Sending messages on the port resets the timer. We reconnect proactively
// at 4 minutes to stay ahead of the disconnect.

let keepAlivePort = null;
let keepAliveInterval = null;
let keepAliveReconnectTimer = null;

function connectKeepAlive() {
  try {
    const previousPort = keepAlivePort;
    keepAlivePort = null;
    if (keepAliveInterval) { clearInterval(keepAliveInterval); keepAliveInterval = null; }
    if (keepAliveReconnectTimer) {
      clearTimeout(keepAliveReconnectTimer);
      keepAliveReconnectTimer = null;
    }
    if (previousPort) { try { previousPort.disconnect(); } catch (_) {} }

    const port = chrome.runtime.connect({ name: 'keepalive' });
    keepAlivePort = port;
    const interval = setInterval(() => {
      if (keepAlivePort !== port) return;
      try { port.postMessage({ type: 'ping', ts: Date.now() }); }
      catch (_) {}
    }, 20000);
    keepAliveInterval = interval;

    let reconnectTimer;
    port.onDisconnect.addListener(() => {
      if (keepAlivePort !== port) return;
      // Service worker died or port timed out — reconnect immediately
      keepAlivePort = null;
      if (keepAliveInterval === interval) {
        clearInterval(interval);
        keepAliveInterval = null;
      }
      if (keepAliveReconnectTimer === reconnectTimer) {
        clearTimeout(reconnectTimer);
        keepAliveReconnectTimer = null;
      }
      const retryTimer = setTimeout(() => {
        if (keepAliveReconnectTimer !== retryTimer) return;
        keepAliveReconnectTimer = null;
        connectKeepAlive();
      }, 500);
      keepAliveReconnectTimer = retryTimer;
    });
    // Proactive reconnect at 4 minutes to stay ahead of Chrome's 5-minute force-disconnect
    reconnectTimer = setTimeout(() => {
      if (keepAlivePort !== port || keepAliveReconnectTimer !== reconnectTimer) return;
      keepAliveReconnectTimer = null;
      connectKeepAlive();
    }, 4 * 60 * 1000);
    keepAliveReconnectTimer = reconnectTimer;
  } catch (_) {
    const retryTimer = setTimeout(() => {
      if (keepAliveReconnectTimer !== retryTimer) return;
      keepAliveReconnectTimer = null;
      connectKeepAlive();
    }, 5000);
    keepAliveReconnectTimer = retryTimer;
  }
}

// ─── Hotfix replay ───

// The background replays: it can reach the page through MAIN-world injection
// and, where page CSP forbids dynamic compilation, through CDP. This script
// can do neither — posting the source into the page left the page's own
// `eval` and a blob <script> as the only options, and a CSP that refuses both
// refused every fix.
//
// The MAIN channel names this document in its `executeScript` target. The
// CDP channel cannot: the protocol has no document identifier, so two
// documents at one url in a tab — a prerender and the visible one — are
// indistinguishable to it. So this document plants a token in its OWN DOM
// and sends it with the request, and the CDP channel reads that token back
// inside the evaluation that runs the fix. A page can read and rewrite the
// token, and that is accepted. It is NOT that a page cannot reach another
// document — a same-origin frame or opened window it holds, it can, and it
// could plant a token in either. It is that the evaluation reads the tab's
// top frame, and the asker holds no handle to a second top-frame document in
// its own tab, so a hostile document can suppress its own fix and can never
// make one run in a document it does not hold. worker/hotfixes.js states the
// mechanism beside the check itself.
(function replayHotfixes() {
  // `crypto.randomUUID` is [SecureContext] and this script is declared on
  // `<all_urls>`, so it is absent on a plain-http page. A token minted there
  // does not have to be unguessable: the binding is directional, not secret.
  // A forged value buys the forger exactly one thing, suppressing its own
  // fix, because the check reads the value in the document the evaluation
  // runs in — the tab's top frame — and a successor document's own token
  // fails that comparison. That is what the argument above turns on.
  const docToken = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2);
  // documentElement is null this early on a document with no root yet, and
  // an attribute cannot be set on nothing. Deferring loses nothing: the
  // background answers the request when it is answered either way.
  if (!document.documentElement) {
    document.addEventListener('DOMContentLoaded', replayHotfixes,
                               { once: true });
    return;
  }
  document.documentElement.setAttribute('data-daedalus-doc', docToken);
  chrome.runtime.sendMessage({ type: 'replayHotfixes', docToken },
                             () => {
                               // The channel is held open until the replay
                               // finishes, so this is the cleanup: the token
                               // is a replay input, not something the page
                               // is left holding.
                               document.documentElement.removeAttribute(
                                 'data-daedalus-doc');
                             });
})();

// ─── Boot ───

chrome.runtime.sendMessage({ type: 'register' });
connectKeepAlive();
console.log('[Daedalus] Content script loaded on', location.hostname);
