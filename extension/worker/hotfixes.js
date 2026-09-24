/* exported handleHotfixReplay, handleStoreHotfix, handleClearHotfix */
/* exported handleClearAllHotfixes, handleSetPermanent, handleListHotfixes */
/* global VERSION, _serializer, _cdpSessions, _netCaptures */
/* global _cdpError, _releaseCdpObjects */
/* global _canUseMainWorldEval, _executeMainWorldEval */
/* global _raceMainWorldEval, postResult */
/* global storageEntryBytes */

// ─── Hotfix system ───

const HOTFIX_KEY = 'daedalus-hotfixes';

// HOTFIX_QUOTA_BYTES bounds the hotfix record in BYTES, not in fixes. The
// record is the extension's own state but shares Chrome's `local` area with
// the token, the seen-delivery ledger and the page-facing GM storage, and a
// store that passes the area's ceiling is followed by the extension's OTHER
// writes failing instead. A count cap does not bound it: 160 fixes of 64 KiB
// is the repro. Chrome measures QUOTA_BYTES as the JSON stringification of
// every value plus every key's length, so the whole record under one key is
// the record's JSON bytes plus the key's length.
//
//   Chrome area                 10,485,760
//   GM_TOTAL_QUOTA_BYTES         5,242,880   (5 MiB, every origin summed)
//   HOTFIX_QUOTA_BYTES           2,097,152   (2 MiB, this bound)
//   ------------------------------------------------------------
//   reserve for the extension    3,145,728   (3 MiB)
//
// The reserve is what the extension's own remaining keys spend:
// daedalus-token, daedalus-server, daedalus-segment-origins and
// daedalus-seen-dids. The ledger is the largest of those, count-capped at
// 1000 entries, and a delivery id is `<ms>_<counter>`, so 1000 of them is on
// the order of 22 KB and the reserve is roughly 140x that term.
const HOTFIX_QUOTA_BYTES = 2 * 1024 * 1024;

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

async function _replayHotfix(chromeTabId, code, reportChannel) {
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
    reportChannel('MAIN-world');
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
  reportChannel('CDP');
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
    // The replay picks its channel at run time, so the bound's refusal
    // carries the one that actually ran; a fix wedged in its own probe
    // reached none and is named without one.
    let channel = '';
    try {
      // The bound covers the WHOLE per-fix operation — the routing decision,
      // the probe and the inject — not only the injection call, so a fix
      // that wedges anywhere in its own replay cannot stop the fixes after
      // it on this or any later load of the page.
      failure = await _raceMainWorldEval(
        _replayHotfix(chromeTabId, hf.code, (c) => { channel = c; }),
        'hotfix fix');
    } catch (error) {
      const detail = error && (error.message || String(error));
      failure = (channel ? channel + ' ' : '') + detail;
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
      // Measured on the composed record, after the replaced fixId has
      // stopped counting, and refused before the write: an eviction would
      // destroy operator-persisted code, and a refusal is reversible
      // through the clear commands the operator already has.
      if (storageEntryBytes(HOTFIX_KEY, stored) > HOTFIX_QUOTA_BYTES) {
        throw new Error(
          'hotfix store would exceed the ' + HOTFIX_QUOTA_BYTES
          + '-byte hotfix limit');
      }
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
