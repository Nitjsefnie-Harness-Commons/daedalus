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
// Consecutive non-OK /stream answers back off exponentially; a connected
// stream resets the count. An auth refusal (401/400) is not transient: it
// records the credential pair that was refused, and connecting stays idle
// until the token or bridge URL changes. Both records are globals, so a
// restarted worker asks once more before re-stopping.
const _STREAM_RETRY_BASE_MS = 1000;
const _STREAM_RETRY_MAX_MS = 60000;
let _streamFailures = 0;
let _authRefusedFor = null;

function _streamCredential() {
  return JSON.stringify([config.serverUrl, config.token]);
}

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
// No stream opens before the dedup-ledger read has completed, so a
// keepalive connect or a heartbeat alarm cannot outrun boot's loadConfig;
// boot calls startStream once the read is done, and the call may still
// stay idle for its own reasons. A failed read still opens the gate.
let _ledgerReady = false;

async function _loadSeenDids() {
  try {
    const stored = await chrome.storage.local.get([_SEEN_DID_KEY]);
    const saved = stored[_SEEN_DID_KEY];
    if (Array.isArray(saved)) {
      for (const did of saved) {
        if (typeof did === 'string' && !_seenDids.has(did)) {
          _seenDids.add(did);
          _seenDidOrder.push(did);
        }
      }
    }
  } catch (e) {
    // A worker that cannot read the ledger still dedups what it sees itself.
    console.warn('[Daedalus] Could not read the delivery ledger:', e.message);
  }
  _ledgerReady = true;
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
  if (!_ledgerReady) return;
  if (!config.token) return;
  // Without a bridge URL the stream URL is relative, so the fetch resolves
  // against the extension's own chrome-extension:// origin and the watchdog
  // retries that forever. Stay idle instead.
  if (!config.serverUrl) return;
  // A retry cannot fix a refused credential, and re-asking hits the bridge
  // with a 401 every few seconds. Stay idle; the storage listener re-enters
  // here once the token or server URL changes, and that fresh pair resumes.
  if (_streamCredential() === _authRefusedFor) return;
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
      const delay = Math.min(
        _STREAM_RETRY_BASE_MS * 2 ** _streamFailures,
        _STREAM_RETRY_MAX_MS);
      _streamFailures++;
      setTimeout(() => { if (myGen === streamGen) startStream(); }, delay);
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
        if (resp.status === 401 || resp.status === 400) {
          _authRefusedFor = _streamCredential();
          // clear this attempt's watchdog; the bump also silences any
          // reschedule from this generation
          stopStream();
          console.error('[Daedalus] Stream auth refused; not retrying '
            + 'until the token or bridge URL changes');
          return;
        }
        sseAbort = null;
        const delay = Math.min(
          _STREAM_RETRY_BASE_MS * 2 ** _streamFailures,
          _STREAM_RETRY_MAX_MS);
        _streamFailures++;
        setTimeout(() => { if (myGen === streamGen) startStream(); }, delay);
      }
      return;
    }
    if (myGen === streamGen) {
      _streamFailures = 0;
      _authRefusedFor = null;
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
      // a dropped connection is a failed attempt, like a refused answer
      _streamFailures++;
    }
  }
  // Only the current generation reschedules. If a stop/restart bumped
  // streamGen while we were running, stay silent — the newer stream owns
  // reconnection, so we never stack overlapping SSE loops. A clean EOF
  // lands here with the counter still at its connect-time zero, so its
  // retry stays at the 1 s base.
  if (myGen === streamGen) {
    sseAbort = null;
    const delay = Math.min(
      _STREAM_RETRY_BASE_MS * 2 ** _streamFailures,
      _STREAM_RETRY_MAX_MS);
    setTimeout(() => { if (myGen === streamGen) startStream(); }, delay);
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
