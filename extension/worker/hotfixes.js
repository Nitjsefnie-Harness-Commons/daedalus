/* exported handleHotfixReplay, handleStoreHotfix, handleClearHotfix */
/* exported handleClearAllHotfixes, handleSetPermanent, handleListHotfixes */
/* global VERSION, _serializer, _cdpSessions, _netCaptures */
/* global _cdpError, _releaseCdpObjects */
/* global _canUseMainWorldEval, _executeMainWorldEval, postResult */

// ─── Hotfix system ───

const HOTFIX_KEY = 'daedalus-hotfixes';

// Replay runs from here rather than from the page relay, because the page
// relay only ever had `eval` and a blob <script> to work with and a page CSP
// that forbids both — github.com's, for one — refused every fix while the
// blocked blob load reported nothing back. This is the routing ordinary eval
// already uses: a source-free probe, banner-free MAIN-world injection when
// dynamic compilation is available, and CDP when it is not.
async function _eligibleHotfixes() {
  const data = await chrome.storage.local.get([HOTFIX_KEY]);
  const stored = data[HOTFIX_KEY];
  if (!stored || !Array.isArray(stored.fixes)) return [];
  return stored.fixes.filter(
    f => f && typeof f.code === 'string'
      && (f.permanent === true || stored.version === VERSION));
}

async function _replayViaCdp(chromeTabId, code) {
  // A capture or a kept session already owns the attachment; reuse it and
  // leave it in place, because detaching would end that capture or session.
  const held = Boolean(_cdpSessions[chromeTabId])
    || Boolean(_netCaptures[chromeTabId]);
  try {
    if (!held) await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
  } catch (error) {
    return 'cdp attach failed: ' + (error && (error.message || String(error)));
  }
  try {
    const evaluated = await chrome.debugger.sendCommand(
      { tabId: chromeTabId }, 'Runtime.evaluate',
      { expression: code, replMode: true, awaitPromise: false });
    const failure = _cdpError(evaluated);
    await _releaseCdpObjects(chromeTabId, evaluated);
    return failure;
  } catch (error) {
    return error && (error.message || String(error));
  } finally {
    if (!held) {
      try {
        await chrome.debugger.detach({ tabId: chromeTabId });
      } catch (_) {}
    }
  }
}

async function _replayHotfix(chromeTabId, code) {
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
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId: chromeTabId },
        world: 'MAIN',
        func: _executeMainWorldEval,
        args: [code],
      });
    } catch (error) {
      return error && (error.message || String(error));
    }
    const frame = Array.isArray(results) ? results[0] : null;
    if (!frame || typeof frame !== 'object') return 'no result frame';
    if (Object.prototype.hasOwnProperty.call(frame, 'error')) {
      const failure = frame.error;
      return typeof failure === 'string'
        ? failure : (failure && failure.message) || String(failure);
    }
    const res = frame.result;
    if (res && typeof res === 'object' && res.e) return res.e;
    return null;
  }
  return _replayViaCdp(chromeTabId, code);
}

async function handleHotfixReplay(chromeTabId) {
  let fixes;
  try {
    fixes = await _eligibleHotfixes();
  } catch (error) {
    console.error('[Daedalus] hotfix replay could not read the store:', error);
    return;
  }
  if (fixes.length === 0) return;
  const failures = [];
  for (const hf of fixes) {
    let failure;
    try {
      failure = await _replayHotfix(chromeTabId, hf.code);
    } catch (error) {
      failure = error && (error.message || String(error));
    }
    if (failure) failures.push(hf.id + ': ' + failure);
  }
  // Reported here rather than in the page: a page console is not where an
  // operator looks, and a page that refuses the fix is exactly the page whose
  // console is least trustworthy about why.
  if (failures.length > 0) {
    console.error('[Daedalus] hotfix replay failed on tab ' + chromeTabId
                  + ': ' + failures.join('; '));
  } else {
    console.log('[Daedalus] replayed ' + fixes.length + ' hotfix(es) on tab '
                + chromeTabId);
  }
}

// Every mutation of the shared hotfix record runs through this lock. Without
// it two stores read the same snapshot, both answer success, and only the
// later write survives — acknowledged loss of persistent user code.
const _withHotfixLock = _serializer();

async function handleStoreHotfix(cmd) {
  try {
    if (!cmd.fixId || !cmd.code) return postResult(
      cmd._execution, null, 'Missing fixId or code', 'extension');
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY] || { version: VERSION, fixes: [] };
      stored.version = VERSION;
      const existing = stored.fixes.find(f => f.id === cmd.fixId);
      const permanent = (cmd.permanent === true) ? true
                      : (cmd.permanent === false) ? false
                      : (existing ? existing.permanent === true : false);
      stored.fixes = stored.fixes.filter(f => f.id !== cmd.fixId);
      stored.fixes.push({
        id: cmd.fixId, code: cmd.code, ts: Date.now(), permanent,
      });
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return { stored: cmd.fixId, total: stored.fixes.length, permanent };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleClearHotfix(cmd) {
  try {
    if (!cmd.fixId) return postResult(
      cmd._execution, null, 'Missing fixId', 'extension');
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY];
      if (!stored) return { cleared: cmd.fixId, found: false };
      stored.fixes = stored.fixes.filter(f => f.id !== cmd.fixId);
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return {
        cleared: cmd.fixId, found: true, remaining: stored.fixes.length,
      };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleClearAllHotfixes(cmd) {
  try {
    const outcome = await _withHotfixLock(async () => {
      if (cmd.includePermanent === true) {
        await chrome.storage.local.remove([HOTFIX_KEY]);
        return { cleared: true, includePermanent: true };
      }
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY];
      if (!stored) return { cleared: true, kept: 0 };
      const before = stored.fixes.length;
      stored.fixes = stored.fixes.filter(f => f.permanent === true);
      const kept = stored.fixes.length;
      if (kept === 0) {
        await chrome.storage.local.remove([HOTFIX_KEY]);
      } else {
        await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      }
      return { cleared: true, removed: before - kept, kept };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleSetPermanent(cmd) {
  try {
    if (!cmd.fixId || typeof cmd.permanent !== 'boolean') {
      return postResult(
        cmd._execution, null, 'Missing fixId or permanent (bool)',
        'extension');
    }
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY];
      if (!stored) return {
        id: cmd.fixId, permanent: cmd.permanent, found: false,
      };
      const fix = stored.fixes.find(f => f.id === cmd.fixId);
      if (!fix) return {
        id: cmd.fixId, permanent: cmd.permanent, found: false,
      };
      fix.permanent = cmd.permanent;
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return { id: cmd.fixId, permanent: cmd.permanent, found: true };
    });
    await postResult(cmd._execution, outcome, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

async function handleListHotfixes(cmd) {
  try {
    const data = await chrome.storage.local.get([HOTFIX_KEY]);
    const stored = data[HOTFIX_KEY] || { version: VERSION, fixes: [] };
    await postResult(cmd._execution, stored, null, 'extension');
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}
