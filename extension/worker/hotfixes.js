/* exported handleHotfixReplay, handleStoreHotfix, handleClearHotfix */
/* exported handleClearAllHotfixes, handleSetPermanent, handleListHotfixes */
/* global VERSION, _serializer, _cdpSessions, _netCaptures */
/* global _cdpError, _releaseCdpObjects */
/* global _canUseMainWorldEval, _executeMainWorldEval */
/* global _raceMainWorldEval, postResult */
/* global storageEntryBytes */

// ─── Hotfix system ───

const HOTFIX_KEY = 'daedalus-hotfixes';

// HOTFIX_QUOTA_BYTES bounds the record in BYTES, not in fixes. The record
// shares Chrome's `local` area with the token, the seen-delivery ledger and
// the page-facing GM storage, and a count cap does not bound that area: 160
// fixes of 64 KiB is the repro.
//
//   Chrome area                 10,485,760
//   GM_TOTAL_QUOTA_BYTES         5,242,880   (5 MiB, every origin summed)
//   HOTFIX_QUOTA_BYTES           2,097,152   (2 MiB, this bound)
//   ------------------------------------------------------------
//   reserve for the extension    3,145,728   (3 MiB)
//
// The reserve spends on daedalus-token, daedalus-server,
// daedalus-segment-origins and daedalus-seen-dids; the ledger is the largest
// of those, count-capped at 1000 entries of `<ms>_<counter>`, so ~22 KB
// against a reserve roughly 140x that term.
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

// ─── Site scope ───
//
// A fix may carry `match`, a Chrome match pattern. Only the schemes a page
// can present and this extension can hold a host permission for are
// accepted. A pattern that does not parse is refused where it is stored,
// never stored and then silently never matched: a scope the operator
// believes exists and does not is worse than a refusal they can see.
const MATCH_SCHEME = /^(\*|http|https|file):\/\//;
const MATCH_LABEL = /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$/;

function _parseMatch(match) {
  if (typeof match !== 'string' || /\s/.test(match)) return null;
  const scheme = MATCH_SCHEME.exec(match);
  if (!scheme) return null;
  const rest = match.slice(scheme[0].length);
  const cut = rest.indexOf('/');
  if (cut === -1) return null;
  const host = rest.slice(0, cut);
  const path = rest.slice(cut);
  if (scheme[1] === 'file') {
    if (host !== '') return null;
  } else if (host === '' || !_matchableHost(host)) {
    return null;
  }
  return { scheme: scheme[1], host, path };
}

function _matchableHost(host) {
  if (host === '*') return true;
  const bare = host.startsWith('*.') ? host.slice(2) : host;
  if (bare === '' || bare.includes('*')) return false;
  return bare.split('.').every((label) => MATCH_LABEL.test(label));
}

// Matched as one string, because a parsed comparison would have to re-decide
// the pattern's own grammar rather than Chrome's.
function _matchesScope(parsed, url) {
  const literal = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const scheme = parsed.scheme === '*' ? 'https?' : parsed.scheme;
  const host = parsed.host === '' ? '' : (parsed.host === '*' ? '[^/]+'
    : parsed.host.split('.').map(
      (label) => label === '*' ? '[^./]+' : literal(label)).join('\\.'));
  const path = literal(parsed.path).replace(/\\\*/g, '.*');
  return new RegExp('^' + scheme + '://' + host + path + '$').test(url);
}

// Matched against the URL the browser reports for the sender, with no
// fallback: a scoped fix whose sender named no usable URL runs nowhere.
function _scopeRefusal(fix, senderUrl) {
  if (typeof fix.match !== 'string') return '';
  const parsed = _parseMatch(fix.match);
  if (!parsed) return 'scope ' + fix.match + ' does not parse';
  if (typeof senderUrl !== 'string' || senderUrl === '') {
    return 'scoped to ' + fix.match + ' and the sender named no url';
  }
  if (!_matchesScope(parsed, senderUrl)) {
    return 'scoped to ' + fix.match + ' and this page is not it';
  }
  return '';
}

// A tab id names a tab, not a document. Both MAIN-channel calls therefore
// name the document the request carried, and the answer's own documentId is
// checked against it. The CDP channel is tab-bound and cannot name one, so
// its check lives inside the evaluation that runs the fix:
// `window.location` is unforgeable in Chrome, and one evaluation leaves no
// window between the check and the run.
const DOCUMENT_GONE = 'the document that asked for this fix is no longer'
  + ' the tab\'s live document';

async function _replayViaCdp(chromeTabId, senderUrl, code) {
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
    // A leading statement, not a wrapper. An IIFE would move the fix's own
    // `var` and function declarations out of global scope and turn its
    // top-level `await` into a syntax error, so every stored fix written
    // that way would stop working.
    const expression = 'if (location.href !== ' + JSON.stringify(senderUrl)
      + ') throw new Error(' + JSON.stringify(DOCUMENT_GONE) + ');\n'
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
    if (!held) {
      try {
        await chrome.debugger.detach({ tabId: chromeTabId });
      } catch (_) {}
    }
  }
}

async function _replayHotfix(chromeTabId, documentId, senderUrl, code,
                             reportChannel) {
  let useMainWorld = false;
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId: chromeTabId, documentIds: [documentId] },
      world: 'MAIN',
      func: _canUseMainWorldEval,
    });
    useMainWorld = probe[0]?.result === true;
  } catch (error) {
    // A document-bound probe that throws means the document that asked is
    // gone. Falling through to the CDP channel here IS the misdelivery:
    // that channel is tab-bound and would run the fix in whatever document
    // now holds the tab. A page whose executeScript fails for another
    // reason loses replay instead, which is an honest refusal.
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
  return _replayViaCdp(chromeTabId, senderUrl, code);
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
  // Every channel delivers to the document the request named, and the site
  // scope is matched against a URL the browser reports, so a request
  // carrying neither can be bound to nothing and runs nothing.
  if (typeof documentId !== 'string' || documentId === ''
      || typeof senderUrl !== 'string' || senderUrl === '') {
    console.error('[Daedalus] hotfix replay on tab ' + chromeTabId
                  + ' ran nothing: the request named no document or url');
    return;
  }
  const failures = [];
  const skipped = [];
  for (const hf of fixes) {
    const outOfScope = _scopeRefusal(hf, senderUrl);
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
        _replayHotfix(chromeTabId, documentId, senderUrl, hf.code,
                      (c) => { channel = c; }),
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
  // Separate from the failure aggregation: a fix the operator deliberately
  // scoped away from this page is not an error, but counting it in the
  // replayed total would read as a success it never was.
  if (skipped.length > 0) {
    console.log('[Daedalus] skipped ' + skipped.length
                + ' hotfix(es) by site scope on tab ' + chromeTabId
                + ': ' + skipped.join('; '));
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
    if (cmd.match !== undefined && cmd.match !== null
        && !_parseMatch(cmd.match)) {
      return postResult(cmd._execution, null,
        'Unusable match pattern: ' + cmd.match, 'extension');
    }
    const outcome = await _withHotfixLock(async () => {
      const data = await chrome.storage.local.get([HOTFIX_KEY]);
      const stored = data[HOTFIX_KEY] || { version: VERSION, fixes: [] };
      stored.version = VERSION;
      const existing = stored.fixes.find(f => f.id === cmd.fixId);
      const permanent = (cmd.permanent === true) ? true
                      : (cmd.permanent === false) ? false
                      : (existing ? existing.permanent === true : false);
      stored.fixes = stored.fixes.filter(f => f.id !== cmd.fixId);
      const entry = {
        id: cmd.fixId, code: cmd.code, ts: Date.now(), permanent,
      };
      if (cmd.match) entry.match = cmd.match;
      stored.fixes.push(entry);
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
      return {
        stored: cmd.fixId, total: stored.fixes.length, permanent,
        match: cmd.match || null,
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
