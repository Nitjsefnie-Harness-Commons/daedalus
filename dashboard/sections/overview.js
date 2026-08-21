// §00 OVERVIEW — live counters + SSE event log.

import { h, fmtTime, truncate, formatEvalWorld } from './_util.js';
import { api, getToken } from '../api.js';

const MAX_EVENTS = 200;

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'stat-grid' },
      stat('Tabs', 'tabs', 'accent'),
      stat('Events / 60s', 'rate', 'cyan'),
      stat('Last event', 'last', ''),
      stat('Session', 'session', ''),
    ),
    h('div', { class: 'toolbar' },
      h('span', { class: 'small dim' }, 'LIVE EVENT STREAM'),
      h('span', { style: { flex: '1' } }),
      h('button', { class: 'ghost sm', onclick: () => clearLog() }, 'clear'),
    ),
    h('div', { class: 'eventlog', data: { role: 'log' } },
      h('div', { class: 'dim italic small' }, 'waiting for events…'),
    ),
  );
  container.appendChild(root);

  const statEls = {
    tabs: root.querySelector('[data-stat=tabs]'),
    rate: root.querySelector('[data-stat=rate]'),
    last: root.querySelector('[data-stat=last]'),
    session: root.querySelector('[data-stat=session]'),
  };
  const logEl = root.querySelector('[data-role=log]');
  const sessionStart = Date.now();
  let events = [];
  let rateWindow = [];

  function clearLog() {
    events = [];
    logEl.textContent = '';
    logEl.appendChild(h('div', { class: 'dim italic small' }, 'cleared.'));
  }

  function fmtBody(ev) {
    switch (ev.type) {
      case 'result': {
        const channel = formatEvalWorld(ev.world) || 'channel=·';
        return `tab=${ev.tabId || '·'}  id=${ev.resultId || '·'}  ${channel}  ${ev.ok ? 'ok' : 'err'}`;
      }
      case 'tab-updated':
        return `${ev.tabId} · ${truncate(ev.title || ev.url || '', 80)}`;
      case 'tabs-synced':
        return `${ev.count} tab(s) registered`;
      case 'tab-unregistered':
        return `${ev.tabId} removed`;
      default:
        return JSON.stringify(ev);
    }
  }

  function push(ev) {
    if (events.length === 0) logEl.textContent = '';
    const row = h('div', { class: 'ev new-row' },
      h('span', { class: 'ev-ts' }, fmtTime(Date.now())),
      h('span', { class: 'ev-type ' + ev.type }, ev.type),
      h('span', { class: 'ev-body' }, fmtBody(ev)),
    );
    events.unshift(ev);
    logEl.insertBefore(row, logEl.firstChild);
    while (logEl.children.length > MAX_EVENTS) logEl.removeChild(logEl.lastChild);
    if (events.length > MAX_EVENTS) events.length = MAX_EVENTS;
  }

  function bumpRate() {
    const now = Date.now();
    rateWindow.push(now);
    rateWindow = rateWindow.filter(t => now - t < 60000);
    renderRate();
  }

  function renderRate() {
    statEls.rate.textContent = rateWindow.length;
    statEls.rate.appendChild(h('small', {}, 'ev/min'));
  }

  function renderSession() {
    const sec = Math.floor((Date.now() - sessionStart) / 1000);
    const m = Math.floor(sec / 60), s = sec % 60;
    statEls.session.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  async function refreshTabs() {
    const token = getToken();
    if (!token) { statEls.tabs.textContent = '—'; return; }
    try {
      const tabs = await api.get('/tabs?token=' + encodeURIComponent(token));
      statEls.tabs.textContent = tabs.length;
      // Also update the status line tab count
      for (const el of document.querySelectorAll('[data-meta="tab-count"]')) el.textContent = tabs.length;
    } catch (e) {
      statEls.tabs.textContent = '—';
    }
  }

  refreshTabs();
  setInterval(refreshTabs, 8000);
  setInterval(renderSession, 1000);
  setInterval(() => { rateWindow = rateWindow.filter(t => Date.now() - t < 60000); renderRate(); }, 2000);
  renderSession(); renderRate();

  bus.on((ev) => {
    if (ev.__internal) return;
    push(ev);
    bumpRate();
    statEls.last.textContent = ev.type;
    if (ev.type === 'tabs-synced') {
      statEls.tabs.textContent = ev.count;
      for (const el of document.querySelectorAll('[data-meta="tab-count"]')) el.textContent = ev.count;
    }
  });
}

function stat(label, key, cls) {
  return h('div', { class: 'stat' },
    h('div', { class: 'stat-k' }, label),
    h('div', { class: 'stat-v ' + (cls || ''), data: { stat: key } }, '—'),
  );
}
