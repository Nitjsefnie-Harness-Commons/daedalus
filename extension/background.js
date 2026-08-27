// Daedalus Extension — background service worker
// @version 0.22.0.1
/* global handleScreenshot */
/* global handleCookies, handleSetCookie */
/* global handleRemoveCookie, handleClearCookies */
/* global handleBlockRequests, handleUnblockRequests */
/* global handleListBlockRules */
/* global handleCloseTab, handleOpenTab, handleOpenTabs, handleFocusTab */
/* global handleNavigate, handleReload, handleInjectCss, handleRemoveCss */
/* global handleExtReload, handleFetchTimings, handleExtTabs */
/* global handleCdp */
/* global handleNetCapture, handleNetCaptureStop */
/* global handleNetCaptureGet */
/* global handleHotfixReplay, handleStoreHotfix, handleClearHotfix */
/* global handleClearAllHotfixes, handleSetPermanent */
/* global handleListHotfixes */
/* global _takeEvalRelay, handleEval */
/* global bytesToBase64, gmResponseLimit, readBoundedBody */
/* global bridgeAuth, bridgeHeaders, _recordTiming */
/* global config, loadConfig, configured, _executionContext, postResult */
/* exported DEFAULT_SERVER */

const VERSION = '0.22.0.1';
// No default server. A bridge URL is deployment-specific, and a build that
// ships someone's hostname would have every install of it call home to that
// host. The extension stays idle until a URL is set in its options page.
/* eslint-disable-next-line no-unused-vars */
const DEFAULT_SERVER = '';

importScripts(
  'worker/util.js',
  'worker/config.js',
  'worker/capture.js',
  'worker/cookies.js',
  'worker/blocking.js',
  'worker/tabs.js',
  'worker/cdp.js',
  'worker/netcapture.js',
  'worker/hotfixes.js',
  'worker/evaluate.js');

let sseAbort = null;
let sseBuf = '';
let sseEventType = '';
let sseData = '';
let lastDataTime = 0;
let watchdogTimer = null;
let keepaliveTimer = null;
let streamGen = 0; // bumped on every (re)start/stop; only the current gen reconnects

// One place for the tab-registry control plane's error handling. fetch
// resolves normally for 401, 413 and 500, so a caller that only catches
// network errors reads a refusal as a success — which is how the server's
// registry could sit stale with nothing said about it anywhere.
//
// Returns the parsed answer, or null when the request was refused or never
// arrived. The response detail is bounded: it is a diagnostic, not a payload.
async function registryPost(path, payload) {
  try {
    const resp = await fetch(config.serverUrl + path, {
      method: 'POST',
      headers: bridgeHeaders(config.token),
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      let detail = '';
      try { detail = (await resp.text()).slice(0, 200); } catch (_) {}
      console.error(`[Daedalus] ${path} refused: HTTP ${resp.status} ${detail}`);
      return null;
    }
    try { return await resp.json(); } catch (_) { return {}; }
  } catch (e) {
    console.error(`[Daedalus] ${path} failed:`, (e && e.message) || e);
    return null;
  }
}

async function registerTab(chromeTabId) {
  if (!configured()) return;
  let tab;
  try {
    tab = await chrome.tabs.get(chromeTabId);
  } catch (_) {
    return;  // the tab went away between the event and this call
  }
  const answer = await registryPost('/register', {
    token: config.token,
    tabId: String(chromeTabId),
    url: tab.url || '',
    title: tab.title || '',
  });
  // /register is update-only: `updated: false` means the server has no such
  // tab, so refreshing it did nothing. A full sync is what supplies it, and
  // scheduleRegisterAllTabs coalesces, so a burst of these costs one.
  if (answer && answer.updated === false) scheduleRegisterAllTabs();
}

// ─── Tab tracking ───

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (configured() && (changeInfo.url || changeInfo.title)) {
    registerTab(tabId);
  }
});

chrome.tabs.onCreated.addListener((tab) => {
  if (configured() && tab.id) scheduleRegisterAllTabs();
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (configured()) {
    // Immediate removal + full sync to ensure clean state
    registryPost('/unregister', { token: config.token, tabId: String(tabId) });
    scheduleRegisterAllTabs();
  }
});

// ─── Command dispatch ───

function dispatchCommand(receivedCommand) {
  const cmd = Object.freeze({
    ...receivedCommand,
    _execution: _executionContext(receivedCommand),
  });
  const type = cmd.type || (cmd.code ? 'eval' : 'unknown');
  switch (type) {
    case 'screenshot': return handleScreenshot(cmd);
    case 'cookies': return handleCookies(cmd);
    case 'set-cookie': return handleSetCookie(cmd);
    case 'remove-cookie': return handleRemoveCookie(cmd);
    case 'clear-cookies': return handleClearCookies(cmd);
    case 'block-requests': return handleBlockRequests(cmd);
    case 'unblock-requests': return handleUnblockRequests(cmd);
    case 'list-block-rules': return handleListBlockRules(cmd);
    case 'fetch-timings': return handleFetchTimings(cmd);
    case 'ext-reload': return handleExtReload(cmd);
    case 'open-tab': return handleOpenTab(cmd);
    case 'open-tabs': return handleOpenTabs(cmd);
    case 'focus-tab': return handleFocusTab(cmd);
    case 'navigate': return handleNavigate(cmd);
    case 'reload': return handleReload(cmd);
    case 'close-tab': return handleCloseTab(cmd);
    case 'inject-css': return handleInjectCss(cmd);
    case 'remove-css': return handleRemoveCss(cmd);
    case 'cdp': return handleCdp(cmd);
    case 'net-capture': return handleNetCapture(cmd);
    case 'net-capture-stop': return handleNetCaptureStop(cmd);
    case 'net-capture-get': return handleNetCaptureGet(cmd);
    case 'store-hotfix': return handleStoreHotfix(cmd);
    case 'clear-hotfix': return handleClearHotfix(cmd);
    case 'clear-all-hotfixes': return handleClearAllHotfixes(cmd);
    case 'list-hotfixes': return handleListHotfixes(cmd);
    case 'set-permanent': return handleSetPermanent(cmd);
    case 'tabs': return handleExtTabs(cmd);
    case 'eval': return handleEval(cmd);
    default: return postResult(cmd._execution, null, 'Unknown command type: ' + type, 'extension');
  }
}

// ─── SSE stream ───

// Dedup redelivered command frames by their server-assigned delivery id (_did).
// At-least-once delivery can (rarely) redeliver a command whose socket write
// succeeded but whose unlink failed; skipping the repeat prevents double-exec of
// non-idempotent typed commands (open-tab, etc.). Legacy frames without _did
// have no stable delivery identity and are not deduplicated here.
// The ledger is PERSISTED, because an MV3 worker is stopped whenever it goes
// idle and the redelivery this guards against is precisely a command that
// outlived one worker: kept in memory alone, at-most-once meant at most once
// per worker boot, and a restart in between ran the command a second time.
const _seenDids = new Set();
const _seenDidOrder = [];
const _SEEN_DID_MAX = 1000;
const _SEEN_DID_KEY = 'daedalus-seen-dids';

async function _loadSeenDids() {
  try {
    const stored = await chrome.storage.local.get([_SEEN_DID_KEY]);
    const saved = stored[_SEEN_DID_KEY];
    if (!Array.isArray(saved)) return;
    for (const did of saved) {
      if (typeof did === 'string' && !_seenDids.has(did)) {
        _seenDids.add(did);
        _seenDidOrder.push(did);
      }
    }
  } catch (e) {
    // A worker that cannot read the ledger still dedups what it sees itself.
    console.warn('[Daedalus] Could not read the delivery ledger:', e.message);
  }
}

function _isDuplicateDelivery(did) {
  if (_seenDids.has(did)) return true;
  _seenDids.add(did);
  _seenDidOrder.push(did);
  if (_seenDidOrder.length > _SEEN_DID_MAX) _seenDids.delete(_seenDidOrder.shift());
  // Written back without awaiting: the in-memory check above is what makes
  // this delivery unique, and the write is what makes the NEXT worker agree.
  chrome.storage.local.set({ [_SEEN_DID_KEY]: _seenDidOrder.slice() })
    .catch(() => {});
  return false;
}

function parseSSEChunk(text) {
  sseBuf += text;
  const lines = sseBuf.split('\n');
  sseBuf = lines.pop();
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      sseEventType = line.slice(7).trim();
    } else if (line.startsWith('data: ')) {
      sseData = line.slice(6);
    } else if (line === '' && sseData) {
      if (sseEventType === 'command') {
        try {
          const cmd = JSON.parse(sseData);
          if (cmd._did && _isDuplicateDelivery(cmd._did)) {
            console.log('[Daedalus] Dedup skip did=' + cmd._did);
          } else {
            console.log('[Daedalus] Command:', cmd.id, cmd.type || 'eval');
            dispatchCommand(cmd);
          }
        } catch (e) {
          console.error('[Daedalus] Parse error:', e);
        }
      }
      sseEventType = '';
      sseData = '';
    } else if (line.startsWith(':')) {
      lastDataTime = Date.now();
    }
  }
}

async function startStream() {
  if (!config.token) return;
  // Without a bridge URL the stream URL is relative, so the fetch resolves
  // against the extension's own chrome-extension:// origin and the watchdog
  // retries that forever. Stay idle instead.
  if (!config.serverUrl) return;
  stopStream();               // tear down any existing stream (also bumps streamGen)
  const myGen = ++streamGen;  // this invocation owns reconnection for its generation

  sseBuf = '';
  sseEventType = '';
  sseData = '';
  lastDataTime = Date.now();

  watchdogTimer = setInterval(() => {
    if (myGen !== streamGen) return; // superseded — a newer stream is live
    if (Date.now() - lastDataTime > 30000) {
      console.warn('[Daedalus] Watchdog: no data in 30s, reconnecting');
      startStream(); // stopStream() inside handles teardown of this generation
    }
  }, 5000);

  // The token travels in a header, not in the target: a stream URL is the
  // one request target a proxy log keeps for the whole life of the stream.
  const url = config.serverUrl + '/stream?tab=extension';
  const controller = new AbortController();
  sseAbort = controller;

  try {
    const resp = await fetch(url, {
      signal: controller.signal, headers: bridgeAuth(config.token),
    });
    if (!resp.ok || !resp.body) {
      console.error('[Daedalus] Stream failed:', resp.status);
      if (myGen === streamGen) {
        sseAbort = null;
        setTimeout(() => { if (myGen === streamGen) startStream(); }, 3000);
      }
      return;
    }
    // Re-register all tabs on stream connect
    registerAllTabs();
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      lastDataTime = Date.now();
      parseSSEChunk(decoder.decode(value, { stream: true }));
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.error('[Daedalus] Stream error:', e);
    }
  }
  // Only the current generation reschedules. If a stop/restart bumped streamGen
  // while we were running, stay silent — the newer stream owns reconnection, so
  // we never stack overlapping SSE loops.
  if (myGen === streamGen) {
    sseAbort = null;
    setTimeout(() => { if (myGen === streamGen) startStream(); }, 1000);
  }
}

function stopStream() {
  streamGen++; // invalidate any in-flight loop so it won't reschedule
  if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
  if (sseAbort) { sseAbort.abort(); sseAbort = null; }
}

// ─── Keep-alive: self-ping to prevent MV3 service-worker dormancy ───
// Incoming SSE bytes do NOT reset the worker's ~30s idle timer, but a chrome
// API call does. Without this the worker goes dormant in idle gaps, the
// /stream SSE dies, and the registry keeps serving entries for tabs that
// empty until the next alarm wakes it. Touch a cheap API every 20s so the
// worker stays warm. Globals reset when the worker is killed, so this is
// re-armed from boot and from the alarm on every respawn; the guard makes
// re-arming idempotent while one is already running.
function ensureKeepAlive() {
  if (keepaliveTimer) return;
  keepaliveTimer = setInterval(() => {
    chrome.runtime.getPlatformInfo(() => {});
  }, 20000);
}

// ─── Message handler (from content scripts) ───

// In-flight GM.xmlhttpRequest relays, by the id the content script minted
// for each one. An entry exists only while its fetch is running: it is
// removed when the fetch settles and when a caller cancels it, so this
// never grows past what is actually in flight.
const _fetchControllers = new Map();

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'result') {
    const tabId = sender.tab ? String(sender.tab.id) : '';
    const execution = _takeEvalRelay(msg.relayId, tabId);
    if (!execution) return;
    const hostname = typeof msg.hostname === 'string' ? msg.hostname : '';
    const extra = { world: `page:${hostname}` };
    if (typeof msg.ms === 'number') extra.exec_ms = msg.ms;
    postResult(execution, msg.result, msg.error, tabId, extra);
  } else if (msg.type === 'register') {
    if (sender.tab) registerTab(sender.tab.id);
  } else if (msg.type === 'replayHotfixes') {
    if (sender.tab) handleHotfixReplay(sender.tab.id);
  } else if (msg.type === 'fetch') {
    // Cross-origin fetch relay — no CORS in service worker
    // Hard timeout: caller-provided or 60s default, prevents hung workers
    const timeoutMs = typeof msg.timeout === 'number' && msg.timeout > 0 ? msg.timeout : 60000;
    const controller = new AbortController();
    // Filed by id so a caller's abort can reach this controller. The entry
    // carries the reason as well, because a timeout and a cancellation both
    // arrive here as AbortError and are two different answers to the caller.
    const fetchEntry = { controller, cancelled: false };
    const fetchId = typeof msg.fetchId === 'string' ? msg.fetchId : '';
    if (fetchId) _fetchControllers.set(fetchId, fetchEntry);
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const t0 = performance.now();
    let tBodyDecoded, tFetchDone, tEncoded;
    (async () => {
      try {
        const opts = {
          method: msg.method || 'GET',
          headers: msg.headers || {},
          signal: controller.signal,
        };
        if (msg.body) {
          if (msg.bodyIsBase64) {
            const binary = atob(msg.body);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            opts.body = bytes.buffer;
          } else {
            opts.body = msg.body;
          }
        }
        tBodyDecoded = performance.now();
        const resp = await fetch(msg.url, opts);
        let data;
        const bytes = await readBoundedBody(resp, gmResponseLimit(msg.maxResponseBytes));
        tFetchDone = performance.now();
        // The raw byte count, for both response types. The text path used to
        // record its character count, which is not the size that matters to
        // a limit measured in bytes.
        const bodySize = bytes.byteLength;
        if (msg.responseType === 'arraybuffer') {
          data = bytesToBase64(bytes);
          tEncoded = performance.now();
        } else {
          // Response.text() is a UTF-8 decode whatever charset the response
          // declares, so decoding the bytes here answers identically.
          data = new TextDecoder().decode(bytes);
          tEncoded = tFetchDone;
        }
        const headers = {};
        resp.headers.forEach((v, k) => { headers[k] = v; });
        clearTimeout(timeoutId);
        if (fetchId) _fetchControllers.delete(fetchId);
        _recordTiming({
          url: msg.url.substring(0, 120),
          method: msg.method || 'GET',
          status: resp.status,
          bodySize,
          ms_bodyDecode: +(tBodyDecoded - t0).toFixed(1),
          ms_fetch: +(tFetchDone - tBodyDecoded).toFixed(1),
          ms_encode: +(tEncoded - tFetchDone).toFixed(1),
          ms_total: +(tEncoded - t0).toFixed(1),
          ts: Date.now(),
        });
        // finalUrl and statusText come from the RESPONSE, not the request:
        // after a redirect chain resp.url is where the body actually came
        // from, and reporting the requested URL told a caller a redirect had
        // not happened. Both are relayed verbatim; content.js falls back to
        // the request URL only when a response carries none.
        sendResponse({ status: resp.status, statusText: resp.statusText,
          finalUrl: resp.url, data, headers });
      } catch (e) {
        clearTimeout(timeoutId);
        if (fetchId) _fetchControllers.delete(fetchId);
        const isAbort = e.name === 'AbortError';
        const isTooLarge = e.gmTooLarge === true;
        // A cancellation and a timeout are the same exception; only the entry
        // says which happened. Reporting a cancelled request as a timeout
        // would tell the caller the endpoint was slow when the caller is the
        // one that stopped it.
        const isCancel = isAbort && fetchEntry.cancelled;
        _recordTiming({
          url: msg.url.substring(0, 120),
          method: msg.method || 'GET',
          error: isCancel ? 'aborted'
            : (isAbort ? 'timeout'
              : (isTooLarge ? 'too-large' : (e.message || 'error'))),
          ms_total: +(performance.now() - t0).toFixed(1),
          ts: Date.now(),
        });
        // `timedOut` travels beside the message because a timeout is its own
        // event to the caller: flattening it into an error string left
        // page.js's ontimeout branch unreachable.
        // `tooLarge` travels beside the message for the same reason
        // `timedOut` does: a refused size is a different answer from a
        // failed request, and a caller that wants to retry smaller can only
        // tell them apart if the relay says which happened.
        sendResponse({
          error: isCancel ? 'aborted by the caller'
            : (isAbort ? `fetch timeout after ${timeoutMs}ms` : e.message),
          timedOut: isAbort && !isCancel,
          aborted: isCancel,
          tooLarge: isTooLarge,
        });
      }
    })();
    return true; // async sendResponse
  } else if (msg.type === 'abortFetch') {
    // Idempotent by construction: the entry is removed as it is used, so a
    // second abort, or one for a fetch that already settled, finds nothing.
    const entry = _fetchControllers.get(msg.fetchId);
    if (entry) {
      _fetchControllers.delete(msg.fetchId);
      entry.cancelled = true;
      entry.controller.abort();
    }
  } else if (msg.type === 'openTab') {
    chrome.tabs.create({ url: msg.url, active: msg.active !== false }, (tab) => {
      sendResponse({ tabId: tab.id });
    });
    return true;
  } else if (msg.type === 'notification') {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
      title: msg.title || 'Daedalus',
      message: msg.text || '',
    });
  } else if (msg.type === 'download') {
    chrome.downloads.download({ url: msg.url, filename: msg.filename }, (downloadId) => {
      if (chrome.runtime.lastError) {
        sendResponse({ error: chrome.runtime.lastError.message });
      } else {
        sendResponse({ downloadId });
      }
    });
    return true;
  }
});

// ─── Register all tabs ───

// Coalesce burst syncs. Opening N tabs fires onCreated N times, and each sync is
// a chrome.tabs.query plus a POST carrying the ENTIRE tab list — for a 10-url
// open_tabs that was 10 full-registry uploads racing the result POST on the same
// service-worker event loop. The 30s heartbeat re-syncs anyway, so losing a
// coalesced trailing sync to a worker shutdown is self-correcting.
let _syncTimer = null;
function scheduleRegisterAllTabs() {
  if (_syncTimer) return;
  _syncTimer = setTimeout(() => { _syncTimer = null; registerAllTabs(); }, 250);
}

function registerAllTabs() {
  // Boot calls this once and the heartbeat alarm calls it every 30 seconds.
  if (!configured()) return;
  chrome.tabs.query({}, (tabs) => {
    const tabList = tabs
      .filter(t => t.id)
      .map(t => ({ tabId: String(t.id), url: t.url || '', title: t.title || '' }));
    // Sync replaces entire server registry — removes ghost tabs from prior sessions
    registryPost('/sync-tabs', { token: config.token, tabs: tabList });
  });
}

// ─── Alarms: survive MV3 service worker kills ───

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'daedalus-heartbeat') {
    // Ensure config is loaded (race: alarm can fire before loadConfig resolves on worker restart)
    if (!config.token) await loadConfig();
    ensureKeepAlive();
    registerAllTabs();
    // Restart stream if dead
    if (!sseAbort && config.token) {
      console.log('[Daedalus] Heartbeat: stream dead, restarting');
      startStream();
    }
  }
});

// ─── Keep-alive: content script ports prevent service worker termination ───

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'keepalive') {
    // Hold the port open — as long as any content script has a port,
    // Chrome keeps the service worker alive. Port messages reset the
    // 5-minute timeout Chrome imposes on long-lived ports.
    port.onMessage.addListener(() => {
      // Receiving a ping keeps the SW alive and resets port timeout
    });
    port.onDisconnect.addListener(() => {});
    // Also ensure stream is running — if we just got revived, restart it
    if (!sseAbort && config.token) {
      console.log('[Daedalus] Keep-alive connect: restarting stream');
      startStream();
    }
  }
});

// ─── Boot ───

loadConfig().then(() => {
  console.log(`[Daedalus] Extension v${VERSION} — token: ${config.token.substring(0, 8)}...`);
  ensureKeepAlive();
  startStream();
  registerAllTabs();
  // chrome.alarms survives service worker kills (setInterval does not)
  chrome.alarms.create('daedalus-heartbeat', { periodInMinutes: 0.5 });
});
