/* exported _cdpSessions, handleCdp, _cdpError */
/* exported _releaseCdpObjects, _cdpSettle */
/* global postResult */

const _cdpSessions = {}; // chromeTabId -> true while a sticky CDP session is held

async function handleCdp(cmd) {
  if (!cmd.method) return postResult(cmd._execution, null, 'Missing CDP method', 'extension');
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId || chromeTabId === 'extension') {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active) return postResult(cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number' ? chromeTabId : parseInt(chromeTabId);

    const heldBefore = !!_cdpSessions[chromeTabId];
    const keep = !!cmd.keep_session;
    if (!heldBefore) {
      await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
    }
    if (keep) _cdpSessions[chromeTabId] = true;
    try {
      const result = await chrome.debugger.sendCommand({ tabId: chromeTabId }, cmd.method, cmd.params || {});
      await postResult(cmd._execution, result, null, 'extension');
    } finally {
      if (!keep) {
        delete _cdpSessions[chromeTabId];
        try { await chrome.debugger.detach({ tabId: chromeTabId }); } catch (_) {}
      }
    }
  } catch (e) {
    await postResult(cmd._execution, null, e.message, 'extension');
  }
}

function _cdpError(response) {
  return response.exceptionDetails?.exception?.description
    || response.exceptionDetails?.text || null;
}

const _CDP_PROMISE_TIMEOUT_MS = 10000;

async function _releaseCdpObjects(chromeTabId, ...values) {
  const objectIds = new Set();
  for (const value of values) {
    const ids = [
      value?.objectId,
      value?.result?.objectId,
      value?.exceptionDetails?.exception?.objectId,
    ];
    for (const objectId of ids) {
      if (objectId) objectIds.add(objectId);
    }
  }
  for (const objectId of objectIds) {
    try {
      await chrome.debugger.sendCommand(
        { tabId: chromeTabId }, 'Runtime.releaseObject', { objectId });
    } catch (_) {}
  }
}

// Read an inspector-held value by value and release every handle returned by
// the protocol. This describes the CDP transport only: submitted source may
// already have routed its value through page-controlled machinery.
async function _cdpSettle(chromeTabId, remote) {
  if (!remote?.objectId) return { value: remote?.value, error: null };
  const settle = remote.subtype === 'promise'
    ? ['Runtime.awaitPromise', { promiseObjectId: remote.objectId }]
    : ['Runtime.callFunctionOn',
      { objectId: remote.objectId,
        functionDeclaration: 'function () { return this; }' }];
  let response;
  let timeoutId;
  let timedOut = false;
  const responsePromise = chrome.debugger.sendCommand(
    { tabId: chromeTabId }, settle[0],
    { ...settle[1], returnByValue: true });
  if (remote.subtype === 'promise') {
    responsePromise.then((lateResponse) => {
      if (timedOut) return _releaseCdpObjects(chromeTabId, lateResponse);
      return undefined;
    }, () => {});
  }
  try {
    response = remote.subtype === 'promise'
      ? await Promise.race([
        responsePromise,
        new Promise((_resolve, reject) => {
          timeoutId = setTimeout(() => {
            timedOut = true;
            reject(new Error(
              `promise settlement timed out after ${_CDP_PROMISE_TIMEOUT_MS} ms`));
          }, _CDP_PROMISE_TIMEOUT_MS);
        }),
      ])
      : await responsePromise;
    return { value: response.result?.value, error: _cdpError(response) };
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    await _releaseCdpObjects(chromeTabId, remote, response);
  }
}
