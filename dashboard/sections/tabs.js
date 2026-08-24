// §01 TABS — live tab registry with row actions.

import { h, clear, fmtAge, truncate, errMsg, toast, armedAction, inlineEdit } from './_util.js';
import { api, extCmd, getToken } from '../api.js';

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'toolbar' },
      h('input', { type: 'text', 'aria-label': 'filter tabs by url or title', placeholder: 'filter url/title…', data: { role: 'filter' }, style: { minWidth: '240px' } }),
      h('button', { class: 'sm', onclick: () => load() }, 'refresh'),
      h('span', { style: { flex: '1' } }),
      h('input', { type: 'text', 'aria-label': 'urls to open, space or newline separated', placeholder: 'https://… (space/newline-separated for bulk)', data: { role: 'open-url' }, style: { minWidth: '320px' } }),
      h('button', { class: 'primary sm', onclick: () => openNew() }, '+ open tab(s)'),
    ),
    h('div', { data: { role: 'table-host' } },
      h('div', { class: 'dim italic small' }, 'loading…'),
    ),
  );
  container.appendChild(root);

  const host = root.querySelector('[data-role=table-host]');
  const filterEl = root.querySelector('[data-role=filter]');
  const openUrlEl = root.querySelector('[data-role=open-url]');

  let tabs = [];
  let filter = '';

  filterEl.addEventListener('input', () => { filter = filterEl.value.toLowerCase(); render(); });

  async function load() {
    const token = getToken();
    if (!token) { host.innerHTML = '<div class="dim italic small">no token (§12 Settings)</div>'; return; }
    try {
      tabs = await api.get('/tabs?token=' + encodeURIComponent(token));
      render();
    } catch (e) {
      host.innerHTML = '';
      host.appendChild(h('pre', { class: 'pane err' }, errMsg(e)));
    }
  }

  function visible(t) {
    if (!filter) return true;
    return ((t.url || '').toLowerCase().includes(filter) || (t.title || '').toLowerCase().includes(filter));
  }

  function render() {
    clear(host);
    const rows = tabs.filter(visible).sort((a, b) => (a.age || 0) - (b.age || 0));
    document.querySelector('#s01 [data-sub]').textContent = `${rows.length}/${tabs.length} tabs`;
    if (rows.length === 0) {
      host.appendChild(h('div', { class: 'dim italic small' }, tabs.length ? 'no tabs match filter.' : 'no tabs registered. is the extension connected?'));
      return;
    }
    const table = h('table', { class: 't' },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '82px' } }, 'tab id'),
        h('th', { style: { width: '52px' } }, 'age'),
        h('th', {}, 'title'),
        h('th', {}, 'url'),
        h('th', { style: { width: '280px', textAlign: 'right' } }, 'actions'),
      )),
      h('tbody', {}, rows.map(renderRow)),
    );
    host.appendChild(table);
  }

  function renderRow(t) {
    const id = t.tabId;
    const originalUrlText = truncate(t.url || '', 80);
    const urlCell = h('td', { class: 'url' }, originalUrlText);
    return h('tr', { data: { tid: id } },
      h('td', { class: 'mono' }, id),
      h('td', { class: 'num' }, fmtAge(t.age)),
      h('td', { class: 'mono' }, truncate(t.title || '', 60)),
      urlCell,
      h('td', { style: { textAlign: 'right' } },
        h('button', { class: 'ghost sm', onclick: () => focus(id) }, 'focus'),
        h('button', { class: 'ghost sm', onclick: () => reload(id) }, 'reload'),
        h('button', { class: 'ghost sm', onclick: () => navTo(id, urlCell, t.url || 'https://', originalUrlText) }, 'nav…'),
        h('button', { class: 'ghost sm', onclick: () => shoot(id) }, 'shot'),
        h('button', { class: 'ghost sm danger', onclick: armedAction(() => close(id)) }, 'close'),
      ),
    );
  }

  async function focus(tabId) {
    try { await extCmd('focus-tab', { tabId: Number(tabId) }); toast('focused tab ' + tabId, 'ok'); }
    catch (e) { toast(errMsg(e), 'err'); }
  }
  async function reload(tabId) {
    try { await extCmd('reload', { tabId: Number(tabId) }); toast('reloaded tab ' + tabId, 'ok'); }
    catch (e) { toast(errMsg(e), 'err'); }
  }
  function navTo(tabId, urlCell, initial, originalText) {
    inlineEdit(urlCell, initial, originalText, async (url) => {
      try { await extCmd('navigate', { tabId: Number(tabId), url }); toast('navigating tab ' + tabId, 'ok'); }
      catch (e) { toast(errMsg(e), 'err'); }
    }, { type: 'url', label: 'navigate tab ' + tabId + ' to a new url' });
  }
  async function shoot(tabId) {
    try {
      const r = await extCmd('screenshot', { tabId: Number(tabId) }, { timeout: 20000 });
      toast('captured: ' + r.path, 'ok');
    } catch (e) { toast(errMsg(e), 'err'); }
  }
  async function close(tabId) {
    try { await extCmd('close-tab', { tabId: Number(tabId) }); toast('closed', 'ok'); load(); }
    catch (e) { toast(errMsg(e), 'err'); }
  }
  async function openNew() {
    const raw = (openUrlEl.value || '').trim();
    if (!raw) { toast('enter a url first', 'warn'); return; }
    const urls = raw.split(/\s+/).filter(Boolean);
    try {
      if (urls.length === 1) {
        const r = await extCmd('open-tab', { url: urls[0] });
        toast('opened tab ' + r.tabId, 'ok');
      } else {
        const r = await extCmd('open-tabs', { urls }, { timeout: 30000 });
        const opened = (r && r.opened) || [];
        const errors = (r && r.errors) || [];
        const msg = `opened ${opened.length}/${urls.length}` + (errors.length ? ` (${errors.length} failed)` : '');
        toast(msg, errors.length ? 'warn' : 'ok');
      }
      openUrlEl.value = '';
    } catch (e) { toast(errMsg(e), 'err'); }
  }

  load();

  // Refresh on tab-related events.
  bus.on((ev) => {
    if (ev.__internal) return;
    if (ev.type === 'tabs-synced' || ev.type === 'tab-updated' || ev.type === 'tab-unregistered') {
      load();
    }
  });
  // Periodic refresh for age column.
  setInterval(load, 15000);
}
