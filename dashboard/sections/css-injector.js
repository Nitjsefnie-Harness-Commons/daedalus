// §10 CSS INJECT — inject + remove CSS per tab via chrome.scripting.

import { h, field, clear, truncate, errMsg, toast, bindTabSelector } from './_util.js';
import { api, extCmd, getToken } from '../api.js';

const STORE_KEY = 'daedalus-dash-css-sessions';
// The store is the panel's only record of what it injected, and
// `chrome.scripting.removeCSS` needs an exact match: a record the cap
// drops is a rule the operator can no longer take off. So the cap holds
// at 20 and the twenty-first injection is refused rather than applied and
// left unrecorded. The number in the refusal message is this constant, not
// a copy of it.
const STORE_MAX = 20;

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', {}, field('tab', h('select', { data: { role: 'tab' }, style: { minWidth: '260px' } }, h('option', { value: '' }, '(active tab)')))),
      h('div', {}, field('all frames', h('input', { type: 'checkbox', data: { role: 'all' } }))),
    ),
    h('div', { style: { marginTop: '8px' } },
      field('css',
        h('textarea', { data: { role: 'css' }, placeholder: 'body { background: #ffcc00 !important; }', spellcheck: false, style: { minHeight: '140px' } })),
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
    try {
      const stored = JSON.parse(localStorage.getItem(STORE_KEY) || '[]');
      // Valid JSON is not necessarily a list. Without this a `{}` store
      // reads as a length of `undefined`, and the first thing to touch it is
      // `renderSessions` at MOUNT, where the row map calls `.slice` on it --
      // so the panel dies before a click is possible, at the table, blaming
      // the table. The cap check would have been no better: it compares the
      // same `undefined` against 20, which is false, and waves the store
      // through.
      return Array.isArray(stored) ? stored : [];
    } catch { return []; }
  }
  function save(arr) { localStorage.setItem(STORE_KEY, JSON.stringify(arr.slice(-STORE_MAX))); }

  // One writer for every post-mount store write, so a refusal is caught
  // and toasted at the label the caller chooses instead of being thrown
  // out of an async handler, where it would take the child down.
  function persist(arr, label) {
    try {
      save(arr);
      renderSessions();
      return true;
    } catch (e) { toast(label + errMsg(e), 'err'); return false; }
  }

  // Splice one row's record out by its CONTENTS, not by the position the
  // row was rendered at. The store is re-read here, so anything that
  // changed it between the render and the click moved every index after
  // the one that changed; a `splice` on an index now past the end takes
  // the LAST record instead, and a record already gone is -1, so nothing
  // is spliced at all.
  function dropByContents(record) {
    const all = load();
    const at = all.findIndex((entry) => entry.css === record.css
      && entry.tabId === record.tabId && entry.allFrames === record.allFrames
      && entry.ts === record.ts);
    if (at < 0) { renderSessions(); return; }
    all.splice(at, 1);
    persist(all, 'session not removed: ');
  }

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
      h('tbody', {}, arr.slice().reverse().map((s) => h('tr', {},
        h('td', { class: 'dimmer small' }, new Date(s.ts).toTimeString().slice(0, 8)),
        h('td', { class: 'mono' }, s.tabId || '—'),
        h('td', { class: 'small dim' }, s.allFrames ? 'all' : 'top'),
        h('td', { class: 'mono-sm' },
          (s.failed ? '[failed] ' : '')
            + truncate(s.css.replace(/\s+/g, ' '), 100)),
        h('td', { style: { textAlign: 'right' } },
          h('button', { class: 'ghost sm', onclick: () => { cssEl.value = s.css; tabSel.value = s.tabId || ''; allEl.checked = !!s.allFrames; toast('loaded', 'info'); } }, 'load'),
          h('button', {
            class: 'ghost sm danger',
            onclick: async () => {
              // A failed record names a rule that was never applied, so
              // there is no removeCSS match for the worker to confirm:
              // drop it here. A live record still goes to the worker
              // first -- the record is the only way back to an exact
              // match, so a refused removal leaves it in place.
              if (s.failed) {
                toast('removed', 'ok');
                dropByContents(s);
                return;
              }
              const f = { css: s.css };
              if (s.tabId) f.tabId = Number(s.tabId);
              if (s.allFrames) f.allFrames = true;
              try { await extCmd('remove-css', f); }
              catch (e) { toast(errMsg(e), 'err'); return; }
              toast('removed', 'ok');
              dropByContents(s);
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
    // Before the command, not after it: applying the rule and then
    // declining to record it is the same defect the cap is here to stop.
    if (load().length >= STORE_MAX) {
      toast('session list full (' + STORE_MAX + ') — remove one first',
            'warn');
      return;
    }
    // The record is reserved BEFORE the command, not written after it. A
    // store that refuses the write then returns here, with nothing
    // injected and nothing to record; the reverse order's failure is a
    // rule applied to a page that nothing recorded, and that one the
    // operator cannot take off at all.
    const sessions = load();
    sessions.push({ css: f.css, tabId: f.tabId || '', allFrames: !!f.allFrames, ts: Date.now() });
    if (!persist(sessions, 'session not recorded: ')) return;
    try {
      const r = await extCmd('inject-css', f);
      toast(`injected ${r && r.injected} chars → tab ${r && r.tabId}`, 'ok');
    } catch (e) {
      toast(errMsg(e), 'err');
      // A command that failed leaves the reservation holding no live
      // rule: mark it, so the row shows the state and its remove drops
      // the record without asking the worker to confirm a match that
      // does not exist. A store that refuses the marking says so here
      // rather than throwing out of this handler.
      sessions[sessions.length - 1].failed = true;
      persist(sessions, 'session not marked: ');
    }
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
