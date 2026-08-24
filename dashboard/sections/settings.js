// §12 SETTINGS — token, server URL, extension reload, broadcast caveat.

import { h, field, spacer, clear, toast, errMsg, armedAction } from './_util.js';
import { getToken, setToken, getServer, setServer, extCmd, api } from '../api.js';
import { restart as restartSse } from '../sse.js';

export function mount(container, bus) {
  const root = h('div', {},
    h('div', { class: 'row' },
      h('div', { class: 'grow' },
        field('bridge token',
          h('input', { type: 'password', value: getToken(), data: { role: 'token' }, placeholder: 'paste from Chrome extension options page', autocomplete: 'off' })),
      ),
      h('div', {},
        spacer(),
        h('button', { class: 'ghost sm', data: { role: 'toggle-visible' } }, 'show'),
      ),
    ),
    h('div', { class: 'row', style: { marginTop: '10px' } },
      h('div', { class: 'grow' },
        field('server url',
          h('input', { type: 'url', value: getServer(), data: { role: 'server' }, placeholder: '(blank = same origin)' })),
      ),
    ),
    h('div', { class: 'toolbar', style: { marginTop: '12px' } },
      h('button', { class: 'primary', data: { role: 'save' } }, 'save + reconnect'),
      h('button', { class: 'ghost sm', data: { role: 'ping' } }, 'ping server'),
      h('button', { class: 'ghost sm danger', data: { role: 'ext-reload' } }, 'ext-reload'),
      h('span', { style: { flex: '1' } }),
      h('span', { class: 'dim small', role: 'status', data: { role: 'status' } }, ''),
    ),
    h('div', { class: 'divider' }),
    h('div', { class: 'dim small' },
      h('p', {}, h('b', {}, 'Token:'), ' single shared secret between the extension and server. Get it from the Chrome extension\'s options page (puzzle icon → Daedalus → Options). Stored in ',
        h('code', {}, 'localStorage.daedalus-token'), '.'),
      h('p', {}, h('b', {}, 'Server:'), ' leave blank to hit the same origin that serves this page. Useful if you point the dashboard at a different host.'),
      h('p', { class: 'amber' }, h('b', {}, 'Caveat:'), ' the Daedalus extension injects ',
        h('code', {}, 'content.js + page.js'),
        ' into every URL including this dashboard. Broadcast eval commands (',
        h('code', {}, 'exec -b'),
        ') run inside this tab too — use per-tab targeting or close the dashboard before broadcasting disruptive code (e.g. ',
        h('code', {}, 'location.reload()'),
        ').'),
    ),
  );
  container.appendChild(root);

  const tokenEl = root.querySelector('[data-role=token]');
  const serverEl = root.querySelector('[data-role=server]');
  const statusEl = root.querySelector('[data-role=status]');
  const toggleBtn = root.querySelector('[data-role=toggle-visible]');

  // Status is built from nodes, never markup: the text can carry an error
  // string the dashboard did not author.
  function setStatus(...parts) {
    clear(statusEl);
    statusEl.append(...parts);
  }

  toggleBtn.addEventListener('click', () => {
    const t = tokenEl.type === 'password' ? 'text' : 'password';
    tokenEl.type = t;
    toggleBtn.textContent = t === 'password' ? 'show' : 'hide';
  });

  root.querySelector('[data-role=save]').addEventListener('click', async () => {
    const tok = (tokenEl.value || '').trim();
    const srv = (serverEl.value || '').trim();
    if (!tok) { toast('token is empty', 'warn'); return; }
    setToken(tok);
    setServer(srv);
    statusEl.textContent = 'saved — verifying…';
    // Update top bar meta
    for (const el of document.querySelectorAll('[data-meta=token]')) el.textContent = tok.slice(0, 8) + '…' + tok.slice(-4);
    for (const el of document.querySelectorAll('[data-meta=token-short]')) el.textContent = tok.slice(0, 8) + '…';
    for (const el of document.querySelectorAll('[data-meta=server]')) el.textContent = srv || '(same origin)';
    try {
      await api.get('/tabs');
      setStatus(h('span', { class: 'green' }, 'connected'));
      toast('settings saved', 'ok');
      restartSse();
    } catch (e) {
      setStatus(h('span', { class: 'red' }, 'server unreachable: ' + errMsg(e)));
    }
  });

  root.querySelector('[data-role=ping]').addEventListener('click', async () => {
    const tok = getToken();
    if (!tok) { toast('save a token first', 'warn'); return; }
    const t0 = Date.now();
    try {
      const tabs = await api.get('/tabs');
      setStatus(h('span', { class: 'green' }, 'ok'),
                `  ${tabs.length} tabs  ${Date.now() - t0}ms`);
    } catch (e) {
      setStatus(h('span', { class: 'red' }, errMsg(e)));
    }
  });

  root.querySelector('[data-role=ext-reload]').addEventListener('click', armedAction(async () => {
    try {
      const r = await extCmd('ext-reload', {}, { timeout: 5000 });
      toast('extension reloading from v' + (r && r.version), 'ok');
    } catch (e) {
      toast(errMsg(e), 'err');
    }
  }, { confirmLabel: 'confirm reload' }));
}
