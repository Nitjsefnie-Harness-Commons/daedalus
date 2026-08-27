/* exported config, loadConfig, configured, _executionContext, postResult */
/* global DEFAULT_SERVER, _loadSeenDids, stopStream, startStream */
/* global bridgeHeaders */

let config = { token: '', serverUrl: DEFAULT_SERVER };

// ─── Config ───

async function loadConfig() {
  const stored = await chrome.storage.local.get([
    'daedalus-token', 'daedalus-server',
  ]);
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
    console.log('[Daedalus] Generated a token; read it in the extension '
      + 'options page.');
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
  // result POST would make the caller time out on work that actually
  // succeeded.
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
