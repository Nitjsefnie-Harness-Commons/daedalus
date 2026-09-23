/* exported _cdpSessions, handleCdp, _cdpError */
/* exported _releaseCdpObjects, _cdpSettle */
/* global postResult */

// chromeTabId -> true while a sticky CDP session is held
const _cdpSessions = {};

async function handleCdp(cmd) {
  if (!cmd.method) return postResult(
    cmd._execution, null, 'Missing CDP method', 'extension');
  try {
    let chromeTabId = cmd.tabId;
    if (!chromeTabId || chromeTabId === 'extension') {
      const [active] = await chrome.tabs.query(
        { active: true, currentWindow: true });
      if (!active) return postResult(
        cmd._execution, null, 'No active tab', 'extension');
      chromeTabId = active.id;
    }
    chromeTabId = typeof chromeTabId === 'number'
      ? chromeTabId : parseInt(chromeTabId);

    const heldBefore = !!_cdpSessions[chromeTabId];
    const keep = !!cmd.keep_session;
    if (!heldBefore) {
      await chrome.debugger.attach({ tabId: chromeTabId }, '1.3');
    }
    if (keep) _cdpSessions[chromeTabId] = true;
    try {
      const result = await chrome.debugger.sendCommand(
        { tabId: chromeTabId }, cmd.method, cmd.params || {});
      await postResult(cmd._execution, result, null, 'extension');
    } finally {
      if (!keep) {
        delete _cdpSessions[chromeTabId];
        try {
          await chrome.debugger.detach({ tabId: chromeTabId });
        } catch (_) {}
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
const _CDP_SAMPLE_MS = 100;

function _cdpNow() {
  return Date.now();
}

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

// Race one inspector settlement against a serviced bound: the sampler
// re-arms every _CDP_SAMPLE_MS, and a worker the host starved is charged
// at most one doubled interval per gap — never the wall length of the
// time it never ran in. `timedOut` flips only when the guard rejects on
// accrued serviced time, and `onLateResponse` then receives the
// settlement the abandoned promise eventually brings.
function _raceCdpSettlement(work, onLateResponse) {
  let sampledAtMs = _cdpNow();
  let accruedMs = 0;
  let timedOut = false;
  let samplerId;
  const guard = new Promise((_resolve, reject) => {
    const sample = () => {
      const nowMs = _cdpNow();
      accruedMs += Math.min(nowMs - sampledAtMs, 2 * _CDP_SAMPLE_MS);
      sampledAtMs = nowMs;
      if (accruedMs >= _CDP_PROMISE_TIMEOUT_MS) {
        timedOut = true;
        reject(new Error(
          `promise settlement timed out after ${
            _CDP_PROMISE_TIMEOUT_MS} ms`));
        return;
      }
      samplerId = setTimeout(sample, _CDP_SAMPLE_MS);
    };
    samplerId = setTimeout(sample, _CDP_SAMPLE_MS);
  });
  if (onLateResponse) {
    work.then((lateResponse) => {
      if (timedOut) onLateResponse(lateResponse);
    }, () => {});
  }
  return Promise.race([work, guard]).finally(() => clearTimeout(samplerId));
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
  const responsePromise = chrome.debugger.sendCommand(
    { tabId: chromeTabId }, settle[0],
    { ...settle[1], returnByValue: true });
  try {
    response = remote.subtype === 'promise'
      ? await _raceCdpSettlement(responsePromise, (lateResponse) => {
        _releaseCdpObjects(chromeTabId, lateResponse);
      })
      : await responsePromise;
    return { value: response.result?.value, error: _cdpError(response) };
  } finally {
    await _releaseCdpObjects(chromeTabId, remote, response);
  }
}
