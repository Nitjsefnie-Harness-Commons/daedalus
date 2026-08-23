// §08 FETCH TIMINGS — diagnostic ring buffer from the service worker's fetch relay.

import { h, clear, fmtSize, truncate, errMsg, toast, armedAction } from './_util.js';
import { extCmd } from '../api.js';

export function mount(container) {
  const root = h('div', {},
    h('div', { class: 'toolbar' },
      h('button', { data: { role: 'refresh' } }, 'refresh'),
      h('button', { class: 'ghost sm danger', data: { role: 'reset' } }, 'reset buffer'),
      h('span', { style: { flex: '1' } }),
      h('span', { class: 'small dim', data: { role: 'meta' } }, ''),
    ),
    h('div', { data: { role: 'list' } }, h('div', { class: 'dim italic small' }, 'loading…')),
  );
  container.appendChild(root);

  const listEl = root.querySelector('[data-role=list]');
  const metaEl = root.querySelector('[data-role=meta]');

  async function load(opts = {}) {
    listEl.innerHTML = '<div class="dim italic small">loading…</div>';
    try {
      const r = await extCmd('fetch-timings', opts);
      const t = (r && r.timings) || [];
      document.querySelector('#s08 [data-sub]').textContent = `${t.length} entr${t.length === 1 ? 'y' : 'ies'}`;
      clear(metaEl);
      metaEl.append('nativeToBase64 = ',
                    h('span', { class: r.hasNativeToBase64 ? 'green' : 'amber' },
                      String(r.hasNativeToBase64)));
      clear(listEl);
      if (t.length === 0) { listEl.appendChild(h('div', { class: 'dim italic small' }, 'ring buffer empty.')); return; }
      const ok = t.filter(x => !x.error);
      if (ok.length > 0) {
        const totals = ok.map(x => x.ms_total).sort((a, b) => a - b);
        const mid = totals[Math.floor(totals.length / 2)];
        const mean = totals.reduce((a, b) => a + b, 0) / totals.length;
        metaEl.append('  ·  median ',
                      h('span', { class: 'cyan' }, `${mid.toFixed(0)}ms`),
                      `  ·  mean ${mean.toFixed(0)}ms`
                      + `  ·  ${ok.length}/${t.length} ok`);
      }
      const table = h('table', { class: 't' },
        h('thead', {}, h('tr', {},
          h('th', { style: { width: '56px' } }, 'method'),
          h('th', { style: { width: '56px' } }, 'status'),
          h('th', { style: { width: '90px' } }, 'size'),
          h('th', { style: { width: '70px' } }, 'decode'),
          h('th', { style: { width: '70px' } }, 'fetch'),
          h('th', { style: { width: '70px' } }, 'encode'),
          h('th', { style: { width: '70px' } }, 'total'),
          h('th', {}, 'url'),
        )),
        h('tbody', {}, t.slice().reverse().map(row)),
      );
      listEl.appendChild(table);
    } catch (e) {
      clear(listEl);
      listEl.appendChild(h('pre', { class: 'pane err' }, errMsg(e)));
    }
  }

  function row(e) {
    if (e.error) {
      return h('tr', {},
        h('td', { class: 'mono' }, e.method || ''),
        h('td', { class: 'mono red' }, 'ERR'),
        h('td', {}, ''),
        h('td', {}, ''),
        h('td', {}, ''),
        h('td', {}, ''),
        h('td', { class: 'num' }, String(e.ms_total || 0)),
        h('td', { class: 'url red' }, `${truncate(e.url || '', 100)}  (${e.error})`),
      );
    }
    return h('tr', {},
      h('td', { class: 'mono' }, e.method || ''),
      h('td', { class: e.status >= 400 ? 'mono red' : 'mono green' }, String(e.status || '')),
      h('td', { class: 'num' }, fmtSize(e.bodySize || 0)),
      h('td', { class: 'num dim' }, String(e.ms_bodyDecode || 0)),
      h('td', { class: 'num' }, String(e.ms_fetch || 0)),
      h('td', { class: 'num dim' }, String(e.ms_encode || 0)),
      h('td', { class: 'num cyan' }, String(e.ms_total || 0)),
      h('td', { class: 'url' }, truncate(e.url || '', 100)),
    );
  }

  root.querySelector('[data-role=refresh]').addEventListener('click', () => load());
  root.querySelector('[data-role=reset]').addEventListener('click', armedAction(async () => {
    try { await load({ reset: true }); toast('reset', 'ok'); }
    catch (e) { toast(errMsg(e), 'err'); }
  }, { confirmLabel: 'confirm reset' }));
  load();
}
