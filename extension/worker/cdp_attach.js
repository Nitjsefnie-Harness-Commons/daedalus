/* exported cdpClaimAttachment, cdpForgetAttachment */

// ─── Debugger attachment ───
//
// Chrome allows one debugger per tab, and a second `attach` while one is
// live is refused with `Another debugger is already attached`. That refusal
// reaches the caller as a failed command, so four features wanting the
// attachment — a cdp command, the eval fallback, a hotfix replay through
// CDP, and a network capture — each attaching on its own meant two commands
// dispatched in the same turn both attached and one of the two came back an
// error. Nothing was misrouted; a call simply failed.
//
// The record is written and `ready` is set from the attach call all before
// this returns. That synchronous stretch is the whole of the fix: the second
// caller in the same turn finds the first caller's `ready` instead of
// racing a second attach, and a refused attach fails every joiner with the
// one error rather than each retrying an attachment Chrome already refused.
const _cdpClaims = new Map();

// A detach that has been issued but has not settled yet. A tab stays held
// until the promise settles, not until the call is made, so a claim arriving
// in that window must chain its attach BEHIND the detach — attaching now
// would be refused by the very detach that is about to make room for it.
const _cdpDetaching = new Map();

function cdpClaimAttachment(tabId, { keep } = {}) {
  const held = _cdpClaims.get(tabId);
  if (held) {
    held.refs += 1;
    // A kept claim outranks a transient one joining it: the session outlives
    // this caller, so the record must not be dropped when they leave.
    if (keep) held.keep = true;
    return { ready: held.ready, release: () => _cdpRelease(held) };
  }
  const entry = {
    tabId, refs: 1, keep: keep === true, ready: null, live: false,
  };
  entry.ready = _cdpAttach(tabId);
  // Registered before the caller can await, so `live` is already true by the
  // time any release runs. A refused attach leaves no claim — the next
  // command has to be free to try again rather than inherit a promise that
  // has already been rejected — and the joiners holding this same `ready`
  // still get the one failure.
  entry.ready.then(
    () => { entry.live = true; },
    () => {
      if (_cdpClaims.get(tabId) === entry) _cdpClaims.delete(tabId);
    });
  _cdpClaims.set(tabId, entry);
  return { ready: entry.ready, release: () => _cdpRelease(entry) };
}

function _cdpAttach(tabId) {
  const attach = () => Promise.resolve(
    chrome.debugger.attach({ tabId }, '1.3'));
  const settling = _cdpDetaching.get(tabId);
  if (!settling) return attach();
  return settling.then(attach, attach);
}

// Returns the detach it issued, or null when the attachment is somebody
// else's to keep: a second claim on the same tab, or a kept session.
function _cdpRelease(entry) {
  entry.refs -= 1;
  if (entry.refs > 0 || entry.keep) return null;
  if (_cdpClaims.get(entry.tabId) === entry) {
    _cdpClaims.delete(entry.tabId);
  }
  // An attach Chrome refused never took, so there is nothing to give back.
  // Detaching a tab nothing is attached to is a refusal from Chrome that
  // would land on whichever command ran next.
  if (!entry.live) return null;
  let settle;
  // Recorded BEFORE the call, not after: a claim that arrives while `detach`
  // is on the stack is already inside the window, and it has to find this.
  const settling = new Promise((resolve) => { settle = resolve; });
  _cdpDetaching.set(entry.tabId, settling);
  // A failed detach is nobody's error to report: the attachment is gone
  // either way, and the next claim re-attaches on its own.
  try {
    chrome.debugger.detach({ tabId: entry.tabId }).then(settle, settle);
  } catch (_) {
    settle();
  }
  settling.then(() => {
    if (_cdpDetaching.get(entry.tabId) === settling) {
      _cdpDetaching.delete(entry.tabId);
    }
  });
  return settling;
}

// `detach` is false where Chrome has already detached us — it will not
// detach twice, and asking is what the DevTools banner reports.
function cdpForgetAttachment(tabId, { detach } = {}) {
  const held = _cdpClaims.get(tabId);
  _cdpClaims.delete(tabId);
  _cdpDetaching.delete(tabId);
  if (!detach || !held) return;
  try {
    const detaching = chrome.debugger.detach({ tabId });
    if (detaching) detaching.catch(() => {});
  } catch (_) {}
}
