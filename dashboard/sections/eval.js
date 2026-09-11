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
      // "Broadcast" does not fan out: a command that names no tab is run
      // once by the extension, in the active tab of the current browser
      // window. The label says where the code will run, not "all tabs".
      h('button', {
        class: 'ghost sm', data: { role: 'active-tab' },
        title: 'send the code with no tab named: the extension runs it once, in the active tab of the current browser window',
      }, 'run in active tab'),
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
  const activeTabBtn = root.querySelector('[data-role=active-tab]');
  const resultEl = root.querySelector('[data-role=result]');
  const metaEl = root.querySelector('[data-role=meta]');
  const historyEl = root.querySelector('[data-role=history]');
  const clearHBtn = root.querySelector('[data-role=clear-history]');

  let history = loadHistory();

  codeEl.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); run(); }
  });
  runBtn.addEventListener('click', () => run());
  activeTabBtn.addEventListener('click', () => run({ activeTab: true }));
  clearHBtn.addEventListener('click', () => { history = []; saveHistory(); renderHistory(); });

  // The 12s poll stays: this selector is the one an operator types into, and
  // it is the only one that must not be wrong between events.
  bindTabSelector(sel, {
    getToken, api, bus, emptyLabel: '(no tabs)', interval: 12000,
    errorLabel: (e) => `(err: ${errMsg(e)})`,
  });

  async function run({ activeTab = false } = {}) {
    const code = (codeEl.value || '').trim();
    if (!code) { toast('code is empty', 'warn'); return; }
    // The selector's empty value is every "nothing to choose" state — tabs
    // still loading, none registered, an error, or the chosen tab gone and
    // reset by the controller — and the bridge reads an empty tab as "no
    // tab named", which the extension runs in whatever tab is active. RUN
    // after the target closed used to run the code on an unintended tab;
    // only the button that says so sends a command with no tab.
    if (!activeTab && !sel.value) {
      metaEl.textContent = 'no target tab selected — choose one, or use "run in active tab"';
      return;
    }
    const tabId = activeTab ? '' : sel.value;
    const timeout = Math.max(1000, Math.min(60000, Number(timeoutEl.value) || 10000));
    resultEl.textContent = 'running…';
    resultEl.className = 'pane';
    metaEl.textContent = `tab=${tabId || 'active tab'}  timeout=${timeout}ms`;
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
        h('span', { class: 'cyan' }, String(envelope && envelope.tabId || tabId || 'active tab')),
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
