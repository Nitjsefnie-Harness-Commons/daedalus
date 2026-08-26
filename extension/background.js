// Daedalus Extension — background service worker
// @version 0.22.0.1
/* global handleCookies, handleSetCookie */
/* global handleRemoveCookie, handleClearCookies */

const VERSION = '0.22.0.1';
// No default server. A bridge URL is deployment-specific, and a build that
// ships someone's hostname would have every install of it call home to that
// host. The extension stays idle until a URL is set in its options page.
const DEFAULT_SERVER = '';

importScripts('worker/cookies.js');

// Fast base64 encode. Prefers native Uint8Array.prototype.toBase64 (TC39,
// Chrome 137+, Node 25+). Falls back to chunked String.fromCharCode.apply
// + array join. Never use TextDecoder('latin1') — that's actually
// windows-1252 per WHATWG and corrupts bytes 0x80-0x9F.
const _hasNativeToBase64 = typeof Uint8Array.prototype.toBase64 === 'function';
function bytesToBase64(bytes) {
  if (_hasNativeToBase64) return bytes.toBase64();
  const parts = [];
  const CHUNK = 32768;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK)));
  }
  return btoa(parts.join(''));
}

// GM.xmlhttpRequest response ceiling. The shim is injected into every
// matching top-level page, so any site a user visits can invoke this relay;
// with no ceiling it could name a response of any size and the worker would
// hold all of it. An 8 MiB response measured 11,184,812 base64 characters on
// top of the 8,388,608 bytes it already held.
//
// A caller that genuinely needs more says so per request, and is still bound
// by the ceiling: the opt-in raises a conservative default, it does not
// remove the limit, because the page asking is not necessarily one the
// operator trusts.
const GM_FETCH_MAX_RESPONSE = 8 * 1024 * 1024;
const GM_FETCH_RESPONSE_CEILING = 64 * 1024 * 1024;

function gmResponseLimit(requested) {
  const asked = typeof requested === 'number' && Number.isFinite(requested)
    ? Math.floor(requested) : 0;
  if (asked <= 0) return GM_FETCH_MAX_RESPONSE;
  return Math.min(asked, GM_FETCH_RESPONSE_CEILING);
}

// Read a response body, counting as it streams and abandoning it at the
// limit. Counting after the fact — resp.text() or resp.arrayBuffer() — is
// what made the ceiling unenforceable: by the time the size is known the
// worker is already holding every byte of it.
async function readBoundedBody(resp, limit) {
  if (!resp.body) return new Uint8Array(0);
  const reader = resp.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      // Cancelling the body stream terminates the fetch, so an oversized
      // response stops arriving rather than merely stopping being read.
      try { await reader.cancel(); } catch { /* the read is over regardless */ }
      const tooLarge = new Error(
        `response exceeded the ${limit}-byte relay limit`);
      tooLarge.gmTooLarge = true;
      throw tooLarge;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  return bytes;
}

// The bridge settles credentials before it reads a request body, so the token
// rides in a header as well as in the payload. A screenshot upload or a large
// eval result carrying only a body token is refused unread, because a body
// token cannot be checked without reading the body.
function bridgeAuth(token) {
  return { 'Authorization': 'Bearer ' + token };
}

function bridgeHeaders(token) {
  return { 'Content-Type': 'application/json', ...bridgeAuth(token) };
}

// Timing ring buffer for diagnostics (last 500 fetch relay entries)
const _fetchTimings = [];
const _FETCH_TIMINGS_MAX = 500;
function _recordTiming(entry) {
  _fetchTimings.push(entry);
  if (_fetchTimings.length > _FETCH_TIMINGS_MAX) _fetchTimings.shift();
}

let config = { token: '', serverUrl: DEFAULT_SERVER };
let sseAbort = null;
let sseBuf = '';
let sseEventType = '';
let sseData = '';
let lastDataTime = 0;
let watchdogTimer = null;
let keepaliveTimer = null;
let streamGen = 0; // bumped on every (re)start/stop; only the current gen reconnects

// ─── Config ───

async function loadConfig() {
  const stored = await chrome.storage.local.get(['daedalus-token', 'daedalus-server']);
  config.token = stored['daedalus-token'] || '';
  config.serverUrl = stored['daedalus-server'] || DEFAULT_SERVER;
  if (!config.serverUrl) {
    console.warn('[Daedalus] No server URL configured — open the extension '
      + 'options and set the bridge URL. Nothing will connect until then.');
  }
  // Before any stream can deliver a command, so a restarted worker knows what
  // the one before it already spent.
  await _loadSeenDids();
  // Auto-generate token on first run
  if (!config.token) {
    config.token = crypto.randomUUID();
    await chrome.storage.local.set({ 'daedalus-token': config.token });
    // The value stays out of the log: it is a reusable browser-control
    // credential, and this line put it into DevTools output, screen
    // recordings and diagnostic bundles on every first run. The options
    // page is where an operator reads it back.
    console.log('[Daedalus] Generated a token; read it in the extension options page.');
  }
  return config;
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  // Only reconnect when the token or server URL actually changed. Every other
  // storage write (GM.setValue, hotfix stores, dashboard prefs) used to tear
  // down and rebuild the SSE stream, dropping any in-flight command.
  let reconnect = false;
  if (changes['daedalus-token']) {
    config.token = changes['daedalus-token'].newValue || '';
    reconnect = true;
  }
  if (changes['daedalus-server']) {
    config.serverUrl = changes['daedalus-server'].newValue || DEFAULT_SERVER;
    reconnect = true;
  }
  if (reconnect) {
    stopStream();
    if (config.token) startStream();
  }
});

// ─── HTTP helpers ───

function _executionContext(cmd) {
  return Object.freeze({
    id: cmd.id,
    deliveryId: typeof cmd._did === 'string' ? cmd._did : '',
    resultRoute: Object.freeze({
      token: config.token,
      serverUrl: config.serverUrl,
    }),
  });
}

async function postResult(execution, result, error, tabId, extra = {}) {
  const payload = {
    token: execution.resultRoute.token,
    tabId: tabId || 'extension',
    id: execution.id,
    error: error || null,
    ts: Date.now(),
    result,
    world: extra.world || 'extension',
    ...extra,
  };
  if (execution.deliveryId) payload._did = execution.deliveryId;
  // Retry on transient network failure: the command already ran, so losing the
  // result POST would make the caller time out on work that actually succeeded.
  const body = JSON.stringify(payload);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await fetch(execution.resultRoute.serverUrl + '/result', {
        method: 'POST',
        headers: bridgeHeaders(execution.resultRoute.token),
        body,
      });
      if (resp.ok) return;
      // Non-OK (e.g. 5xx during a restart): retry unless it's a client error.
      if (resp.status < 500) return;
    } catch (e) {
      if (attempt === 2) console.error('[Daedalus] Result POST failed:', e);
    }
    await new Promise(r => setTimeout(r, 300 * (attempt + 1)));
  }
}

// A bridge is usable only with BOTH a token and a URL. The token is generated
// on install so it is always set; the URL is not, and every listener below
// fires on ordinary browsing. Checking only the token means an unconfigured
// install issues a relative request per tab event, forever.
function configured() {
  return Boolean(config.token && config.serverUrl);
}

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

// ─── Extension command handlers ───

async function handleScreenshot(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId || chromeTabId === 'extension') {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    const tab = await chrome.tabs.get(typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId));
    const fmt = cmd.format || 'png';
    // captureVisibleTab photographs whatever is ACTIVE in the window it is
    // given, so a tab that is merely named is not the tab that gets captured:
    // a screenshot aimed at an inactive tab returned the active sibling's
    // pixels under the requested tab's url and title. Bring the target
    // forward, capture, and put the window back as it was found.
    //
    // Serialized, because two captures interleaving would each restore the
    // other's tab and both would photograph the wrong page.
    const dataUrl = await _captureQueue(async () => {
      let restore = null;
      if (!tab.active) {
        const [previous] = await chrome.tabs.query({
          active: true, windowId: tab.windowId,
        });
        if (previous && previous.id !== tab.id) restore = previous.id;
        await chrome.tabs.update(tab.id, { active: true });
      }
      try {
        return await chrome.tabs.captureVisibleTab(tab.windowId, {
          format: fmt,
          quality: cmd.quality || 80,
        });
      } finally {
        if (restore !== null) await chrome.tabs.update(restore, { active: true });
      }
    });
    const base64 = dataUrl.replace(/^data:image\/\w+;base64,/, '');
    const uploadResp = await fetch(cmd._execution.resultRoute.serverUrl + '/upload', {
      method: 'POST',
      headers: bridgeHeaders(cmd._execution.resultRoute.token),
      body: JSON.stringify({
        token: cmd._execution.resultRoute.token,
        id: cmd.id,
        data: base64,
        format: fmt,
      }),
    });
    let uploadResult = null;
    try {
      uploadResult = await uploadResp.json();
    } catch (_) {}
    // A rejected upload stored nothing. Reporting success here would hand the
    // caller an envelope with no path and no size but `error: null`.
    if (!uploadResp.ok) {
      const detail = (uploadResult && uploadResult.error)
        || 'HTTP ' + uploadResp.status;
      await postResult(
        cmd._execution, null, 'Screenshot upload failed: ' + detail,
        'extension');
      return;
    }
    await postResult(cmd._execution, {
      path: uploadResult && uploadResult.path,
      size: uploadResult && uploadResult.size,
      format: fmt, tabUrl: tab.url, tabTitle: tab.title,
    }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// Serialize read-modify-write cycles over one shared store. The SSE parser
// dispatches commands without awaiting them, so two handlers can otherwise
// read the same snapshot and the second write silently drops the first.
function _serializer() {
  let tail = Promise.resolve();
  return (mutate) => {
    const run = tail.then(mutate);
    tail = run.then(() => {}, () => {});
    return run;
  };
}

// Captures activate the tab they photograph, so they cannot overlap.
const _captureQueue = _serializer();

// ─── Request blocking via declarativeNetRequest ───

const BLOCK_RULE_BASE_ID = 9000; // Dynamic rule IDs start here
let blockRuleCounter = 0;
const _withBlockRuleLock = _serializer();

// Session rules survive service-worker suspension but `blockRuleCounter` does
// not, so a restarted worker would re-issue an id that is still installed and
// updateSessionRules would reject the duplicate. Seed the counter from the
// rules that are actually present before allocating.
async function _nextBlockRuleId() {
  const existing = await chrome.declarativeNetRequest.getSessionRules();
  let highest = BLOCK_RULE_BASE_ID + blockRuleCounter;
  for (const rule of existing) {
    if (typeof rule.id === 'number' && rule.id > highest) highest = rule.id;
  }
  blockRuleCounter = highest - BLOCK_RULE_BASE_ID;
  return BLOCK_RULE_BASE_ID + (++blockRuleCounter);
}

async function handleBlockRequests(cmd) {
  try {
    if (!cmd.pattern) return postResult(cmd._execution, null, 'Missing pattern', 'extension');
    // SAFETY: use session-scoped rules (required for tabIds support).
    // Always scope to tab IDs so only page-originated requests are blocked —
    // never extension service worker fetches (SSE, relay, result POSTs).
    let tabIds;
    if (cmd.tabId) {
      tabIds = [typeof cmd.tabId === 'number' ? cmd.tabId : parseInt(cmd.tabId)];
    } else {
      // No tab specified — block in ALL current tabs (but not the service worker)
      const tabs = await chrome.tabs.query({});
      tabIds = tabs.map(t => t.id).filter(id => id > 0);
    }
    // Extract server hostname to always exclude from blocking
    let serverHost;
    try {
      serverHost = new URL(cmd._execution.resultRoute.serverUrl).hostname;
    } catch (_) {}
    const rule = {
      priority: 1,
      action: { type: 'block' },
      condition: {
        urlFilter: cmd.pattern,
        resourceTypes: ['xmlhttprequest', 'media', 'other'],
        tabIds,
        excludedRequestDomains: serverHost ? [serverHost] : [],
      },
    };
    // Allocate and install under one lock: two concurrent adds that both read
    // the rule list before either wrote would otherwise pick the same id.
    const ruleId = await _withBlockRuleLock(async () => {
      const id = await _nextBlockRuleId();
      await chrome.declarativeNetRequest.updateSessionRules({
        addRules: [{ id, ...rule }],
        removeRuleIds: [],
      });
      return id;
    });
    await postResult(cmd._execution, { ruleId, pattern: cmd.pattern, tabIds }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleUnblockRequests(cmd) {
  try {
    if (cmd.ruleId !== undefined && cmd.ruleId !== null) {
      // Remove specific rule. A PRESENT id is never a request to remove
      // everything, however malformed it is: `if (cmd.ruleId)` was false for
      // 0, so the narrowest request fell through to the branch below and
      // destroyed every session rule while reporting them as removed.
      const ids = (Array.isArray(cmd.ruleId) ? cmd.ruleId : [cmd.ruleId]).map(Number);
      if (!ids.length || ids.some((id) => !Number.isInteger(id) || id <= 0)) {
        await postResult(cmd._execution, null,
          'ruleId must be a positive integer', 'extension');
        return;
      }
      await chrome.declarativeNetRequest.updateSessionRules({
        removeRuleIds: ids,
        addRules: [],
      });
      await postResult(cmd._execution, { removed: ids }, null, 'extension');
    } else {
      // Remove all session block rules
      const existing = await chrome.declarativeNetRequest.getSessionRules();
      const ids = existing.map(r => r.id);
      if (ids.length > 0) {
        await chrome.declarativeNetRequest.updateSessionRules({
          removeRuleIds: ids,
          addRules: [],
        });
      }
      await postResult(cmd._execution, { removed: ids }, null, 'extension');
    }
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleListBlockRules(cmd) {
  try {
    const rules = await chrome.declarativeNetRequest.getSessionRules();
    await postResult(cmd._execution, rules, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

const _cdpSessions = {}; // chromeTabId -> true while a sticky CDP session is held

async function handleCdp(cmd) {
  if (!cmd.method) return postResult(cmd._execution, null, 'Missing CDP method', 'extension');
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId || chromeTabId === 'extension') {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

    const heldBefore = !!_cdpSessions[chromeTabId];
    const keep = !!cmd.keep_session;
    if (!heldBefore) {
      await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
    }
    if (keep) _cdpSessions[chromeTabId] = true;
    try {
      const result = await chrome.debugger.sendCommand({ tabId: chromeTabId }, cmd.method, cmd.params || {});
      await postResult(cmd._execution, result, null, 'extension');
    } finally {
      if (!keep) {
        delete _cdpSessions[chromeTabId];
        try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
      }
    }
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleCloseTab(cmd) {
  if (!cmd.tabId && !cmd.tabIds) return postResult(cmd._execution, null, 'Missing tabId or tabIds', 'extension');
  let ids = cmd.tabIds || [cmd.tabId];
  ids = ids.map(id => typeof id === 'number' ? id : parseInt(id));
  const closed = [];
  const errors = [];
  for (const id of ids) {
    try {
      await chrome.tabs.remove(id);
      closed.push(id);
    } catch (e) {
      errors.push({ id, error: e.message });
    }
  }
  // onRemoved listener handles unregister + sync
  await postResult(cmd._execution, { closed, errors }, null, 'extension');
}

async function handleFetchTimings(cmd) {
  const timings = _fetchTimings.slice();
  if (cmd.reset) _fetchTimings.length = 0;
  await postResult(cmd._execution, { timings, hasNativeToBase64: _hasNativeToBase64, count: timings.length }, null, 'extension');
}

async function handleExtReload(cmd) {
  // Post result before reloading — reload kills the service worker
  await postResult(cmd._execution, { reloading: true, version: VERSION }, null, 'extension');
  // Small delay to ensure result POST completes
  setTimeout(() => chrome.runtime.reload(), 500);
}

async function handleOpenTab(cmd) {
  try {
    if (!cmd.url) return postResult(cmd._execution, null, 'Missing url', 'extension');
    const opts = { url: cmd.url };
    if (cmd.active !== undefined) opts.active = cmd.active;
    if (cmd.pinned) opts.pinned = true;
    if (cmd.windowId) opts.windowId = typeof cmd.windowId === 'number' ? cmd.windowId : parseInt(cmd.windowId);
    const t0 = Date.now();
    const tab = await chrome.tabs.create(opts);
    const create_ms = Date.now() - t0;
    await postResult(cmd._execution, { tabId: tab.id, url: tab.url, windowId: tab.windowId, create_ms }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleOpenTabs(cmd) {
  try {
    const urls = Array.isArray(cmd.urls) ? cmd.urls : [];
    if (urls.length === 0) return postResult(cmd._execution, null, 'Missing urls', 'extension');
    const baseOpts = {};
    if (cmd.active !== undefined) baseOpts.active = cmd.active;
    if (cmd.pinned) baseOpts.pinned = true;
    if (cmd.windowId) baseOpts.windowId = typeof cmd.windowId === 'number' ? cmd.windowId : parseInt(cmd.windowId);
    // Dispatch every create before awaiting any. chrome.tabs.create resolves in
    // ~150ms, so awaiting them one at a time made a 6-tab call take ~1s with a
    // visible stagger between the first and last tab. The create IPCs stay
    // ordered, so the resulting tab order still follows `urls`.
    const t0 = Date.now();
    const settled = await Promise.allSettled(
      urls.map(url => chrome.tabs.create({ ...baseOpts, url }))
    );
    // create_ms is the create phase alone. roundtrip_ms - create_ms is everything
    // else (queue + SSE + registry traffic + the result POST), so the two together
    // account for the whole and a gap points at an unmeasured phase.
    const create_ms = Date.now() - t0;
    const opened = [];
    const errors = [];
    settled.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        opened.push({ tabId: r.value.id, url: r.value.url, windowId: r.value.windowId });
      } else {
        errors.push({ url: urls[i], error: r.reason?.message || String(r.reason) });
      }
    });
    // No per-tab registerTab here: onCreated already schedules a full registry
    // sync that covers every one of them, and doing both meant N extra
    // chrome.tabs.get + POST /register round trips per call.
    await postResult(cmd._execution, { opened, errors, create_ms }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleFocusTab(cmd) {
  try {
    if (!cmd.tabId) return postResult(cmd._execution, null, 'Missing tabId', 'extension');
    const tabId = typeof cmd.tabId === 'number' ? cmd.tabId : parseInt(cmd.tabId);
    const tab = await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    await postResult(cmd._execution, { tabId: tab.id, windowId: tab.windowId }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleNavigate(cmd) {
  try {
    if (!cmd.url) return postResult(cmd._execution, null, 'Missing url', 'extension');
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    const tab = await chrome.tabs.update(tabId, { url: cmd.url });
    await postResult(cmd._execution, { tabId: tab.id, url: cmd.url }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleReload(cmd) {
  try {
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    await chrome.tabs.reload(tabId, { bypassCache: !!cmd.bypassCache });
    await postResult(cmd._execution, { tabId, bypassCache: !!cmd.bypassCache }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleInjectCss(cmd) {
  try {
    if (!cmd.css) return postResult(cmd._execution, null, 'Missing css', 'extension');
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    await chrome.scripting.insertCSS({
      target: { tabId, allFrames: !!cmd.allFrames },
      css: cmd.css,
    });
    await postResult(cmd._execution, { tabId, injected: cmd.css.length }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleRemoveCss(cmd) {
  try {
    if (!cmd.css) return postResult(cmd._execution, null, 'Missing css', 'extension');
    let tabId = cmd.tabId;
    if (!tabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      tabId = active.id;
    }
    tabId = typeof tabId === 'number' ? tabId : parseInt(tabId);
    await chrome.scripting.removeCSS({
      target: { tabId, allFrames: !!cmd.allFrames },
      css: cmd.css,
    });
    await postResult(cmd._execution, { tabId, removed: cmd.css.length }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// ─── Network capture (CDP) ───

const _netCaptures = {}; // tabId → { requests: [], maxRequests }

// A capture buffer lives in the service worker and grows to hold headers and
// response bodies, so its size is a memory budget rather than a preference.
// `cmd.maxRequests || 1000` accepted anything a caller sent: -1 was kept and
// evicted the only event on arrival, leaving an empty capture, and 1e9 was
// kept and buffered everything.
const NET_CAPTURE_DEFAULT = 1000;
const NET_CAPTURE_MAX = 20000;

function _netCaptureLimit(value) {
  if (value === undefined || value === null || value === '') return NET_CAPTURE_DEFAULT;
  const limit = Number(value);
  if (!Number.isInteger(limit) || limit < 1) {
    throw new Error(`maxRequests must be an integer from 1 to ${NET_CAPTURE_MAX}`);
  }
  return Math.min(limit, NET_CAPTURE_MAX);
}

function _netEventHandler(source, method, params) {
  const tabId = source.tabId;
  const cap = _netCaptures[tabId];
  if (!cap) return;

  if (method === 'Network.requestWillBeSent') {
    const entry = {
      requestId: params.requestId,
      url: params.request.url,
      method: params.request.method,
      headers: params.request.headers,
      postData: params.request.postData || null,
      type: params.type || '',
      frameId: params.frameId || '',
      ts: params.wallTime || (params.timestamp ? params.timestamp * 1000 : Date.now()),
      initiator: params.initiator ? (params.initiator.url || params.initiator.type || '') : '',
    };
    cap.requests.push(entry);
    // Find matching entry to attach response later
  } else if (method === 'Network.responseReceived') {
    const entry = cap.requests.find(r => r.requestId === params.requestId);
    if (entry) {
      entry.status = params.response.status;
      entry.statusText = params.response.statusText || '';
      entry.responseHeaders = params.response.headers || {};
      entry.mimeType = params.response.mimeType || '';
      entry.responseUrl = params.response.url || '';
    }
  } else if (method === 'Network.loadingFinished') {
    const entry = cap.requests.find(r => r.requestId === params.requestId);
    if (entry) {
      entry.done = true;
      entry.encodedLength = params.encodedDataLength || 0;
    }
  }

  // Evict oldest if over limit
  if (cap.requests.length > cap.maxRequests) {
    cap.requests.shift();
  }
}

async function handleNetCapture(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);
    // Before the attach: a limit this worker will not honour is not a reason
    // to attach a debugger to someone's tab. Throws, and the catch below
    // reports it to the caller.
    const limit = _netCaptureLimit(cmd.maxRequests);

    if (_netCaptures[chromeTabId]) {
      return postResult(cmd._execution, { already: true, tabId: chromeTabId, buffered: _netCaptures[chromeTabId].requests.length }, null, 'extension');
    }

    // Publish the capture only once attach AND Network.enable have succeeded.
    // A half-set-up capture would make the next call answer `already: true`
    // over a tab nothing is attached to, and leak the attachment when
    // Network.enable is what failed.
    let attached = false;
    try {
      await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
      attached = true;
      await chrome.debugger.sendCommand({ tabId: chromeTabId }, 'Network.enable', {});
    } catch (e) {
      if (attached) {
        try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
      }
      return postResult(cmd._execution, null, e.message, 'extension');
    }
    _netCaptures[chromeTabId] = { requests: [], maxRequests: limit };
    await postResult(cmd._execution, { capturing: true, tabId: chromeTabId }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleNetCaptureStop(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

    const cap = _netCaptures[chromeTabId];
    if (!cap) return postResult(cmd._execution, { stopped: false, reason: 'not capturing' }, null, 'extension');

    // Optionally fetch response bodies before stopping
    if (cmd.bodies) {
      for (const entry of cap.requests) {
        if (entry.done && !entry.body) {
          try {
            const resp = await chrome.debugger.sendCommand({ tabId: chromeTabId }, 'Network.getResponseBody', { requestId: entry.requestId });
            entry.body = resp.body;
            entry.bodyBase64 = resp.base64Encoded || false;
          } catch (_) {}
        }
      }
    }

    const requests = cap.requests.slice();
    delete _netCaptures[chromeTabId];
    try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
    await postResult(cmd._execution, { stopped: true, tabId: chromeTabId, count: requests.length, requests }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleNetCaptureGet(cmd) {
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId) {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

    const cap = _netCaptures[chromeTabId];
    if (!cap) return postResult(cmd._execution, null, 'Not capturing on this tab', 'extension');

    let requests = cap.requests;
    if (cmd.filter) {
      const pat = new RegExp(cmd.filter, 'i');
      requests = requests.filter(r => pat.test(r.url) || pat.test(r.type));
    }

    // Optionally fetch response bodies
    if (cmd.bodies) {
      for (const entry of requests) {
        if (entry.done && !entry.body) {
          try {
            const resp = await chrome.debugger.sendCommand({ tabId: chromeTabId }, 'Network.getResponseBody', { requestId: entry.requestId });
            entry.body = resp.body;
            entry.bodyBase64 = resp.base64Encoded || false;
          } catch (_) {}
        }
      }
    }

    await postResult(cmd._execution, { tabId: chromeTabId, count: requests.length, requests }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// Wire up CDP event listener (once, globally)
chrome.debugger.onEvent.addListener(_netEventHandler);

// Clean up on tab close
chrome.tabs.onRemoved.addListener((tabId) => {
  if (_netCaptures[tabId]) {
    delete _netCaptures[tabId];
    try { chrome.debugger.detach({ tabId }); } catch (_) {}
  }
  if (_cdpSessions[tabId]) {
    delete _cdpSessions[tabId];
    try { chrome.debugger.detach({ tabId }); } catch (_) {}
  }
});

// Drop sticky CDP state when Chrome detaches us (DevTools opened, target
// crashed, etc.). A capture whose attachment is gone receives no further
// events, so it must not keep answering `already: true` either.
chrome.debugger.onDetach.addListener((source) => {
  if (source && source.tabId != null) {
    delete _cdpSessions[source.tabId];
    delete _netCaptures[source.tabId];
  }
});

// ─── Hotfix system ───

const HOTFIX_KEY = 'daedalus-hotfixes';

// Replay runs from here rather than from the page relay, because the page
// relay only ever had `eval` and a blob <script> to work with and a page CSP
// that forbids both — github.com's, for one — refused every fix while the
// blocked blob load reported nothing back. This is the routing ordinary eval
// already uses: a source-free probe, banner-free MAIN-world injection when
// dynamic compilation is available, and CDP when it is not.
async function _eligibleHotfixes() {
  const data = await chrome.storage.local.get([HOTFIX_KEY]);
  const stored = data[HOTFIX_KEY];
  if (!stored || !Array.isArray(stored.fixes)) return [];
  return stored.fixes.filter(
    f => f && typeof f.code === 'string'
      && (f.permanent === true || stored.version === VERSION));
}

async function _replayViaCdp(chromeTabId, code) {
  // A capture or a kept session already owns the attachment; reuse it and
  // leave it in place, because detaching would end that capture or session.
  const held = Boolean(_cdpSessions[chromeTabId]) || Boolean(_netCaptures[chromeTabId]);
  try {
    if (!held) await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
  } catch (error) {
    return 'cdp attach failed: ' + (error && (error.message || String(error)));
  }
  try {
    const evaluated = await chrome.debugger.sendCommand(
      { tabId: chromeTabId }, 'Runtime.evaluate',
      { expression: code, replMode: true, awaitPromise: false });
    const failure = _cdpError(evaluated);
    await _releaseCdpObjects(chromeTabId, evaluated);
    return failure;
  } catch (error) {
    return error && (error.message || String(error));
  } finally {
    if (!held) {
      try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
    }
  }
}

async function _replayHotfix(chromeTabId, code) {
  let useMainWorld = false;
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId: chromeTabId },
      world: 'MAIN',
      func: _canUseMainWorldEval,
    });
    useMainWorld = probe[0]?.result === true;
  } catch (_) {}
  if (useMainWorld) {
    let results;
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId: chromeTabId },
        world: 'MAIN',
        func: _executeMainWorldEval,
        args: [code],
      });
    } catch (error) {
      return error && (error.message || String(error));
    }
    const frame = Array.isArray(results) ? results[0] : null;
    if (!frame || typeof frame !== 'object') return 'no result frame';
    if (Object.prototype.hasOwnProperty.call(frame, 'error')) {
      const failure = frame.error;
      return typeof failure === 'string'
        ? failure : (failure && failure.message) || String(failure);
    }
    const res = frame.result;
    if (res && typeof res === 'object' && res.e) return res.e;
    return null;
  }
  return _replayViaCdp(chromeTabId, code);
}

async function handleHotfixReplay(chromeTabId) {
  let fixes;
  try {
    fixes = await _eligibleHotfixes();
  } catch (error) {
    console.error('[Daedalus] hotfix replay could not read the store:', error);
    return;
  }
  if (fixes.length === 0) return;
  const failures = [];
  for (const hf of fixes) {
    let failure;
    try {
      failure = await _replayHotfix(chromeTabId, hf.code);
    } catch (error) {
      failure = error && (error.message || String(error));
    }
    if (failure) failures.push(hf.id + ': ' + failure);
  }
  // Reported here rather than in the page: a page console is not where an
  // operator looks, and a page that refuses the fix is exactly the page whose
  // console is least trustworthy about why.
  if (failures.length > 0) {
    console.error('[Daedalus] hotfix replay failed on tab ' + chromeTabId
                  + ': ' + failures.join('; '));
  } else {
    console.log('[Daedalus] replayed ' + fixes.length + ' hotfix(es) on tab '
                + chromeTabId);
  }
}

// Every mutation of the shared hotfix record runs through this lock. Without
// it two stores read the same snapshot, both answer success, and only the
// later write survives — acknowledged loss of persistent user code.
const _withHotfixLock = _serializer();

async function handleStoreHotfix(cmd) {
  try {
    if (!cmd.fixId || !cmd.code) return postResult(cmd._execution, null, 'Missing fixId or code', 'extension');
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY] || { version: VERSION, fixes: [] };
      stored.version = VERSION;
      const existing = stored.fixes.find(f => f.id === cmd.fixId);
      const permanent = (cmd.permanent === true) ? true
                      : (cmd.permanent === false) ? false
                      : (existing ? existing.permanent === true : false);
      stored.fixes = stored.fixes.filter(f => f.id !== cmd.fixId);
      stored.fixes.push({ id: cmd.fixId, code: cmd.code, ts: Date.now(), permanent });
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return { stored: cmd.fixId, total: stored.fixes.length, permanent };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleClearHotfix(cmd) {
  try {
    if (!cmd.fixId) return postResult(cmd._execution, null, 'Missing fixId', 'extension');
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY];
      if (!stored) return { cleared: cmd.fixId, found: false };
      stored.fixes = stored.fixes.filter(f => f.id !== cmd.fixId);
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return { cleared: cmd.fixId, found: true, remaining: stored.fixes.length };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleClearAllHotfixes(cmd) {
  try {
    const outcome = await _withHotfixLock(async () => {
      if (cmd.includePermanent === true) {
        await chrome.storage.local.remove([HOTFIX_KEY]);
        return { cleared: true, includePermanent: true };
      }
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY];
      if (!stored) return { cleared: true, kept: 0 };
      const before = stored.fixes.length;
      stored.fixes = stored.fixes.filter(f => f.permanent === true);
      const kept = stored.fixes.length;
      if (kept === 0) {
        await chrome.storage.local.remove([HOTFIX_KEY]);
      } else {
        await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      }
      return { cleared: true, removed: before - kept, kept };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleSetPermanent(cmd) {
  try {
    if (!cmd.fixId || typeof cmd.permanent !== 'boolean') {
      return postResult(cmd._execution, null, 'Missing fixId or permanent (bool)', 'extension');
    }
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY];
      if (!stored) return { id: cmd.fixId, permanent: cmd.permanent, found: false };
      const fix = stored.fixes.find(f => f.id === cmd.fixId);
      if (!fix) return { id: cmd.fixId, permanent: cmd.permanent, found: false };
      fix.permanent = cmd.permanent;
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return { id: cmd.fixId, permanent: cmd.permanent, found: true };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleListHotfixes(cmd) {
  try {
    const data = await chrome.storage.local.get([HOTFIX_KEY]);
    const stored = data[HOTFIX_KEY] || { version: VERSION, fixes: [] };
    await postResult(cmd._execution, stored, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleExtTabs(cmd) {
  try {
    const tabs = await chrome.tabs.query({});
    const result = tabs.map(t => ({
      id: t.id, url: t.url, title: t.title,
      active: t.active, windowId: t.windowId,
    }));
    await postResult(cmd._execution, result, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// ─── Eval: route to content script ───

const _evalRelays = new Map();
const _EVAL_RELAY_MAX = 1000;
const _EVAL_RELAY_TTL_MS = 300000;

function _removeEvalRelay(relayId) {
  const pending = _evalRelays.get(relayId);
  if (!pending) return null;
  _evalRelays.delete(relayId);
  clearTimeout(pending.timeoutId);
  return pending;
}

function _expireEvalRelay(relayId) {
  const pending = _removeEvalRelay(relayId);
  if (!pending) return;
  postResult(
    pending.execution, null,
    `Eval relay timed out after ${_EVAL_RELAY_TTL_MS} ms`, pending.tabId);
}

function _registerEvalRelay(execution, tabId) {
  if (_evalRelays.size >= _EVAL_RELAY_MAX) return null;
  let relayId;
  do { relayId = crypto.randomUUID(); } while (_evalRelays.has(relayId));
  const timeoutId = setTimeout(
    () => _expireEvalRelay(relayId), _EVAL_RELAY_TTL_MS);
  _evalRelays.set(relayId, Object.freeze({ execution, tabId, timeoutId }));
  return relayId;
}

function _takeEvalRelay(relayId, tabId) {
  if (typeof relayId !== 'string') return null;
  const pending = _evalRelays.get(relayId);
  if (!pending || pending.tabId !== tabId) return null;
  _removeEvalRelay(relayId);
  return pending.execution;
}

function _cdpError(response) {
  return response.exceptionDetails?.exception?.description
    || response.exceptionDetails?.text || null;
}

const _CDP_PROMISE_TIMEOUT_MS = 10000;

async function _releaseCdpObjects(chromeTabId, ...values) {
  const objectIds = new Set();
  for (const value of values) {
    const ids = [
      value?.objectId,
      value?.result?.objectId,
      value?.exceptionDetails?.exception?.objectId,
    ];
    for (const objectId of ids) {
      if (objectId) objectIds.add(objectId);
    }
  }
  for (const objectId of objectIds) {
    try {
      await chrome.debugger.sendCommand(
        { tabId: chromeTabId }, 'Runtime.releaseObject', { objectId });
    } catch (_) {}
  }
}

// Read an inspector-held value by value and release every handle returned by
// the protocol. This describes the CDP transport only: submitted source may
// already have routed its value through page-controlled machinery.
async function _cdpSettle(chromeTabId, remote) {
  if (!remote?.objectId) return { value: remote?.value, error: null };
  const settle = remote.subtype === 'promise'
    ? ['Runtime.awaitPromise', { promiseObjectId: remote.objectId }]
    : ['Runtime.callFunctionOn',
      { objectId: remote.objectId,
        functionDeclaration: 'function () { return this; }' }];
  let response;
  let timeoutId;
  let timedOut = false;
  const responsePromise = chrome.debugger.sendCommand(
    { tabId: chromeTabId }, settle[0],
    { ...settle[1], returnByValue: true });
  if (remote.subtype === 'promise') {
    responsePromise.then((lateResponse) => {
      if (timedOut) return _releaseCdpObjects(chromeTabId, lateResponse);
      return undefined;
    }, () => {});
  }
  try {
    response = remote.subtype === 'promise'
      ? await Promise.race([
        responsePromise,
        new Promise((_resolve, reject) => {
          timeoutId = setTimeout(() => {
            timedOut = true;
            reject(new Error(
              `promise settlement timed out after ${_CDP_PROMISE_TIMEOUT_MS} ms`));
          }, _CDP_PROMISE_TIMEOUT_MS);
        }),
      ])
      : await responsePromise;
    return { value: response.result?.value, error: _cdpError(response) };
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    await _releaseCdpObjects(chromeTabId, remote, response);
  }
}

// Evaluate through the debugger's Runtime domain. The V8 inspector compiles
// source without calling the page's `eval` / `Function` bindings, and REPL mode
// supplies top-level await. That says how the source ran, not whether its value
// is trustworthy: page code and page promise machinery can still choose it.
// Returns true after CDP dispatch, false only before submitted source runs.
async function _evalViaCdp(cmd, chromeTabId) {
  // A capture or a kept CDP session already owns the attachment; reuse it and
  // leave it in place, because detaching would end that capture or session.
  const held = Boolean(_cdpSessions[chromeTabId]) || Boolean(_netCaptures[chromeTabId]);
  try {
    if (!held) await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
  } catch (_) {
    return false;
  }
  try {
    let expression = cmd.code;
    try {
      if (/\breturn\b/.test(cmd.code)) {
        // REPL mode supplies top-level `await`, but `return` still needs a
        // function around it — and only when the source is a body rather than an
        // expression that merely contains the word. This parser heuristic is not
        // a security boundary: submitted text can escape the probe wrapper.
        // Without a successful probe, assume a body.
        const stripped = cmd.code.replace(/[\s;]+$/, '');
        let isExpr = false;
        let probe;
        try {
          probe = await chrome.debugger.sendCommand(
            { tabId: chromeTabId }, 'Runtime.evaluate',
            { expression: 'typeof (function(){return (async()=>{return ('
                + stripped + ')})()})',
              returnByValue: true });
          isExpr = !probe.exceptionDetails;
        } catch (_) {
        } finally {
          await _releaseCdpObjects(chromeTabId, probe);
        }
        if (!isExpr) {
          expression = cmd.code.includes('await')
            ? '(async()=>{' + cmd.code + '})()'
            : '(function(){' + cmd.code + '})()';
        }
      }
    } catch (_) {
      // A code value that is not a string can fail the shape checks above.
      // Nothing has been dispatched yet, so falling back repeats no work.
      return false;
    }
    // Dispatching may start the submitted source, so every outcome from here on
    // is terminal. Returning false could execute its side effects twice.
    let val;
    let err;
    try {
      const evaluated = await chrome.debugger.sendCommand(
        { tabId: chromeTabId }, 'Runtime.evaluate',
        { expression, replMode: true, awaitPromise: false }
      );
      err = _cdpError(evaluated);
      if (!err) {
        const settled = await _cdpSettle(chromeTabId, evaluated.result);
        val = settled.value;
        err = settled.error;
      } else {
        await _releaseCdpObjects(chromeTabId, evaluated);
      }
    } catch (error) {
      err = 'CDP eval failed: ' + (error.message || String(error));
    }
    try {
      await postResult(
        cmd._execution, val, err, String(chromeTabId), { world: 'cdp' });
    } catch (_) {}
    return true;
  } finally {
    if (!held) {
      try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
    }
  }
}

function _canUseMainWorldEval() {
  try {
    // Constant source only: this probes page CSP before submitted source runs.
    // The page owns `Function` and can influence the answer, which may change
    // the selected channel but conveys no value-integrity property.
    new Function('return undefined');
    return true;
  } catch (_) {
    return false;
  }
}

function _executeMainWorldEval(code) {
  try {
    const started = performance.now();
    const complete = (value) => ({
      ...value,
      ms: +(performance.now() - started).toFixed(1),
    });
    const errorResult = (error) => complete({
      e: error && (error.message || String(error)),
    });
    const stripped = code.replace(/[\s;]+$/, '');
    let isExpr = false;
    try {
      new Function('return (async()=>{return (' + stripped + ')})()');
      isExpr = true;
    } catch (_) {}
    const hasAwait = code.includes('await');
    const hasReturn = /\breturn\b/.test(code);
    if (hasAwait) {
      const body = isExpr
        ? 'return (async()=>{return (' + stripped + ')})()'
        : 'return (async()=>{' + code + '})()';
      return Promise.resolve((new Function(body))()).then(
        (result) => complete({ r: result }),
        (error) => errorResult(error)
      );
    }
    if (hasReturn) {
      const body = isExpr ? 'return (' + stripped + ')' : code;
      return complete({ r: (new Function(body))() });
    }
    return complete({ r: eval(code) });
  } catch (error) {
    return { e: error && (error.message || String(error)) };
  }
}

async function handleEval(cmd) {
  // Find which chrome tab to target
  let chromeTabId = cmd.chromeTab;
  if (!chromeTabId) {
    const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!active) return postResult(cmd._execution, null, 'No active tab', cmd.tabId);
    chromeTabId = active.id;
  }
  chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

  // Prefer banner-free MAIN-world injection. A constant, source-free probe
  // checks whether page CSP permits dynamic compilation; the page can influence
  // that diagnostic choice, but no submitted source has run at this point.
  let useMainWorld = false;
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId: chromeTabId },
      world: 'MAIN',
      func: _canUseMainWorldEval,
    });
    useMainWorld = probe[0]?.result === true;
  } catch (_) {}

  if (useMainWorld) {
    let results;
    const failMainWorld = async (detail) => postResult(
      cmd._execution, null, 'MAIN-world eval failed: ' + detail,
      String(chromeTabId), { world: 'page-main' });
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId: chromeTabId },
        world: 'MAIN',
        func: _executeMainWorldEval,
        args: [cmd.code],
      });
    } catch (error) {
      await failMainWorld(error.message || String(error));
      return;
    }
    if (!Array.isArray(results) || results.length === 0) {
      await failMainWorld('no result frame');
      return;
    }
    const frame = results[0];
    if (frame === null || typeof frame !== 'object') {
      await failMainWorld('invalid result frame');
      return;
    }
    if (Object.prototype.hasOwnProperty.call(frame, 'error')) {
      const frameError = frame.error;
      const detail = typeof frameError === 'string'
        ? frameError
        : frameError && frameError.message || String(frameError);
      await failMainWorld(detail);
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(frame, 'result')) {
      await failMainWorld('result frame has no result');
      return;
    }
    const res = frame.result;
    if (res === null || res === undefined) {
      await failMainWorld('no result envelope');
      return;
    }
    // `typeof null === 'object'`, so this test is only safe because the
    // guard above already returned on null and undefined. Keep them
    // together: separating them makes every non-envelope result look like an
    // envelope with undefined fields.
    const isEnvelope = typeof res === 'object';
    const val = isEnvelope ? res.r : res;
    const err = isEnvelope ? res.e || null : null;
    const extra = { world: 'page-main' };
    if (isEnvelope && typeof res.ms === 'number') extra.exec_ms = res.ms;
    await postResult(
      cmd._execution, val, err, String(chromeTabId), extra);
    return;
  }

  // The source-free probe could not establish a usable injection path, most
  // commonly because of page CSP. CDP is the fallback and shows Chrome's
  // debugger banner when it attaches. Once CDP dispatches, its result is
  // terminal; only a pre-dispatch attach/shape failure reaches the page relay.
  if (await _evalViaCdp(cmd, chromeTabId)) return;

  const relayId = _registerEvalRelay(cmd._execution, String(chromeTabId));
  if (!relayId) {
    await postResult(
      cmd._execution, null, 'Eval relay capacity exceeded',
      String(chromeTabId));
    return;
  }
  try {
    await chrome.tabs.sendMessage(chromeTabId, {
      type: 'eval',
      id: cmd.id,
      relayId,
      code: cmd.code,
      tabId: String(chromeTabId),
    });
  } catch (error) {
    _removeEvalRelay(relayId);
    await postResult(
      cmd._execution, null, error.message, String(chromeTabId));
  }
}

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
