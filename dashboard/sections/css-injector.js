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
        h('td', { class: 'mono-sm' }, truncate(s.css.replace(/\s+/g, ' '), 100)),
        h('td', { style: { textAlign: 'right' } },
          h('button', { class: 'ghost sm', onclick: () => { cssEl.value = s.css; tabSel.value = s.tabId || ''; allEl.checked = !!s.allFrames; toast('loaded', 'info'); } }, 'load'),
          h('button', {
            class: 'ghost sm danger',
            onclick: async () => {
              const f = { css: s.css };
              if (s.tabId) f.tabId = Number(s.tabId);
              if (s.allFrames) f.allFrames = true;
              // The record is the only way back to an exact removeCSS
              // match, so a refused removal leaves it in place.
              try { await extCmd('remove-css', f); }
              catch (e) { toast(errMsg(e), 'err'); return; }
              toast('removed', 'ok');
              const all = load();
              // By the record's own contents, not by the position this row
              // was rendered at: the store is re-read here, so anything that
              // changed it between the render and this click moved every
              // index after the one that changed. A `splice` on an index
              // that is now past the end takes the LAST record instead.
              const at = all.findIndex((entry) => entry.css === s.css
                && entry.tabId === s.tabId && entry.allFrames === s.allFrames
                && entry.ts === s.ts);
              if (at < 0) { renderSessions(); return; }
              all.splice(at, 1);
              // The rule is already off the page, so a store that refuses
              // this write costs a record and not a rule. Say which, rather
              // than dropping the throw out of an async handler where it
              // would take the child down instead of reaching the operator.
              try { save(all); renderSessions(); }
              catch (e) { toast('session not removed: ' + errMsg(e), 'err'); }
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
    // injected and nothing to record. A command that fails after the write
    // leaves a record with no live rule, which is the safe direction: the
    // reverse order's failure is a rule applied to a page that nothing
    // recorded, and that one the operator cannot take off at all.
    //
    // The residue is not free, and saying otherwise would be false. The
    // row's remove asks the worker to remove a rule that is not there, the
    // worker refuses, and the record stays -- so a failed injection leaves
    // a row this panel will not clear, and enough of them reach the cap,
    // where the next injection is refused too. Clearing the store key by
    // hand is the only way out. That is a bounded annoyance against an
    // unrecoverable rule, which is why the order is this way round.
    const sessions = load();
    sessions.push({ css: f.css, tabId: f.tabId || '', allFrames: !!f.allFrames, ts: Date.now() });
    try { save(sessions); renderSessions(); }
    catch (e) { toast('session not recorded: ' + errMsg(e), 'err'); return; }
    try {
      const r = await extCmd('inject-css', f);
      toast(`injected ${r && r.injected} chars → tab ${r && r.tabId}`, 'ok');
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
