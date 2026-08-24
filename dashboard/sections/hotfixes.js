// §05 HOTFIXES — list, store, clear persistent code injected on every page load.

import { h, field, clear, fmtDateTime, truncate, errMsg, toast, armedAction } from './_util.js';
import { extCmd } from '../api.js';

export function mount(container) {
  const root = h('div', {},
    h('div', { class: 'toolbar' },
      h('button', { data: { role: 'refresh' } }, 'refresh'),
      h('button', { class: 'ghost sm danger', data: { role: 'clear-all' } }, 'clear all (keep perm)'),
      h('button', { class: 'ghost sm danger', data: { role: 'clear-all-incl' } }, 'clear all (incl. perm)'),
      h('span', { style: { flex: '1' } }),
      h('span', { class: 'small dim', data: { role: 'ver' } }, ''),
    ),
    h('div', { data: { role: 'list' } }, h('div', { class: 'dim italic small' }, 'loading…')),
    h('div', { class: 'divider' }),
    h('div', { class: 'small dim', style: { marginBottom: '8px' } }, 'STORE NEW / UPDATE'),
    h('div', { class: 'row' },
      h('div', {}, field('fix id', h('input', { type: 'text', data: { role: 'id' }, placeholder: 'my-fix' }))),
      h('div', { class: 'grow' }, field('code', h('textarea', { data: { role: 'code' }, placeholder: 'console.log("hotfix ran")', spellcheck: false }))),
    ),
    h('div', { class: 'toolbar', style: { marginTop: '8px' } },
      h('button', { class: 'primary', data: { role: 'store' } }, 'STORE'),
      h('label', { style: { marginLeft: '8px' } },
        h('input', { type: 'checkbox', data: { role: 'permanent' } }),
        ' permanent',
      ),
      h('span', { class: 'hint' }, 'Hotfixes replay on every page load. Non-permanent fixes are cleared on extension version bump; permanent fixes survive.'),
    ),
  );
  container.appendChild(root);

  const listEl = root.querySelector('[data-role=list]');
  const verEl = root.querySelector('[data-role=ver]');

  async function load() {
    listEl.innerHTML = '<div class="dim italic small">loading…</div>';
    try {
      const r = await extCmd('list-hotfixes');
      verEl.textContent = 'extension v' + (r && r.version);
      const fixes = (r && r.fixes) || [];
      const permCount = fixes.filter(f => f.permanent).length;
      document.querySelector('#s05 [data-sub]').textContent = `${fixes.length} stored (${permCount} permanent)`;
      clear(listEl);
      if (fixes.length === 0) { listEl.appendChild(h('div', { class: 'dim italic small' }, 'no hotfixes.')); return; }
      const table = h('table', { class: 't' },
        h('thead', {}, h('tr', {},
          h('th', { style: { width: '60px' } }, ''),
          h('th', { style: { width: '180px' } }, 'id'),
          h('th', {}, 'code preview'),
          h('th', { style: { width: '150px' } }, 'stored'),
          h('th', { style: { width: '220px', textAlign: 'right' } }, ''),
        )),
        h('tbody', {}, fixes.map(hf =>
          h('tr', {},
            h('td', {},
              hf.permanent
                ? h('span', { class: 'mono amber', title: 'permanent — survives version bumps' }, 'PERM')
                : h('span', { class: 'dim small' }, '—'),
            ),
            h('td', { class: 'mono amber' }, hf.id),
            h('td', {},
              h('details', { class: 'collapse' },
                h('summary', {}, h('span', { class: 'mono-sm' }, truncate(hf.code.replace(/\s+/g, ' '), 80))),
                h('div', {}, h('pre', { class: 'pane', style: { margin: '0' } }, hf.code)),
              ),
            ),
            h('td', { class: 'dimmer small' }, fmtDateTime(hf.ts)),
            h('td', { style: { textAlign: 'right' } },
              h('button', {
                class: 'ghost sm',
                onclick: armedAction(async () => {
                  try { await extCmd('set-permanent', { fixId: hf.id, permanent: !hf.permanent }); toast(hf.permanent ? 'made temporary' : 'made permanent', 'ok'); load(); }
                  catch (e) { toast(errMsg(e), 'err'); }
                }),
              }, hf.permanent ? 'make temp' : 'make perm'),
              h('button', {
                class: 'ghost sm', onclick: () => {
                  root.querySelector('[data-role=id]').value = hf.id;
                  root.querySelector('[data-role=code]').value = hf.code;
                  root.querySelector('[data-role=permanent]').checked = !!hf.permanent;
                  toast('loaded into form', 'info');
                },
              }, 'edit'),
              h('button', {
                class: 'ghost sm danger',
                onclick: armedAction(async () => {
                  try { await extCmd('clear-hotfix', { fixId: hf.id }); toast('cleared', 'ok'); load(); }
                  catch (e) { toast(errMsg(e), 'err'); }
                }),
              }, 'clear'),
            ),
          )
        )),
      );
      listEl.appendChild(table);
    } catch (e) {
      clear(listEl);
      listEl.appendChild(h('pre', { class: 'pane err' }, errMsg(e)));
    }
  }

  root.querySelector('[data-role=refresh]').addEventListener('click', load);
  root.querySelector('[data-role=clear-all]').addEventListener('click', armedAction(async () => {
    try { await extCmd('clear-all-hotfixes', { includePermanent: false }); toast('cleared (perm kept)', 'ok'); load(); }
    catch (e) { toast(errMsg(e), 'err'); }
  }, { confirmLabel: 'confirm clear all (keep perm)' }));
  root.querySelector('[data-role=clear-all-incl]').addEventListener('click', armedAction(async () => {
    try { await extCmd('clear-all-hotfixes', { includePermanent: true }); toast('all cleared (incl. perm)', 'ok'); load(); }
    catch (e) { toast(errMsg(e), 'err'); }
  }, { confirmLabel: 'CONFIRM CLEAR ALL INCL. PERMANENT' }));
  root.querySelector('[data-role=store]').addEventListener('click', async () => {
    const id = (root.querySelector('[data-role=id]').value || '').trim();
    const code = (root.querySelector('[data-role=code]').value || '').trim();
    const permanent = !!root.querySelector('[data-role=permanent]').checked;
    if (!id || !code) { toast('id + code required', 'warn'); return; }
    try { await extCmd('store-hotfix', { fixId: id, code, permanent }); toast('stored ' + id + (permanent ? ' [perm]' : ''), 'ok'); load(); }
    catch (e) { toast(errMsg(e), 'err'); }
  });

  load();
}
