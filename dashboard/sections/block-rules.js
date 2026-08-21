// §06 BLOCK RULES — declarativeNetRequest session rules.

import { h, clear, errMsg, toast, armedAction } from './_util.js';
import { extCmd } from '../api.js';

export function mount(container) {
  const root = h('div', {},
    h('div', { class: 'toolbar' },
      h('button', { data: { role: 'refresh' } }, 'refresh'),
      h('button', { class: 'ghost sm danger', data: { role: 'clear-all' } }, 'remove all'),
    ),
    h('div', { data: { role: 'list' } }, h('div', { class: 'dim italic small' }, 'loading…')),
    h('div', { class: 'divider' }),
    h('div', { class: 'small dim', style: { marginBottom: '8px' } }, 'ADD NEW RULE'),
    h('div', { class: 'row' },
      h('div', { class: 'grow' }, h('label', {}, 'url filter pattern'), h('input', { type: 'text', data: { role: 'pat' }, placeholder: '*/favicon.ico  ·  *.ad-server.com/*  ·  ||tracker.io^' })),
      h('div', {}, h('label', {}, 'chrome tab id (optional)'), h('input', { type: 'number', data: { role: 'tid' }, placeholder: 'all tabs', style: { width: '140px' } })),
      h('div', {}, h('label', {}, '\u00a0'), h('button', { class: 'primary', data: { role: 'add' } }, 'ADD')),
    ),
    h('div', { class: 'hint' }, 'Session rules only. Resource types: xmlhttprequest, media, other. Scoped to specified tab or (if blank) all currently open tabs. Extension host always excluded.'),
  );
  container.appendChild(root);

  const listEl = root.querySelector('[data-role=list]');

  async function load() {
    listEl.innerHTML = '<div class="dim italic small">loading…</div>';
    try {
      const rules = await extCmd('list-block-rules');
      clear(listEl);
      const arr = Array.isArray(rules) ? rules : [];
      document.querySelector('#s06 [data-sub]').textContent = `${arr.length} active`;
      if (arr.length === 0) { listEl.appendChild(h('div', { class: 'dim italic small' }, 'no active block rules.')); return; }
      const table = h('table', { class: 't' },
        h('thead', {}, h('tr', {},
          h('th', { style: { width: '60px' } }, 'id'),
          h('th', {}, 'pattern'),
          h('th', { style: { width: '160px' } }, 'tabs'),
          h('th', { style: { width: '100px', textAlign: 'right' } }, ''),
        )),
        h('tbody', {}, arr.map(r => {
          const cond = r.condition || {};
          const tabs = cond.tabIds && cond.tabIds.length ? cond.tabIds.join(',') : 'all';
          return h('tr', {},
            h('td', { class: 'mono' }, r.id),
            h('td', { class: 'mono' }, cond.urlFilter || ''),
            h('td', { class: 'dim small' }, tabs),
            h('td', { style: { textAlign: 'right' } },
              h('button', {
                class: 'ghost sm danger',
                onclick: async () => {
                  try { await extCmd('unblock-requests', { ruleId: r.id }); toast('removed', 'ok'); load(); }
                  catch (e) { toast(errMsg(e), 'err'); }
                },
              }, 'remove'),
            ),
          );
        })),
      );
      listEl.appendChild(table);
    } catch (e) {
      clear(listEl);
      listEl.appendChild(h('pre', { class: 'pane err' }, errMsg(e)));
    }
  }

  root.querySelector('[data-role=refresh]').addEventListener('click', load);
  root.querySelector('[data-role=clear-all]').addEventListener('click', armedAction(async () => {
    try { await extCmd('unblock-requests', {}); toast('all removed', 'ok'); load(); }
    catch (e) { toast(errMsg(e), 'err'); }
  }, { confirmLabel: 'confirm remove all' }));
  root.querySelector('[data-role=add]').addEventListener('click', async () => {
    const pat = (root.querySelector('[data-role=pat]').value || '').trim();
    const tidVal = root.querySelector('[data-role=tid]').value;
    if (!pat) { toast('pattern required', 'warn'); return; }
    const fields = { pattern: pat };
    if (tidVal) fields.tabId = Number(tidVal);
    try {
      const r = await extCmd('block-requests', fields);
      toast('added rule ' + (r && r.ruleId), 'ok');
      load();
    } catch (e) { toast(errMsg(e), 'err'); }
  });

  load();
}
