/* exported sseAbort, _loadSeenDids, startStream, stopStream */
/* exported ensureKeepAlive */
/* global config, bridgeAuth, dispatchCommand, registerAllTabs */

let sseAbort = null;
let sseBuf = '';
let sseEventType = '';
let sseData = '';
let lastDataTime = 0;
let watchdogTimer = null;
let keepaliveTimer = null;
// bumped on every (re)start/stop; only the current gen reconnects
let streamGen = 0;

// ─── SSE stream ───

// Dedup redelivered command frames by their server-assigned delivery id
// (_did). At-least-once delivery can (rarely) redeliver a command whose
// socket write succeeded but whose unlink failed; skipping the repeat
// prevents double-exec of non-idempotent typed commands (open-tab, etc.).
// Legacy frames without _did have no stable delivery identity and are not
// deduplicated here.
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
  if (_seenDidOrder.length > _SEEN_DID_MAX) {
    _seenDids.delete(_seenDidOrder.shift());
  }
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
  // tear down any existing stream (also bumps streamGen)
  stopStream();
  // this invocation owns reconnection for its generation
  const myGen = ++streamGen;

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
  // Only the current generation reschedules. If a stop/restart bumped
  // streamGen while we were running, stay silent — the newer stream owns
  // reconnection, so we never stack overlapping SSE loops.
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

// ─── Keep-alive: self-ping to prevent MV3 service-worker
// dormancy ───
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
