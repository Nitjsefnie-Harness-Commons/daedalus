/* exported handleHotfixReplay, handleStoreHotfix, handleClearHotfix */
/* exported handleClearAllHotfixes, handleSetPermanent, handleListHotfixes */
/* global VERSION, _serializer, cdpClaimAttachment */
/* global _cdpError, _releaseCdpObjects */
/* global _canUseMainWorldEval, _executeMainWorldEval */
/* global _raceMainWorldEval, postResult */
/* global storageEntryBytes */
/* global _parseMatch, _scopeRefusal */

// ─── Hotfix system ───

const HOTFIX_KEY = 'daedalus-hotfixes';

// HOTFIX_QUOTA_BYTES bounds the record in BYTES, not in fixes. The record
// shares Chrome's `local` area with the token, the seen-delivery ledger and
// page-facing GM storage; a count cap does not bound that area — 160 fixes of
// 64 KiB is the repro.
//
//   Chrome area                 10,485,760
//   GM_TOTAL_QUOTA_BYTES         5,242,880   (5 MiB, every origin summed)
//   HOTFIX_QUOTA_BYTES           2,097,152   (2 MiB, this bound)
//   ------------------------------------------------------------
//   reserve for the extension    3,145,728   (3 MiB)
//
// The reserve spends on daedalus-token, daedalus-server,
// daedalus-segment-origins and daedalus-seen-dids; the ledger is largest,
// capped at 1000 entries of `<ms>_<counter>`, so ~22 KB against a reserve
// 140x that term.
const HOTFIX_QUOTA_BYTES = 2 * 1024 * 1024;

// Replay runs from here rather than the page relay, whose `eval` and blob
// <script> a page CSP forbids. It is the routing ordinary eval uses: a
// source-free probe, banner-free MAIN-world injection when dynamic
// compilation is available, CDP when not.
async function _eligibleHotfixes() {
  const data = await chrome.storage.local.get([HOTFIX_KEY]);
  const stored = data[HOTFIX_KEY];
  if (!stored || !Array.isArray(stored.fixes)) return [];
  return stored.fixes.filter(
    f => f && typeof f.code === 'string'
      && (f.permanent === true || stored.version === VERSION));
}

// Chrome supplies a url on every content-script message, so a null here is
// an unbindable request, not a page's path.
function _pageUrl(url) {
  if (typeof url !== 'string' || url === '') return null;
  try {
    return new URL(url);
  } catch (_) {
    return null;
  }
}

// What the CDP channel compares: the document binding, so it keeps the port,
// a real discriminator between two documents on one host. Spelled out rather
// than folded into `origin` because `file:` has none — its origin is "null",
// which a pattern from its own text can never match.
function _boundIdentity(parsed) {
  return parsed.protocol + '//' + parsed.host + parsed.pathname
    + parsed.search;
}

// What the site scope is matched against, a different job: Chrome match
// patterns have no port in the host position and ignore one, so a scope
// naming a host covers its other ports — what an operator writing
// `*://shop.example.com/*` means. Both drop the fragment: a hash change is
// not a new document, and the MAIN channel binds by document.
function _scopedIdentity(parsed) {
  return parsed.protocol + '//' + parsed.hostname + parsed.pathname
    + parsed.search;
}

// A tab id names a tab, not a document. Both MAIN-channel calls therefore name
// the document the request carried, and the answer's own documentId is
// checked against it. CDP is tab-bound and cannot name one, so its check lives
// inside the evaluation that runs the fix: `location` is [LegacyUnforgeable],
// so the page cannot spoof it, and one evaluation leaves no window between the
// check and the run.
const DOCUMENT_GONE = 'the document that asked for this fix is no longer'
  + ' the tab\'s live document';
const PAGE_IDENTITY = 'location.protocol + \'//\' + location.host'
  + ' + location.pathname + location.search';

async function _replayViaCdp(chromeTabId, identity, code) {
  // A capture or a kept session already owns the attachment; the claim
  // joins it rather than attaching over it, and the release below gives back
  // only this replay's share.
  const claim = cdpClaimAttachment(chromeTabId);
  try {
    await claim.ready;
  } catch (error) {
    return 'cdp attach failed: ' + (error && (error.message || String(error)));
  }
  try {
    // A leading statement, not a wrapper: an IIFE would move the fix's own
    // `var` and function declarations out of global scope and turn its
    // top-level `await` into a syntax error, so every stored fix written that
    // way breaks.
    const expression = 'if (' + PAGE_IDENTITY + ' !== '
      + JSON.stringify(identity) + ') throw new Error('
      + JSON.stringify(DOCUMENT_GONE) + ');\n'
      + code;
    const evaluated = await chrome.debugger.sendCommand(
      { tabId: chromeTabId }, 'Runtime.evaluate',
      { expression, replMode: true, awaitPromise: false });
    const failure = _cdpError(evaluated);
    await _releaseCdpObjects(chromeTabId, evaluated);
    return failure;
  } catch (error) {
    return error && (error.message || String(error));
  } finally {
    await claim.release();
  }
}

async function _replayHotfix(chromeTabId, documentId, identity, code,
                             reportChannel) {
  let useMainWorld;
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId: chromeTabId, documentIds: [documentId] },
      world: 'MAIN',
      func: _canUseMainWorldEval,
    });
    useMainWorld = probe[0]?.result === true;
  } catch (error) {
    // A document-bound probe that throws means the document that asked is
    // gone. Falling through to CDP here IS the misdelivery: that channel is
    // tab-bound and would run the fix in whatever document now holds the tab.
    // A page whose executeScript fails for another reason loses replay
    // instead, an honest refusal.
    return DOCUMENT_GONE + ': '
      + (error && (error.message || String(error)));
  }
  if (useMainWorld) {
    reportChannel('MAIN-world');
    let results;
    try {
      results = await chrome.scripting.executeScript({
        target: { tabId: chromeTabId, documentIds: [documentId] },
        world: 'MAIN',
        func: _executeMainWorldEval,
        args: [code],
      });
    } catch (error) {
      return error && (error.message || String(error));
    }
    const frame = Array.isArray(results) ? results[0] : null;
    if (!frame || typeof frame !== 'object') return 'no result frame';
    if (frame.documentId !== documentId) {
      return DOCUMENT_GONE + ': the answer was for ' + frame.documentId;
    }
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
  return _replayViaCdp(chromeTabId, identity, code);
}

async function handleHotfixReplay(chromeTabId, documentId, senderUrl) {
  let fixes;
  try {
    fixes = await _eligibleHotfixes();
  } catch (error) {
    console.error('[Daedalus] hotfix replay could not read the store:', error);
    return;
  }
  if (fixes.length === 0) return;
  // Chrome supplies the document and url on every content-script message, so
  // no real page reaches this; it is what an unbindable shape gets.
  const page = _pageUrl(senderUrl);
  if (typeof documentId !== 'string' || documentId === '' || !page) {
    console.error('[Daedalus] hotfix replay on tab ' + chromeTabId
                  + ' ran nothing: the request named no document, or a url'
                  + ' that names no page');
    return;
  }
  const identity = _boundIdentity(page);
  const failures = [];
  const skipped = [];
  let ran = 0;
  for (const hf of fixes) {
    const outOfScope = _scopeRefusal(hf, _scopedIdentity(page));
    if (outOfScope) {
      skipped.push(hf.id + ': ' + outOfScope);
      continue;
    }
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
        _replayHotfix(chromeTabId, documentId, identity, hf.code,
                      (c) => { channel = c; }),
        'hotfix fix');
    } catch (error) {
      const detail = error && (error.message || String(error));
      failure = (channel ? channel + ' ' : '') + detail;
    }
    if (failure) failures.push(hf.id + ': ' + failure);
    else ran++;
  }
  // Reported here rather than in the page: a page console is not where an
  // operator looks, and a page that refuses the fix is exactly the page whose
  // console is least trustworthy about why.
  if (failures.length > 0) {
    console.error('[Daedalus] hotfix replay failed on tab ' + chromeTabId
                  + ': ' + failures.join('; '));
  } else {
    console.log('[Daedalus] replayed ' + ran + ' hotfix(es) on tab '
                + chromeTabId);
  }
  // Separate from the failure aggregation: a fix the operator deliberately
  // scoped away is not an error, and the replayed count excludes it.
  if (skipped.length > 0) {
    console.log('[Daedalus] skipped ' + skipped.length
                + ' hotfix(es) by site scope on tab ' + chromeTabId
                + ': ' + skipped.join('; '));
  }
}

// Every mutation of the shared hotfix record runs through this lock. Without
// it two stores read one snapshot, both answer success, and only the later
// write survives: acknowledged loss of persisted user code.
const _withHotfixLock = _serializer();

async function handleStoreHotfix(cmd) {
  try {
    if (!cmd.fixId || !cmd.code) return postResult(
      cmd._execution, null, 'Missing fixId or code', 'extension');
    // The clear is a boolean beside the scope, not a value in it.
    if (cmd.clearScope !== undefined
        && typeof cmd.clearScope !== 'boolean') {
      return postResult(cmd._execution, null,
        'clearScope must be a boolean, not '
        + JSON.stringify(cmd.clearScope), 'extension');
    }
    const clearing = cmd.clearScope === true;
    const stated = cmd.match !== undefined && cmd.match !== null;
    if (clearing ? stated : (stated && !_parseMatch(cmd.match))) {
      return postResult(cmd._execution, null,
        clearing
          ? 'clearScope asks for the scope to go, so it cannot travel with'
            + ' a match pattern'
          : 'Unusable match pattern: '
            + (typeof cmd.match === 'string' ? cmd.match
              : JSON.stringify(cmd.match)),
        'extension');
    }
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY] || { version: VERSION, fixes: [] };
      stored.version = VERSION;
      const existing = stored.fixes.find(f => f.id === cmd.fixId);
      const permanent = (cmd.permanent === true) ? true
                      : (cmd.permanent === false) ? false
                      : (existing ? existing.permanent === true : false);
      // Carried over the way `permanent` is: a store leaving the field out
      // means "update the code", and dropping a scope would widen a fix for
      // one site into a fix for every site. `clearing` takes it away.
      const match = clearing ? undefined
        : (cmd.match === undefined || cmd.match === null)
          ? (existing ? existing.match : undefined) : cmd.match;
      stored.fixes = stored.fixes.filter(f => f.id !== cmd.fixId);
      const entry = {
        id: cmd.fixId, code: cmd.code, ts: Date.now(), permanent,
      };
      if (match) entry.match = match;
      stored.fixes.push(entry);
      // Measured on the composed record, after the replaced fixId stopped
      // counting, and refused before the write: an eviction would destroy
      // operator-persisted code, and a refusal is reversible through the
      // clear commands the operator already has.
      if (storageEntryBytes(HOTFIX_KEY, stored) > HOTFIX_QUOTA_BYTES) {
        throw new Error(
          'hotfix store would exceed the ' + HOTFIX_QUOTA_BYTES
          + '-byte hotfix limit');
      }
      await chrome.storage.local.set({ [HOTFIX_KEY]: stored });
      return {
        stored: cmd.fixId, total: stored.fixes.length, permanent,
        match: match || null,
      };
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
