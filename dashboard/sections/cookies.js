// §04 COOKIES — inspect + edit + remove cookies via extension.

import { h, field, spacer, clear, truncate, errMsg, toast, armedAction } from './_util.js';
import { extCmd } from '../api.js';

export function mount(container) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', { class: 'grow' },
        field('domain or url',
          h('input', { type: 'text', data: { role: 'q' }, placeholder: 'example.com or https://example.com/' })),
      ),
      h('div', {},
        spacer(),
        h('button', { class: 'primary', data: { role: 'load' } }, 'LIST'),
      ),
      h('div', {},
        spacer(),
        h('button', { class: 'danger', data: { role: 'clear' } }, 'clear all'),
      ),
    ),
    h('div', { class: 'divider' }),
    h('div', { class: 'small dim', style: { marginBottom: '8px' } }, 'SET / UPDATE COOKIE'),
    h('div', { class: 'row' },
      h('div', { class: 'grow' }, field('url', h('input', { type: 'text', data: { role: 'su' }, placeholder: 'https://example.com/' }))),
      h('div', {}, field('name', h('input', { type: 'text', data: { role: 'sn' } }))),
      h('div', { class: 'grow' }, field('value', h('input', { type: 'text', data: { role: 'sv' } }))),
      h('div', {}, field('domain', h('input', { type: 'text', data: { role: 'sd' } }))),
      h('div', {}, field('path', h('input', { type: 'text', data: { role: 'sp' }, value: '/' }))),
      h('div', {}, spacer(), h('button', { data: { role: 'set' } }, 'SET')),
    ),
    h('div', { class: 'hint' }, 'Tip: for httpOnly/secure/sameSite flags, use the MCP tool (',
      h('code', {}, 'set_cookie'),
      '). This form sets defaults (secure off, sameSite unset).'),
    h('div', { class: 'divider' }),
    h('div', { data: { role: 'table-host' } }, h('div', { class: 'dim italic small' }, 'enter a domain/url and click LIST.')),
  );
  container.appendChild(root);

  const qEl = root.querySelector('[data-role=q]');
  const host = root.querySelector('[data-role=table-host]');

  async function load() {
    const q = (qEl.value || '').trim();
    if (!q) { toast('enter domain or url', 'warn'); return; }
    const fields = q.includes('://') ? { url: q } : { domain: q };
    host.innerHTML = '<div class="dim italic small">loading…</div>';
    try {
      const cookies = await extCmd('cookies', fields);
      render(cookies || []);
    } catch (e) {
      clear(host);
      host.appendChild(h('pre', { class: 'pane err' }, errMsg(e)));
    }
  }

  function render(cookies) {
    clear(host);
    document.querySelector('#s04 [data-sub]').textContent = `${cookies.length} cookie(s)`;
    if (cookies.length === 0) { host.appendChild(h('div', { class: 'dim italic small' }, 'no cookies.')); return; }
    const table = h('table', { class: 't' },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '40%' } }, 'name'),
        h('th', { style: { width: '40%' } }, 'value'),
        h('th', { style: { width: '140px' } }, 'domain'),
        h('th', { style: { width: '80px' } }, 'path'),
        h('th', { style: { width: '80px' } }, 'flags'),
        h('th', { style: { width: '120px', textAlign: 'right' } }, ''),
      )),
      h('tbody', {}, cookies.map((c) => cookieRow(c, load))),
    );
    host.appendChild(table);
  }

  root.querySelector('[data-role=load]').addEventListener('click', load);
  qEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(); });

  root.querySelector('[data-role=clear]').addEventListener('click', armedAction(async () => {
    const q = (qEl.value || '').trim();
    if (!q) { toast('enter domain or url first', 'warn'); return; }
    const fields = q.includes('://') ? { url: q } : { domain: q };
    try {
      const r = await extCmd('clear-cookies', fields);
      toast(`cleared ${r && r.removed || 0}`, 'ok');
      load();
    } catch (e) { toast(errMsg(e), 'err'); }
  }, { confirmLabel: 'confirm clear all' }));

  root.querySelector('[data-role=set]').addEventListener('click', async () => {
    const url = (root.querySelector('[data-role=su]').value || '').trim();
    const name = (root.querySelector('[data-role=sn]').value || '').trim();
    const value = root.querySelector('[data-role=sv]').value || '';
    const domain = (root.querySelector('[data-role=sd]').value || '').trim();
    const path = (root.querySelector('[data-role=sp]').value || '/').trim();
    if (!url || !name) { toast('url + name are required', 'warn'); return; }
    const fields = { url, name, value, path };
    if (domain) fields.domain = domain;
    try {
      await extCmd('set-cookie', fields);
      toast('set ' + name, 'ok');
      load();
    } catch (e) { toast(errMsg(e), 'err'); }
  });
}

function cookieRow(c, reload) {
  const flags = [c.secure && 'S', c.httpOnly && 'H', c.sameSite && c.sameSite[0]].filter(Boolean).join('·');
  const row = h('tr', {},
    h('td', { class: 'mono' }, c.name || ''),
    h('td', { class: 'url', style: { maxWidth: '320px' } }, truncate(c.value || '', 140)),
    h('td', { class: 'mono-sm dim' }, c.domain || ''),
    h('td', { class: 'mono-sm dim' }, c.path || ''),
    h('td', { class: 'small dim' }, flags),
    h('td', { style: { textAlign: 'right' } },
      h('button', {
        class: 'ghost sm danger',
        onclick: async () => {
          const proto = c.secure ? 'https' : 'http';
          const url = `${proto}://${(c.domain || '').replace(/^\./, '')}${c.path || '/'}`;
          try { await extCmd('remove-cookie', { url, name: c.name }); reload(); }
          catch (e) { toast(errMsg(e), 'err'); }
        },
      }, 'remove'),
    ),
  );
  return row;
}
