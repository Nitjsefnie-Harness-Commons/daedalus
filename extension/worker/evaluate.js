/* exported _takeEvalRelay, _canUseMainWorldEval */
/* exported _executeMainWorldEval, handleEval */
/* global _cdpSessions, _netCaptures, _releaseCdpObjects */
/* global _cdpError, _cdpSettle, postResult */

// ─── Eval: route to content script ───

const _evalRelays = new Map();
const _EVAL_RELAY_MAX = 1000;
const _EVAL_RELAY_TTL_MS = 300000;

function _removeEvalRelay(relayId) {
  const pending = _evalRelays.get(relayId);
  if (!pending) return null;
  _evalRelays.delete(relayId);
  clearTimeout(pending.timeoutId);
  return pending;
}

function _expireEvalRelay(relayId) {
  const pending = _removeEvalRelay(relayId);
  if (!pending) return;
  postResult(
    pending.execution, null,
    `Eval relay timed out after ${_EVAL_RELAY_TTL_MS} ms`, pending.tabId);
}

function _registerEvalRelay(execution, tabId) {
  if (_evalRelays.size >= _EVAL_RELAY_MAX) return null;
  let relayId;
  do { relayId = crypto.randomUUID(); } while (_evalRelays.has(relayId));
  const timeoutId = setTimeout(
    () => _expireEvalRelay(relayId), _EVAL_RELAY_TTL_MS);
  _evalRelays.set(relayId, Object.freeze({ execution, tabId, timeoutId }));
  return relayId;
}

function _takeEvalRelay(relayId, tabId) {
  if (typeof relayId !== 'string') return null;
  const pending = _evalRelays.get(relayId);
  if (!pending || pending.tabId !== tabId) return null;
  _removeEvalRelay(relayId);
  return pending.execution;
}

// Evaluate through the debugger's Runtime domain. The V8 inspector compiles
// source without calling the page's `eval` / `Function` bindings, and REPL mode
// supplies top-level await. That says how the source ran, not whether its value
// is trustworthy: page code and page promise machinery can still choose it.
// Returns true after CDP dispatch, false only before submitted source runs.
async function _evalViaCdp(cmd, chromeTabId) {
  // A capture or a kept CDP session already owns the attachment; reuse it and
  // leave it in place, because detaching would end that capture or session.
  const held = Boolean(_cdpSessions[chromeTabId]) || Boolean(_netCaptures[chromeTabId]);
  try {
    if (!held) await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
  } catch (_) {
    return false;
  }
  try {
    let expression = cmd.code;
    try {
      if (/\breturn\b/.test(cmd.code)) {
        // REPL mode supplies top-level `await`, but `return` still needs a
        // function around it — and only when the source is a body rather than an
        // expression that merely contains the word. This parser heuristic is not
        // a security boundary: submitted text can escape the probe wrapper.
        // Without a successful probe, assume a body.
        const stripped = cmd.code.replace(/[\s;]+$/, '');
        let isExpr = false;
        let probe;
        try {
          probe = await chrome.debugger.sendCommand(
            { tabId: chromeTabId }, 'Runtime.evaluate',
            { expression: 'typeof (function(){return (async()=>{return ('
                + stripped + ')})()})',
              returnByValue: true });
          isExpr = !probe.exceptionDetails;
        } catch (_) {
        } finally {
          await _releaseCdpObjects(chromeTabId, probe);
        }
        if (!isExpr) {
          expression = cmd.code.includes('await')
            ? '(async()=>{' + cmd.code + '})()'
            : '(function(){' + cmd.code + '})()';
        }
      }
    } catch (_) {
      // A code value that is not a string can fail the shape checks above.
      // Nothing has been dispatched yet, so falling back repeats no work.
      return false;
    }
    // Dispatching may start the submitted source, so every outcome from here on
    // is terminal. Returning false could execute its side effects twice.
    let val;
    let err;
    try {
      const evaluated = await chrome.debugger.sendCommand(
        { tabId: chromeTabId }, 'Runtime.evaluate',
        { expression, replMode: true, awaitPromise: false }
      );
      err = _cdpError(evaluated);
      if (!err) {
        const settled = await _cdpSettle(chromeTabId, evaluated.result);
        val = settled.value;
        err = settled.error;
      } else {
        await _releaseCdpObjects(chromeTabId, evaluated);
      }
    } catch (error) {
      err = 'CDP eval failed: ' + (error.message || String(error));
    }
    try {
      await postResult(
        cmd._execution, val, err, String(chromeTabId), { world: 'cdp' });
    } catch (_) {}
    return true;
  } finally {
    if (!held) {
      try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
    }
  }
}

function _canUseMainWorldEval() {
  try {
    // Constant source only: this probes page CSP before submitted source runs.
    // The page owns `Function` and can influence the answer, which may change
    // the selected channel but conveys no value-integrity property.
    new Function('return undefined');
    return true;
  } catch (_) {
    return false;
  }
}

function _executeMainWorldEval(code) {
  try {
    const started = performance.now();
    const complete = (value) => ({
      ...value,
      ms: +(performance.now() - started).toFixed(1),
    });
    const errorResult = (error) => complete({
      e: error && (error.message || String(error)),
    });
    const stripped = code.replace(/[\s;]+$/, '');
    let isExpr = false;
    try {
      new Function('return (async()=>{return (' + stripped + ')})()');
      isExpr = true;
    } catch (_) {}
    const hasAwait = code.includes('await');
    const hasReturn = /\breturn\b/.test(code);
    if (hasAwait) {
      const body = isExpr
        ? 'return (async()=>{return (' + stripped + ')})()'
        : 'return (async()=>{' + code + '})()';
      return Promise.resolve((new Function(body))()).then(
        (result) => complete({ r: result }),
        (error) => errorResult(error)
      );
    }
    if (hasReturn) {
      const body = isExpr ? 'return (' + stripped + ')' : code;
      return complete({ r: (new Function(body))() });
    }
    return complete({ r: eval(code) });
  } catch (error) {
    return { e: error && (error.message || String(error)) };
  }
}

async function handleEval(cmd) {
  // Find which chrome tab to target
  let chromeTabId = cmd.chromeTab;
  if (!chromeTabId) {
    const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!active) return postResult(cmd._execution, null, 'No active tab', cmd.tabId);
    chromeTabId = active.id;
  }
  chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

  // Prefer banner-free MAIN-world injection. A constant, source-free probe
  // checks whether page CSP permits dynamic compilation; the page can influence
  // that diagnostic choice, but no submitted source has run at this point.
  let useMainWorld = false;
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId: chromeTabId },
      world: 'MAIN',
      func: _canUseMainWorldEval,
    });
    useMainWorld = probe[0]?.result === true;
  } catch (_) {}

  if (useMainWorld) {
    let results;
    const failMainWorld = async (detail) => postResult(
      cmd._execution, null, 'MAIN-world eval failed: ' + detail,
      String(chromeTabId), { world: 'page-main' });
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId: chromeTabId },
        world: 'MAIN',
        func: _executeMainWorldEval,
        args: [cmd.code],
      });
    } catch (error) {
      await failMainWorld(error.message || String(error));
      return;
    }
    if (!Array.isArray(results) || results.length === 0) {
      await failMainWorld('no result frame');
      return;
    }
    const frame = results[0];
    if (frame === null || typeof frame !== 'object') {
      await failMainWorld('invalid result frame');
      return;
    }
    if (Object.prototype.hasOwnProperty.call(frame, 'error')) {
      const frameError = frame.error;
      const detail = typeof frameError === 'string'
        ? frameError
        : frameError && frameError.message || String(frameError);
      await failMainWorld(detail);
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(frame, 'result')) {
      await failMainWorld('result frame has no result');
      return;
    }
    const res = frame.result;
    if (res === null || res === undefined) {
      await failMainWorld('no result envelope');
      return;
    }
    // `typeof null === 'object'`, so this test is only safe because the
    // guard above already returned on null and undefined. Keep them
    // together: separating them makes every non-envelope result look like an
    // envelope with undefined fields.
    const isEnvelope = typeof res === 'object';
    const val = isEnvelope ? res.r : res;
    const err = isEnvelope ? res.e || null : null;
    const extra = { world: 'page-main' };
    if (isEnvelope && typeof res.ms === 'number') extra.exec_ms = res.ms;
    await postResult(
      cmd._execution, val, err, String(chromeTabId), extra);
    return;
  }

  // The source-free probe could not establish a usable injection path, most
  // commonly because of page CSP. CDP is the fallback and shows Chrome's
  // debugger banner when it attaches. Once CDP dispatches, its result is
  // terminal; only a pre-dispatch attach/shape failure reaches the page relay.
  if (await _evalViaCdp(cmd, chromeTabId)) return;

  const relayId = _registerEvalRelay(cmd._execution, String(chromeTabId));
  if (!relayId) {
    await postResult(
      cmd._execution, null, 'Eval relay capacity exceeded',
      String(chromeTabId));
    return;
  }
  try {
    await chrome.tabs.sendMessage(chromeTabId, {
      type: 'eval',
      id: cmd.id,
      relayId,
      code: cmd.code,
      tabId: String(chromeTabId),
    });
  } catch (error) {
    _removeEvalRelay(relayId);
    await postResult(
      cmd._execution, null, error.message, String(chromeTabId));
  }
}
