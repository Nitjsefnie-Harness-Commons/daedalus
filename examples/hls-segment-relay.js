// HLS segment relay — a worked example of the /segment + /segment-status API.
//
// Run it in a page with `daedalus put` (or the MCP `put` tool). It reads a
// media playlist, resolves every segment URI against the playlist URL, fetches
// the playlist and segments through the background GM relay, and POSTs the raw
// bytes to the bridge, which assembles them under the job name. The point of
// the example is the RELAY protocol, not any particular site.
//
// Placeholders are substituted before the script is sent (the bridge cannot
// pass arguments to `put`):
//   __SERVER__   bridge base URL, e.g. http://127.0.0.1:8081
//   __JOB__      server-safe path component, for example relay_job-1
//   __SIG__      job capability — `daedalus segment-job <job>` prints it
//   __PLAYLIST__ absolute URL of the media playlist (.m3u8) to download
//   __CONC__     concurrent workers (default 3)
//
// Three endpoints do all the work; the first runs on the trusted side (CLI or
// MCP), and this script only ever calls the other two:
//   POST /segment-job                        {token, job} -> {ok, sig}
//   GET  /segment-status?job=J&sig=S         -> {done:[1,2,...], count:N}
//   POST /segment?job=J&seg=N&total=T&sig=S  raw arraybuffer body
//
// __SIG__ is a capability scoped to this one job, minted by POST /segment-job
// — deliberately NOT the bridge token. This script runs in the page's MAIN
// world, so anything it carries the page can read, and the bridge token grants
// full browser control. A stolen sig authorizes only this job's status reads
// and segment writes; the finalized segment set stays within the job record's
// quotas, and stale temporary writes are cleared before another is admitted.
// The sig cannot access browser-control or other jobs' routes.
//
// `/segment-status` is what makes this resumable when its status GET is
// available: the script asks which segments the bridge already holds and skips
// them, so re-running after a crashed tab or reload costs only what is missing.
// Segment writes are independently idempotent — posting the same index again
// replaces the same file.
//
// Stop a run from outside with `window.__relayStop['<job>'] = true` via
// `daedalus exec`; workers check the flag in both loops and exit cleanly,
// leaving everything already uploaded in place.

const SERVER = '__SERVER__';
const JOB = '__JOB__';
const SIG = '__SIG__';
const PLAYLIST = '__PLAYLIST__';
const CONCURRENCY = '__CONC__'.startsWith('__') ? 3 : parseInt('__CONC__', 10);

// Playlist and segment reads go through the background GM bridge, which is not
// subject to page CORS. The status GET and segment POST below are plain
// page-side fetches to the bridge, so a cross-origin deployment must allow both
// routes and handle the POST's preflight. The bridge adds no CORS headers; see
// "Deployment" in README.md. Without that policy, route both bridge requests
// through the GM bridge instead.
function gmGet(url, responseType) {
  return new Promise((resolve, reject) => {
    window.GM.xmlhttpRequest({
      method: 'GET', url, responseType, timeout: 30000,
      onload: r => (r.status === 200
        ? resolve(responseType === 'arraybuffer' ? r.response : r.responseText)
        : reject(new Error('HTTP ' + r.status))),
      onerror: e => reject(new Error((e && (e.error || e.message)) || 'network error')),
      ontimeout: () => reject(new Error('timeout')),
    });
  });
}

// Segment URIs come out of the playlist itself rather than being guessed from
// a filename pattern: every encoder names them differently, and a URI line may
// be absolute, root-relative, or relative to the playlist's own directory.
function parsePlaylist(body, playlistUrl) {
  const base = playlistUrl.replace(/[^/]*(\?.*)?$/, '');
  return body.split('\n')
    .map(line => line.trim())
    .filter(line => line && !line.startsWith('#'))
    .map(uri => new URL(uri, base).href);
}

window.__relayStop = window.__relayStop || {};
window.__relayStop[JOB] = false;

function badge(text, color) {
  let el = document.getElementById('relay-badge');
  if (!el) {
    el = document.createElement('div');
    el.id = 'relay-badge';
    el.style.cssText = 'position:fixed;top:8px;right:8px;z-index:999999;'
      + 'padding:6px 12px;border-radius:6px;font:bold 14px monospace;'
      + 'color:#fff;pointer-events:none;';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.style.background = color || 'rgba(0,0,0,0.75)';
}

async function run() {
  badge('relay: reading playlist…', '#555');
  const segments = parsePlaylist(await gmGet(PLAYLIST, 'text'), PLAYLIST);
  const TOTAL = segments.length;
  if (!TOTAL) { badge('relay: no segments in playlist', '#dc2626'); return; }

  // A blocked, unavailable, non-2xx, or invalid status response leaves `done`
  // empty. The run then POSTs every segment again: existing per-index files are
  // replaced safely, but the status-based resume/skip benefit is lost.
  let done = new Set();
  try {
    const r = await fetch(`${SERVER}/segment-status?job=${encodeURIComponent(JOB)}&sig=${encodeURIComponent(SIG)}`);
    if (r.ok) done = new Set((await r.json()).done || []);
  } catch (_) { /* done stays empty; a fresh run is still idempotent */ }

  const queue = [];
  for (let i = 1; i <= TOTAL; i++) if (!done.has(i)) queue.push(i);
  const already = done.size;
  badge(`relay: ${already}/${TOTAL}`, '#2563eb');

  let idx = 0, completed = 0, errors = 0;

  async function worker() {
    while (idx < queue.length) {
      if (window.__relayStop[JOB]) return;
      const seg = queue[idx++];
      // Bounded retry with exponential backoff. A 429 is the origin asking
      // for less load, so it is honoured the same as any other failure and
      // the attempt budget still applies -- retrying it indefinitely would
      // just be ignoring the answer.
      for (let attempt = 1; ; attempt++) {
        if (window.__relayStop[JOB]) return;
        try {
          const bytes = await gmGet(segments[seg - 1], 'arraybuffer');
          const relay = `${SERVER}/segment?job=${encodeURIComponent(JOB)}`
            + `&seg=${seg}&total=${TOTAL}&sig=${encodeURIComponent(SIG)}`;
          const resp = await fetch(relay, {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: bytes,
          });
          if (!resp.ok) throw new Error('relay HTTP ' + resp.status);
          completed++;
          badge(`relay: ${already + completed}/${TOTAL}`
            + (errors ? ` (${errors}err)` : ''), '#2563eb');
          break;
        } catch (e) {
          if (attempt >= 5) {
            errors++;
            console.warn(`[relay] segment ${seg} gave up after ${attempt}:`, e);
            break;
          }
          await new Promise(r => setTimeout(r, Math.min(1000 * 2 ** attempt, 16000)));
        }
      }
    }
  }

  await Promise.all(Array.from({ length: CONCURRENCY }, worker));

  const stopped = window.__relayStop[JOB];
  const state = stopped ? 'STOPPED' : (errors ? 'DONE (with errors)' : 'DONE');
  badge(`${state}: ${already + completed}/${TOTAL}`,
    stopped ? '#f59e0b' : (errors ? '#dc2626' : '#16a34a'));
}

run();
return `relay started: job=${JOB} concurrency=${CONCURRENCY}`;
