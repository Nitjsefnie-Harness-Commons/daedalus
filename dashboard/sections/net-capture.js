// §07 NET CAPTURE — CDP-backed request/response capture.

import { h, clear, fmtSize, truncate, errMsg, toast, pretty } from './_util.js';
import { api, extCmd, getToken } from '../api.js';

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', {}, h('label', {}, 'tab'), h('select', { data: { role: 'tab' }, style: { minWidth: '260px' } }, h('option', { value: '' }, '(active tab)'))),
      h('div', {}, h('label', {}, 'max'), h('input', { type: 'number', value: '1000', min: '10', max: '10000', data: { role: 'max' }, style: { width: '90px' } })),
      h('div', {}, h('label', {}, 'regex filter'), h('input', { type: 'text', data: { role: 'filter' }, placeholder: 'e.g. \\.m3u8$' })),
      h('div', {},
        h('label', {}, '\u00a0'),
        h('button', { class: 'primary', data: { role: 'start' } }, 'START'),
      ),
      h('div', {},
        h('label', {}, '\u00a0'),
        h('button', { data: { role: 'poll' } }, 'poll'),
      ),
      h('div', {},
        h('label', {}, '\u00a0'),
        h('label', { style: { display: 'inline-flex', gap: '6px', alignItems: 'center' } },
          h('input', { type: 'checkbox', data: { role: 'bodies' } }),
          h('span', {}, 'bodies'),
        ),
      ),
      h('div', {},
        h('label', {}, '\u00a0'),
        h('button', { class: 'danger', data: { role: 'stop' } }, 'STOP'),
      ),
    ),
    h('div', { class: 'hint' }, 'START attaches CDP Network domain to the tab. Requests buffer until STOP (or manual poll). bodies=on fetches response bodies before closing — slow for large captures.'),
    h('div', { class: 'toolbar', style: { marginTop: '10px' } },
      h('span', { class: 'small dim', data: { role: 'status' } }, 'not capturing.'),
    ),
    h('div', { data: { role: 'list' } }),
  );
  container.appendChild(root);

  const tabSel = root.querySelector('[data-role=tab]');
  const statusEl = root.querySelector('[data-role=status]');
  const listEl = root.querySelector('[data-role=list]');

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

  function fields(withMax) {
    const f = {};
    if (tabSel.value) f.tabId = Number(tabSel.value);
    if (withMax) f.maxRequests = Number(root.querySelector('[data-role=max]').value) || 1000;
    const filt = (root.querySelector('[data-role=filter]').value || '').trim();
    if (filt) f.filter = filt;
    if (root.querySelector('[data-role=bodies]').checked) f.bodies = true;
    return f;
  }

  root.querySelector('[data-role=start]').addEventListener('click', async () => {
    try {
      const r = await extCmd('net-capture', fields(true));
      statusEl.innerHTML = r.already
        ? `<span class="amber">already capturing</span> on tab ${r.tabId} · ${r.buffered} buffered`
        : `<span class="cyan">capturing</span> on tab ${r.tabId}`;
    } catch (e) { toast(errMsg(e), 'err'); }
  });

  root.querySelector('[data-role=poll]').addEventListener('click', async () => {
    try {
      const r = await extCmd('net-capture-get', fields(false), { timeout: 30000 });
      statusEl.innerHTML = `<span class="cyan">${r.count}</span> request(s) on tab ${r.tabId}`;
      render(r.requests || []);
    } catch (e) {
      statusEl.innerHTML = '<span class="red">' + errMsg(e) + '</span>';
    }
  });

  root.querySelector('[data-role=stop]').addEventListener('click', async () => {
    try {
      const r = await extCmd('net-capture-stop', fields(false), { timeout: 30000 });
      if (!r.stopped) {
        statusEl.innerHTML = '<span class="dim">' + (r.reason || 'not capturing') + '</span>';
        return;
      }
      statusEl.innerHTML = `<span class="green">stopped</span>  tab=${r.tabId}  captured=${r.count}`;
      render(r.requests || []);
    } catch (e) { toast(errMsg(e), 'err'); }
  });

  function render(rows) {
    clear(listEl);
    document.querySelector('#s07 [data-sub]').textContent = `${rows.length} req`;
    if (rows.length === 0) { listEl.appendChild(h('div', { class: 'dim italic small', style: { marginTop: '8px' } }, 'no requests captured.')); return; }
    const table = h('table', { class: 't', style: { marginTop: '10px' } },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '54px' } }, 'status'),
        h('th', { style: { width: '62px' } }, 'method'),
        h('th', { style: { width: '90px' } }, 'type'),
        h('th', {}, 'url'),
        h('th', { style: { width: '90px' } }, 'size'),
      )),
      h('tbody', {}, rows.map(requestRow)),
    );
    listEl.appendChild(table);
  }

  function requestRow(r) {
    const tr = h('tr', {},
      h('td', { class: r.status >= 400 ? 'mono red' : r.status >= 300 ? 'mono amber' : 'mono green' }, String(r.status || '-')),
      h('td', { class: 'mono' }, r.method || ''),
      h('td', { class: 'dim small' }, r.type || ''),
      h('td', { class: 'url' }, truncate(r.url || '', 140)),
      h('td', { class: 'num' }, fmtSize(r.encodedLength || 0)),
    );
    // Expand on click to show headers + body
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => {
      const next = tr.nextSibling;
      if (next && next.dataset && next.dataset.detail) { next.remove(); return; }
      const detail = h('tr', { data: { detail: '1' } },
        h('td', { colspan: '5', style: { background: 'var(--bg)' } },
          h('pre', { class: 'pane', style: { margin: '0' } }, pretty({
            url: r.url, method: r.method, status: r.status, statusText: r.statusText,
            mimeType: r.mimeType, requestHeaders: r.headers, responseHeaders: r.responseHeaders,
            initiator: r.initiator, ts: r.ts,
            bodyBase64: r.bodyBase64 || false,
            body: r.body ? (r.body.length > 2000 ? r.body.slice(0, 2000) + '\n…(truncated ' + (r.body.length - 2000) + ' chars)' : r.body) : '(no body — use bodies=on)',
          })),
        ),
      );
      tr.parentNode.insertBefore(detail, tr.nextSibling);
    });
    return tr;
  }
}
