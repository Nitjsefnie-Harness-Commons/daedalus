/* exported mintSegmentSig, handleAllowSegmentOrigin */
/* exported handleRevokeSegmentOrigin, handleListSegmentOrigins */
/* global config, configured, bridgeHeaders, postResult, _serializer */
/* global canonicalOrigin */

// ─── Segment-job mint on a page's behalf ───

const SEGMENT_ORIGINS_KEY = 'daedalus-segment-origins';
const SEGMENT_ORIGIN_ERROR =
  'Missing or invalid origin (http(s) origin required)';

// SEGMENT_ORIGIN_CAP bounds the allowlist in ENTRIES, not in bytes: a
// canonical origin is on the order of 22 bytes, so 256 of them is ~6 KB
// against the 3 MiB the GM budget reserves for the extension's own keys
// (see gm_storage.js). Count-bounded, it is one more term in that budget
// rather than a new subtraction from it, and no real operator reaches it.
const SEGMENT_ORIGIN_CAP = 256;

async function _storedOrigins() {
  const data = await chrome.storage.local.get([SEGMENT_ORIGINS_KEY]);
  const stored = data[SEGMENT_ORIGINS_KEY];
  return Array.isArray(stored) ? stored : [];
}

// The answer is exactly `{sig}` or `{error}`: it is relayed into the page,
// so nothing else — the bridge token above all — may ride along.
async function mintSegmentSig(sender, job) {
  const origin = canonicalOrigin(sender && sender.origin);
  if (origin === null) return { error: 'origin not allowed' };
  const origins = await _storedOrigins();
  if (!origins.includes(origin)) return { error: 'origin not allowed' };
  if (typeof job !== 'string' || !job) return { error: 'Missing job' };
  if (!configured()) return { error: 'bridge not configured' };
  let resp;
  try {
    resp = await fetch(config.serverUrl + '/segment-job', {
      method: 'POST',
      headers: bridgeHeaders(config.token),
      body: JSON.stringify({ token: config.token, job }),
    });
  } catch (e) {
    return { error: 'segment-job unreachable: ' + e.message };
  }
  if (!resp.ok) return { error: 'segment-job refused (' + resp.status + ')' };
  let body;
  try {
    body = await resp.json();
  } catch (_) {
    body = null;
  }
  if (!body || typeof body.sig !== 'string' || !body.sig) {
    return { error: 'segment-job answered no sig' };
  }
  return { sig: body.sig };
}

// ─── Allowlist commands ───

const _withOriginLock = _serializer();

async function handleAllowSegmentOrigin(cmd) {
  try {
    const origin = canonicalOrigin(cmd.origin);
    if (origin === null) return postResult(
      cmd._execution, null, SEGMENT_ORIGIN_ERROR, 'extension');
    const outcome = await _withOriginLock(async () => {
      const origins = await _storedOrigins();
      const added = !origins.includes(origin);
      if (added) {
        // Refuse at the cap, never evict: an eviction would make an origin
        // the operator permitted stop being permitted, and the failure would
        // surface at `GM.segmentJob` pointing nowhere near its cause. The
        // refusal is reversible through `revoke-segment-origin`, the command
        // the operator already has for making room.
        if (origins.length >= SEGMENT_ORIGIN_CAP) {
          throw new Error(
            'segment origin allowlist is at its ' + SEGMENT_ORIGIN_CAP
            + '-origin limit; revoke one to make room');
        }
        origins.push(origin);
        origins.sort();
        await chrome.storage.local.set({ [SEGMENT_ORIGINS_KEY]: origins });
      }
      return { origin, origins, added };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleRevokeSegmentOrigin(cmd) {
  try {
    const origin = canonicalOrigin(cmd.origin);
    if (origin === null) return postResult(
      cmd._execution, null, SEGMENT_ORIGIN_ERROR, 'extension');
    const outcome = await _withOriginLock(async () => {
      const stored = await _storedOrigins();
      const origins = stored.filter(o => o !== origin);
      const found = origins.length !== stored.length;
      if (found) {
        await chrome.storage.local.set({ [SEGMENT_ORIGINS_KEY]: origins });
      }
      return { origin, origins, found };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

// Read under the same lock, because the stream dispatches without
// awaiting: a list sent right behind an allow answers the settled store.
async function handleListSegmentOrigins(cmd) {
  try {
    const origins = await _withOriginLock(_storedOrigins);
    await postResult(cmd._execution, { origins }, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}
