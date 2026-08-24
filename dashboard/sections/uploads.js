// §11 UPLOADS — paged browser of $DAEDALUS_DIR/uploads/<token>/…

import { h, clear, fmtSize, fmtDateTime, truncate, errMsg, toast, armedAction } from './_util.js';
import { api, getToken, getServer } from '../api.js';

const PAGE_SIZE = 50;

export function mount(container) {
  const root = h('div', {},
    h('div', { class: 'toolbar' },
      h('button', { data: { role: 'refresh' } }, 'refresh'),
      h('input', { type: 'text', 'aria-label': 'filter uploads by id or filename', data: { role: 'filter' }, placeholder: 'filter id/filename…', style: { minWidth: '220px' } }),
      h('button', { class: 'ghost sm danger', data: { role: 'clear-all' } }, 'clear all'),
      h('span', { style: { flex: '1' } }),
      h('span', { class: 'small dim', role: 'status', data: { role: 'meta' } }, ''),
      h('button', { class: 'ghost sm', data: { role: 'prev' } }, '← prev'),
      h('button', { class: 'ghost sm', data: { role: 'next' } }, 'next →'),
    ),
    h('div', { data: { role: 'list' } }, h('div', { class: 'dim italic small' }, 'loading…')),
  );
  container.appendChild(root);

  const listEl = root.querySelector('[data-role=list]');
  const metaEl = root.querySelector('[data-role=meta]');
  const filterEl = root.querySelector('[data-role=filter]');
  const prevBtn = root.querySelector('[data-role=prev]');
  const nextBtn = root.querySelector('[data-role=next]');

  let offset = 0;
  let total = 0;
  let items = [];

  async function load() {
    const token = getToken();
    if (!token) { listEl.innerHTML = '<div class="dim italic small">no token.</div>'; return; }
    listEl.innerHTML = '<div class="dim italic small">loading…</div>';
    try {
      const r = await api.get(`/upload?limit=${PAGE_SIZE}&offset=${offset}`);
      items = r.items || [];
      total = r.total || 0;
      render();
    } catch (e) {
      clear(listEl);
      listEl.appendChild(h('pre', { class: 'pane err' }, errMsg(e)));
    }
  }

  function render() {
    const q = (filterEl.value || '').toLowerCase();
    const visible = q ? items.filter(f => (f.id + '/' + f.filename).toLowerCase().includes(q)) : items;
    document.querySelector('#s11 [data-sub]').textContent = `${total} total`;
    metaEl.textContent = `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} / ${total}`;
    prevBtn.disabled = offset === 0;
    nextBtn.disabled = offset + PAGE_SIZE >= total;
    clear(listEl);
    if (visible.length === 0) { listEl.appendChild(h('div', { class: 'dim italic small' }, total === 0 ? 'no uploads.' : 'no matches on this page.')); return; }
    const table = h('table', { class: 't' },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '28%' } }, 'id'),
        h('th', {}, 'filename'),
        h('th', { style: { width: '90px' } }, 'size'),
        h('th', { style: { width: '160px' } }, 'mtime'),
        h('th', { style: { width: '160px', textAlign: 'right' } }, ''),
      )),
      h('tbody', {}, visible.map(fileRow)),
    );
    listEl.appendChild(table);
  }

  function fileRow(f) {
    const server = getServer() || '';
    // Served by the deployment's reverse proxy, not by the bridge:
    // server.py has no /uploads/ route. See "Deployment" in README.md.
    const dlUrl = `${server}/uploads/${f.path}`;
    const isImage = /\.(png|jpe?g|gif|webp)$/i.test(f.filename);
    // The proxy-served path, not /screenshot?token=…: a preview link is a URL
    // the browser keeps in its history, and this one names the exact file the
    // row describes rather than whichever capture under that id finished last.
    const previewUrl = isImage ? dlUrl : '';
    return h('tr', {},
      h('td', {},
        h('span', { class: 'mono amber', title: f.id }, truncate(f.id, 32)),
        h('button', {
          class: 'ghost sm danger',
          style: { marginLeft: '6px' },
          title: 'remove all files under this id',
          onclick: armedAction(async () => {
            try {
              await api.del('/upload', { token: getToken(), id: f.id });
              toast('removed id ' + truncate(f.id, 20), 'ok'); load();
            } catch (e) { toast(errMsg(e), 'err'); }
          }, { confirmLabel: 'clear id?' }),
        }, '× id'),
      ),
      h('td', {},
        h('a', { href: dlUrl, target: '_blank', rel: 'noopener', class: 'mono-sm' }, f.filename),
        isImage && h('div', { class: 'dimmer small' }, h('a', { href: previewUrl, target: '_blank', rel: 'noopener' }, 'preview')),
      ),
      h('td', { class: 'num' }, fmtSize(f.size)),
      h('td', { class: 'dimmer small' }, fmtDateTime(f.mtime)),
      h('td', { style: { textAlign: 'right' } },
        h('a', { href: dlUrl, download: f.filename, class: 'btn sm ghost', style: { textDecoration: 'none' } }, 'download'),
        h('button', {
          class: 'ghost sm danger',
          onclick: armedAction(async () => {
            try {
              await api.del('/upload', { token: getToken(), id: f.id, filename: f.filename });
              toast('deleted', 'ok'); load();
            } catch (e) { toast(errMsg(e), 'err'); }
          }),
        }, 'delete'),
      ),
    );
  }

  prevBtn.addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); load(); });
  nextBtn.addEventListener('click', () => { offset = offset + PAGE_SIZE; load(); });
  root.querySelector('[data-role=refresh]').addEventListener('click', () => { offset = 0; load(); });
  filterEl.addEventListener('input', render);

  root.querySelector('[data-role=clear-all]').addEventListener('click', armedAction(async () => {
    const token = getToken();
    if (!token) { toast('no token', 'warn'); return; }
    try {
      await api.del('/upload', { token });
      toast('all uploads cleared', 'ok');
      offset = 0; load();
    } catch (e) { toast(errMsg(e), 'err'); }
  }, { confirmLabel: 'confirm wipe everything' }));

  load();
}
