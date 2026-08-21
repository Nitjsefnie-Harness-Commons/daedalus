// §09 CDP — raw Chrome DevTools Protocol method invocation.

import { h, clear, truncate, errMsg, pretty, toast } from './_util.js';
import { api, extCmd, getToken } from '../api.js';

const COMMON = [
  'Page.captureScreenshot',
  'Page.reload',
  'Page.navigate',
  'Runtime.evaluate',
  'Runtime.enable',
  'DOM.getDocument',
  'DOM.querySelector',
  'Network.enable',
  'Network.getCookies',
  'Network.setCookie',
  'Network.getResponseBody',
  'Target.getTargets',
  'Emulation.setDeviceMetricsOverride',
  'Emulation.clearDeviceMetricsOverride',
  'Input.dispatchMouseEvent',
  'Input.dispatchKeyEvent',
];

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', {}, h('label', {}, 'tab'), h('select', { data: { role: 'tab' }, style: { minWidth: '260px' } }, h('option', { value: '' }, '(active tab)'))),
      h('div', { class: 'grow' }, h('label', {}, 'method'), h('input', { type: 'text', data: { role: 'method' }, placeholder: 'Page.navigate', list: 'cdp-common' })),
      h('datalist', { id: 'cdp-common' }, COMMON.map(m => h('option', { value: m }))),
    ),
    h('div', { style: { marginTop: '8px' } },
      h('label', {}, 'params (json)'),
      h('textarea', { data: { role: 'params' }, placeholder: '{}', spellcheck: false, style: { fontFamily: 'var(--mono)' } }),
    ),
    h('div', { class: 'toolbar', style: { marginTop: '8px' } },
      h('button', { class: 'primary', data: { role: 'run' } }, 'RUN'),
      h('span', { class: 'hint' }, 'Attaches chrome.debugger, sends the CDP command, detaches. A devtools banner appears briefly on the target tab.'),
    ),
    h('div', { style: { marginTop: '10px' } },
      h('div', { class: 'small dim' }, 'result'),
      h('pre', { class: 'pane empty', data: { role: 'result' } }, 'no result yet.'),
    ),
  );
  container.appendChild(root);

  const tabSel = root.querySelector('[data-role=tab]');
  const methodEl = root.querySelector('[data-role=method]');
  const paramsEl = root.querySelector('[data-role=params]');
  const resultEl = root.querySelector('[data-role=result]');

  async function populateTabs() {
    const token = getToken();
    if (!token) return;
    try {
      const tabs = await api.get('/tabs?token=' + encodeURIComponent(token));
      const current = tabSel.value;
      clear(tabSel);
      tabSel.appendChild(h('option', { value: '' }, '(active tab)'));
      for (const t of tabs) {
        tabSel.appendChild(h('option', { value: t.tabId }, `${t.tabId}  ${truncate(t.title || t.url || '', 60)}`));
      }
      if (current) tabSel.value = current;
    } catch {}
  }
  populateTabs();
  bus.on((ev) => { if (!ev.__internal && ev.type === 'tabs-synced') populateTabs(); });

  root.querySelector('[data-role=run]').addEventListener('click', async () => {
    const method = (methodEl.value || '').trim();
    if (!method) { toast('method required', 'warn'); return; }
    let params = {};
    const raw = (paramsEl.value || '').trim();
    if (raw) {
      try { params = JSON.parse(raw); }
      catch (e) { resultEl.className = 'pane err'; resultEl.textContent = 'invalid params JSON: ' + e.message; return; }
    }
    const fields = { method, params };
    if (tabSel.value) fields.tabId = tabSel.value;
    resultEl.textContent = 'running…';
    resultEl.className = 'pane';
    try {
      const r = await extCmd('cdp', fields, { timeout: 20000 });
      resultEl.className = 'pane flash';
      resultEl.textContent = pretty(r);
    } catch (e) {
      resultEl.className = 'pane err';
      resultEl.textContent = errMsg(e);
    }
  });
}
