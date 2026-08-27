/* exported registerTab, registerAllTabs */
/* global config, configured, bridgeHeaders */

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
      console.error(
        `[Daedalus] ${path} refused: HTTP ${resp.status} ${detail}`);
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

// ─── Register all tabs ───

// Coalesce burst syncs. Opening N tabs fires onCreated N times, and each sync
// is a chrome.tabs.query plus a POST carrying the ENTIRE tab list — for a
// 10-url open_tabs that was 10 full-registry uploads racing the result POST on
// the same service-worker event loop. The 30s heartbeat re-syncs anyway, so
// losing a coalesced trailing sync to a worker shutdown is self-correcting.
let _syncTimer = null;
function scheduleRegisterAllTabs() {
  if (_syncTimer) return;
  _syncTimer = setTimeout(() => {
    _syncTimer = null;
    registerAllTabs();
  }, 250);
}

function registerAllTabs() {
  // Boot calls this once and the heartbeat alarm calls it every 30 seconds.
  if (!configured()) return;
  chrome.tabs.query({}, (tabs) => {
    const tabList = tabs
      .filter(t => t.id)
      .map(t => ({
        tabId: String(t.id), url: t.url || '', title: t.title || '',
      }));
    // Sync replaces entire server registry — removes ghost tabs from prior
    // sessions
    registryPost('/sync-tabs', { token: config.token, tabs: tabList });
  });
}
