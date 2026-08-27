/* global _takeEvalRelay, postResult, registerTab, handleHotfixReplay */
/* global readBoundedBody, gmResponseLimit, bytesToBase64, _recordTiming */

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
    const timeoutMs = typeof msg.timeout === 'number' && msg.timeout > 0
      ? msg.timeout : 60000;
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
            for (let i = 0; i < binary.length; i++) {
              bytes[i] = binary.charCodeAt(i);
            }
            opts.body = bytes.buffer;
          } else {
            opts.body = msg.body;
          }
        }
        tBodyDecoded = performance.now();
        const resp = await fetch(msg.url, opts);
        let data;
        const bytes = await readBoundedBody(
          resp, gmResponseLimit(msg.maxResponseBytes));
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
    chrome.tabs.create(
      { url: msg.url, active: msg.active !== false }, (tab) => {
        sendResponse({ tabId: tab.id });
      });
    return true;
  } else if (msg.type === 'notification') {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'data:image/png;base64,'
        + 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9Q'
        + 'DwADhgGAWjR9awAAAABJRU5ErkJggg==',
      title: msg.title || 'Daedalus',
      message: msg.text || '',
    });
  } else if (msg.type === 'download') {
    chrome.downloads.download(
      { url: msg.url, filename: msg.filename }, (downloadId) => {
        if (chrome.runtime.lastError) {
          sendResponse({ error: chrome.runtime.lastError.message });
        } else {
          sendResponse({ downloadId });
        }
      });
    return true;
  }
});
