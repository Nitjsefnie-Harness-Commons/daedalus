// Daedalus dashboard — SSE connection wrapper.
// Subscribes to /stream?tab=dashboard and dispatches kind:'event' payloads
// to all registered listeners. EventSource handles reconnect natively.

import { getServer, getToken } from './api.js';

const listeners = new Set();
let es = null;
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

export function start() {
  const token = getToken();
  currentToken = token;
  if (!token) {
    dispatch(statusEvent('no-token'));
    return;
  }
  if (es) { es.close(); es = null; }

  const server = getServer() || '';
  const streamUrl = server + '/stream?token=' + encodeURIComponent(token) + '&tab=dashboard';
  dispatch(statusEvent('connecting'));

  try {
    es = new EventSource(streamUrl);
  } catch (e) {
    console.error('[sse] EventSource construct failed', e);
    dispatch(statusEvent('error'));
    return;
  }

  es.addEventListener('open', () => {
    lastEventTs = Date.now();
    dispatch(statusEvent('connected'));
  });
  es.addEventListener('error', () => {
    // EventSource will auto-reconnect; surface the transition visually.
    dispatch(statusEvent('reconnecting'));
  });
  es.addEventListener('command', (e) => {
    lastEventTs = Date.now();
    let payload;
    try { payload = JSON.parse(e.data); }
    catch (err) { console.error('[sse] parse error', err, e.data); return; }
    // Only dispatch our own event frames. Broadcast eval commands
    // that happen to reach this stream lack kind='event' and are ignored.
    if (payload && payload.kind === 'event') {
      dispatch(payload);
    }
  });
}

export function stop() {
  if (es) { es.close(); es = null; }
  dispatch(statusEvent('idle'));
}

export function restart() { stop(); start(); }

// Re-subscribe if token changed (e.g. user updated settings in another tab)
window.addEventListener('storage', (e) => {
  if (e.key === 'daedalus-token' && e.newValue !== currentToken) {
    restart();
  }
});
