// Dashboard section — shared DOM + formatting helpers.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'data' && typeof v === 'object') for (const dk of Object.keys(v)) el.dataset[dk] = v[dk];
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value' || k === 'checked' || k === 'disabled') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  appendAll(el, children);
  return el;
}

function appendAll(target, items) {
  for (const c of items) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) { appendAll(target, c); continue; }
    if (typeof c === 'string' || typeof c === 'number') target.appendChild(document.createTextNode(String(c)));
    else if (c instanceof Node) target.appendChild(c);
  }
}

// One tab-<select> controller for every section that offers a tab target.
//
// Five near-copies existed, and four of them refreshed on `tabs-synced`
// alone. The dashboard also emits `tab-updated` and `tab-unregistered`, so a
// tab that had been retitled or had gone away stayed offered — and selectable
// — until the next full sync happened to arrive.
//
// A selection survives a refresh only while its tab is still listed. Keeping
// a value that has gone is what turns a stale picker into a command aimed at
// a target that no longer exists.
//
// `placeholder` is the empty-value option's label, for sections where "no tab
// named" means the active one. Sections without a placeholder say so when the
// list is empty instead, because there an empty select offers nothing at all.
export function bindTabSelector(select, options) {
  const { getToken, api, bus, placeholder = null,
          emptyLabel = null, errorLabel = null, interval = 0 } = options;

  function only(label) {
    clear(select);
    select.appendChild(h('option', { value: '' }, label));
  }

  async function populate() {
    const token = getToken();
    if (!token) {
      if (emptyLabel !== null) only('(no token)');
      return;
    }
    let tabs;
    try {
      tabs = await api.get('/tabs?token=' + encodeURIComponent(token));
    } catch (e) {
      if (errorLabel) only(errorLabel(e));
      return;
    }
    const current = select.value;
    clear(select);
    if (placeholder !== null) {
      select.appendChild(h('option', { value: '' }, placeholder));
    }
    for (const t of tabs) {
      select.appendChild(h('option', { value: t.tabId },
        `${t.tabId}  ${truncate(t.title || t.url || '', 60)}`));
    }
    if (tabs.length === 0 && placeholder === null && emptyLabel !== null) {
      select.appendChild(h('option', { value: '' }, emptyLabel));
    }
    // Only if it is still on offer. Clearing it otherwise is the point:
    // a value naming a tab that has gone is what aims the next command at a
    // target that no longer exists. A browser resets a select whose selected
    // option is removed, but saying so here does not depend on that.
    if (current && Array.from(select.options).some((o) => o.value === current)) {
      select.value = current;
    } else if (current) {
      select.value = '';
    }
  }

  populate();
  bus.on((ev) => {
    if (ev.__internal) return;
    if (ev.type === 'tabs-synced' || ev.type === 'tab-updated'
        || ev.type === 'tab-unregistered') populate();
  });
  if (interval) setInterval(populate, interval);
  return populate;
}

// Label a control, and associate the two.
//
// Every section renders into one document, so a `for`/`id` pair only works
// when the id is unique document-wide; a counter is what guarantees that
// without each section inventing a naming scheme of its own. A sibling
// <label> with no `for` is not a label at all — the accessibility tree showed
// thirty controls with no name and fourteen with an empty one.
let fieldSeq = 0;

export function field(text, control, attrs) {
  if (!control.id) {
    fieldSeq += 1;
    control.id = 'dfx-' + fieldSeq;
  }
  return [h('label', { ...(attrs || {}), for: control.id }, text), control];
}

// The blank cell that keeps a button's top edge aligned with the inputs
// beside it. It was written as an empty <label>, which announces a nameless
// label and associates with nothing; it is presentation, so it says so.
export function spacer() {
  return h('span', { class: 'label-spacer', 'aria-hidden': 'true' }, '\u00a0');
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export function fmtTime(ms) {
  if (!ms) return '—';
  const d = new Date(typeof ms === 'number' ? ms : Date.parse(ms));
  return d.toTimeString().slice(0, 8);
}

export function fmtDateTime(ms) {
  if (!ms) return '—';
  const d = new Date(typeof ms === 'number' ? ms * (ms < 1e12 ? 1000 : 1) : Date.parse(ms));
  return d.toISOString().replace('T', ' ').slice(0, 19);
}

export function fmtSize(n) {
  if (n == null) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB';
}

export function fmtAge(sec) {
  if (sec == null) return '—';
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  return Math.floor(sec / 3600) + 'h';
}

export function truncate(s, n) {
  if (!s) return '';
  s = String(s);
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function errMsg(e) {
  return e && e.message ? e.message : String(e);
}

export function formatEvalWorld(world) {
  return world ? `channel=${world}` : '';
}

export function toast(msg, type = 'info') {
  let host = document.getElementById('daedalus-toasts');
  if (!host) {
    host = document.createElement('div');
    host.id = 'daedalus-toasts';
    // The audit found no live region anywhere on the page, so every toast
    // was invisible to a screen reader. Polite for the host; an error names
    // itself an alert so it interrupts rather than queues behind a success.
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    host.style.cssText = 'position:fixed;top:54px;right:18px;z-index:100;display:flex;flex-direction:column;gap:6px;pointer-events:none;max-width:380px;';
    document.body.appendChild(host);
  }
  const el = h('div', {
    class: 'chip ' + type,
    role: type === 'err' ? 'alert' : null,
    style: {
      padding: '6px 12px',
      background: 'var(--panel)',
      border: '1px solid var(--border-2)',
      pointerEvents: 'auto',
      fontSize: '11px',
      textTransform: 'none',
      letterSpacing: '0.02em',
    },
  }, msg);
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity 300ms';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 300);
  }, 2600);
}

// Return a short JSON preview suitable for inline display.
export function preview(v, max = 80) {
  if (v == null) return 'null';
  if (typeof v === 'string') return truncate(JSON.stringify(v), max);
  try { return truncate(JSON.stringify(v), max); }
  catch { return truncate(String(v), max); }
}

// Pretty-print a value for a <pre> pane.
export function pretty(v) {
  if (v === undefined) return '(undefined)';
  if (v === null) return 'null';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); }
  catch { return String(v); }
}

// Wrap a click handler with a two-step "armed" confirm: first click recolors
// the button and changes its label; second click (within `timeout` ms) runs
// the handler. No modal dialogs — the button itself carries the state.
//   <button onclick={armedAction(() => doIt(), 'sure?')}>delete</button>
export function armedAction(handler, { confirmLabel = 'sure?', timeout = 2500 } = {}) {
  let armed = false;
  let resetTimer = null;
  return function(e) {
    const btn = e.currentTarget;
    if (!armed) {
      armed = true;
      btn.dataset.original = btn.dataset.original || btn.textContent;
      btn.textContent = confirmLabel;
      btn.classList.add('armed');
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        armed = false;
        btn.textContent = btn.dataset.original || btn.textContent;
        btn.classList.remove('armed');
      }, timeout);
      return;
    }
    clearTimeout(resetTimer);
    armed = false;
    btn.textContent = btn.dataset.original || btn.textContent;
    btn.classList.remove('armed');
    return handler(e);
  };
}

// Swap a cell's content for a single-line input. Enter submits via onSubmit
// (called with the trimmed string); Escape or blur restores `originalText`.
// Returns nothing — the caller owns the cell element.
export function inlineEdit(cell, initial, originalText, onSubmit,
                           { type = 'text', label = 'edit' } = {}) {
  const input = h('input', {
    // No visible label can exist here — the input replaces the cell it edits
    // — so the name has to be carried on the control itself.
    type, value: initial, 'aria-label': label,
    style: { width: '100%', fontSize: '11px', padding: '2px 4px', background: 'var(--bg)' },
  });
  let done = false;
  function restore() {
    if (done) return;
    done = true;
    cell.textContent = originalText;
  }
  input.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = (input.value || '').trim();
      done = true;
      if (v) {
        try { await onSubmit(v); }
        catch (err) { /* caller toasts */ }
      }
      cell.textContent = originalText;
    } else if (e.key === 'Escape') {
      restore();
    }
  });
  input.addEventListener('blur', restore);
  cell.textContent = '';
  cell.appendChild(input);
  input.focus();
  input.select();
}

