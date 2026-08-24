// Daedalus dashboard — SSE connection wrapper.
// Subscribes to /stream?tab=dashboard and dispatches kind:'event' payloads
// to all registered listeners.
//
// The stream is read with fetch, not EventSource. EventSource cannot set a
// request header, so its only way to authenticate is to write the reusable
// bridge token into the request target — where a reverse-proxy access log
// keeps it for the whole life of the stream. Reconnect is consequently ours
// to do: a generation counter makes sure only the newest attempt reschedules,
// so a restart during a retry never leaves two readers running.

import { getServer, getToken } from './api.js';

const RETRY_MS = 3000;

const listeners = new Set();
let abort = null;
let retryTimer = null;
let generation = 0;
let currentToken = '';
let lastEventTs = 0;

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function dispatch(event) {
  for (const fn of listeners) {
    try { fn(event); } catch (e) { console.error('[sse] listener error', e, event); }
  }
}

function statusEvent(status) {
  return { __internal: true, type: 'sse-status', status };
}

export function lastEventAt() {
  return lastEventTs;
}

function emit(raw) {
  lastEventTs = Date.now();
  let payload;
  try { payload = JSON.parse(raw); }
  catch (err) { console.error('[sse] parse error', err, raw); return; }
  // Only dispatch our own event frames. Broadcast eval commands
  // that happen to reach this stream lack kind='event' and are ignored.
  if (payload && payload.kind === 'event') {
    dispatch(payload);
  }
}

// One frame parser per connection. A frame can straddle two reads, so the
// partial line and the frame being assembled are both state that has to
// survive between chunks rather than being rebuilt from each one.
function frameParser() {
  let buffer = '';
  let eventType = '';
  let data = '';
  return (chunk) => {
    buffer += chunk;
    for (;;) {
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      const line = buffer.slice(0, end).replace(/\r$/, '');
      buffer = buffer.slice(end + 1);
      if (line === '') {
        if (eventType === 'command' && data) emit(data);
        eventType = '';
        data = '';
      } else if (line.startsWith(':')) {
        // keepalive comment — the bridge is alive, there is nothing to read
      } else if (line.startsWith('event:')) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        data += line.slice(5).trim();
      }
    }
  };
}

function teardown() {
  generation++;
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  if (abort) { abort.abort(); abort = null; }
}

async function run(myGen) {
  const token = getToken();
  if (!token) { dispatch(statusEvent('no-token')); return; }
  const server = getServer() || '';
  dispatch(statusEvent('connecting'));
  const controller = new AbortController();
  abort = controller;
  try {
    const resp = await fetch(server + '/stream?tab=dashboard', {
      signal: controller.signal,
      headers: { 'Authorization': 'Bearer ' + token },
    });
    if (myGen !== generation) return;
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
    lastEventTs = Date.now();
    dispatch(statusEvent('connected'));
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    const feed = frameParser();
    for (;;) {
      const { done, value } = await reader.read();
      if (myGen !== generation) return;
      if (done) break;
      feed(decoder.decode(value, { stream: true }));
    }
  } catch (e) {
    if (e.name === 'AbortError' || myGen !== generation) return;
    console.error('[sse] stream error', e);
  }
  if (myGen !== generation) return;
  abort = null;
  dispatch(statusEvent('reconnecting'));
  retryTimer = setTimeout(() => {
    if (myGen === generation) run(myGen);
  }, RETRY_MS);
}

export function start() {
  currentToken = getToken();
  teardown();
  run(generation);
}

export function stop() {
  teardown();
  dispatch(statusEvent('idle'));
}

export function restart() { stop(); start(); }

// Re-subscribe if token changed (e.g. user updated settings in another tab)
window.addEventListener('storage', (e) => {
  if (e.key === 'daedalus-token' && e.newValue !== currentToken) {
    restart();
  }
});
