// Daedalus Extension — page context script (MAIN world)
// Injected via manifest world:"MAIN" — bypasses CSP
// Communicates with content.js via window.postMessage

(function() {
  'use strict';

  const _pending = {};
  let _reqId = 0;

  // ─── Receive responses from content script ───

  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data || e.data.direction !== 'daedalus-bg-to-page') return;
    const msg = e.data;
    const cb = _pending[msg.reqId];
    if (!cb) return;

    if (msg.handler === 'xmlhttpRequest') {
      if (msg.event === 'load') {
        let response = msg.data;
        if (cb._responseType === 'arraybuffer' && typeof response === 'string') {
          // Fast base64 decode: TextEncoder latin1 roundtrip via atob+string
          const binary = atob(response);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
          response = bytes.buffer;
        }
        if (cb.onload) cb.onload({
          status: msg.status,
          statusText: msg.statusText || '',
          responseHeaders: msg.headers || '',
          response: response,
          responseText: typeof response === 'string' ? response : undefined,
          finalUrl: msg.finalUrl || '',
        });
        delete _pending[msg.reqId];
      } else if (msg.event === 'error') {
        if (cb.onerror) cb.onerror({ error: msg.error });
        delete _pending[msg.reqId];
      } else if (msg.event === 'timeout') {
        if (cb.ontimeout) cb.ontimeout({});
        delete _pending[msg.reqId];
      }
    } else if (msg.handler === 'openInTab') {
      delete _pending[msg.reqId];
    } else if (msg.handler === 'getValue') {
      if (msg.error && cb.reject) cb.reject(new Error(msg.error));
      else if (cb.resolve) cb.resolve(msg.value);
      delete _pending[msg.reqId];
    } else if (msg.handler === 'setValue' || msg.handler === 'deleteValue') {
      if (msg.error && cb.reject) cb.reject(new Error(msg.error));
      else if (cb.resolve) cb.resolve();
      delete _pending[msg.reqId];
    } else if (msg.handler === 'listValues') {
      if (msg.error && cb.reject) cb.reject(new Error(msg.error));
      else if (cb.resolve) cb.resolve(msg.keys);
      delete _pending[msg.reqId];
    } else if (msg.handler === 'download') {
      if (msg.event === 'load' && cb.onload) cb.onload();
      if (msg.event === 'error' && cb.onerror) cb.onerror({ error: msg.error });
      delete _pending[msg.reqId];
    } else if (msg.handler === 'setClipboard') {
      if (msg.error && cb.reject) cb.reject(new Error(msg.error));
      else if (cb.resolve) cb.resolve();
      delete _pending[msg.reqId];
    } else {
      // notification, addStyle — fire and forget
      if (cb.resolve) cb.resolve();
      delete _pending[msg.reqId];
    }
  });

  // ─── CSP fallback: blob-URL script injection ───
  // When page CSP forbids 'unsafe-eval', fall back to injecting an external
  // <script src="blob:..."> whose body is the wrapped code. blob: URLs
  // survive:
  //   - 'strict-dynamic' — the loading script (page.js) is extension-trusted,
  //     so scripts it appends via DOM inherit that trust regardless of source.
  //   - Explicit `blob:` in a source-list (mega.nz style).
  // Inline <script>textContent</script> injection is a weaker alternative —
  // rejected by CSPs like mega's that have no strict-dynamic and no
  // 'unsafe-inline'. Blob dominates it, so we don't bother with inline.
  function _isCspEval(err) {
    const msg = (err && (err.message || String(err))) || '';
    return err instanceof EvalError || msg.indexOf('unsafe-eval') !== -1;
  }
  // Agents frequently auto-wrap code in IIFEs like `(async()=>{return X})()`.
  // The default statement-style wrap discards the IIFE's return value. If the
  // (de-semicoloned) code parses as an expression inside an async function,
  // we emit a `return (…)` wrap so the inner value (Promise or sync) propagates.
  // Tested in async context so top-level `await` and async-IIFEs both pass.
  function _isExpression(codeExpr) {
    try { new Function('return (async()=>{return (' + codeExpr + ')})()'); return true; }
    catch (_) { return false; }
  }
  function _stripTrailing(code) {
    return code.replace(/[\s;]+$/, '');
  }
  function _wrap(code, key, hasAwait, hasReturn) {
    const codeExpr = _stripTrailing(code);
    const isExpr = _isExpression(codeExpr);
    if (hasAwait) {
      if (isExpr) {
        return "(async()=>{try{const __r=await(async()=>{return (" + codeExpr + ")})();window['" + key + "']={r:__r}}catch(e){window['" + key + "']={e:e.message||String(e)}}})()";
      }
      return "(async()=>{try{const __r=await(async()=>{" + code + "})();window['" + key + "']={r:__r}}catch(e){window['" + key + "']={e:e.message||String(e)}}})()";
    } else if (hasReturn) {
      if (isExpr) {
        return "try{window['" + key + "']={r:(" + codeExpr + ")}}catch(e){window['" + key + "']={e:e.message||String(e)}}";
      }
      return "try{window['" + key + "']={r:(()=>{" + code + "})()}}catch(e){window['" + key + "']={e:e.message||String(e)}}";
    } else {
      return "try{window['" + key + "']={r:(" + code + ")}}catch(e){window['" + key + "']={e:e.message||String(e)}}";
    }
  }

  // ─── Receive eval commands from content script ───

  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data || e.data.direction !== 'daedalus-eval') return;
    const { id, relayId, code } = e.data;
    const key = '__daedalus_' + relayId;
    const hasAwait = code.includes('await');
    const hasReturn = /\breturn\b/.test(code);
    const t0 = performance.now();

    const postR = (r) => window.postMessage({ direction: 'daedalus-eval-result', id, relayId, r, ms: +(performance.now() - t0).toFixed(1) }, '*');
    const postE = (err) => window.postMessage({ direction: 'daedalus-eval-result', id, relayId, e: (err && (err.message || String(err))), ms: +(performance.now() - t0).toFixed(1) }, '*');

    const runCspFallback = () => {
      const wrapped = _wrap(code, key, hasAwait, hasReturn);
      let done = false;
      let cspBlocked = false;
      const cspListener = (ev) => {
        if (ev && ev.effectiveDirective && ev.effectiveDirective.indexOf('script') !== -1) {
          cspBlocked = true;
        }
      };
      document.addEventListener('securitypolicyviolation', cspListener);
      const finish = (err, result) => {
        if (done) return;
        done = true;
        try { delete window[key]; } catch (_) {}
        document.removeEventListener('securitypolicyviolation', cspListener);
        if (err) postE(err); else postR(result);
      };
      let blobUrl;
      try {
        const blob = new Blob([wrapped], { type: 'application/javascript' });
        blobUrl = URL.createObjectURL(blob);
        const s = document.createElement('script');
        s.src = blobUrl;
        s.onload = () => { try { URL.revokeObjectURL(blobUrl); } catch (_) {} s.remove(); };
        s.onerror = () => { try { URL.revokeObjectURL(blobUrl); } catch (_) {} s.remove(); };
        (document.head || document.documentElement).appendChild(s);
      } catch (err) {
        finish({ message: 'CSP-fallback setup: ' + (err && (err.message || String(err))) });
        return;
      }
      const start = Date.now();
      const timeoutMs = hasAwait ? 10000 : 3000;
      const tick = () => {
        if (done) return;
        const v = window[key];
        if (v && ('r' in v || 'e' in v)) {
          if ('e' in v) return finish({ message: v.e });
          return finish(null, v.r);
        }
        if (cspBlocked && Date.now() - start > 200) {
          return finish({ message: 'CSP blocks eval and blob: scripts — use cdp tool' });
        }
        if (Date.now() - start > timeoutMs) {
          return finish({ message: 'CSP-fallback timeout — use cdp tool' });
        }
        setTimeout(tick, 10);
      };
      tick();
    };

    const codeExpr = _stripTrailing(code);
    const isExpr = _isExpression(codeExpr);

    try {
      if (hasAwait) {
        const body = isExpr
          ? 'return (async()=>{return (' + codeExpr + ')})()'
          : 'return (async()=>{' + code + '})()';
        const fn = new Function(body);
        fn().then(postR, postE);
      } else if (hasReturn) {
        if (isExpr) {
          try { postR((new Function('return (' + codeExpr + ')'))()); }
          catch (err) { if (_isCspEval(err)) return runCspFallback(); postE(err); }
        } else {
          try { postR((new Function(code))()); }
          catch (err) { if (_isCspEval(err)) return runCspFallback(); postE(err); }
        }
      } else {
        try { postR(eval(code)); }
        catch (err) { if (_isCspEval(err)) return runCspFallback(); postE(err); }
      }
    } catch (err) {
      if (_isCspEval(err)) return runCspFallback();
      postE(err);
    }
  });

  // ─── Post to content script ───

  function gmPost(handler, detail) {
    const reqId = ++_reqId;
    detail.reqId = reqId;
    detail.handler = handler;
    detail.direction = 'daedalus-page-to-bg';
    window.postMessage(detail, '*');
    return reqId;
  }

  // ─── GM API ───

  // Fast base64 encode. Prefers native Uint8Array.prototype.toBase64 (TC39,
  // Chrome 137+). Falls back to chunked String.fromCharCode.apply + array join.
  // NOT TextDecoder('latin1') — that's actually windows-1252 per WHATWG.
  const _hasNativeToBase64 = typeof Uint8Array.prototype.toBase64 === 'function';
  function bytesToBase64(bytes) {
    if (_hasNativeToBase64) return bytes.toBase64();
    const parts = [];
    const CHUNK = 32768;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK)));
    }
    return btoa(parts.join(''));
  }

  window.GM = {
    xmlhttpRequest: function(opts) {
      let body = opts.data || null;
      let bodyIsBase64 = false;
      // ArrayBuffer/TypedArray can't survive chrome.runtime.sendMessage (JSON serialization).
      // Encode as base64 via chunked fromCharCode (fast path).
      if (body && (body instanceof ArrayBuffer || ArrayBuffer.isView(body))) {
        const bytes = body instanceof ArrayBuffer ? new Uint8Array(body) : new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
        body = bytesToBase64(bytes);
        bodyIsBase64 = true;
      }
      const reqId = gmPost('xmlhttpRequest', {
        url: opts.url,
        method: opts.method || 'GET',
        headers: opts.headers || {},
        data: body,
        bodyIsBase64: bodyIsBase64,
        responseType: opts.responseType || 'text',
        timeout: opts.timeout || 0,
        // Opt-in only: omitted, the relay applies its conservative default.
        // A value above the relay's ceiling is clamped there, not honoured.
        maxResponseBytes: opts.maxResponseBytes || 0,
      });
      opts._responseType = opts.responseType;
      _pending[reqId] = opts;
      let aborted = false;
      return {
        // Terminal on this side the moment it is called: the pending entry is
        // dropped, so a load or error already in flight finds no callback and
        // is ignored, and the relay cancels the fetch itself so the request
        // stops rather than merely stopping being listened to. Idempotent —
        // a second call, or one after the request already settled, has
        // nothing left to cancel and says nothing to the relay.
        abort: function() {
          if (aborted || !_pending[reqId]) return;
          aborted = true;
          const handlers = _pending[reqId];
          delete _pending[reqId];
          gmPost('abortRequest', { target: reqId });
          if (handlers.onabort) handlers.onabort({});
        },
      };
    },

    openInTab: function(url, opts) {
      const active = (opts && typeof opts === 'object') ? opts.active !== false :
                     (typeof opts === 'boolean') ? !opts : true;
      gmPost('openInTab', { url, active });
    },

    getValue: function(key, defaultValue) {
      return new Promise((resolve, reject) => {
        const reqId = gmPost('getValue', { key, defaultValue });
        _pending[reqId] = { resolve, reject };
      });
    },

    setValue: function(key, value) {
      return new Promise((resolve, reject) => {
        const reqId = gmPost('setValue', { key, value });
        _pending[reqId] = { resolve, reject };
      });
    },

    deleteValue: function(key) {
      return new Promise((resolve, reject) => {
        const reqId = gmPost('deleteValue', { key });
        _pending[reqId] = { resolve, reject };
      });
    },

    listValues: function() {
      return new Promise((resolve, reject) => {
        const reqId = gmPost('listValues', {});
        _pending[reqId] = { resolve, reject };
      });
    },

    addStyle: function(css) {
      const style = document.createElement('style');
      style.textContent = css;
      (document.head || document.documentElement).appendChild(style);
    },

    setClipboard: function(text, type) {
      // A promise, because the write can be refused and the caller has no
      // other way to find out.
      return new Promise((resolve, reject) => {
        const reqId = gmPost('setClipboard', { text, type: type || 'text/plain' });
        _pending[reqId] = { resolve, reject };
      });
    },

    notification: function(opts) {
      const detail = typeof opts === 'string'
        ? { text: opts } : { title: opts.title, text: opts.text || opts.body };
      gmPost('notification', detail);
    },

    download: function(opts) {
      const reqId = gmPost('download', {
        url: typeof opts === 'string' ? opts : opts.url,
        name: (typeof opts === 'object' ? opts.name : null) || 'download',
      });
      if (typeof opts === 'object') _pending[reqId] = opts;
    },

    info: { script: { version: '0.24.0' }, scriptHandler: 'Daedalus' },
  };

  console.log('[Daedalus] GM bridge v' + window.GM.info.script.version + ' ready');
})();
