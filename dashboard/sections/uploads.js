// §11 UPLOADS — paged browser of $DAEDALUS_DIR/uploads/<token>/…

import { h, clear, fmtSize, fmtDateTime, truncate, errMsg, toast, armedAction } from './_util.js';
import { api, getToken, objectUrl } from '../api.js';

const PAGE_SIZE = 50;
// The types the bridge stores as screenshots, which an <img> can show.
const PREVIEWABLE = /\.(png|jpe?g|webp)$/i;

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

  // An <a href> cannot carry an Authorization header, so a file is fetched
  // with one and handed to the browser as an object URL, the way §03 shows
  // a screenshot. Links used to name /uploads/<path>, a route the bridge
  // does not have, and since the listed path begins with the token every
  // click both 404ed and wrote the credential into browser history and
  // the proxy access log. Fetched on demand rather than with the listing:
  // a page holds fifty arbitrary files, and fetching each one up front is
  // a download of the page. One fetch per file serves both its preview and
  // its download, and the page's URLs are revoked when its rows go.
  let held = new Map();

  function release() {
    for (const pending of held.values()) {
      pending.then((url) => URL.revokeObjectURL(url), () => {});
    }
    held = new Map();
  }

  function fileUrl(f) {
    if (!held.has(f.path)) {
      // `<id>/<file>`, relative to the namespace the Bearer header
      // establishes, and never the listed path: that one begins with the
      // token, and a request target reaches the proxy access log whether
      // or not a link carried it. encodeURIComponent, because `#`, `%`
      // and `&` are legal in a filename and each would end or split the
      // query otherwise.
      const selector = encodeURIComponent(f.id + '/' + f.filename);
      const fetched = objectUrl('/upload?path=' + selector)
        .catch((e) => {
          // Only this fetch's own entry: a re-render may already hold a
          // newer fetch for the same path, and deleting that one would
          // leave its object URL out of every release() to come.
          if (held.get(f.path) === fetched) held.delete(f.path);
          throw e;
        });
      held.set(f.path, fetched);
    }
    return held.get(f.path);
  }

  async function download(f) {
    try {
      const url = await fileUrl(f);
      // A synthesized anchor: the object URL exists only once the fetch
      // has settled, so a rendered href could never have carried it.
      const anchor = h('a', { href: url, download: f.filename });
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (e) { toast(errMsg(e), 'err'); }
  }

  async function preview(f, cell, button) {
    button.disabled = true;
    try {
      const url = await fileUrl(f);
      button.remove();
      cell.appendChild(
        h('a', { href: url, target: '_blank', rel: 'noopener', title: 'open full-size in new tab', style: { display: 'block', cursor: 'zoom-in', marginTop: '4px' } },
          h('img', { src: url, alt: 'Preview of ' + f.filename, style: { maxWidth: '320px', border: '1px solid var(--border)', display: 'block' } }),
        ),
      );
    } catch (e) {
      button.disabled = false;
      toast(errMsg(e), 'err');
    }
  }

  async function load() {
    const token = getToken();
    // The rows go either way from here, so what they held goes first: a
    // refresh with no token, or one whose listing failed, used to replace
    // them with a notice and leave every object URL of the page alive.
    release();
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
    release();
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
    const nameCell = h('td', {}, h('span', { class: 'mono-sm' }, f.filename));
    if (PREVIEWABLE.test(f.filename)) {
      const button = h('button', { class: 'ghost sm', style: { marginLeft: '6px' } }, 'preview');
      button.addEventListener('click', () => preview(f, nameCell, button));
      nameCell.appendChild(button);
    }
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
      nameCell,
      h('td', { class: 'num' }, fmtSize(f.size)),
      h('td', { class: 'dimmer small' }, fmtDateTime(f.mtime)),
      h('td', { style: { textAlign: 'right' } },
        h('button', { class: 'ghost sm', onclick: () => download(f) }, 'download'),
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
