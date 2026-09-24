/* exported config, loadConfig, configured, _executionContext, postResult */
/* global DEFAULT_SERVER, _loadSeenDids, stopStream, startStream */
/* global bridgeHeaders */

let config = { token: '', serverUrl: DEFAULT_SERVER };

// ─── Config ───

// Boot's loadConfig and a restart's heartbeat alarm can both reach loadConfig
// while the first is still parked on its storage read (config.token is ''), so
// the generation is memoized: a second caller joins the first one's result
// instead of starting a second generation that races it to auto-generate a
// token. The memo lives for the worker's lifetime: chrome.storage.onChanged
// below updates config in place, so a token CHANGED to a new value needs no
// re-read, and a worker restart re-imports this module with fresh state. A
// token CLEARED to '' is the case that makes never clearing on success
// deliberate rather than incidental — the options page writes whatever its
// trimmed field holds and renders the empty one as "Not configured", so a
// cleared token is an operator saying "not configured" and must stay cleared.
// A fresh generation would re-read the empty token and auto-generate a new
// browser-control credential behind that operator.
let _configPromise = null;

function loadConfig() {
  if (!_configPromise) {
    _configPromise = _loadConfigOnce();
    // A rejected generation must not wedge the worker for its lifetime: drop
    // the memo so a later caller retries, while the callers already waiting
    // on THIS promise all observe the same rejection.
    _configPromise.catch(() => { _configPromise = null; });
  }
  return _configPromise;
}

async function _loadConfigOnce() {
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
  let lastStatus = 0;
  for (let attempt = 0; attempt < 3; attempt++) {
    let resp = null;
    try {
      resp = await fetch(execution.resultRoute.serverUrl + '/result', {
        method: 'POST',
        headers: bridgeHeaders(execution.resultRoute.token),
        body,
      });
    } catch (e) {
      // A last network error names itself below; an earlier one stays silent
      // and only spends its retry.
      lastStatus = 0;
      if (attempt === 2) console.error('[Daedalus] Result POST failed:', e);
    }
    if (resp) {
      if (resp.ok) return;
      lastStatus = resp.status;
      // A 413 is the one client error the worker can fix: the refused body is
      // its own doing, so a small terminal error result goes out in its place.
      // Any other 4xx is a credential, routing or shape problem no retry can
      // fix, so it is named once and given up.
      if (resp.status === 413) {
        await _postResultTooLarge(execution, payload, body);
        return;
      }
      if (resp.status < 500) {
        console.error('[Daedalus] Result POST for command ' + execution.id
          + (execution.deliveryId
            ? ' (delivery ' + execution.deliveryId + ')'
            : '')
          + ' refused: HTTP ' + resp.status);
        return;
      }
    }
    await new Promise(r => setTimeout(r, 300 * (attempt + 1)));
  }
  // Exhausting the 5xx retries is the same give-up the 4xx returns were, and
  // it names itself the same way. A last network error already logged itself
  // in the catch above, which is why lastStatus was reset there.
  if (lastStatus) {
    console.error('[Daedalus] Result POST for command ' + execution.id
      + ' gave up after 3 attempts: HTTP ' + lastStatus);
  }
}

// The one 4xx the worker can fix: the oversized body is the worker's own
// doing, so the caller gets a small terminal error result naming the refusal
// instead of timing out on a result that was refused unread. One POST, never
// retried and never recursive: whatever becomes of the substitute, it is
// logged and done.
async function _postResultTooLarge(execution, refused, body) {
  const substitute = {
    ...refused,
    result: null,
    ts: Date.now(),
    error: {
      message: 'result too large',
      size: new TextEncoder().encode(body).length,
    },
  };
  try {
    const resp = await fetch(execution.resultRoute.serverUrl + '/result', {
      method: 'POST',
      headers: bridgeHeaders(execution.resultRoute.token),
      body: JSON.stringify(substitute),
    });
    if (!resp.ok) {
      console.error('[Daedalus] Substitute result POST for command '
        + execution.id + ' refused: HTTP ' + resp.status);
    }
  } catch (e) {
    console.error('[Daedalus] Substitute result POST failed:', e);
  }
}

// A bridge is usable only with BOTH a token and a URL. The token is generated
// on install so it is always set; the URL is not, and every listener below
// fires on ordinary browsing. Checking only the token means an unconfigured
// install issues a relative request per tab event, forever.
function configured() {
  return Boolean(config.token && config.serverUrl);
}
