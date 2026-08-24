// §10 CSS INJECT — inject + remove CSS per tab via chrome.scripting.

import { h, clear, truncate, errMsg, toast, bindTabSelector } from './_util.js';
import { api, extCmd, getToken } from '../api.js';

const STORE_KEY = 'daedalus-dash-css-sessions';

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', {}, h('label', {}, 'tab'), h('select', { data: { role: 'tab' }, style: { minWidth: '260px' } }, h('option', { value: '' }, '(active tab)'))),
      h('div', {}, h('label', {}, 'all frames'), h('input', { type: 'checkbox', data: { role: 'all' } })),
    ),
    h('div', { style: { marginTop: '8px' } },
      h('label', {}, 'css'),
      h('textarea', { data: { role: 'css' }, placeholder: 'body { background: #ffcc00 !important; }', spellcheck: false, style: { minHeight: '140px' } }),
    ),
    h('div', { class: 'toolbar', style: { marginTop: '8px' } },
      h('button', { class: 'primary', data: { role: 'inject' } }, 'INJECT'),
      h('button', { class: 'danger', data: { role: 'remove' } }, 'REMOVE'),
      h('span', { class: 'hint' }, 'chrome.scripting.insertCSS / removeCSS. removeCSS requires an exact match — use session list below to re-remove what was injected.'),
    ),
    h('div', { class: 'divider' }),
    h('div', { class: 'small dim', style: { marginBottom: '8px' } }, 'SESSION INJECTIONS (stored in localStorage, not the extension)'),
    h('div', { data: { role: 'sessions' } }),
  );
  container.appendChild(root);

  const tabSel = root.querySelector('[data-role=tab]');
  const cssEl = root.querySelector('[data-role=css]');
  const allEl = root.querySelector('[data-role=all]');
  const sessionsEl = root.querySelector('[data-role=sessions]');

  bindTabSelector(tabSel, {
    getToken, api, bus, placeholder: '(active tab)',
  });

  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); } catch { return []; }
  }
  function save(arr) { localStorage.setItem(STORE_KEY, JSON.stringify(arr.slice(-20))); }

  function renderSessions() {
    clear(sessionsEl);
    const arr = load();
    if (arr.length === 0) { sessionsEl.appendChild(h('div', { class: 'dim italic small' }, 'none.')); return; }
    const table = h('table', { class: 't' },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '130px' } }, 'time'),
        h('th', { style: { width: '60px' } }, 'tab'),
        h('th', { style: { width: '60px' } }, 'frames'),
        h('th', {}, 'css (preview)'),
        h('th', { style: { width: '160px', textAlign: 'right' } }, ''),
      )),
      h('tbody', {}, arr.slice().reverse().map((s, i) => h('tr', {},
        h('td', { class: 'dimmer small' }, new Date(s.ts).toTimeString().slice(0, 8)),
        h('td', { class: 'mono' }, s.tabId || '—'),
        h('td', { class: 'small dim' }, s.allFrames ? 'all' : 'top'),
        h('td', { class: 'mono-sm' }, truncate(s.css.replace(/\s+/g, ' '), 100)),
        h('td', { style: { textAlign: 'right' } },
          h('button', { class: 'ghost sm', onclick: () => { cssEl.value = s.css; tabSel.value = s.tabId || ''; allEl.checked = !!s.allFrames; toast('loaded', 'info'); } }, 'load'),
          h('button', {
            class: 'ghost sm danger',
            onclick: async () => {
              const f = { css: s.css };
              if (s.tabId) f.tabId = Number(s.tabId);
              if (s.allFrames) f.allFrames = true;
              try { await extCmd('remove-css', f); toast('removed', 'ok'); }
              catch (e) { toast(errMsg(e), 'err'); }
              const all = load();
              all.splice(all.length - 1 - i, 1);
              save(all); renderSessions();
            },
          }, 'remove'),
        ),
      ))),
    );
    sessionsEl.appendChild(table);
  }
  renderSessions();

  function buildFields() {
    const f = { css: cssEl.value || '' };
    if (tabSel.value) f.tabId = Number(tabSel.value);
    if (allEl.checked) f.allFrames = true;
    return f;
  }

  root.querySelector('[data-role=inject]').addEventListener('click', async () => {
    const f = buildFields();
    if (!f.css.trim()) { toast('css is empty', 'warn'); return; }
    try {
      const r = await extCmd('inject-css', f);
      toast(`injected ${r && r.injected} chars → tab ${r && r.tabId}`, 'ok');
      const sessions = load();
      sessions.push({ css: f.css, tabId: f.tabId || '', allFrames: !!f.allFrames, ts: Date.now() });
      save(sessions); renderSessions();
    } catch (e) { toast(errMsg(e), 'err'); }
  });
  root.querySelector('[data-role=remove]').addEventListener('click', async () => {
    const f = buildFields();
    if (!f.css.trim()) { toast('css is empty', 'warn'); return; }
    try {
      const r = await extCmd('remove-css', f);
      toast(`removed ${r && r.removed} chars`, 'ok');
    } catch (e) { toast(errMsg(e), 'err'); }
  });
}
