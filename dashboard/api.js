// Daedalus dashboard — API wrapper. Reads token + server URL from localStorage.
// All calls default to same-origin (empty server URL) since the dashboard is
// served by the same server.py that exposes the endpoints.

const KEY_TOKEN = 'daedalus-token';
const KEY_SERVER = 'daedalus-server';

export function getToken() {
  return localStorage.getItem(KEY_TOKEN) || '';
}
export function setToken(t) {
  localStorage.setItem(KEY_TOKEN, (t || '').trim());
}
export function getServer() {
  // Empty string = same origin. Users can override via §12 Settings to hit a different host.
  return localStorage.getItem(KEY_SERVER) || '';
}
export function setServer(s) {
  localStorage.setItem(KEY_SERVER, (s || '').trim());
}

export function url(path) {
  return (getServer() || '') + path;
}

export function q(params) {
  const p = new URLSearchParams();
  for (const k of Object.keys(params || {})) {
    const v = params[k];
    if (v === undefined || v === null || v === '') continue;
    p.set(k, v);
  }
  const s = p.toString();
  return s ? '?' + s : '';
}

async function req(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const r = await fetch(url(path), init);
  const ct = r.headers.get('content-type') || '';
  const isJson = ct.includes('application/json');
  const data = isJson ? await r.json().catch(() => ({})) : await r.text();
  if (!r.ok) {
    const msg = typeof data === 'object' ? (data.error || JSON.stringify(data)) : data;
    throw new Error(`HTTP ${r.status}: ${msg}`);
  }
  return data;
}

export const api = {
  get: (path) => req('GET', path),
  post: (path, body) => req('POST', path, body),
  put: (path, body) => req('PUT', path, body),
  del: (path, body) => req('DELETE', path, body),
};

// ─── Command execution (PUT /command + poll /result) ───
//
// Mirrors mcp_server.py's _ext_cmd / _send_eval flow: write a command, then
// poll for the result carrying that command delivery id. Field names must
// match exactly (id, code, type, tab, token).

let _idCounter = 0;

export function nextId(prefix = 'dash') {
  _idCounter++;
  return `_${prefix}_${_idCounter}_${Date.now().toString(36)}`;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// Per-tab mutex. Commands no longer collide -- they go to a per-target FIFO
// directory -- but RESULTS still share one `{token}_{tab}` file, so two
// concurrent calls against the same tab can cross-deliver: whichever poller
// looks first sees the other's result. Serialize per tab; distinct tabs
// proceed in parallel.
const _tabLocks = new Map();

function withTabLock(tab, fn) {
  const key = tab || '__broadcast__';
  const prev = _tabLocks.get(key) || Promise.resolve();
  const next = prev.catch(() => {}).then(fn);
  _tabLocks.set(key, next);
  const release = () => {
    if (_tabLocks.get(key) === next) _tabLocks.delete(key);
  };
  next.then(release, release);
  return next;
}

/**
 * Run a command and wait for result.
 * @param {object} opts
 * @param {string} [opts.tab='extension'] — tab target ('extension' for typed cmds, chromeTabId for evals, '' for broadcast)
 * @param {string} [opts.type] — typed extension command (screenshot, cookies, net-capture, etc.)
 * @param {string} [opts.code] — JS code (for eval commands)
 * @param {string} [opts.id] — override command ID
 * @param {number} [opts.timeout=15000] — ms to wait for result
 * @param {object} [opts.fields] — extra command fields (tabId, url, name, pattern, etc.)
 */
export async function runCommand({ tab = 'extension', type, code, id, timeout = 15000, fields = {} } = {}) {
  const token = getToken();
  if (!token) throw new Error('No token set (§12 Settings)');
  const cmdId = id || nextId(type || 'eval');
  const tabParam = tab || '';

  return withTabLock(tabParam, async () => {
    const payload = { token, tab: tabParam, id: cmdId };
    if (type) payload.type = type;
    if (code) payload.code = code;
    Object.assign(payload, fields);
    const sent = await api.put('/command', payload);
    const deliveryId = sent && sent.did;
    if (!deliveryId) throw new Error('Command response has no delivery id');

    const t0 = Date.now();
    const pollMs = 250;
    while (Date.now() - t0 < timeout) {
      await sleep(pollMs);
      // Match both the command and its fresh delivery. Conditional consume
      // then deletes only the generation just peeked; if another caller
      // replaces the shared slot first, their result remains for them.
      const res = await api.get('/result' + q({ token, tab: tabParam }));
      if (!res || res.pending) continue;
      if (res.id !== cmdId || res.deliveryId !== deliveryId) continue;
      const generation = res.resultGeneration;
      if (!generation) continue;
      const consumed = await api.get('/result' + q({
        token, tab: tabParam, consume: 1, expected: generation,
      }));
      if (!consumed || consumed.consumed !== true
          || consumed.resultGeneration !== generation) continue;
      if (res.error) {
        const err = new Error(res.error);
        err.resultEnvelope = res;
        throw err;
      }
      return { result: res.result, envelope: res };
    }
    throw new Error(`Timeout (${timeout}ms) waiting for ${cmdId}`);
  });
}

// Shorthand: send a typed extension command and return the result.
export async function extCmd(type, fields = {}, opts = {}) {
  const { result } = await runCommand({ tab: 'extension', type, fields, ...opts });
  return result;
}
