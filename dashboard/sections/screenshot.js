// §03 SCREENSHOT — capture + latest image viewer per tab.

import { h, clear, fmtSize, fmtDateTime, truncate, errMsg, toast, bindTabSelector } from './_util.js';
import { api, extCmd, getToken, nextId, getServer } from '../api.js';

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', {},
        h('label', {}, 'tab'),
        h('select', { data: { role: 'tab' }, style: { minWidth: '260px' } },
          h('option', { value: '' }, '(active tab)'),
        ),
      ),
      h('div', {},
        h('label', {}, 'format'),
        h('select', { data: { role: 'fmt' } },
          h('option', { value: 'png' }, 'png'),
          h('option', { value: 'jpeg' }, 'jpeg'),
        ),
      ),
      h('div', {},
        h('label', {}, 'quality (jpeg)'),
        h('input', { type: 'number', value: '80', min: '1', max: '100', data: { role: 'q' }, style: { width: '64px' } }),
      ),
      h('div', {},
        h('label', {}, '\u00a0'),
        h('button', { class: 'primary', data: { role: 'capture' } }, 'CAPTURE'),
      ),
    ),
    h('div', { class: 'dim small', data: { role: 'meta' }, style: { marginTop: '10px' } }, 'no capture yet.'),
    h('div', { data: { role: 'image' }, style: { marginTop: '10px' } }),
    h('div', { class: 'divider' }),
    h('div', { class: 'small dim', style: { marginBottom: '8px' } }, 'RECENT CAPTURES'),
    h('div', { data: { role: 'recent' } }, h('div', { class: 'dim italic small' }, 'loading…')),
  );
  container.appendChild(root);

  const tabSel = root.querySelector('[data-role=tab]');
  const fmtSel = root.querySelector('[data-role=fmt]');
  const qEl = root.querySelector('[data-role=q]');
  const captureBtn = root.querySelector('[data-role=capture]');
  const meta = root.querySelector('[data-role=meta]');
  const imgHost = root.querySelector('[data-role=image]');
  const recent = root.querySelector('[data-role=recent]');

  bindTabSelector(tabSel, {
    getToken, api, bus, placeholder: '(active tab)',
  });

  captureBtn.addEventListener('click', async () => {
    captureBtn.disabled = true;
    meta.textContent = 'capturing…';
    const fields = { format: fmtSel.value };
    if (fmtSel.value === 'jpeg') fields.quality = Math.max(1, Math.min(100, Number(qEl.value) || 80));
    if (tabSel.value) fields.tabId = Number(tabSel.value);
    const id = nextId('ss');
    try {
      const r = await extCmd('screenshot', fields, { id, timeout: 20000 });
      const imgUrl = (getServer() || '') + '/screenshot?token=' + encodeURIComponent(getToken()) + '&id=' + encodeURIComponent(id) + '&t=' + Date.now();
      // r.tabUrl is captured-tab data: text nodes only, never innerHTML.
      clear(meta);
      meta.append(
        h('span', { class: 'cyan' }, truncate(r.tabUrl || '', 80)),
        `  ·  ${fmtSize(r.size)}  ·  ${r.format}  ·  `,
        h('code', {}, String(r.path)),
        '  ·  ',
        h('a', { href: imgUrl, target: '_blank', rel: 'noopener' }, 'open'),
      );
      clear(imgHost);
      imgHost.appendChild(
        h('a', { href: imgUrl, target: '_blank', rel: 'noopener', title: 'open full-size in new tab', style: { display: 'block', cursor: 'zoom-in' } },
          h('img', { src: imgUrl, style: { maxWidth: '100%', border: '1px solid var(--border)', display: 'block' } }),
        ),
      );
      loadRecent();
    } catch (e) {
      clear(meta);
      meta.append(h('span', { class: 'red' }, errMsg(e)));
      toast(errMsg(e), 'err');
    } finally {
      captureBtn.disabled = false;
    }
  });

  async function loadRecent() {
    const token = getToken();
    if (!token) return;
    try {
      const resp = await api.get('/upload?token=' + encodeURIComponent(token) + '&limit=200&offset=0');
      const files = (resp.items || []).filter(f => /\.(png|jpe?g)$/i.test(f.filename));
      clear(recent);
      if (files.length === 0) { recent.appendChild(h('div', { class: 'dim italic small' }, 'no screenshots.')); return; }
      const grid = h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '8px' } });
      for (const f of files.slice(0, 24)) {
        const url = (getServer() || '') + '/screenshot?token=' + encodeURIComponent(token) + '&id=' + encodeURIComponent(f.id);
        grid.appendChild(h('a', { href: url, target: '_blank', rel: 'noopener', style: { display: 'block', border: '1px solid var(--border)', padding: '4px', background: 'var(--bg)' } },
          h('img', { src: url, style: { width: '100%', display: 'block' } }),
          h('div', { class: 'dimmer small', style: { marginTop: '4px' } }, truncate(f.id, 22)),
          h('div', { class: 'dimmer small' }, `${fmtSize(f.size)}  ${fmtDateTime(f.mtime)}`),
        ));
      }
      recent.appendChild(grid);
    } catch (e) {
      recent.textContent = '';
      recent.appendChild(h('div', { class: 'red small' }, errMsg(e)));
    }
  }
  loadRecent();
}
