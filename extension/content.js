// Daedalus Extension — content script (message relay)
// Bridges page context (page.js) ↔ background service worker

// ─── Relay: page context → background ───

// The bridge's own settings live in the same chrome.storage.local that the GM
// shim exposes to page scripts, and the only thing gating that relay is a
// message `direction` any page can spell. Without this, ordinary site
// JavaScript reads the bridge token with
// `GM.getValue('daedalus-token')`, or repoints the bridge with
// `GM.setValue('daedalus-server', ...)`.
//
// So the relay owns a namespace: keys beginning `daedalus-` are the
// extension's, and page context may not read or write them. Everything else
// with a string key behaves as a userscript expects.
const RESERVED_KEY = /^daedalus-/;

// chrome.runtime.lastError is the ONLY report a storage callback receives:
// Chrome invokes the callback on failure exactly as on success, with the
// store unchanged, so a callback that does not read it cannot tell a written
// value from a quota rejection. Reading it also clears Chrome's own
// "unchecked lastError" warning, which is why every callback reads it rather
// than only the ones failure seems likely in.
function storageError() {
  return (chrome.runtime.lastError && chrome.runtime.lastError.message) || '';
}
const KEYED_STORAGE_HANDLERS = new Set(['getValue', 'setValue', 'deleteValue']);
// One entry per in-flight GM.xmlhttpRequest: the page's request id to
// the id the service worker files its AbortController under. Deleted when
// the request settles and when it is cancelled, so an abort arriving after
// either finds nothing and does nothing.
const _fetchIds = {};

window.addEventListener('message', (e) => {
  if (e.source !== window || !e.data || e.data.direction !== 'daedalus-page-to-bg') return;
  const msg = e.data;
  const reqId = msg.reqId;

  if (KEYED_STORAGE_HANDLERS.has(msg.handler)) {
    if (typeof msg.key !== 'string') {
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId,
                           handler: msg.handler, error: 'invalid key' }, '*');
      return;
    }
    if (RESERVED_KEY.test(msg.key)) {
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId,
                           handler: msg.handler, error: 'reserved key' }, '*');
      return;
    }
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
    const fetchId = crypto.randomUUID();
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
  } else if (msg.handler === 'getValue') {
    chrome.storage.local.get([msg.key], (data) => {
      const err = storageError();
      if (err) return window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'getValue', error: err }, '*');
      const val = data[msg.key] !== undefined ? data[msg.key] : msg.defaultValue;
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'getValue', value: val }, '*');
    });
  } else if (msg.handler === 'setValue') {
    chrome.storage.local.set({ [msg.key]: msg.value }, () => {
      const err = storageError();
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'setValue', error: err }, '*');
    });
  } else if (msg.handler === 'deleteValue') {
    chrome.storage.local.remove([msg.key], () => {
      const err = storageError();
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'deleteValue', error: err }, '*');
    });
  } else if (msg.handler === 'listValues') {
    chrome.storage.local.get(null, (data) => {
      const err = storageError();
      if (err) return window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'listValues', error: err }, '*');
      // The reserved keys are filtered here too. Blocking reads while still
      // listing the names tells a page exactly what to go after and confirms a
      // bridge is configured; the namespace has to be invisible, not just
      // unreadable.
      const keys = Object.keys(data).filter((k) => !RESERVED_KEY.test(k));
      window.postMessage({ direction: 'daedalus-bg-to-page', reqId, handler: 'listValues', keys }, '*');
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
(function replayHotfixes() {
  chrome.runtime.sendMessage({ type: 'replayHotfixes' });
})();

// ─── Boot ───

chrome.runtime.sendMessage({ type: 'register' });
connectKeepAlive();
console.log('[Daedalus] Content script loaded on', location.hostname);
