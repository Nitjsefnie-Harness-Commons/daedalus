// §02 EVAL — run JS in a target tab, see results. History in localStorage.

import { h, field, clear, fmtTime, truncate, errMsg, toast, pretty, formatEvalWorld, bindTabSelector } from './_util.js';
import { api, runCommand, getToken } from '../api.js';

const HISTORY_KEY = 'daedalus-dash-eval-history';
const HISTORY_MAX = 30;

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', {}, field('target tab',
        h('select', { data: { role: 'tab-select' } },
          h('option', { value: '' }, 'loading tabs…'),
        ))),
      h('div', {}, field('timeout (ms)', h('input', { type: 'number', value: '10000', min: '1000', max: '60000', data: { role: 'timeout' }, style: { width: '96px' } }))),
      h('div', { class: 'grow' }, field('code', h('textarea', { data: { role: 'code' }, spellcheck: false, placeholder: 'document.title' }))),
    ),
    h('div', { class: 'toolbar', style: { marginTop: '10px' } },
      h('button', { class: 'primary', data: { role: 'run' } }, 'RUN ⏎'),
      h('button', { class: 'ghost sm', data: { role: 'broadcast' } }, 'broadcast'),
      h('span', { style: { flex: '1' } }),
      h('span', { class: 'dim small', role: 'status', data: { role: 'meta' } }, ''),
    ),
    h('div', { style: { display: 'grid', gridTemplateColumns: '3fr 2fr', gap: '12px', marginTop: '10px' } },
      h('div', {},
        h('div', { class: 'small dim', style: { marginBottom: '4px' } }, 'result'),
        h('pre', { class: 'pane empty', role: 'status', data: { role: 'result' } }, 'no result yet. ⏎ or Cmd/Ctrl-Enter to run.'),
      ),
      h('div', {},
        h('div', { class: 'toolbar', style: { marginBottom: '4px' } },
          h('span', { class: 'small dim' }, 'history'),
          h('span', { style: { flex: '1' } }),
          h('button', { class: 'ghost sm', data: { role: 'clear-history' } }, 'clear'),
        ),
        h('div', { class: 'pane', data: { role: 'history' }, style: { maxHeight: '280px' } }),
      ),
    ),
  );
  container.appendChild(root);

  const sel = root.querySelector('[data-role=tab-select]');
  const codeEl = root.querySelector('[data-role=code]');
  const timeoutEl = root.querySelector('[data-role=timeout]');
  const runBtn = root.querySelector('[data-role=run]');
  const bcastBtn = root.querySelector('[data-role=broadcast]');
  const resultEl = root.querySelector('[data-role=result]');
  const metaEl = root.querySelector('[data-role=meta]');
  const historyEl = root.querySelector('[data-role=history]');
  const clearHBtn = root.querySelector('[data-role=clear-history]');

  let history = loadHistory();

  codeEl.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); run(); }
  });
  runBtn.addEventListener('click', () => run());
  bcastBtn.addEventListener('click', () => run({ broadcast: true }));
  clearHBtn.addEventListener('click', () => { history = []; saveHistory(); renderHistory(); });

  // The 12s poll stays: this selector is the one an operator types into, and
  // it is the only one that must not be wrong between events.
  bindTabSelector(sel, {
    getToken, api, bus, emptyLabel: '(no tabs)', interval: 12000,
    errorLabel: (e) => `(err: ${errMsg(e)})`,
  });

  async function run({ broadcast = false } = {}) {
    const code = (codeEl.value || '').trim();
    if (!code) { toast('code is empty', 'warn'); return; }
    const tabId = broadcast ? '' : sel.value;
    const timeout = Math.max(1000, Math.min(60000, Number(timeoutEl.value) || 10000));
    resultEl.textContent = 'running…';
    resultEl.className = 'pane';
    metaEl.textContent = `tab=${tabId || 'broadcast'}  timeout=${timeout}ms`;
    const t0 = Date.now();
    try {
      const { envelope } = await runCommand({ tab: tabId, code, timeout });
      const ms = Date.now() - t0;
      resultEl.classList.add('flash');
      setTimeout(() => resultEl.classList.remove('flash'), 240);
      resultEl.className = 'pane flash';
      resultEl.textContent = pretty(envelope && envelope.result !== undefined ? envelope.result : envelope);
      const world = envelope && envelope.world;
      // envelope.tabId and envelope.world are remote result data: text nodes
      // only, never innerHTML. `world` identifies the execution channel; it is
      // not a value-integrity signal.
      clear(metaEl);
      metaEl.append(
        'tab=',
        h('span', { class: 'cyan' }, String(envelope && envelope.tabId || tabId || 'broadcast')),
        '  ',
        h('span', { class: 'cyan' }, formatEvalWorld(world) || '—'),
        `  ${ms}ms`,
      );
      pushHistory({ code, tabId, ms, world, ok: true, ts: Date.now() });
    } catch (e) {
      const ms = Date.now() - t0;
      resultEl.className = 'pane err';
      resultEl.textContent = errMsg(e);
      clear(metaEl);
      metaEl.append(h('span', { class: 'red' }, 'error'), `  ${ms}ms`);
      pushHistory({ code, tabId, ms, ok: false, err: errMsg(e), ts: Date.now() });
    }
  }

  function pushHistory(e) {
    history.unshift(e);
    if (history.length > HISTORY_MAX) history.length = HISTORY_MAX;
    saveHistory();
    renderHistory();
  }

  function renderHistory() {
    clear(historyEl);
    if (history.length === 0) {
      historyEl.appendChild(h('div', { class: 'dim italic small' }, 'empty.'));
      return;
    }
    for (const e of history) {
      const row = h('div', {
        style: { padding: '4px 0', borderBottom: '1px dashed var(--border)', cursor: 'pointer', display: 'grid', gridTemplateColumns: '56px 44px 1fr', gap: '8px', alignItems: 'baseline' },
        onclick: () => { codeEl.value = e.code; if (e.tabId) sel.value = e.tabId; codeEl.focus(); },
        title: 'click to reload',
      },
        h('span', { class: 'dimmer small' }, fmtTime(e.ts)),
        h('span', { class: 'small', style: { color: e.ok ? 'var(--green)' : 'var(--red)' } }, e.ok ? 'ok' : 'err'),
        h('span', { class: 'mono-sm', style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, truncate(e.code, 80)),
      );
      historyEl.appendChild(row);
    }
  }

  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch { return []; }
  }
  function saveHistory() {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch {}
  }
  renderHistory();
}
