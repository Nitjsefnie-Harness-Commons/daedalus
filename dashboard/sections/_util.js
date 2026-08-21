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
    host.style.cssText = 'position:fixed;top:54px;right:18px;z-index:100;display:flex;flex-direction:column;gap:6px;pointer-events:none;max-width:380px;';
    document.body.appendChild(host);
  }
  const el = h('div', {
    class: 'chip ' + type,
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
export function inlineEdit(cell, initial, originalText, onSubmit, { type = 'text' } = {}) {
  const input = h('input', {
    type, value: initial,
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

// Tiny debounce helper.
export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
