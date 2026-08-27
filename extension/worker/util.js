/* exported _hasNativeToBase64, bytesToBase64, gmResponseLimit */
/* exported readBoundedBody, bridgeAuth, bridgeHeaders, _fetchTimings */
/* exported _recordTiming, _serializer */

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

// ─── Extension command handlers ───

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
