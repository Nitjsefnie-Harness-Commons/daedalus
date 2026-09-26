/* exported cdpClaimAttachment, cdpForgetAttachment */

// ─── Debugger attachment ───
//
// Chrome allows one debugger per tab, and a second `attach` while one is
// live is refused with `Another debugger is already attached` — a refusal
// that reaches the caller as a failed command. Four features want the
// attachment, so two dispatched in the same turn both attached and one of
// the two came back an error. Nothing was misrouted; a call simply failed.
//
// The record and `ready` are written before this returns, and that
// synchronous stretch is the whole of the fix: the second caller in the same
// turn finds the first caller's `ready` instead of racing a second attach,
// and a refused attach fails every joiner with the one error rather than
// each retrying.
const _cdpClaims = new Map();

// A detach issued but not yet settled. A tab stays held until the PROMISE
// settles, not until the call is made, so a claim arriving in that window
// must chain behind the detach rather than be refused by it.
const _cdpDetaching = new Map();

function cdpClaimAttachment(tabId, { keep } = {}) {
  const held = _cdpClaims.get(tabId);
  if (held) {
    held.refs += 1;
    // Unreachable at every shipped call site, and kept on purpose: no
    // claimant releases a kept claim today (cdp.js never releases one it
    // asked to keep), so `refs` cannot reach 0 on a
    // kept entry and the `keep` arm in `_cdpRelease` never fires. A caller
    // that did release its kept share would find the record standing, which
    // is the right answer, and this is what makes it so.
    if (keep) held.keep = true;
    return _cdpClaimHandle(held);
  }
  const entry = {
    tabId, refs: 1, keep: keep === true, ready: null, live: false,
  };
  entry.ready = _cdpAttach(tabId);
  // Registered before the caller can await, so `live` is true by the time
  // any release runs. A refused attach drops the record, so the next command
  // is free to try again rather than inherit a rejected promise; the joiners
  // holding this same `ready` still get the one failure.
  entry.ready.then(
    () => { entry.live = true; },
    () => {
      if (_cdpClaims.get(tabId) === entry) _cdpClaims.delete(tabId);
    });
  _cdpClaims.set(tabId, entry);
  return _cdpClaimHandle(entry);
}

// Two joiners share one entry and each owns one share of it, so the count is
// per-HANDLE: on the entry, the first joiner's release would speak for the
// second one's.
//
// This guard is defence in depth. The ENTRY guard in `_cdpRelease` is what
// carries the property — removing this one alone is green across every suite —
// because the first release already took the entry out of the map. This one
// is for a caller that releases twice, which no shipped call site does.
function _cdpClaimHandle(entry) {
  let released = false;
  return {
    ready: entry.ready,
    release() {
      if (released) return null;
      released = true;
      return _cdpRelease(entry);
    },
  };
}

function _cdpAttach(tabId) {
  const attach = () => Promise.resolve(
    chrome.debugger.attach({ tabId }, '1.3'));
  const settling = _cdpDetaching.get(tabId);
  if (!settling) return attach();
  return settling.then(attach, attach);
}

// Returns the detach it issued, or null when the attachment is not this
// release's to give back.
function _cdpRelease(entry) {
  entry.refs -= 1;
  if (entry.refs > 0 || entry.keep) return null;
  // The entry identity guards the ATTACHMENT as well as the map slot. A
  // release arriving after `cdpForgetAttachment` dropped this entry and a
  // NEWER claim re-attached the tab must not detach that newer attachment:
  // the stale release owns nothing that is still installed.
  if (_cdpClaims.get(entry.tabId) !== entry) return null;
  _cdpClaims.delete(entry.tabId);
  // A refused attach never took, so there is nothing to give back — and
  // detaching an unattached tab is a refusal that lands on whatever runs
  // next.
  if (!entry.live) return null;
  let settle;
  // Recorded BEFORE the call, not after: a claim that arrives while `detach`
  // is on the stack is already inside the window, and it has to find this.
  const settling = new Promise((resolve) => { settle = resolve; });
  _cdpDetaching.set(entry.tabId, settling);
  // Recorded rather than swallowed: a caught refusal that leaves no trace is
  // a refused detach and a successful one wearing the same face. Settling
  // is unconditional on purpose — a joining claim chains onto this promise,
  // and one that never settles is worse than the refusal it hid.
  try {
    chrome.debugger.detach({ tabId: entry.tabId })
      .then(settle, (error) => { _cdpRefused(entry.tabId, error); settle(); });
  } catch (error) {
    _cdpRefused(entry.tabId, error);
    settle();
  }
  settling.then(() => {
    if (_cdpDetaching.get(entry.tabId) === settling) {
      _cdpDetaching.delete(entry.tabId);
    }
  });
  return settling;
}

// `detach` is false where Chrome has already detached us — asking again is a
// refusal, and it is what the DevTools banner reports.
//
// Dropping `_cdpDetaching` here is an OPEN QUESTION, not a decision: a claim
// arriving afterwards attaches into a window Chrome may still be holding,
// which needs `chrome.debugger.onDetach` to fire while one of OUR detaches is
// in flight. Its documented reasons — `canceled_by_user`, `target_closed`,
// `replaced_with_chrome_devtools` — name none of them, so the window is
// unreachable on the documented reasons, unguarded in the code, and one line
// from guarded. Settling the Chrome behaviour settles it.
function cdpForgetAttachment(tabId, { detach } = {}) {
  const held = _cdpClaims.get(tabId);
  _cdpClaims.delete(tabId);
  _cdpDetaching.delete(tabId);
  if (!detach || !held) return;
  try {
    chrome.debugger.detach({ tabId })
      .catch((error) => { _cdpRefused(tabId, error); });
  } catch (error) {
    _cdpRefused(tabId, error);
  }
}

// The one place a refused detach becomes visible, reached from both sites
// above. The message is Chrome's, not a caller's, so it carries no bridge
// data to redact.
function _cdpRefused(tabId, error) {
  console.warn('[Daedalus] debugger detach refused on tab ' + tabId + ': '
               + ((error && error.message) || String(error)));
}
