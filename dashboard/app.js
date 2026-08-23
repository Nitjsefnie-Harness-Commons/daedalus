// Daedalus dashboard — entry point. Loads sections, boots SSE, wires meta bars.

import { getToken, getServer } from './api.js';
import { h, clear } from './sections/_util.js';
import { start as startSse, subscribe, lastEventAt } from './sse.js';

import { mount as mountOverview } from './sections/overview.js';
import { mount as mountTabs } from './sections/tabs.js';
import { mount as mountEval } from './sections/eval.js';
import { mount as mountScreenshot } from './sections/screenshot.js';
import { mount as mountCookies } from './sections/cookies.js';
import { mount as mountHotfixes } from './sections/hotfixes.js';
import { mount as mountBlockRules } from './sections/block-rules.js';
import { mount as mountNetCapture } from './sections/net-capture.js';
import { mount as mountFetchTimings } from './sections/fetch-timings.js';
import { mount as mountCdp } from './sections/cdp.js';
import { mount as mountCssInjector } from './sections/css-injector.js';
import { mount as mountUploads } from './sections/uploads.js';
import { mount as mountSettings } from './sections/settings.js';

const MOUNTS = {
  overview: mountOverview,
  tabs: mountTabs,
  eval: mountEval,
  screenshot: mountScreenshot,
  cookies: mountCookies,
  hotfixes: mountHotfixes,
  'block-rules': mountBlockRules,
  'net-capture': mountNetCapture,
  'fetch-timings': mountFetchTimings,
  cdp: mountCdp,
  'css-injector': mountCssInjector,
  uploads: mountUploads,
  settings: mountSettings,
};

function maskToken(t) {
  if (!t) return '(none)';
  if (t.length <= 12) return t;
  return t.slice(0, 8) + '…' + t.slice(-4);
}

function shortToken(t) {
  if (!t) return '—';
  return t.slice(0, 8) + '…';
}

function setAll(selector, text) {
  for (const el of document.querySelectorAll(selector)) el.textContent = text;
}

function relTime(ms) {
  if (!ms) return '—';
  const d = (Date.now() - ms) / 1000;
  if (d < 2) return 'now';
  if (d < 60) return Math.floor(d) + 's ago';
  if (d < 3600) return Math.floor(d / 60) + 'm ago';
  return Math.floor(d / 3600) + 'h ago';
}

// Simple global bus for cross-section messages (e.g. "tabs updated, refresh").
const busListeners = new Set();
export const bus = {
  emit(event) { for (const fn of busListeners) { try { fn(event); } catch (e) { console.error(e); } } },
  on(fn) { busListeners.add(fn); return () => busListeners.delete(fn); },
};

// Forward SSE events onto the bus so any section can listen.
subscribe((ev) => bus.emit(ev));

function mountSections() {
  for (const el of document.querySelectorAll('[data-section]')) {
    const name = el.dataset.section;
    const fn = MOUNTS[name];
    if (!fn) {
      clear(el);
      el.appendChild(h('div', { class: 'dim italic' }, `(no module for "${name}")`));
      continue;
    }
    try {
      fn(el, bus);
    } catch (e) {
      console.error(`[mount] ${name} failed`, e);
      clear(el);
      el.appendChild(h('pre', { class: 'pane err' }, `mount failed: ${e.message}`));
    }
  }
}

function wireMetaBar() {
  const token = getToken();
  const server = getServer();
  setAll('[data-meta="token"]', maskToken(token));
  setAll('[data-meta="token-short"]', shortToken(token));
  setAll('[data-meta="server"]', server || '(same origin)');
}

function wireStatusLine() {
  const dots = document.querySelectorAll('[data-meta="sse-dot"]');
  const txt1 = document.querySelectorAll('[data-meta="sse-text"]');
  const txt2 = document.querySelectorAll('[data-meta="sse-text2"]');
  const last = document.querySelectorAll('[data-meta="last-event"]');

  subscribe((ev) => {
    if (ev.__internal && ev.type === 'sse-status') {
      for (const d of dots) d.dataset.status = ev.status;
      for (const t of txt1) t.textContent = ev.status;
      for (const t of txt2) t.textContent = ev.status;
    }
  });

  // Last-event clock tick
  setInterval(() => {
    for (const l of last) l.textContent = relTime(lastEventAt());
  }, 1000);
}

function wireRailHighlight() {
  const links = Array.from(document.querySelectorAll('.rail-list a'));
  const sections = links
    .map(a => ({ a, el: document.querySelector(a.getAttribute('href')) }))
    .filter(x => x.el);

  const activate = (id) => {
    for (const { a } of sections) a.classList.toggle('active', a.getAttribute('href') === '#' + id);
  };

  const io = new IntersectionObserver((entries) => {
    // Pick the entry nearest the top of the viewport that's intersecting.
    const visible = entries.filter(e => e.isIntersecting);
    if (visible.length === 0) return;
    visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    activate(visible[0].target.id);
  }, { rootMargin: '-80px 0px -60% 0px', threshold: 0 });

  for (const { el } of sections) io.observe(el);

  // Also react to explicit clicks immediately.
  for (const { a } of sections) {
    a.addEventListener('click', () => activate(a.getAttribute('href').slice(1)));
  }
}

function boot() {
  wireMetaBar();
  wireStatusLine();
  wireRailHighlight();
  mountSections();
  startSse();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
